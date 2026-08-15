#!/usr/bin/env python3
"""
GitHub org 웹훅을 받아 칸반 리뷰 태스크를 만든다.

스캐너(hermes-pr-scanner.py)는 3분마다 조직을 훑는 폴링 방식이다. 이 수신기는
PR 이 열리는 즉시 반응한다. 둘을 같이 돌려도 안전하다. 칸반의 idempotency key
가 같으면 태스크가 중복 생성되지 않으므로, 수신기가 놓친 이벤트는 스캐너가
3분 안에 주워간다.

태스크 생성 로직은 스캐너의 것을 그대로 불러 쓴다. 규칙이 두 곳으로 갈라지면
언젠가 반드시 어긋나기 때문이다.

사용법:
  hermes-webhook-receiver.py [--port 8645]

환경변수는 스캐너와 같은 것을 쓴다. 여기에 더해:
  WEBHOOK_SECRET_FILE   GitHub 웹훅 시크릿 (기본 ~/.hermes/webhook_secret)
"""

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))
LOG_DIR = os.environ.get("HERMES_OPS_LOG_DIR", os.path.join(HOME, "hermes-ops", "logs"))
LOG_PATH = os.path.join(LOG_DIR, "hermes-webhook-receiver.log")
SECRET_FILE = os.environ.get("WEBHOOK_SECRET_FILE",
                             os.path.join(HERMES_HOME, "webhook_secret"))
SCANNER = os.environ.get(
    "HERMES_SCANNER_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "hermes-pr-scanner.py"))
BIND = os.environ.get("WEBHOOK_BIND", "127.0.0.1")

# 이 액션에만 반응한다. 닫힘/라벨 변경 등은 리뷰할 것이 없다.
ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}

_seen_deliveries = {}
_seen_lock = threading.Lock()
DELIVERY_TTL = 3600


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_scanner():
    spec = importlib.util.spec_from_file_location("pr_scanner", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SC = load_scanner()


def secret():
    try:
        with open(SECRET_FILE, encoding="utf-8") as f:
            return f.read().strip().encode()
    except OSError:
        return b""


def valid_signature(body, header):
    sec = secret()
    if not sec:
        log("경고: 시크릿이 없어 서명을 검증할 수 없다. 요청을 거부한다.")
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(sec, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def already_handled(delivery_id):
    now = time.time()
    with _seen_lock:
        for k, t in list(_seen_deliveries.items()):
            if now - t > DELIVERY_TTL:
                del _seen_deliveries[k]
        if delivery_id in _seen_deliveries:
            return True
        _seen_deliveries[delivery_id] = now
        return False


def handle_pull_request(payload):
    action = payload.get("action")
    if action not in ACTIONS:
        return
    pr = payload.get("pull_request") or {}
    repo = ((payload.get("repository") or {}).get("name")) or ""
    number = pr.get("number")
    if not repo or not number:
        return
    if repo in SC.DENYLIST:
        log("  제외 목록: %s#%s" % (repo, number))
        return
    if pr.get("draft"):
        log("  초안 건너뜀: %s#%s" % (repo, number))
        return

    token = SC.gh_token()
    if not token:
        log("  토큰 없음, 중단")
        return

    # 기준 시각 이후에 열린 PR 만 본다. 스캐너와 같은 규칙이다.
    _, watermark, _ = SC.load_state()
    created_at = pr.get("created_at") or ""
    if watermark and created_at and created_at <= watermark:
        log("  기준 시각 이전 PR: %s#%s" % (repo, number))
        return

    sha = (pr.get("head") or {}).get("sha") or ""
    title = pr.get("title") or ""
    url = pr.get("html_url") or ""

    repo_path = SC.ensure_repo(token, repo)
    if not repo_path:
        return

    tid = SC.create_task(repo, number, title, sha, url, False, repo_path)
    if not tid:
        return

    detail = {"additions": pr.get("additions") or 0,
              "deletions": pr.get("deletions") or 0}
    model = SC.pick_model(token, repo, number, detail)
    if model and SC.set_model_override(tid, model):
        log("  생성 %s  %s#%s  action=%s  (문서 변경 -> %s)"
            % (tid, repo, number, action, model))
    else:
        log("  생성 %s  %s#%s  action=%s" % (tid, repo, number, action))


class Handler(BaseHTTPRequestHandler):
    server_version = "hermes-webhook/1.0"

    def _reply(self, code, msg="ok"):
        body = json.dumps({"status": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self._reply(200, "healthy")
        else:
            self._reply(404, "not found")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 5_000_000:
            self._reply(400, "bad length")
            return
        body = self.rfile.read(n)

        if not valid_signature(body, self.headers.get("X-Hub-Signature-256")):
            self._reply(401, "invalid signature")
            return

        event = self.headers.get("X-GitHub-Event") or ""
        delivery = self.headers.get("X-GitHub-Delivery") or ""
        if delivery and already_handled(delivery):
            self._reply(202, "duplicate")
            return

        if event == "ping":
            log("ping 수신")
            self._reply(200, "pong")
            return
        if event != "pull_request":
            self._reply(202, "ignored")
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            self._reply(400, "bad json")
            return

        # GitHub 은 10초 안에 응답을 기대한다. 작업은 뒤로 넘긴다.
        self._reply(202, "accepted")
        threading.Thread(target=self._safe_handle, args=(payload,),
                         daemon=True).start()

    def _safe_handle(self, payload):
        try:
            handle_pull_request(payload)
        except Exception as e:
            log("처리 실패: %s: %s" % (type(e).__name__, e))

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("WEBHOOK_PORT", "8645")))
    args = ap.parse_args()
    log("수신기 시작 %s:%d  (스캐너: %s)" % (BIND, args.port, SCANNER))
    ThreadingHTTPServer((BIND, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

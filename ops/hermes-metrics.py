#!/usr/bin/env python3
"""
칸반의 PR 리뷰 실적을 Prometheus 형식으로 내보낸다.

로그를 뒤지지 않고 "이번 주 몇 건 리뷰했나", "평균 얼마나 걸리나",
"보류 비율이 얼마나 되나" 를 바로 볼 수 있게 하는 것이 목적이다.

127.0.0.1 에만 바인딩한다. Prometheus 가 같은 호스트에서 긁어간다.

사용법:
  hermes-metrics.py [--port 10104] [--once]

  --once 를 주면 서버를 띄우지 않고 현재 메트릭을 표준출력에 찍는다. 점검용.
"""

import argparse
import json
import os
import re
import sqlite3
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))
KANBAN_DB = os.path.join(HERMES_HOME, "kanban.db")
BIND = os.environ.get("METRICS_BIND", "127.0.0.1")

TITLE_RE = re.compile(r"\[([A-Za-z0-9._-]+) PR review\] #(\d+)")
# 완료 요약 첫 줄에서 판정을 읽는다. 워커가 이 형식으로 쓴다.
VERDICT_RE = re.compile(r"PR #\d+\s+(승인|변경 요청|의견|리뷰 완료|리뷰 보류)")

VERDICT_MAP = {
    "승인": "approved",
    "변경 요청": "changes_requested",
    "의견": "commented",
    "리뷰 완료": "completed",
    "리뷰 보류": "blocked",
}


def snapshot(path):
    """WAL 을 반영한 일관된 사본을 만든다.

    파일 복사로는 -wal 에 있는 최근 커밋이 빠져 지표가 과거 상태로 남는다.
    backup() 은 SQLite 가 직접 일관된 상태를 떠 주므로 워커가 쓰는 중이어도 안전하다.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(path, timeout=15)
    try:
        dst = sqlite3.connect(tmp.name)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return tmp.name


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def collect():
    if not os.path.exists(KANBAN_DB):
        return ["# kanban.db 없음"]

    db = snapshot(KANBAN_DB)
    conn = None
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row

        tasks = conn.execute(
            "SELECT id, title, status, created_at, started_at, completed_at, "
            "       consecutive_failures "
            "FROM tasks WHERE created_by = 'pr-scanner'"
        ).fetchall()

        # 판정은 완료 이벤트의 요약 첫 줄에서 읽는다.
        verdicts = {}
        for r in conn.execute(
            "SELECT task_id, payload FROM task_events "
            "WHERE kind IN ('completed', 'blocked', 'gave_up') ORDER BY rowid"
        ):
            try:
                summary = (json.loads(r["payload"] or "{}").get("summary") or "")
            except ValueError:
                continue
            m = VERDICT_RE.search(summary.splitlines()[0] if summary else "")
            if m:
                verdicts[r["task_id"]] = VERDICT_MAP.get(m.group(1), "unknown")

        by_repo_status = {}
        by_verdict = {}
        durations = []
        now = time.time()
        last_completed = 0.0

        for t in tasks:
            m = TITLE_RE.match(t["title"] or "")
            repo = m.group(1) if m else "unknown"
            key = (repo, t["status"])
            by_repo_status[key] = by_repo_status.get(key, 0) + 1

            v = verdicts.get(t["id"])
            if v:
                by_verdict[v] = by_verdict.get(v, 0) + 1

            if t["started_at"] and t["completed_at"]:
                d = float(t["completed_at"]) - float(t["started_at"])
                if 0 < d < 86400:
                    durations.append(d)
            if t["completed_at"]:
                last_completed = max(last_completed, float(t["completed_at"]))

        running = sum(1 for t in tasks if t["status"] == "running")
        ready = sum(1 for t in tasks if t["status"] == "ready")

        out = []
        add = out.append

        add("# HELP hermes_pr_review_tasks 리뷰 태스크 수 (레포/상태별)")
        add("# TYPE hermes_pr_review_tasks gauge")
        for (repo, status), n in sorted(by_repo_status.items()):
            add('hermes_pr_review_tasks{repo="%s",status="%s"} %d'
                % (esc(repo), esc(status), n))

        add("# HELP hermes_pr_review_verdicts 판정별 누계")
        add("# TYPE hermes_pr_review_verdicts gauge")
        for v, n in sorted(by_verdict.items()):
            add('hermes_pr_review_verdicts{verdict="%s"} %d' % (esc(v), n))

        add("# HELP hermes_pr_review_running 실행 중인 리뷰 워커 수")
        add("# TYPE hermes_pr_review_running gauge")
        add("hermes_pr_review_running %d" % running)

        add("# HELP hermes_pr_review_queued 대기 중인 리뷰 태스크 수")
        add("# TYPE hermes_pr_review_queued gauge")
        add("hermes_pr_review_queued %d" % ready)

        add("# HELP hermes_pr_review_duration_seconds 리뷰 소요 시간")
        add("# TYPE hermes_pr_review_duration_seconds summary")
        if durations:
            durations.sort()
            def q(p):
                return durations[min(int(len(durations) * p), len(durations) - 1)]
            add('hermes_pr_review_duration_seconds{quantile="0.5"} %.1f' % q(0.5))
            add('hermes_pr_review_duration_seconds{quantile="0.9"} %.1f' % q(0.9))
            add("hermes_pr_review_duration_seconds_sum %.1f" % sum(durations))
        add("hermes_pr_review_duration_seconds_count %d" % len(durations))

        add("# HELP hermes_pr_review_last_completed_age_seconds 마지막 완료 이후 경과")
        add("# TYPE hermes_pr_review_last_completed_age_seconds gauge")
        add("hermes_pr_review_last_completed_age_seconds %d"
            % (int(now - last_completed) if last_completed else -1))

        return out
    finally:
        # 상주 서버라 스크레이프마다 연결을 닫지 않으면 FD 가 쌓인다.
        if conn is not None:
            conn.close()
        os.unlink(db)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            body = ("\n".join(collect()) + "\n").encode("utf-8")
        except Exception as e:
            body = ("# 수집 실패: %s\n" % e).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # 스크레이프마다 찍히면 로그만 커진다


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("METRICS_PORT", "10104")))
    ap.add_argument("--once", action="store_true", help="한 번 찍고 끝낸다")
    args = ap.parse_args()

    if args.once:
        print("\n".join(collect()))
        return 0

    HTTPServer((BIND, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

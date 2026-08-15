#!/usr/bin/env python3
"""
Hermes 게이트웨이 생존 감시. 죽으면 되살리고 Slack 으로 알린다.

launchd 의 KeepAlive 는 프로세스가 종료됐을 때만 재시작한다. 2026-08-12 사고는
프로세스가 살아있는 채로 Slack 소켓만 죽어서 35시간 방치됐다. 이 스크립트는
그 유형을 잡는다.

점검 항목:
  1. 게이트웨이 프로세스 존재
  2. Slack 소켓 재연결 루프에 빠졌는지 (로그의 연결 실패 패턴)
  3. webhook 포트 8644 응답

조치:
  비정상이면 launchctl kickstart 로 재시작하고 결과를 Slack 에 보고한다.
  폭주 방지: RESTART_COOLDOWN 안에는 재시작하지 않고,
  WINDOW 안에 MAX_RESTARTS 를 넘으면 재시작을 멈추고 사람을 부른다.

상태 파일: ~/.hermes/watchdog-state.json
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")

# ── 환경 설정 ──────────────────────────────────────────────────────────
# 아래 값은 전부 환경변수로 덮어쓸 수 있다. 기본값은 단독 Mac mini 기준.
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))
LOG_DIR = os.environ.get("HERMES_OPS_LOG_DIR", os.path.join(HOME, "hermes-ops", "logs"))
GH = os.environ.get("GH_BIN", "/opt/homebrew/bin/gh")
GATEWAY_LABEL = os.environ.get("HERMES_GATEWAY_LABEL", "ai.hermes.gateway")
# 알림을 받을 Slack 채널 ID. 비우면 알림을 보내지 않는다.
ALERT_CHANNEL = os.environ.get("ALERT_CHANNEL", "")
ENV_PATH = os.path.join(HERMES_HOME, ".env")
STATE_PATH = os.path.join(HERMES_HOME, "watchdog-state.json")
LOG_PATH = os.path.join(LOG_DIR, "hermes-watchdog.log")

AGENT_LOG = os.path.join(HERMES_HOME, "logs", "agent.log")
GATEWAY_LOG = os.path.join(HERMES_HOME, "logs", "gateway.log")

WEBHOOK_PORT = 8644

# 최근 이 시간(초) 안의 로그만 본다. 실행 주기보다 넉넉히 잡는다.
LOOKBACK = 300
# 이 횟수 이상 연결 실패가 찍혀 있으면 재연결 루프로 판정한다.
FAIL_THRESHOLD = 5

RESTART_COOLDOWN = 900        # 15분 안에는 다시 재시작하지 않는다
WINDOW = 7200                 # 2시간 안에
MAX_RESTARTS = 3              # 3회를 넘으면 사람을 부른다

FAIL_RE = re.compile(r"Failed to connect|Session is closed")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")



def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_env(key):
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def notify(text):
    """Slack 으로 보고. 헤르메스가 죽어 있어도 되게 봇 토큰으로 직접 호출한다."""
    token = read_env("SLACK_BOT_TOKEN")
    if not token:
        log("  경고: SLACK_BOT_TOKEN 을 못 읽어 알림을 못 보냄")
        return False
    body = urllib.parse.urlencode({
        "channel": ALERT_CHANNEL,
        "text": text,
    }).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
        if not resp.get("ok"):
            log("  경고: Slack 발송 실패 (%s)" % resp.get("error"))
            return False
        return True
    except (urllib.error.URLError, OSError, ValueError) as e:
        log("  경고: Slack 발송 예외 (%s)" % e)
        return False


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"restarts": [], "last_restart": 0, "escalated": False}


def save_state(s):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except OSError:
        pass


def gateway_pid():
    r = subprocess.run(["/usr/bin/pgrep", "-f", "hermes_cli.main gateway"],
                       capture_output=True, text=True)
    pids = [p for p in r.stdout.split() if p.strip()]
    return int(pids[0]) if pids else None


def tail_lines(path, limit=400):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = min(size, 200_000)
            f.seek(size - block)
            return f.read().decode("utf-8", "replace").splitlines()[-limit:]
    except OSError:
        return []


def recent_connect_failures():
    """최근 LOOKBACK 초 안에 찍힌 연결 실패 줄 수."""
    now = time.time()
    count = 0
    for path in (AGENT_LOG, GATEWAY_LOG):
        for line in tail_lines(path):
            if not FAIL_RE.search(line):
                continue
            m = TS_RE.match(line)
            if not m:
                continue
            try:
                ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if now - ts <= LOOKBACK:
                count += 1
    return count


def webhook_alive():
    try:
        with socket.create_connection(("127.0.0.1", WEBHOOK_PORT), timeout=5):
            return True
    except OSError:
        return False


def restart():
    uid = os.getuid()
    r = subprocess.run(
        ["/bin/launchctl", "kickstart", "-k", "gui/%d/%s" % (uid, GATEWAY_LABEL)],
        capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def main():
    problems = []

    pid = gateway_pid()
    if pid is None:
        problems.append("게이트웨이 프로세스 없음")

    fails = recent_connect_failures()
    if fails >= FAIL_THRESHOLD:
        problems.append("Slack 재연결 루프 (최근 %d분간 실패 %d회)"
                        % (LOOKBACK // 60, fails))

    if pid is not None and not webhook_alive():
        problems.append("webhook 포트 %d 무응답" % WEBHOOK_PORT)

    if not problems:
        return 0

    log("이상 감지: " + " / ".join(problems))

    state = load_state()
    now = time.time()
    state["restarts"] = [t for t in state.get("restarts", []) if now - t < WINDOW]

    if now - state.get("last_restart", 0) < RESTART_COOLDOWN:
        log("  쿨다운 중이라 재시작 보류")
        save_state(state)
        return 0

    if len(state["restarts"]) >= MAX_RESTARTS:
        if not state.get("escalated"):
            notify(":rotating_light: *Hermes 워치독 · 사람 호출*\n"
                   "%s\n최근 %d시간 안에 %d회 재시작했는데도 복구되지 않습니다. "
                   "자동 재시작을 중단합니다."
                   % ("\n".join("- " + p for p in problems),
                      WINDOW // 3600, len(state["restarts"])))
            state["escalated"] = True
            save_state(state)
            log("  재시작 한도 초과, 에스컬레이션")
        return 1

    ok, detail = restart()
    state["restarts"].append(now)
    state["last_restart"] = now
    state["escalated"] = False
    save_state(state)

    time.sleep(25)
    new_pid = gateway_pid()
    recovered = new_pid is not None and recent_connect_failures() < FAIL_THRESHOLD

    if recovered:
        msg = (":wrench: *Hermes 자동 복구*\n"
               "%s\n재시작 완료 (PID %s). 정상 동작 확인했습니다."
               % ("\n".join("- " + p for p in problems), new_pid))
        log("  재시작 성공 (PID %s)" % new_pid)
    else:
        msg = (":warning: *Hermes 재시작했으나 확인 실패*\n"
               "%s\n재시작 결과: %s / PID %s"
               % ("\n".join("- " + p for p in problems), ok, new_pid))
        log("  재시작 후에도 비정상 (PID %s, %s)" % (new_pid, detail[:120]))

    notify(msg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("오류: %s: %s" % (type(exc).__name__, exc))
        sys.exit(1)

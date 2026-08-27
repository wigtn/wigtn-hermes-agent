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
import shutil
import socket
import sqlite3
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

KANBAN_DB = os.path.join(HERMES_HOME, "kanban.db")
# 복구본을 놓아 둘 자리. 사람이 검증하고 넣는다.
RECOVER_DIR = os.environ.get("HERMES_RECOVER_DIR",
                             os.path.join(HOME, "hermes-ops", "kanban-recover"))
# 같은 손상으로 다시 알리기까지의 최소 간격. 2분마다 도는데 매번 보내면
# 소음이 되고, 소음이 되면 사람이 알림을 끈다.
CORRUPT_RENOTIFY = 3600
# 손상을 몇 번 연속으로 본 뒤에 사람을 부를지. 보드를 교체하는 찰나에는
# 파일이 없는 창이 반드시 생기고, 그것은 장애가 아니라 복구 작업이다.
CORRUPT_CONFIRM = 2
# 알림에 넣을 복구 도구 경로. 워치독과 같은 자리에 설치된다(install.sh 의 같은
# 루프). launchd 는 WorkingDirectory 를 주지 않아 cwd 가 `/` 이므로, 상대 경로를
# 안내하면 사람이 그대로 붙여넣었을 때 파일을 찾지 못한다. 보드가 깨져 리뷰가
# 멈춘 상황에서 읽는 안내다. 실행되지 않는 명령은 없느니만 못하다.
RESTORE_TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "hermes-kanban-restore.py")

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
# 연결 성공. 어댑터와 slack_bolt 가 각각 찍는다.
CONNECTED_RE = re.compile(r"Socket Mode connected|Bolt app is running")
# 세션 ID. 좀비 루프는 이미 대체된 옛 세션을 계속 물고 실패한다.
NEW_SESSION_RE = re.compile(r"A new session \((s_\d+)\) has been established")
OLD_SESSION_RE = re.compile(r"The old session \((s_\d+)\) has been abandoned")
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


def recent_connect_failures(since=None):
    """지금 연결에 대한 실패만 센다. 좀비 세션 것은 세지 않는다.

    좀비 실패는 바로 앞줄에 `The old session (X) has been abandoned` 가
    붙고 X 가 현재 세션이 아니다. 이미 대체된 연결 얘기라 장애가 아니다.
    실측 44건 중 42건이 그 짝으로 찍혔다.

    줄 단위로 귀속시키는 이유가 있다. 집계로 비교하면(가장 최신 new 세션
    vs 가장 최신 old 세션) 세션 교체가 한 번만 있어도 그 뒤 현재 세션의
    진짜 실패까지 좀비로 오판한다. 감시가 영구히 꺼진다.

    현재 세션을 모를 때는(로그 창 안에 확립 줄이 없을 때) 실패를 그대로
    센다. 모르면 시끄러운 쪽으로 기울어야 한다.

    since 를 주면 그 시각 이후만, 없으면 최근 LOOKBACK 초를 본다.
    """
    now = time.time()
    count = 0
    for path in (AGENT_LOG, GATEWAY_LOG):
        cur = None        # 지금 살아 있는 세션
        prev_old = None   # 직전 줄이 가리킨 옛 세션
        last_ok = None    # 마지막 연결 성공 시각
        cand = []         # 좀비를 걸러낸 실패 후보. 마지막에 다시 거른다
        for line in tail_lines(path):
            m = TS_RE.match(line)
            if not m:
                continue
            g = NEW_SESSION_RE.search(line)
            if g:
                cur = g.group(1)
                prev_old = None
                try:
                    last_ok = time.mktime(
                        time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    pass
                continue
            if CONNECTED_RE.search(line):
                try:
                    last_ok = time.mktime(
                        time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    pass
                prev_old = None
                continue
            g = OLD_SESSION_RE.search(line)
            if g:
                prev_old = g.group(1)
                continue
            if FAIL_RE.search(line):
                try:
                    ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    prev_old = None
                    continue
                in_window = (ts >= since) if since is not None else (now - ts <= LOOKBACK)
                zombie = (prev_old is not None and cur is not None and prev_old != cur)
                if in_window and not zombie:
                    # 여기서 바로 세지 않는다. 이 줄을 읽는 시점에는 뒤에
                    # 연결 성공이 오는지 알 수 없기 때문이다. 모아 두고
                    # 파일을 다 읽은 뒤 마지막 성공 이후 것만 남긴다.
                    cand.append(ts)
            prev_old = None
        # 마지막 연결 성공 이후의 실패만 센다. 그 이전 실패는 이미 복구된
        # 지난 일이다. 토큰 교체나 일시적 끊김이 여기 해당한다.
        count += sum(1 for t in cand if last_ok is None or t > last_ok)
    return count

def kanban_corrupt():
    """보드가 깨졌으면 사유를, 성하면 None.

    quick_check 를 쓴다. integrity_check 는 인덱스까지 전수 검사라 보드가
    커지면 2분 주기 안에 안 끝날 수 있다. quick_check 로도 실제 손상은
    잡혔다(`invalid page number`, `btreeInitPage() returns error code 11`).

    파일이 없는 것은 장애가 아니다. 아직 안 만들어졌을 수 있다.
    """
    if not os.path.exists(KANBAN_DB):
        return None
    # `mode=ro` 를 먼저 쓴다. `-shm` 이 있으면 WAL 안의 최신 커밋까지 본다.
    #
    # 실패했을 때 종류를 가린다. 예전에는 무조건 `immutable=1` 로 다시 봤는데,
    # 그것은 WAL 을 통째로 무시하고 본체만 읽으므로 WAL 이 깨진 보드를 `ok` 로
    # 판정한다. 감시가 손상을 놓친다.
    #
    #   WAL + sidecar 없음 (정상)  OperationalError  unable to open database file
    #   WAL 프레임 훼손 (손상)      DatabaseError     database disk image is malformed
    #   본체 훼손 (손상)           DatabaseError     file is not a database
    #
    # OperationalError 는 DatabaseError 의 하위 클래스라 먼저 잡으면 갈린다.
    # 열지 못한 것은 `-wal` 도 `-shm` 도 없을 때이고, 그때는 WAL 에 든 내용
    # 자체가 없으므로 본체만 봐도 잃는 것이 없다.
    try:
        c = sqlite3.connect("file:%s?mode=ro" % KANBAN_DB, uri=True, timeout=20)
        try:
            r = c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
        return None if r == "ok" else r
    except sqlite3.OperationalError as e:
        if "unable to open" not in str(e):
            return "열 수 없음: %s" % e
    except sqlite3.DatabaseError as e:
        # malformed, not a database. 손상이다. immutable 로 덮지 않는다.
        return "열 수 없음: %s" % e

    # 여기까지 왔으면 sidecar 가 없어 열지 못한 것이다. 본체만 본다.
    try:
        c = sqlite3.connect("file:%s?immutable=1" % KANBAN_DB, uri=True, timeout=20)
        try:
            r = c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error as e:
        return "열 수 없음: %s" % e
    return None if r == "ok" else r


def snapshot_board(dest):
    """보드를 뜬다. `-wal` 까지 함께 뜬다.

    본체만 뜨면 WAL 에 아직 체크포인트되지 않은 커밋이 빠진다. 그 상태로
    `.recover` 를 돌리면 최근 태스크가 사라진 복구본이 나오는데
    `quick_check` 는 `ok` 라서 사람이 검증해도 통과한다.

    `-shm` 은 뜨지 않는다. SQLite 가 `-wal` 로부터 다시 만든다. stale 한
    `-shm` 을 같이 두면 어긋난 인덱스를 물려줄 수 있다.

    순서는 본체 다음 WAL 이다. 반대로 하면 그 사이의 체크포인트가 WAL 에서
    빠진 내용을 본체에도 없는 것으로 만든다.
    """
    shutil.copy2(KANBAN_DB, dest)
    src = KANBAN_DB + "-wal"
    if os.path.exists(src):
        shutil.copy2(src, dest + "-wal")


def prepare_recovery():
    """`.recover` 로 복구본을 만들어 둔다. 넣지는 않는다.

    넣는 것은 사람이 판단한다. `.recover` 는 손상된 페이지 안의 행을 살리지
    못해 데이터가 줄 수 있다. 2026-08-27 손상에서 태스크 1건이 사라졌다.

    실패해도 조용히 넘긴다. 이건 덤이고, 본 일은 사람을 부르는 것이다.
    """
    try:
        os.makedirs(RECOVER_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        snap = os.path.join(RECOVER_DIR, "kanban.db.corrupt-%s" % ts)
        out = os.path.join(RECOVER_DIR, "recovered-%s.db" % ts)
        # 빈 자리에서 시작한다. 이름이 초 단위라 같은 초에 두 번 돌면 겹치는데,
        # 이미 있는 파일에 .recover 결과를 쏟으면 CREATE TABLE 이 "already
        # exists" 로 실패한다. 그 실패는 알림을 "복구본 생성 실패" 로 바꾸고,
        # 사람은 보드가 깨진 채 도구 없이 남는다.
        for p in (snap, snap + "-wal", out):
            if os.path.exists(p):
                os.unlink(p)
        snapshot_board(snap)
        sql = subprocess.run(["/usr/bin/sqlite3", snap, ".recover"],
                             capture_output=True, text=True, timeout=300)
        if not sql.stdout:
            return None
        r = subprocess.run(["/usr/bin/sqlite3", out], input=sql.stdout,
                           capture_output=True, text=True, timeout=300)
        # rc 만 보면 안 된다. sqlite3 CLI 는 스크립트 중간이 깨져도 0 으로
        # 끝나는 경우가 있다. stderr 가 비어 있어야 한다.
        if r.returncode != 0 or r.stderr.strip():
            return None
        c = sqlite3.connect("file:%s?mode=ro" % out, uri=True, timeout=20)
        try:
            if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return None
            n = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        finally:
            c.close()
        # 빈 보드를 "복구본" 이라며 내미는 것은 안내하지 않느니만 못하다.
        # 사람은 알림을 믿고 넣는다.
        if n == 0:
            return None
        return out, n
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        return None


def check_kanban():
    """보드 손상을 감시한다. 재시작으로 못 고치므로 알리기만 한다.

    detect_problems() 와 분리한 이유가 여기 있다. 거기 걸리면 게이트웨이를
    재시작하는데, 보드 손상은 재시작해도 그대로고 Slack 세션만 끊긴다.

    연속 CORRUPT_CONFIRM 회를 본 뒤에 부른다. 보드를 교체하는 동안에는
    파일이 없는 창이 생기고, 그것은 복구 작업이지 장애가 아니다.
    """
    reason = kanban_corrupt()
    state = load_state()

    if reason is None:
        # 확정 전에 사라진 것은 없던 일로 한다. 알리지 않았으니 거둘 것도 없다.
        if state.get("kanban_corrupt"):
            log("칸반 보드 복구 확인")
            notify(":white_check_mark: *칸반 보드 복구됨*\n"
                   "PR 자동리뷰가 다시 돕니다.")
            state.pop("kanban_corrupt", None)
            state.pop("kanban_notified", None)
        state.pop("kanban_seen", None)
        state.pop("kanban_streak", None)
        save_state(state)
        return False

    # 사유가 바뀌면 연속으로 보지 않은 것이다. 다시 센다.
    streak = state.get("kanban_streak", 0) + 1 \
        if state.get("kanban_seen") == reason else 1
    state["kanban_seen"] = reason
    state["kanban_streak"] = streak
    log("칸반 보드 손상 %d회: %s" % (streak, reason.replace("\n", " / ")[:200]))

    if streak < CORRUPT_CONFIRM:
        # 교체 중일 수 있다. 다음 주기에 다시 본다.
        save_state(state)
        return True

    now = time.time()
    known = (state.get("kanban_corrupt") == reason)
    if known and now - state.get("kanban_notified", 0) < CORRUPT_RENOTIFY:
        save_state(state)
        return True

    rec = prepare_recovery() if not known else None
    if rec:
        line = ("복구본을 만들어 뒀습니다 (태스크 %d건).\n"
                "```\n%s %s\n```" % (rec[1], RESTORE_TOOL, rec[0]))
    else:
        line = ("복구본 자동 생성에 실패했습니다. "
                "`sqlite3 <손상본> .recover` 로 직접 떠야 합니다.")

    notify(":rotating_light: *칸반 보드 손상 — PR 자동리뷰가 멈췄습니다*\n"
           "```\n%s\n```\n"
           "게이트웨이 재시작으로는 고쳐지지 않습니다. %s"
           % (reason[:400], line))
    state["kanban_corrupt"] = reason
    state["kanban_notified"] = now
    save_state(state)
    return True


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


def detect_problems(since=None):
    """지금 이상이 있으면 목록으로. 없으면 빈 리스트.

    since 를 주면 Slack 판정을 그 시각 이후로 좁힌다. 재시작 뒤 검증에
    쓴다. 재시작 전의 연결 성공은 복구의 근거가 될 수 없기 때문이다.
    """
    problems = []

    pid = gateway_pid()
    if pid is None:
        problems.append("게이트웨이 프로세스 없음")

    # 좀비 실패는 이미 걸러진 값이다. 여기에 가드를 더 얹지 않는다.
    # 가드를 겹칠수록 구멍이 늘어난다는 것을 두 번 겪었다.
    fails = recent_connect_failures(since=since)
    if fails >= FAIL_THRESHOLD:
        problems.append("Slack 재연결 루프 (최근 %d분간 실패 %d회)"
                        % (LOOKBACK // 60, fails))

    if pid is not None and not webhook_alive():
        problems.append("webhook 포트 %d 무응답" % WEBHOOK_PORT)

    return problems


def main():
    # 보드 손상은 재시작과 무관하다. 재시작 판정보다 먼저, 따로 본다.
    check_kanban()

    problems = detect_problems()

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

    restart_ts = time.time()

    ok, detail = restart()
    state["restarts"].append(now)
    state["last_restart"] = now
    state["escalated"] = False
    save_state(state)

    # 재시작 뒤 같은 점검을 다시 돌려 그 문제가 사라졌는지 본다.
    # Slack 연결 성공만 요구하면 두 방향으로 틀린다. webhook 때문에
    # 재시작했는데 Slack 로그가 안 찍히면 복구해도 실패로 보고하고,
    # 반대로 webhook 이 여전히 죽어 있는데 Slack 만 붙으면 복구됐다고 한다.
    time.sleep(30)
    new_pid = gateway_pid()
    remaining = detect_problems(since=restart_ts)
    recovered = new_pid is not None and not remaining

    if recovered:
        msg = (":wrench: *Hermes 자동 복구*\n"
               "%s\n재시작 완료 (PID %s). 정상 동작 확인했습니다."
               % ("\n".join("- " + p for p in problems), new_pid))
        log("  재시작 성공 (PID %s)" % new_pid)
    else:
        msg = (":warning: *Hermes 재시작했으나 확인 실패*\n"
               "%s\n재시작 결과: %s / PID %s\n남은 문제: %s"
               % ("\n".join("- " + p for p in problems), ok, new_pid,
                  ", ".join(remaining) if remaining else "없음(프로세스 확인 실패)"))
        log("  재시작 후에도 비정상 (PID %s, %s)" % (new_pid, detail[:120]))

    notify(msg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("오류: %s: %s" % (type(exc).__name__, exc))
        sys.exit(1)

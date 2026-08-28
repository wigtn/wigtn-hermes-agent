#!/usr/bin/env python3
"""리뷰가 설계대로 동작했는지 검사한다.

"리뷰가 쓸모 있었나" 는 보지 않는다. 그건 사람 판단과 시간이 필요하다.
여기서 보는 것은 참/거짓으로 떨어지는 계약 준수뿐이라 표본이 적어도 결론이 난다.

무엇을 보나. 리뷰 실행 1건마다 여섯 가지다.

  1. 요약 코멘트가 PR 에 하나만 있는가
  2. 첫 줄이 마커로 시작하는가        (아니면 다음 리뷰가 새 코멘트를 만든다)
  3. `### 검증` 블록이 있는가
  4. 검증 방법이 무엇으로 적혔는가     (CI / 로컬 / 없음)
  5. 검토한 head SHA 가 적혔는가
  6. 칸반 완료 첫 줄이 형식에 맞는가   ← 슬랙 알림이 이 줄로 만들어진다

6번이 중요하다. 코멘트만 검사하면 이 실패가 보이지 않는다. 실제로 2026-08-22
리뷰 6건 중 1건이 여기서 깨져 슬랙에 "변경 파일 4개(ops/..." 가 나갔다.

코멘트 검사의 한계. 요약 코멘트는 하나를 계속 수정하므로 PR 하나에 리뷰가
여러 번 돌면 마지막 것만 남는다. 그래서 코멘트에 적힌 head SHA 가 그 실행의
SHA 와 같을 때만 판정하고, 아니면 `덮어씀` 으로 남긴다. 이전 실행의 코멘트를
현재 코멘트로 채점하면 없는 성공과 없는 실패를 만든다.

읽기만 한다. GitHub 에 아무것도 쓰지 않는다.

사용법: hermes-review-audit.py [--days N] [--db 경로] [--quiet]
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))
KANBAN_DB = os.path.join(HERMES_HOME, "kanban.db")
GH = os.environ.get("GH_BIN", "/opt/homebrew/bin/gh")
ORG = os.environ.get("GITHUB_ORG", "")
TOKEN_FILE = os.environ.get("GH_TOKEN_FILE", os.path.join(HERMES_HOME, "gh_token"))
# 리뷰 코멘트를 남기는 계정. 이 계정의 코멘트만 센다.
BOT_LOGIN = os.environ.get("HERMES_REVIEW_BOT", "wigtn-contact")
# 태스크를 만든 주체. 스캐너와 웹훅 수신기가 같은 값을 쓴다.
CREATED_BY = os.environ.get("HERMES_TASK_CREATOR", "pr-scanner")
OUT_DB = os.environ.get(
    "HERMES_AUDIT_DB", os.path.join(HOME, "hermes-ops", "review-outcomes.db"))

MARKER = "<!-- hermes-review -->"

# 알림. 워치독과 같은 경로를 쓴다 — 봇 토큰으로 직접 부른다.
ENV_PATH = os.path.join(HERMES_HOME, ".env")
ALERT_CHANNEL = os.environ.get("ALERT_CHANNEL", "")
# 상태(마지막 성공·마지막 실패 알림). 요약에 마지막 실행 시각을 싣는다.
STATE_PATH = os.environ.get(
    "HERMES_AUDIT_STATE", os.path.join(HOME, "hermes-ops", "audit-state.json"))
# 같은 실패로 다시 부르기까지의 최소 간격. 매일 같은 소리를 하면 꺼진다.
FAIL_RENOTIFY = 6 * 3600

# 스캐너가 만드는 태스크 제목: [<레포> PR review] #<번호> <제목> (<sha>)
TITLE_RE = re.compile(r"\[([A-Za-z0-9._-]+) PR review\] #(\d+)")
SHA_RE = re.compile(r"\(([0-9a-f]{7,40})\)\s*$")
# 칸반 완료 첫 줄. 노티파이어가 이 줄을 그대로 슬랙에 보낸다.
# 판정별로 이모지가 갈린다. 승인·변경 요청·의견은 리뷰를 마친 것이고,
# `⏸` 는 리뷰를 하지 못한 것이다. 차단급 지적을 `⏸` 로 내면 팀이
# "리뷰가 멈췄다" 로 읽는다.
KANBAN_RE = re.compile(r"^[✅🔴💬⏸]\s*\[[^\]]+\]\s*PR\s*#\d+\s+.*—\s*\S")
# 코멘트에 적힌 검토 SHA
BODY_SHA_RE = re.compile(r"검토한 head SHA[^`]*`([0-9a-f]{7,40})`")
VERIFY_RE = re.compile(r"^###\s*검증\s*$", re.MULTILINE)
# `\s*` 를 쓰면 개행을 넘어가서, 값이 비었을 때 아랫줄을 값으로 집는다.
# 줄 안의 공백만 허용한다.
METHOD_RE = re.compile(r"^-[^\S\n]*방법[^\S\n]*:[^\S\n]*(\S[^\n]*)$", re.MULTILINE)
# 계약이 정한 값. 이 셋 중 정확히 하나여야 한다.
VALID_METHODS = ("CI", "로컬", "없음")
# 검증 블록의 끝. 다음 제목이 나오면 블록이 끝난 것으로 본다.
NEXT_HEADING_RE = re.compile(r"^#{1,4}\s", re.MULTILINE)
# ── 검사의 원칙 ────────────────────────────────────────────────────────
# 모든 검사는 "어디를 볼 것인가" 를 먼저 정하고 그 범위 안에서만 본다.
# 존재 여부만 보면 범위 밖의 우연한 일치에 속는다. 이 도구는 PR #11 리뷰에서
# 같은 뿌리의 결함을 네 번 냈다.
#
#   1. 방법 값을 검사하지 않음         (값을 안 봄)
#   2. 정규식이 개행을 넘어 다음 줄을 집음 (범위를 안 봄)
#   3. 검증 블록 밖의 방법에 속음       (범위를 안 봄)
#   4. 신고 줄이 마지막 줄인지 안 봄     (범위를 안 봄)
#
# 그래서 검사마다 범위를 아래처럼 못박는다.
#
#   검사              범위
#   marker            첫 줄
#   verify / method   `### 검증` 블록 안
#   sha               코멘트 전체 (계약이 위치를 정하지 않는다)
#   feedback          마지막 비어있지 않은 줄
#   kanban            칸반 완료 요약의 첫 줄
#
# 새 검사를 넣을 때는 범위를 먼저 정하고 이 표에 적는다.

FEEDBACK_TEXT = "오탐 집계에 씁니다"
# 마지막 줄은 이것과 정확히 같아야 한다. 두 번째는 2026-08-23 이전 계약이고,
# 그때 리뷰를 위반으로 세지 않기 위해 남겨 둔다.
FEEDBACK_LINES = (
    "> 이 리뷰가 도움이 됐으면 :+1:, 지적이 틀렸으면 :-1: 를 눌러주세요. 오탐 집계에 씁니다.",
    "> 틀린 지적이면 이 코멘트에 👎 반응을 남겨주세요. 오탐 집계에 씁니다.",
)


def read_env(key):
    """`.env` 에서 값 하나. 게이트웨이가 죽어 있어도 읽을 수 있어야 한다."""
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
    """Slack 으로 보고. 채널이 비어 있으면 조용히 넘어간다."""
    token = read_env("SLACK_BOT_TOKEN")
    if not token or not ALERT_CHANNEL:
        return False
    body = urllib.parse.urlencode({"channel": ALERT_CHANNEL, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=body,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return bool(json.loads(r.read().decode()).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def load_audit_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_audit_state(st):
    try:
        parent = os.path.dirname(STATE_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass


def norm_method(v):
    """백틱과 공백을 벗긴 값."""
    return (v or "").strip().strip("`").strip()


def last_line(body):
    """마지막 비어있지 않은 줄. 신고 줄은 여기 있어야 한다."""
    lines = [x.strip() for x in (body or "").splitlines() if x.strip()]
    return lines[-1] if lines else ""


def verify_block(body):
    """`### 검증` 블록 본문만 잘라낸다. 없으면 None.

    코멘트 전체에서 방법 줄을 찾으면, 블록 안에 방법이 없는데 다른 절에
    `- 방법: ...` 이 있으면 통과해버린다. 계약은 블록 안을 규정한다.
    """
    m = VERIFY_RE.search(body)
    if not m:
        return None
    rest = body[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    return rest[:nxt.start()] if nxt else rest


def gh_token():
    tok = os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def gh_api(path, jq, token):
    r = subprocess.run([GH, "api", path, "--paginate", "--jq", jq],
                       capture_output=True, text=True,
                       env=dict(os.environ, GH_TOKEN=token), timeout=60)
    if r.returncode != 0:
        return None
    return r.stdout


def db_snapshot(path):
    """WAL 을 반영한 일관된 사본. 워커가 쓰는 중이어도 안전하다."""
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


SCHEMA = """
CREATE TABLE IF NOT EXISTS review_runs (
  task_id            TEXT PRIMARY KEY,
  repo               TEXT,
  pr                 INTEGER,
  head_sha           TEXT,
  status             TEXT,
  created_at         INTEGER,
  duration_min       REAL,
  kanban_first_line  TEXT,
  kanban_ok          INTEGER,
  comment_scope      TEXT,     -- current | superseded | none
  comment_count      INTEGER,
  marker_ok          INTEGER,
  verify_ok          INTEGER,
  verify_method      TEXT,
  method_ok          INTEGER,
  sha_ok             INTEGER,
  feedback_ok        INTEGER,
  comment_body       TEXT,     -- 원본 보존. 기준이 바뀌면 재집계한다
  reactions_up       INTEGER,  -- 사람이 누른 :+1:. SHA 가 맞는 실행에만 귀속
  reactions_down     INTEGER,  -- 사람이 누른 :-1:. 오탐 신고다
  audited_at         INTEGER
);
"""

# 이미 만들어진 DB 에는 CREATE TABLE IF NOT EXISTS 가 컬럼을 더해 주지 않는다.
MIGRATIONS = (
    ("reactions_up", "ALTER TABLE review_runs ADD COLUMN reactions_up INTEGER"),
    ("reactions_down", "ALTER TABLE review_runs ADD COLUMN reactions_down INTEGER"),
)


def migrate(conn):
    """없는 컬럼만 더한다. 기존 행은 NULL 로 남는다 — 그때는 안 세었다는 뜻이고,
    0 으로 채우면 "아무도 안 눌렀다" 와 구분이 안 된다."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(review_runs)")}
    for col, ddl in MIGRATIONS:
        if col not in have:
            conn.execute(ddl)


def load_runs(days):
    """감사 대상 리뷰 실행 목록."""
    snap = db_snapshot(KANBAN_DB)
    try:
        c = sqlite3.connect(snap)
        c.row_factory = sqlite3.Row
        cut = time.time() - days * 86400
        rows = c.execute(
            "SELECT id, title, status, created_at, started_at, completed_at "
            "FROM tasks WHERE created_by = ? AND status IN ('done','blocked') "
            "AND created_at > ? ORDER BY created_at",
            (CREATED_BY, cut)).fetchall()
        out = []
        for r in rows:
            m = TITLE_RE.search(r["title"] or "")
            if not m:
                continue
            sm = SHA_RE.search(r["title"] or "")
            ev = c.execute(
                "SELECT payload FROM task_events WHERE task_id = ? "
                "AND kind IN ('completed','blocked','gave_up') "
                "ORDER BY rowid DESC LIMIT 1", (r["id"],)).fetchone()
            summary = ""
            if ev and ev["payload"]:
                try:
                    summary = (json.loads(ev["payload"]).get("summary") or "").strip()
                except (ValueError, AttributeError):
                    summary = ""
            dur = None
            if r["started_at"] and r["completed_at"]:
                dur = (r["completed_at"] - r["started_at"]) / 60.0
            out.append({
                "task_id": r["id"], "repo": m.group(1), "pr": int(m.group(2)),
                "head_sha": sm.group(1) if sm else "",
                "status": r["status"], "created_at": r["created_at"],
                "duration_min": dur,
                "first_line": summary.splitlines()[0] if summary else "",
            })
        return out
    finally:
        os.unlink(snap)


def fetch_comments(repo, pr, token, cache):
    """PR 의 봇 코멘트 목록. 각 항목은 {body, up, down}. PR 단위로 한 번만 조회.

    예전에는 `.body` 만 뽑아 받은 문자열을 MARKER 로 쪼갰다. 본문 안에 마커가
    우연히 들어 있으면 한 코멘트를 둘로 세는 방식이었고, 반응은 아예 못 읽었다.
    한 줄에 JSON 하나씩 받으면 쪼갤 필요가 없고 반응도 같이 온다.
    """
    key = (repo, pr)
    if key in cache:
        return cache[key]
    out = gh_api(
        "repos/%s/%s/issues/%d/comments" % (ORG, repo, pr),
        '.[] | select(.user.login=="%s") '
        '| {body: .body, up: .reactions["+1"], down: .reactions["-1"]}' % BOT_LOGIN,
        token)
    if out is None:
        cache[key] = None
        return None
    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        items.append({"body": d.get("body") or "",
                      "up": d.get("up") or 0,
                      "down": d.get("down") or 0})
    cache[key] = items
    return items


def audit(run, bodies):
    """실행 1건 채점. 코멘트 검사는 SHA 가 맞을 때만 한다."""
    r = dict(run)
    r["kanban_ok"] = 1 if KANBAN_RE.match(r["first_line"] or "") else 0

    if bodies is None:
        r.update(comment_scope="조회실패", comment_count=None, marker_ok=None,
                 verify_ok=None, verify_method=None, method_ok=None, sha_ok=None,
                 feedback_ok=None, comment_body=None,
                 reactions_up=None, reactions_down=None)
        return r

    r["comment_count"] = len(bodies)
    last = bodies[-1] if bodies else {"body": "", "up": 0, "down": 0}
    body = last["body"]
    # 반응은 SHA 가 맞을 때만 귀속한다. 아래에서 same 을 보고 채운다.
    r["reactions_up"] = None
    r["reactions_down"] = None

    # 이 실행의 코멘트가 맞는지. 아니면 이후 리뷰가 덮어쓴 것이다.
    bm = BODY_SHA_RE.search(body)
    same = bool(bm and r["head_sha"] and bm.group(1).startswith(r["head_sha"][:7]))
    if not bodies:
        r.update(comment_scope="없음", marker_ok=0, verify_ok=0,
                 verify_method=None, method_ok=0, sha_ok=0, feedback_ok=0,
                 comment_body="")
        return r
    if same:
        # 이 실행의 코멘트가 지금 남아 있는 것이다. 사람이 본 것도 이것이므로
        # 반응을 여기 붙인다. 덮어쓴 실행에 붙이면 같은 반응이 여러 번 세어진다.
        r["reactions_up"] = last["up"]
        r["reactions_down"] = last["down"]
    if not same:
        r.update(comment_scope="덮어씀", marker_ok=None, verify_ok=None,
                 verify_method=None, method_ok=None, sha_ok=None, feedback_ok=None,
                 comment_body=body)
        return r

    vblock = verify_block(body)
    lline = last_line(body)
    methods = [norm_method(x) for x in METHOD_RE.findall(vblock or "")]
    r.update(
        comment_scope="현재",
        marker_ok=1 if body.lstrip().startswith(MARKER) else 0,
        verify_ok=1 if vblock is not None else 0,
        # 방법은 정확히 하나여야 하고, 값도 계약이 정한 셋 중 하나여야 한다.
        # 존재만 보면 `방법: 야매` 도 통과한다.
        verify_method=("/".join(methods) if methods else None),
        # 검증 블록 자체가 없으면 "블록 누락" 으로 이미 센다. 여기서 또 세면
        # 같은 실패가 두 번 잡혀 위반 수가 부풀려진다. 그때는 해당없음(None).
        method_ok=(None if vblock is None
                   else (1 if (len(methods) == 1
                               and methods[0] in VALID_METHODS) else 0)),
        sha_ok=1 if bm else 0,
        # 마지막 줄이 계약 문구와 정확히 같아야 한다. 코멘트 어딘가에
        # 비슷한 말이 있는 것으로는 안 된다. 2 = 줄은 있는데 변형됨.
        feedback_ok=(1 if lline in FEEDBACK_LINES
                     else (2 if FEEDBACK_TEXT in lline else 0)),
        comment_body=body,
    )
    return r


def save(rows, db_path):
    # `--db audit.db` 처럼 파일명만 주면 dirname 이 빈 문자열이고
    # os.makedirs("") 는 FileNotFoundError 를 낸다. 그때는 현재 디렉터리다.
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    c = sqlite3.connect(db_path)
    c.executescript(SCHEMA)
    migrate(c)
    now = int(time.time())
    with c:
        for r in rows:
            c.execute(
                "INSERT INTO review_runs (task_id,repo,pr,head_sha,status,"
                "created_at,duration_min,kanban_first_line,kanban_ok,"
                "comment_scope,comment_count,marker_ok,verify_ok,verify_method,"
                "method_ok,sha_ok,feedback_ok,comment_body,"
                "reactions_up,reactions_down,audited_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET "
                "kanban_first_line=excluded.kanban_first_line,"
                "kanban_ok=excluded.kanban_ok,comment_scope=excluded.comment_scope,"
                "comment_count=excluded.comment_count,marker_ok=excluded.marker_ok,"
                "verify_ok=excluded.verify_ok,verify_method=excluded.verify_method,"
                "method_ok=excluded.method_ok,sha_ok=excluded.sha_ok,"
                "feedback_ok=excluded.feedback_ok,"
                "comment_body=excluded.comment_body,"
                "reactions_up=excluded.reactions_up,"
                "reactions_down=excluded.reactions_down,"
                "audited_at=excluded.audited_at",
                (r["task_id"], r["repo"], r["pr"], r["head_sha"], r["status"],
                 r["created_at"], r["duration_min"], r["first_line"],
                 r["kanban_ok"], r["comment_scope"], r.get("comment_count"),
                 r.get("marker_ok"), r.get("verify_ok"), r.get("verify_method"),
                 r.get("method_ok"), r.get("sha_ok"), r.get("feedback_ok"),
                 r.get("comment_body"), r.get("reactions_up"),
                 r.get("reactions_down"), now))
    c.close()


def mark(v):
    # 2 = 줄은 있는데 내용이 변형됨. 누락(X)과 구분해야 원인이 보인다.
    return {None: "-", 0: "X", 1: "O", 2: "~"}.get(v, "?")


def report(rows):
    if not rows:
        print("대상 리뷰 실행이 없습니다.")
        return 0
    print("리뷰 실행 %d건  (%s ~ %s)\n" % (
        len(rows),
        time.strftime("%m-%d", time.localtime(rows[0]["created_at"])),
        time.strftime("%m-%d", time.localtime(rows[-1]["created_at"]))))
    print("  %-12s %-22s %-5s %-7s %-5s %-6s %-5s %-6s %-5s %s" % (
        "태스크", "레포", "PR", "코멘트", "마커", "검증", "방법", "SHA", "👎", "칸반"))
    for r in rows:
        print("  %-12s %-22s #%-4d %-7s %-5s %-6s %-5s %-6s %-5s %s" % (
            r["task_id"], r["repo"][:22], r["pr"],
            "-" if r.get("comment_count") is None else (
                "%d%s" % (r["comment_count"],
                          "" if r["comment_count"] == 1 else " ←")),
            mark(r.get("marker_ok")), mark(r.get("verify_ok")),
            (r.get("verify_method") or "-")[:5],
            mark(r.get("sha_ok")), mark(r.get("feedback_ok")),
            mark(r["kanban_ok"])))

    viol = []
    dup = [r for r in rows if (r.get("comment_count") or 1) > 1]
    if dup:
        viol.append("코멘트 중복 %d건" % len(dup))
    for key, label in (("marker_ok", "마커 누락"), ("verify_ok", "검증 블록 누락"),
                       ("sha_ok", "head SHA 누락"), ("feedback_ok", "오탐 신고줄 누락"),
                       ("__drift", "오탐 신고줄 변형(👎 아님)"),
                       ("kanban_ok", "칸반 첫 줄 형식")):
        if key == "__drift":
            bad = [r for r in rows if r.get("feedback_ok") == 2]
        else:
            bad = [r for r in rows if r.get(key) == 0]
        if bad:
            viol.append("%s %d건" % (label, len(bad)))
    bad_m = [r for r in rows if r.get("method_ok") == 0
             and r.get("comment_scope") == "현재"]
    if bad_m:
        viol.append("검증 방법 값 위반 %d건 (%s)"
                    % (len(bad_m),
                       ", ".join(sorted({(r.get("verify_method") or "빈값")
                                         for r in bad_m}))))
    scored = [r for r in rows if r.get("comment_scope") == "현재"]
    print()
    print("  코멘트 채점 대상 %d건 (나머지 %d건은 이후 리뷰가 덮어씀 · 조회실패)"
          % (len(scored), len(rows) - len(scored)))
    print("  칸반 채점 대상 %d건 (전부)" % len(rows))
    print()
    if viol:
        print("  위반: " + " · ".join(viol))
        return 1
    print("  위반 없음")
    return 0


def weekly_summary(rows, days, state):
    """슬랙에 보낼 한 덩어리. 읽는 사람이 무엇을 할지 알 수 있게 쓴다.

    숫자만 던지면 읽히지 않는다. 어느 레포에서 무엇이 깨졌는지, 오탐 신고가
    달린 PR 이 어디인지를 짚는다.
    """
    scored = [r for r in rows if r.get("comment_scope") == "현재"]
    durs = sorted(r["duration_min"] for r in rows if r.get("duration_min"))
    median = durs[len(durs) // 2] if durs else None

    # 계약 위반을 레포별로 모은다. 한 레포에서 반복되면 그것이 고칠 지점이다.
    broken = {}
    for r in scored:
        bad = []
        if r.get("marker_ok") == 0:
            bad.append("마커")
        if r.get("verify_ok") == 0:
            bad.append("검증블록")
        if r.get("method_ok") == 0:
            bad.append("검증방법")
        if r.get("sha_ok") == 0:
            bad.append("head SHA")
        if r.get("comment_count") and r["comment_count"] > 1:
            bad.append("코멘트중복")
        if bad:
            broken.setdefault(r["repo"], []).extend(bad)
    kanban_bad = [r for r in rows if r.get("kanban_ok") == 0]

    up = sum(r.get("reactions_up") or 0 for r in scored)
    down = sum(r.get("reactions_down") or 0 for r in scored)
    flagged = [r for r in scored if (r.get("reactions_down") or 0) > 0]

    lines = ["*PR 자동리뷰 주간 요약* — 최근 %d일" % days]
    lines.append("리뷰 %d건 · 채점 대상 %d건%s"
                 % (len(rows), len(scored),
                    " · 중앙 %.1f분" % median if median else ""))

    if broken:
        lines.append("")
        lines.append("*계약 위반*")
        for repo, items in sorted(broken.items(),
                                  key=lambda kv: -len(kv[1]))[:5]:
            cnt = {}
            for it in items:
                cnt[it] = cnt.get(it, 0) + 1
            lines.append("  • %s — %s" % (
                repo, " · ".join("%s %d" % (k, v) for k, v in
                                 sorted(cnt.items(), key=lambda kv: -kv[1]))))
    if kanban_bad:
        lines.append("  • 칸반 첫 줄 형식 %d건 — 슬랙 알림이 태스크 제목으로 대체된다"
                     % len(kanban_bad))
    if not broken and not kanban_bad:
        lines.append("계약 위반 없음")

    lines.append("")
    if up or down:
        lines.append("*반응* 👍 %d · 👎 %d" % (up, down))
        for r in flagged[:3]:
            lines.append("  • 오탐 신고: %s #%d" % (r["repo"], r["pr"]))
    else:
        lines.append("*반응* 아직 없음 — 리뷰가 맞았는지 틀렸는지 재는 유일한 수단입니다. "
                     "코멘트에 👍/👎 를 눌러주세요.")

    last = state.get("last_ok")
    if last:
        lines.append("")
        lines.append("_마지막 감사 %s_"
                     % time.strftime("%m-%d %H:%M", time.localtime(last)))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="며칠치를 볼지 (기본 30)")
    ap.add_argument("--db", default=OUT_DB, help="원본을 쌓을 SQLite 경로")
    ap.add_argument("--quiet", action="store_true", help="표를 찍지 않는다")
    ap.add_argument("--slack-report", action="store_true",
                    help="월요일이면 주간 요약을 Slack 으로 보낸다")
    ap.add_argument("--force-report", action="store_true",
                    help="요일과 무관하게 요약을 보낸다 (점검용)")
    args = ap.parse_args()

    if not ORG:
        print("GITHUB_ORG 가 비어 있습니다.", file=sys.stderr)
        return 2
    token = gh_token()
    if not token:
        print("GitHub 토큰을 못 읽었습니다.", file=sys.stderr)
        return 2

    runs = load_runs(args.days)
    cache = {}
    rows = [audit(r, fetch_comments(r["repo"], r["pr"], token, cache)) for r in runs]
    save(rows, args.db)

    state = load_audit_state()
    # 월요일에만 보낸다. 매일 통계를 보내면 소음이 되고, 소음이 되면 사람이
    # 알림을 끈다. 지금 슬랙에는 리뷰 알림이 하루 12건 나간다.
    if (args.force_report
            or (args.slack_report and time.localtime().tm_wday == 0)):
        notify(weekly_summary(rows, args.days, state))
    state["last_ok"] = int(time.time())
    state.pop("last_fail_notified", None)
    save_audit_state(state)

    if args.quiet:
        return 0
    rc = report(rows)
    print("\n  원본 %d건을 %s 에 저장했습니다." % (len(rows), args.db))
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # 조용히 죽는 것이 실제로 일어난 일이다. 2026-08-24 이후 나흘간
        # 멈춰 있었고 아무도 몰랐다. 같은 실패를 매일 보내지는 않는다.
        import traceback
        traceback.print_exc()
        try:
            st = load_audit_state()
            now = int(time.time())
            if now - st.get("last_fail_notified", 0) > FAIL_RENOTIFY:
                notify(":warning: *리뷰 감사가 실패했습니다*\n```\n%s\n```\n"
                       "PR 자동리뷰 자체는 계속 돕니다. 측정만 멈춥니다."
                       % str(exc)[:300])
                st["last_fail_notified"] = now
                save_audit_state(st)
        except Exception:
            pass
        sys.exit(2)

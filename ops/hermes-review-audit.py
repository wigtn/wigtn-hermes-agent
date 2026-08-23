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

# 스캐너가 만드는 태스크 제목: [<레포> PR review] #<번호> <제목> (<sha>)
TITLE_RE = re.compile(r"\[([A-Za-z0-9._-]+) PR review\] #(\d+)")
SHA_RE = re.compile(r"\(([0-9a-f]{7,40})\)\s*$")
# 칸반 완료 첫 줄. 노티파이어가 이 줄을 그대로 슬랙에 보낸다.
KANBAN_RE = re.compile(r"^[✅⏸]\s*\[[^\]]+\]\s*PR\s*#\d+\s+.*—\s*\S")
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
  audited_at         INTEGER
);
"""


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
    """PR 의 봇 코멘트 목록. PR 단위로 한 번만 조회한다."""
    key = (repo, pr)
    if key in cache:
        return cache[key]
    out = gh_api("repos/%s/%s/issues/%d/comments" % (ORG, repo, pr),
                 '.[] | select(.user.login=="%s") | .body' % BOT_LOGIN, token)
    if out is None:
        cache[key] = None
        return None
    # --jq 가 코멘트마다 한 덩어리씩 낸다. 마커로 나눠 되붙인다.
    bodies = [b for b in out.split(MARKER) if b.strip()]
    bodies = [MARKER + b for b in bodies] if MARKER in out else (
        [out] if out.strip() else [])
    cache[key] = bodies
    return bodies


def audit(run, bodies):
    """실행 1건 채점. 코멘트 검사는 SHA 가 맞을 때만 한다."""
    r = dict(run)
    r["kanban_ok"] = 1 if KANBAN_RE.match(r["first_line"] or "") else 0

    if bodies is None:
        r.update(comment_scope="조회실패", comment_count=None, marker_ok=None,
                 verify_ok=None, verify_method=None, method_ok=None, sha_ok=None,
                 feedback_ok=None, comment_body=None)
        return r

    r["comment_count"] = len(bodies)
    body = bodies[-1] if bodies else ""

    # 이 실행의 코멘트가 맞는지. 아니면 이후 리뷰가 덮어쓴 것이다.
    bm = BODY_SHA_RE.search(body)
    same = bool(bm and r["head_sha"] and bm.group(1).startswith(r["head_sha"][:7]))
    if not bodies:
        r.update(comment_scope="없음", marker_ok=0, verify_ok=0,
                 verify_method=None, method_ok=0, sha_ok=0, feedback_ok=0,
                 comment_body="")
        return r
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
    now = int(time.time())
    with c:
        for r in rows:
            c.execute(
                "INSERT INTO review_runs (task_id,repo,pr,head_sha,status,"
                "created_at,duration_min,kanban_first_line,kanban_ok,"
                "comment_scope,comment_count,marker_ok,verify_ok,verify_method,"
                "method_ok,sha_ok,feedback_ok,comment_body,audited_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET "
                "kanban_first_line=excluded.kanban_first_line,"
                "kanban_ok=excluded.kanban_ok,comment_scope=excluded.comment_scope,"
                "comment_count=excluded.comment_count,marker_ok=excluded.marker_ok,"
                "verify_ok=excluded.verify_ok,verify_method=excluded.verify_method,"
                "method_ok=excluded.method_ok,sha_ok=excluded.sha_ok,"
                "feedback_ok=excluded.feedback_ok,"
                "comment_body=excluded.comment_body,audited_at=excluded.audited_at",
                (r["task_id"], r["repo"], r["pr"], r["head_sha"], r["status"],
                 r["created_at"], r["duration_min"], r["first_line"],
                 r["kanban_ok"], r["comment_scope"], r.get("comment_count"),
                 r.get("marker_ok"), r.get("verify_ok"), r.get("verify_method"),
                 r.get("method_ok"), r.get("sha_ok"), r.get("feedback_ok"),
                 r.get("comment_body"), now))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="며칠치를 볼지 (기본 30)")
    ap.add_argument("--db", default=OUT_DB, help="원본을 쌓을 SQLite 경로")
    ap.add_argument("--quiet", action="store_true", help="표를 찍지 않는다")
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
    if args.quiet:
        return 0
    rc = report(rows)
    print("\n  원본 %d건을 %s 에 저장했습니다." % (len(rows), args.db))
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("오류: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        sys.exit(2)

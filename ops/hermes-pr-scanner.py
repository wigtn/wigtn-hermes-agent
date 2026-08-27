#!/usr/bin/env python3
"""
조직의 열린 PR을 훑어 칸반 리뷰 태스크를 만든다.

배경: 웹훅으로 들어온 이벤트를 에이전트가 받아 칸반 태스크를 만드는 경로가
동작하지 않는다(웹훅 세션에 실행 도구가 붙지 않는다). 태스크 생성은 판단이
필요한 일이 아니므로 여기서 결정적으로 처리하고, 리뷰 자체는 기존 칸반 워커에
맡긴다. 워커는 도구를 정상적으로 받는다(7월에 268건을 이 경로로 처리했다).

부수 효과로 org 웹훅 권한(admin:org_hook) 없이도 조직 전체 레포가 커버된다.

중복 방지: idempotency key 를 `<repo>-pr-review:<번호>:<head sha>` 로 잡는다.
같은 PR 이라도 새 커밋이 올라오면 sha 가 바뀌어 다시 리뷰한다.

사용법: hermes-pr-scanner.py [--dry-run] [--limit N]
"""

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
import subprocess
import sys
import time

HOME = os.path.expanduser("~")

# ── 환경 설정 ──────────────────────────────────────────────────────────
# 아래 값은 전부 환경변수로 덮어쓸 수 있다. 기본값은 단독 Mac mini 기준.
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))
LOG_DIR = os.environ.get("HERMES_OPS_LOG_DIR", os.path.join(HOME, "hermes-ops", "logs"))
GH = os.environ.get("GH_BIN", "/opt/homebrew/bin/gh")
GATEWAY_LABEL = os.environ.get("HERMES_GATEWAY_LABEL", "ai.hermes.gateway")
# 알림을 받을 Slack 채널 ID. 비우면 알림을 보내지 않는다.
ALERT_CHANNEL = os.environ.get("ALERT_CHANNEL", "")
# 토큰 파일. GH_TOKEN 환경변수가 있으면 그쪽이 우선한다.
KANBAN_DB = os.path.join(HERMES_HOME, "kanban.db")
TOKEN_FILE = os.environ.get("GH_TOKEN_FILE", os.path.join(HERMES_HOME, "gh_token"))
LOG_PATH = os.path.join(LOG_DIR, "hermes-pr-scanner.log")
STATE_PATH = os.path.join(HERMES_HOME, "pr-scanner-state.json")

HERMES_BIN = os.environ.get("HERMES_BIN", "hermes")

ORG = os.environ.get("GITHUB_ORG", "")

# 리뷰 대상에서 뺀다. 포크·논문·일회성 레포.
DENYLIST = {
    r.strip() for r in os.environ.get("HERMES_PR_DENYLIST", "").split(",") if r.strip()
}

REVIEW_ROOT = os.environ.get(
    "HERMES_REVIEW_ROOT", os.path.join(HOME, "hermes-ops", "reviews")
)
GIT = "/usr/bin/git"

MAX_RUNTIME = "45m"
SKILL = "hermes-pr-autoreview"
ASSIGNEE = "default"


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def gh_token():
    # 우선순위: GH_TOKEN 환경변수 -> 토큰 파일
    tok = os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

def run(args, env_extra=None, timeout=120):
    env = dict(os.environ)
    env["HERMES_HOME"] = HERMES_HOME
    if env_extra:
        env.update(env_extra)
    return subprocess.run(args, capture_output=True, text=True,
                          env=env, timeout=timeout)


def open_prs(token, limit):
    """조직 전체의 열린 PR 목록. 실패하면 빈 리스트."""
    r = run([GH, "api", "-X", "GET", "search/issues",
             "-f", "q=is:pr is:open org:%s" % ORG,
             "-F", "per_page=%d" % min(limit, 100),
             "--jq", ".items"],
            {"GH_TOKEN": token})
    if r.returncode != 0:
        log("PR 목록 조회 실패: %s" % r.stderr.strip()[:200])
        return []
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        log("PR 목록 파싱 실패")
        return []
    out = []
    for it in items:
        repo = it.get("repository_url", "").rsplit("/", 1)[-1]
        if not repo or repo in DENYLIST:
            continue
        out.append({"repo": repo, "number": it["number"],
                    "title": it["title"],
                    "created_at": it.get("created_at", "")})
    return out


def pr_detail(token, repo, number):
    r = run([GH, "api", "repos/%s/%s/pulls/%d" % (ORG, repo, number),
             "--jq", "{sha: .head.sha, draft: .draft, state: .state, "
                      "url: .html_url, additions: .additions, "
                      "deletions: .deletions, changed: .changed_files}"],
            {"GH_TOKEN": token})
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


MARKER = "<!-- hermes-review -->"

# 레포별 검증 수단. 모델이 추측할 값이 아니라 우리가 아는 값이라서 여기서 준다.
#   ci  = PR 에 CI 가 붙어 있는가 (있으면 로컬 실행은 중복이다)
#   cmd = CI 가 없을 때 로컬에서 돌릴 명령. 빈 문자열이면 로컬 수단이 없다는 뜻.
# 새 레포는 여기에 없어도 된다. 없으면 모델이 CI 를 먼저 보고 스스로 판단한다.
REPO_VERIFY = {
    "wigtn-spear":                {"ci": True,  "cmd": "npm ci && npm run check"},
    "wigtn-platform-mvp":         {"ci": True,  "cmd": "pnpm i && pnpm run typecheck"},
    "wigtn-hermes-agent":         {"ci": True,  "cmd": "make check"},
    "wigtn-plugins":              {"ci": True,  "cmd": ""},
    "wigtn-foundry":              {"ci": True,  "cmd": ""},
    "wigex":                      {"ci": True,  "cmd": ""},
    "portfolio-recruit-platform": {"ci": True,  "cmd": "npm ci && npm run typecheck"},
    "wigtn-webagency-template":   {"ci": False, "cmd": "npm ci && npm run lint && npm run typecheck"},
    "wigtn-webpage":              {"ci": False, "cmd": "npm ci && npm run lint"},
    "wigtn-tech-report":          {"ci": False, "cmd": "npm ci && npm run lint"},
    "NAACL-2027-DEMO":            {"ci": False, "cmd": "python -m pytest -q"},
    "web-agency":                 {"ci": False, "cmd": ""},
}

VERIFY_TIMEOUT = os.environ.get("HERMES_VERIFY_TIMEOUT", "8분")


def verify_block(repo):
    """레포에 맞는 검증 지시문. 모델이 알 수 없는 값만 준다."""
    v = REPO_VERIFY.get(repo)
    if v is None:
        return (
            "이 레포는 검증 수단이 등록되어 있지 않다.\n"
            "`gh pr checks` 로 CI 가 있는지 먼저 본다. 있으면 그 결과를 근거로 쓴다.\n"
            "없으면 레포에서 검증 명령을 찾아 돌린다. 없으면 \"없음\" 으로 기록한다."
        )
    if v["ci"] and not v["cmd"]:
        return (
            "**이 레포는 PR 에 CI 가 있다. `gh pr checks` 결과를 근거로 쓴다.**\n"
            "로컬에서 따로 돌리지 않는다. CI 가 더 정확하고 빠르다.\n"
            "체크가 없다고 나오면 \"없음\" 으로 기록한다."
        )
    if v["ci"]:
        return (
            "**이 레포는 PR 에 CI 가 있다. `gh pr checks` 결과를 근거로 쓴다.**\n"
            "로컬에서 따로 돌리지 않는다.\n"
            "체크가 없다고 나오면 그때만 로컬로 `%s` 를 돌린다. %s에서 끊는다."
            % (v["cmd"], VERIFY_TIMEOUT)
        )
    if v["cmd"]:
        return (
            "**이 레포는 PR CI 가 없다. 로컬에서 직접 돌린다.**\n"
            "`%s` 를 워크트리에서 실행한다. %s에서 끊는다.\n"
            "이 명령은 의존성 설치를 포함한다. 그 외의 설치는 하지 않는다."
            % (v["cmd"], VERIFY_TIMEOUT)
        )
    return (
        "**이 레포는 PR CI 도 없고 등록된 검증 명령도 없다.**\n"
        "검증은 \"없음\" 으로 기록하고 넘어간다. 없는 명령을 찾아 헤매지 않는다."
    )


def build_body(repo, number, title, sha, url):
    return f"""{ORG}/{repo} PR #{number} 를 리뷰한다.

- PR: {url}
- 제목: {title}
- 큐에 들어온 head SHA: `{sha}`

## 먼저

**워크트리 HEAD 를 맞춘다.** 워크트리는 베이스 클론의 HEAD 에서 만들어지므로
목표 커밋이 아닐 수 있다. 다음을 그대로 실행한다.

```
git rev-parse HEAD                      # 현재 워크트리 HEAD
git fetch origin {sha} || git fetch origin
git checkout --detach {sha}
git rev-parse HEAD                      # {sha} 와 같은지 확인
```

체크아웃에 성공하면 그대로 리뷰를 진행한다. 이것은 판단이 아니라 정해진
절차다. HEAD 가 다르다는 이유만으로 보류하지 않는다.

체크아웃이 **실패했을 때만** 보류한다.

GitHub 의 현재 head SHA 가 `{sha}` 와 다르면 낡은 큐이므로 보류한다.

## 리뷰

변경된 코드를 직접 읽는다. 보는 순서는 이렇다.

1. 이 변경으로 깨지는 것이 있는가 (동작, 계약, 경쟁 조건, 실패 경로)
2. 이 변경이 의도한 일을 실제로 하는가
3. 레포 관례를 따랐는가

1번만 차단 사유가 된다. 2번은 지적하되 등급을 낮추고, 3번은 참고로만 적는다.
취향 문제는 쓰지 않는다.

## 검증

{verify_block(repo)}

요약 코멘트에 이 블록을 그대로 넣는다.

### 검증
- 방법: `CI` · `로컬` · `없음` 중 **하나만** 적는다. 줄을 늘리지 않는다.
  여러 수단을 썼으면 판정 근거로 삼은 것 하나를 적고 나머지는 `근거` 에 쓴다
- 명령/체크: `...`
- 결과: 통과 | 실패 | 미실행 | 없음
- 근거: ...

## 근거 기준

**심각도는 근거로 정한다.**

- 차단 문제로 올릴 수 있는 것은 재현했거나 실행해서 확인한 것뿐이다.
- **검증 수단이 있는데 확인하지 않았으면 REQUEST_CHANGES 를 낼 수 없다.**
  검증 수단이 없는 레포는 아래 기준을 그대로 적용한다.
- 실행하지 못했으면 등급을 낮추고 무엇을 확인하지 못했는지 밝힌다.
  지적 자체는 살린다. 논증만으로 보이는 문제도 가치가 있다.
- 틀린 차단 지적 하나가 맞는 지적 열 개보다 비싸다. 사람이 PR 을 멈추기 때문이다.
- **깨끗하면 승인한다.** 차단할 것이 없는데 습관적으로 의견으로 내리지 않는다.
  판정은 승인이 기본값이고, 차단은 근거가 있을 때만 올린다.

특히 두 가지를 조심한다. 둘 다 실제로 틀렸던 유형이다.

- **없다고 쓰기 전에 실행한다.** CLI 옵션이나 명령이 없다는 주장은
  `--help` 나 실제 실행으로 확인한 뒤에만 한다.
- **diff 와 레포는 다르다.** 이 PR 이 바꾸지 않은 파일은 당연히 diff 에 없다.
  파일이나 디렉터리의 존재는 워크트리에서 직접 확인한다.

## 게시

산출물이 둘이다. 서로 섞지 않는다.

### (1) 요약 코멘트 — 하나만 유지한다

`{MARKER}` 가 들어 있는 자기 코멘트를 찾아 **수정한다.** 없을 때만 새로 만든다.
`gh pr comment` 는 항상 새로 만들기 때문에 그대로 쓰면 안 된다.
첫 줄은 `{MARKER}` 로 시작해야 다음 리뷰가 찾을 수 있다.

담는 것은 이것뿐이다.

- 판정 (승인 · 변경 요청 · 의견 중 하나)
- **판정이 승인이 아니면 그 이유를 반드시 한 줄로 적는다.** 차단 문제가 있어서인지,
  자기가 작성자라 GitHub 가 승인을 거부해서인지 읽는 사람이 구분할 수 있어야 한다.
  이유 없이 의견으로 내려놓지 않는다.
- 변경된 파일
- 확인한 것, 남은 문제
- 위의 `### 검증` 블록
- 검토한 head SHA

마지막 줄은 아래를 **그대로 복사한다.** 한 글자도 바꾸지 않는다.
이모지를 다른 것으로 바꾸면 신고 신호가 뒤집힌다.

> 이 리뷰가 도움이 됐으면 :+1:, 지적이 틀렸으면 :-1: 를 눌러주세요. 오탐 집계에 씁니다.

**이 줄이 코멘트의 끝이다.** 아래 (2) 는 코멘트에 들어가지 않는다.
이 지시문의 제목·문장을 코멘트에 옮겨 적지 않는다.

### (2) 정식 리뷰 제출

`{ORG}/{repo}` PR #{number} 에 리뷰를 제출한다. 코멘트와 별개의 API 호출이다.

`commit_id` 는 검토한 head SHA.
event 는 문제 없으면 APPROVE, 반드시 고칠 것이 있으면 REQUEST_CHANGES, 그 외 COMMENT.
자기가 PR 작성자면 APPROVE 와 REQUEST_CHANGES 가 모두 거부되므로 COMMENT 로 내린다.
그때는 (1) 에 그 사유를 반드시 적는다.
지적 지점에는 인라인 코멘트를 단다. `line` 은 diff 에 포함된 라인이어야 한다.
리뷰 body 는 한 줄로 둔다. 상세는 요약 코멘트에 있다.

## 칸반 완료

첫 줄은 이 형식이어야 한다.

`✅ [{repo}] PR #{number} <판정> — <요약 코멘트 URL>`

판정은 승인, 변경 요청, 의견 중 하나다.

보류일 때도 **첫 줄은 반드시** 이 형식이어야 한다.

`⏸ [{repo}] PR #{number} 리뷰 보류 — <한 줄 사유>`

사유를 먼저 쓰거나 다른 말로 시작하면, 알림이 이 줄을 못 읽고 태스크 제목으로
대체한다. 그러면 슬랙을 보는 사람에게 **왜 멈췄는지가 통째로 사라진다.**
실제로 2026-08-26 에 보류 4건이 그렇게 나가서 팀이 원인을 알 수 없었다.
긴 설명은 둘째 줄부터 쓴다.

모든 산출물은 쉬운 한국어로 쓴다. 같은 head SHA 에 이미 제출했으면 중복 제출하지 않는다.
"""


def git_env(token):
    # 토큰을 argv 에 노출하지 않고 git 인증에 넘긴다.
    # git 은 Bearer 를 받지 않으므로 Basic 으로 보낸다.
    import base64
    cred = base64.b64encode(
        ("x-access-token:%s" % token).encode()
    ).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "Authorization: Basic %s" % cred,
        "GIT_TERMINAL_PROMPT": "0",
    }


def ensure_repo(token, repo):
    # 워크트리를 뜰 원본 클론을 준비한다. 없으면 받고, 있으면 최신화한다.
    path = os.path.join(REVIEW_ROOT, repo)
    env = git_env(token)
    if os.path.isdir(os.path.join(path, ".git")):
        run([GIT, "-C", path, "fetch", "--quiet", "--prune", "origin"],
            env, timeout=600)
        return path
    os.makedirs(REVIEW_ROOT, exist_ok=True)
    url = "https://github.com/%s/%s.git" % (ORG, repo)
    # 파셜 클론. 리뷰는 diff 와 최근 트리만 보면 되는데 조직 레포 50개를
    # 전부 풀 클론하면 수십 GB 로 간다. blob 은 실제로 읽을 때 받아온다.
    # 서버가 filter 를 거부하면 일반 클론으로 물러선다.
    r = run([GIT, "clone", "--quiet", "--filter=blob:none", url, path],
            env, timeout=900)
    if r.returncode != 0:
        log("  파셜 클론 실패 %s, 일반 클론으로 재시도: %s"
            % (repo, r.stderr.strip()[:120]))
        shutil.rmtree(path, ignore_errors=True)
        r = run([GIT, "clone", "--quiet", url, path], env, timeout=900)
    if r.returncode != 0:
        log("  클론 실패 %s: %s" % (repo, r.stderr.strip()[:160]))
        return None
    log("  클론 완료: %s" % path)
    return path


# 문서/설정으로 취급할 확장자. 이것만 바뀌었고 규모가 작으면 가벼운 모델로 돌린다.
def create_task(repo, number, title, sha, url, dry_run, repo_path=None):
    key = "%s-pr-review:%d:%s" % (repo, number, sha)
    task_title = "[%s PR review] #%d %s (%s)" % (repo, number, title, sha)
    if dry_run:
        log("  [dry-run] 생성 예정: %s" % task_title[:90])
        return None
    r = run([HERMES_BIN, "kanban", "create", task_title,
             "--body", build_body(repo, number, title, sha, url),
             "--assignee", ASSIGNEE,
             "--workspace", "worktree:%s" % repo_path,
             "--branch", "pr-%d-%s" % (number, sha[:7]),
             "--priority", "50",
             "--max-runtime", MAX_RUNTIME,
             "--skill", SKILL,
             "--idempotency-key", key,
             "--created-by", "pr-scanner",
             "--json"],
            timeout=180)
    if r.returncode != 0:
        log("  생성 실패 %s#%d: %s" % (repo, number, r.stderr.strip()[:160]))
        return None
    try:
        return json.loads(r.stdout).get("id")
    except (json.JSONDecodeError, AttributeError):
        # --json 이 없어도 stdout 에 id 가 찍히는 경우 대비
        out = (r.stdout or "").strip().split()
        return out[-1] if out else None


def subscribe_slack(task_id):
    r = run([HERMES_BIN, "kanban", "notify-subscribe", task_id,
             "--platform", "slack", "--chat-id", ALERT_CHANNEL], timeout=60)
    return r.returncode == 0


def db_snapshot(path):
    """WAL 을 반영한 일관된 사본을 만든다.

    파일 복사로는 -wal 에 있는 최근 커밋이 빠진다. backup() 은 SQLite 가
    직접 일관된 상태를 떠 주므로 워커가 쓰는 중이어도 안전하다.
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


def slack_post(text):
    # 봇 토큰으로 직접 보낸다. 칸반 기본 알림은 태스크 제목만 실어
    # 판정과 코멘트 URL 이 빠지기 때문이다.
    if not ALERT_CHANNEL:
        return False
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        try:
            with open(os.path.join(HERMES_HOME, ".env"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("SLACK_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip(chr(34)).strip(chr(39))
                        break
        except OSError:
            pass
    if not token:
        return False
    data = urllib.parse.urlencode({"channel": ALERT_CHANNEL, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=data,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception:
        return False


def announce_finished(announced, dry_run):
    # 끝난 리뷰 태스크를 판정과 링크를 담아 알린다.
    # 워커가 남긴 완료 요약의 첫 줄이 이미 원하는 형식이므로 그대로 쓴다.
    if not os.path.exists(KANBAN_DB):
        return announced, 0
    try:
        db = db_snapshot(KANBAN_DB)
    except sqlite3.Error as e:
        # 스냅샷 실패로 스캔 전체를 중단시키지 않는다. 다음 주기에 다시 시도한다.
        log("  칸반 스냅샷 실패, 이번 주기 알림 건너뜀: %s" % e)
        return announced, 0
    sent = 0
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, status, title FROM tasks "
            "WHERE created_by = 'pr-scanner' AND status IN ('done', 'blocked')"
        ).fetchall()
        for r in rows:
            if r["id"] in announced:
                continue
            ev = conn.execute(
                "SELECT payload FROM task_events WHERE task_id = ? "
                "AND kind IN ('completed', 'blocked', 'gave_up') "
                "ORDER BY rowid DESC LIMIT 1", (r["id"],)).fetchone()
            summary = ""
            if ev and ev["payload"]:
                try:
                    summary = (json.loads(ev["payload"]).get("summary") or "").strip()
                except (ValueError, AttributeError):
                    summary = ""
            first = summary.splitlines()[0] if summary else ""
            if not first:
                first = "%s %s" % ("✅" if r["status"] == "done" else "⏸", r["title"])
            if dry_run:
                log("  [dry-run] 알림 예정: %s" % first[:100])
                continue
            # 전송에 성공했을 때만 기록한다. 실패하면 다음 주기에 다시 시도한다.
            if slack_post(first):
                sent += 1
                announced.add(r["id"])
            else:
                log("  알림 전송 실패, 다음 주기에 재시도: %s" % r["id"])
    finally:
        os.unlink(db)
    return announced, sent


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return (set(d.get("keys", [])), d.get("watermark"),
                set(d.get("announced", [])))
    except (OSError, ValueError):
        return set(), None, set()


def save_state(keys, watermark, announced=()):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"keys": sorted(keys)[-2000:],
                       "watermark": watermark,
                       "announced": sorted(announced)[-2000:]}, f)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    token = gh_token()
    if not token:
        log("GitHub 토큰을 못 읽어 중단. GH_TOKEN 환경변수 또는 %s 를 확인하세요" % TOKEN_FILE)
        return 1

    prs = open_prs(token, args.limit)
    log("열린 PR %d건 (제외 목록 적용 후)" % len(prs))
    # 열린 PR 이 없어도 여기서 끝내지 않는다. 완료 알림이 남아 있을 수 있다.

    seen, watermark, announced = load_state()

    # 기준 시각이 없으면 지금으로 잡고 끝낸다. 이미 열려 있던 PR 은
    # 의도적으로 남겨둔 것이므로 건드리지 않는다.
    if not watermark:
        watermark = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if args.dry_run:
            log("[dry-run] 기준 시각을 %s 로 잡을 예정 (저장하지 않음)" % watermark)
        else:
            save_state(seen, watermark, announced)
            log("기준 시각 설정: %s — 이후 새로 열린 PR 만 리뷰한다" % watermark)
        log("기존에 열려 있던 PR %d건은 대상에서 제외" % len(prs))
        # 초기화 경로에서도 알림은 계속 보낸다. 상태 파일이 지워졌을 때
        # 이미 끝난 리뷰의 알림이 영영 누락되면 안 된다.
        announced, sent = announce_finished(announced, args.dry_run)
        if not args.dry_run:
            save_state(seen, watermark, announced)
        if sent:
            log("알림 %d건 발송" % sent)
        return 0

    created = skipped_draft = skipped_seen = skipped_old = skipped_closed = 0

    for p in prs:
        if p.get("created_at", "") <= watermark:
            skipped_old += 1
            continue
        d = pr_detail(token, p["repo"], p["number"])
        if not d:
            continue
        if d.get("draft"):
            skipped_draft += 1
            continue
        if (d.get("state") or "open") != "open":
            skipped_closed += 1
            continue
        key = "%s-pr-review:%d:%s" % (p["repo"], p["number"], d["sha"])
        if key in seen:
            skipped_seen += 1
            continue

        repo_path = None
        if not args.dry_run:
            repo_path = ensure_repo(token, p["repo"])
            if not repo_path:
                continue
        tid = create_task(p["repo"], p["number"], p["title"], d["sha"],
                          d["url"], args.dry_run, repo_path)
        if args.dry_run:
            # dry-run 은 상태를 남기지 않는다. 점검 후 실제 실행했을 때
            # 같은 PR 이 중복으로 건너뛰어지면 안 된다.
            continue
        if tid:
            # 칸반 기본 알림은 구독하지 않는다. 판정과 코멘트 URL 이 빠지기 때문에
            # announce_finished() 가 완료 요약 첫 줄을 그대로 보낸다.
            log("  생성 %s  %s#%d" % (tid, p["repo"], p["number"]))
            created += 1
            seen.add(key)

    # 완료 알림은 hermes-pr-notifier.py 가 20초 주기로 담당한다.
    # 스캐너는 3분 주기라 알림까지 맡으면 최대 3분 늦어진다.
    sent = 0
    if not args.dry_run:
        save_state(seen, watermark, announced)
    log("완료: 생성 %d · 알림 %d · 초안 %d · 중복 %d · 기준시각이전 %d · 닫힘 %d"
        % (created, sent, skipped_draft, skipped_seen, skipped_old, skipped_closed))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("오류: %s: %s" % (type(exc).__name__, exc))
        sys.exit(1)

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
PLIST = os.path.join(HOME, "Library", "LaunchAgents", "ai.hermes.gateway.plist")
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
SKILL = "github-code-review"
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
    """게이트웨이와 같은 팀 계정 토큰을 쓴다."""
    tok = os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:
        import plistlib
        with open(PLIST, "rb") as f:
            d = plistlib.load(f)
        return d.get("EnvironmentVariables", {}).get("GH_TOKEN")
    except Exception:
        return None


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
             "--jq", "{sha: .head.sha, draft: .draft, state: .state, url: .html_url}"],
            {"GH_TOKEN": token})
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def build_body(repo, number, title, sha, url):
    return f"""wigtn/{repo} 의 PR #{number} 를 리뷰한다.

- PR: {url}
- 제목: {title}
- 큐에 넣을 때의 head SHA: `{sha}`

작업 순서:
1. GitHub 에서 이 PR 의 현재 상태를 먼저 조회한다.
   - 이미 MERGED 이거나 closed 면 재리뷰하지 말고, 병합 완료된 큐 항목으로 보고 그대로 완료 처리한다.
   - 아직 열려 있는데 현재 head SHA 가 위 값과 다르면, 낡은 큐이므로 짧은 한국어 사유로 보류(block)한다.
2. 그 외에는 `{SKILL}` 절차대로 리뷰한다. 변경된 코드를 직접 보고, 가능하면 검사를 실제로 실행한다.
3. 같은 head SHA 에 대해 이미 남긴 리뷰가 있으면 중복으로 다시 달지 않는다.
4. 리뷰 결과를 GitHub 코멘트로 남기고, 그 URL 을 확인한 뒤 칸반을 완료한다.

작성 규칙:
- GitHub 코멘트와 칸반 요약은 모두 쉬운 한국어로 쓴다.
- 칸반 완료 요약의 첫 줄은 반드시 아래 형식 한 문장으로 시작한다.
  `✅ [{repo}] PR #{number} 리뷰 완료 — <GitHub 코멘트 URL>`
  보류일 때는 `⏸ [{repo}] PR #{number} 리뷰 보류 — <짧은 사유>`
- 첫 줄 다음에 PR, 결과, 핵심 발견, 다음 할 일을 짧은 항목으로 덧붙인다.
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
    r = run([GIT, "clone", "--quiet", url, path], env, timeout=900)
    if r.returncode != 0:
        log("  클론 실패 %s: %s" % (repo, r.stderr.strip()[:160]))
        return None
    log("  클론 완료: %s" % path)
    return path


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
             "--json"], timeout=180)
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


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return set(d.get("keys", [])), d.get("watermark")
    except (OSError, ValueError):
        return set(), None


def save_state(keys, watermark):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"keys": sorted(keys)[-2000:],
                       "watermark": watermark}, f)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    token = gh_token()
    if not token:
        log("GH_TOKEN 을 못 읽어 중단")
        return 1

    prs = open_prs(token, args.limit)
    log("열린 PR %d건 (제외 목록 적용 후)" % len(prs))
    if not prs:
        return 0

    seen, watermark = load_state()

    # 기준 시각이 없으면 지금으로 잡고 끝낸다. 이미 열려 있던 PR 은
    # 의도적으로 남겨둔 것이므로 건드리지 않는다.
    if not watermark:
        watermark = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if args.dry_run:
            log("[dry-run] 기준 시각을 %s 로 잡을 예정 (저장하지 않음)" % watermark)
        else:
            save_state(seen, watermark)
            log("기준 시각 설정: %s — 이후 새로 열린 PR 만 리뷰한다" % watermark)
        log("기존에 열려 있던 PR %d건은 대상에서 제외" % len(prs))
        return 0

    created = skipped_draft = skipped_seen = skipped_old = 0

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
            ok = subscribe_slack(tid)
            log("  생성 %s  %s#%d  slack구독=%s"
                % (tid, p["repo"], p["number"], "OK" if ok else "실패"))
            created += 1
            seen.add(key)

    if not args.dry_run:
        save_state(seen, watermark)
    log("완료: 생성 %d · 초안 %d · 중복 %d · 기준시각이전 %d"
        % (created, skipped_draft, skipped_seen, skipped_old))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("오류: %s: %s" % (type(exc).__name__, exc))
        sys.exit(1)

#!/usr/bin/env python3
"""
완료된 PR 리뷰 태스크의 워크트리를 회수한다.

기존 hermes-worktree-gc.sh 는 재생성 가능한 산출물(node_modules 등)만 지우고
워크트리 자체는 남긴다. 특히 git 에 '등록된' 워크트리는 설계상 절대 건드리지
않아서, 완료된 태스크의 작업방이 영구히 쌓인다. 이 스크립트가 그 구멍을 막는다.

삭제 판정 (전부 만족해야 지운다):
  1. 칸반 태스크 상태가 done
  2. 해당 PR 이 GitHub 에서 MERGED   (머지됐으면 재리뷰가 없다)
  3. 완료 후 GRACE_DAYS 경과
  4. 워크트리 경로가 허용된 루트 아래에 있다
  5. 추적 파일에 수정이 없다        (미추적 리뷰 초안·락파일은 허용)

지우기 전에 에이전트가 남긴 리뷰 초안(.md/.json)은 아카이브에 보관한다.
초안 내용은 이미 GitHub PR 코멘트로 게시되어 있으므로 사본이지만, 감사 추적용으로 남긴다.

사용법:
  hermes-worktree-reaper.py [--dry-run] [--grace-days N]
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
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
# GitHub 조직 이름. PR 병합 여부를 확인할 때 쓴다.
ORG = os.environ.get("GITHUB_ORG", "")
# 토큰 파일. GH_TOKEN 환경변수가 있으면 그쪽이 우선한다.
TOKEN_FILE = os.environ.get("GH_TOKEN_FILE", os.path.join(HERMES_HOME, "gh_token"))


def gh_token():
    tok = os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
KANBAN_DB = os.path.join(HERMES_HOME, "kanban.db")
ARCHIVE_DIR = os.environ.get(
    "HERMES_DRAFT_ARCHIVE", os.path.join(HOME, "hermes-ops", "review-drafts")
)
LOG_PATH = os.path.join(LOG_DIR, "hermes-worktree-reaper.log")

# 리뷰 원본 클론이 있는 곳. 스캐너의 HERMES_REVIEW_ROOT 와 같은 값이어야 한다.
REVIEW_ROOT = os.environ.get(
    "HERMES_REVIEW_ROOT", os.path.join(HOME, "hermes-ops", "reviews"))

# 이 아래에 있는 경로만 건드린다. 경로 탈출 방지.
ALLOWED_ROOTS = [
    os.path.join(HOME, ".worktrees"),
    os.path.join(HERMES_HOME, "hermes-agent", ".worktrees"),
]
# 리뷰 워크트리는 `<리뷰클론>/.worktrees/` 아래에 생긴다. 여기가 빠져 있어서
# 리뷰 1건당 워크트리 하나와 pr-* 브랜치 하나가 영구히 남았다.
# 레포별로 명시해서 더한다. REVIEW_ROOT 자체를 넣으면 원본 클론까지
# 회수 대상이 되므로 그렇게 하지 않는다.
if os.path.isdir(REVIEW_ROOT):
    for _repo in sorted(os.listdir(REVIEW_ROOT)):
        _wt = os.path.join(REVIEW_ROOT, _repo, ".worktrees")
        if os.path.isdir(_wt):
            ALLOWED_ROOTS.append(_wt)

GIT = "/usr/bin/git"

TITLE_RE = re.compile(r"\[([A-Za-z0-9._-]+) PR review\] #(\d+)")
# 스캐너가 만드는 리뷰 브랜치 이름. `pr-<번호>-<sha 앞 7자리>`.
BRANCH_RE = re.compile(r"^pr-\d+-[0-9a-f]{7}$")
DRAFT_RE = re.compile(r"(review|리뷰).*\.(md|json)$", re.IGNORECASE)


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run(args, cwd=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True)


def under_allowed_root(path):
    rp = os.path.realpath(path)
    return any(rp.startswith(os.path.realpath(r) + os.sep) for r in ALLOWED_ROOTS)


def load_done_tasks():
    """칸반에서 done 상태의 PR 리뷰 태스크를 읽는다 (읽기 전용 사본 사용)."""
    if not os.path.exists(KANBAN_DB):
        log("칸반 DB 없음: %s" % KANBAN_DB)
        return []
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    shutil.copy2(KANBAN_DB, tmp.name)
    try:
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, status, workspace_path, completed_at "
            "FROM tasks WHERE status = 'done' AND workspace_path IS NOT NULL"
        ).fetchall()
        out = []
        for r in rows:
            m = TITLE_RE.match(r["title"] or "")
            if not m:
                continue
            out.append({
                "task": r["id"],
                "repo": m.group(1),
                "pr": int(m.group(2)),
                "path": r["workspace_path"],
                "done_at": r["completed_at"],
            })
        return out
    finally:
        os.unlink(tmp.name)


def merged_prs(repo):
    """레포의 MERGED PR 번호 집합. 조회 실패 시 None (실패 시엔 아무것도 안 지운다)."""
    r = run([GH, "pr", "list", "-R", "%s/%s" % (ORG, repo),
             "--state", "merged", "--limit", "500",
             "--json", "number", "--jq", "[.[].number]"],
            env={"GH_TOKEN": gh_token()})
    if r.returncode != 0:
        log("  경고: %s 의 PR 목록 조회 실패, 이 레포는 건너뜀 (%s)"
            % (repo, r.stderr.strip()[:120]))
        return None
    try:
        return set(json.loads(r.stdout or "[]"))
    except json.JSONDecodeError:
        log("  경고: %s 의 PR 목록 파싱 실패, 건너뜀" % repo)
        return None


def tracked_changes(path):
    """추적 파일 변경 목록. 미추적('??')은 제외한다."""
    r = run([GIT, "-C", path, "status", "--porcelain"])
    if r.returncode != 0:
        return ["<git status 실패>"]
    return [ln for ln in r.stdout.splitlines() if ln and not ln.startswith("??")]


def archive_drafts(paths, dry_run):
    """리뷰 초안을 하나의 tar.gz 로 모은다."""
    files = []
    for p in paths:
        try:
            for name in os.listdir(p):
                if DRAFT_RE.search(name) or name.startswith(".hermes-"):
                    fp = os.path.join(p, name)
                    if os.path.isfile(fp):
                        files.append(fp)
        except OSError:
            pass
    if not files:
        return 0
    if dry_run:
        return len(files)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    out = os.path.join(ARCHIVE_DIR, "drafts-%s.tgz" % time.strftime("%Y%m%d-%H%M%S"))
    with tarfile.open(out, "w:gz") as tf:
        for fp in files:
            tf.add(fp, arcname=os.path.join(os.path.basename(os.path.dirname(fp)),
                                            os.path.basename(fp)))
    log("  리뷰 초안 %d건 보관: %s" % (len(files), out))
    return len(files)


def remove_worktree(path, dry_run):
    """연결 워크트리는 git 에게, 독립 클론은 직접 지운다. 반환: 회수 MB."""
    try:
        size_kb = int(run(["/usr/bin/du", "-sk", path]).stdout.split()[0])
    except (ValueError, IndexError):
        size_kb = 0
    if dry_run:
        return size_kb / 1024.0

    gitpath = os.path.join(path, ".git")
    parent = None
    if os.path.isfile(gitpath):
        try:
            gd = open(gitpath, encoding="utf-8").read().strip().replace("gitdir: ", "")
            parent = gd.split("/.git/worktrees/")[0]
        except OSError:
            parent = None

    # 워크트리가 물고 있던 브랜치. 지우기 전에 알아둬야 한다.
    branch = ""
    try:
        branch = run([GIT, "-C", path, "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    except Exception:
        branch = ""

    if parent and os.path.isdir(parent):
        r = run([GIT, "-C", parent, "worktree", "remove", "--force", path])
        if r.returncode != 0:
            shutil.rmtree(path, ignore_errors=True)
        run([GIT, "-C", parent, "worktree", "prune"])
        # 워크트리만 지우면 pr-* 브랜치가 남는다. 리뷰 1건당 하나씩 쌓인다.
        # 이름이 스캐너가 만든 형식과 정확히 맞을 때만 지운다. main 같은 것을
        # 실수로 지우지 않기 위한 방어다.
        if BRANCH_RE.match(branch):
            run([GIT, "-C", parent, "branch", "-D", branch])
    else:
        shutil.rmtree(path, ignore_errors=True)
    return size_kb / 1024.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="판정만 하고 지우지 않는다")
    ap.add_argument("--grace-days", type=int,
                    default=int(os.environ.get("GRACE_DAYS", "2")),
                    help="완료 후 이 기간이 지나야 회수한다 (기본 2일)")
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "실행"
    log("=== 워크트리 회수 시작 (%s, grace %d일) ===" % (mode, args.grace_days))

    tasks = load_done_tasks()
    log("done 상태 PR 리뷰 태스크: %d건" % len(tasks))
    if not tasks:
        log("=== 대상 없음 ===")
        return 0

    cutoff = time.time() - args.grace_days * 86400
    merged_cache = {}

    targets, skipped = [], {"미존재": 0, "경로벗어남": 0, "유예기간": 0,
                            "미머지": 0, "조회실패": 0, "추적파일수정": 0}

    for t in tasks:
        p = t["path"]
        if not p or not os.path.isdir(p):
            skipped["미존재"] += 1
            continue
        if not under_allowed_root(p):
            log("  건너뜀(경로): %s" % p)
            skipped["경로벗어남"] += 1
            continue
        if t["done_at"] and float(t["done_at"]) > cutoff:
            skipped["유예기간"] += 1
            continue
        if t["repo"] not in merged_cache:
            merged_cache[t["repo"]] = merged_prs(t["repo"])
        m = merged_cache[t["repo"]]
        if m is None:
            skipped["조회실패"] += 1
            continue
        if t["pr"] not in m:
            skipped["미머지"] += 1
            continue
        changes = tracked_changes(p)
        if changes:
            log("  보존(추적파일 수정): %s  %s" % (t["task"], changes[0].strip()[:70]))
            skipped["추적파일수정"] += 1
            continue
        targets.append(t)

    log("회수 대상: %d건" % len(targets))
    for k, v in skipped.items():
        if v:
            log("  보존 %s: %d건" % (k, v))

    if not targets:
        log("=== 회수할 것 없음 ===")
        return 0

    archive_drafts([t["path"] for t in targets], args.dry_run)

    total = 0.0
    for t in targets:
        total += remove_worktree(t["path"], args.dry_run)

    log("=== %s: %d건 / 약 %.0fMB ===" % (mode, len(targets), total))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 스케줄 실행이라 죽어도 로그는 남긴다
        log("오류: %s: %s" % (type(exc).__name__, exc))
        sys.exit(1)

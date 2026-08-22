#!/bin/bash
# 오래 방치된 에이전트 작업 디렉토리의 재생성 가능 산출물을 정리한다.
# 사용법: hermes-worktree-gc.sh [--dry-run]
#
# 대상: ~/.worktrees, ~/.hermes/hermes-agent/.worktrees
#   Hermes 는 태스크(주로 PR 작업/리뷰)마다 t_<id> 워크트리를 만든다.
#   코덱스·클로드코드 등 다른 도구가 같은 위치를 쓰더라도 아래 판정이 적용된다.
#
# 두 종류가 섞여 있다 — 반드시 구분해야 한다:
#   (a) 연결 워크트리 — .git 이 파일. git worktree list 에 등록된다.
#   (b) 독립 클론     — .git 이 디렉토리. 자기 자신이 worktree list 에 나오므로
#                       등록 여부로 판단하면 영원히 "살아있음"이 되어 정리가 안 된다.
#
# 삭제 판정을 통과해야 하는 조건 (하나라도 걸리면 건너뜀):
#   1. 등록된 연결 워크트리인가            → 보호
#   2. 커밋 안 된 변경이 있는가            → 보호 (작업 중 신호)
#   3. 최근 AGE_DAYS 안에 활동이 있었는가  → 보호
# 통과해도 지우는 것은 재생성 가능한 산출물뿐이다. 소스와 커밋은 건드리지 않으므로
# 최악의 경우 손실은 재설치·재빌드 시간뿐이다.
set -uo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

WORKTREE_ROOTS=(
  "$HOME/.worktrees"
  "$HOME/.hermes/hermes-agent/.worktrees"
  # 리뷰 원본 클론. 여기는 어떤 잡도 훑지 않아서 web-agency 가 빌드 산출물로
  # 590MB 까지 부푼 적이 있다. 로컬 검증(npm ci)을 켠 뒤로는 더 빨리 쌓인다.
  "$HOME/reviews"
)
# 리뷰 원본 클론 아래에 붙은 연결 워크트리는 각각 독립 대상으로 올린다.
# 부모(원본 클론)의 find 로 함께 쓸어버리면 그 워크트리 자신의 등록·미커밋변경·
# 최근활동 검사를 건너뛰게 되어, 등록된 작업 디렉터리를 보호한다는 이 스크립트의
# 안전 계약이 깨진다.
for _nested in "$HOME"/reviews/*/.worktrees; do
  [ -d "$_nested" ] && WORKTREE_ROOTS+=("$_nested")
done
# 삭제 대상 이름 (아래 find 식과 일치해야 한다): node_modules .next venv .venv
# 이 기간 안에 활동이 있으면 건드리지 않는다. 환경변수로 덮어쓸 수 있다.
# 짧게 잡아도 안전한 이유: 지우는 것이 전부 gitignore 대상이라 커밋 안 된
# 소스 변경은 손실되지 않는다. 손실은 재설치·재빌드 시간뿐이다.
AGE_DAYS="${AGE_DAYS:-7}"
# 1 이면 미커밋 변경이 있는 워크트리도 보호한다(재빌드 수고 회피용, 데이터 보호 아님)
PROTECT_DIRTY="${PROTECT_DIRTY:-1}"
# 1 이면 등록된 워크트리도 정리 대상에 넣되, AGE_DAYS 규칙은 그대로 적용한다.
# 수동 실행용. 등록돼 있어도 며칠간 변화가 없으면 실행 중인 빌드가 아니다.
# 스케줄 실행은 기본값 0(등록된 것은 무조건 보호)을 쓴다.
PURGE_REGISTERED="${PURGE_REGISTERED:-0}"

log() { echo "[$(date '+%F %T')] $*"; }

# 최근 AGE_DAYS 안에 수정됐으면 참
recently_active() {
  [ -e "$1" ] && [ -n "$(find "$1" -maxdepth 0 -mtime -"$AGE_DAYS" 2>/dev/null)" ]
}

# 등록된 "연결" 워크트리인가. 독립 클론은 대상이 아니므로 거짓을 반환한다.
# 저장소 목록을 하드코딩하지 않고 워크트리 자신에게 묻는다 →
# 어떤 도구가 만든 워크트리든 등록돼 있으면 보호된다.
is_linked_and_registered() {
  local wt="$1"
  [ -f "$wt/.git" ] || return 1          # .git 이 파일일 때만 연결 워크트리
  git -C "$wt" worktree list --porcelain 2>/dev/null |
    awk -v target="$wt" '
      /^worktree /{p=$2}
      /^prunable/{p=""}
      /^$/{if(p==target){f=1}; p=""}
      END{if(p==target)f=1; exit !f}'
}

# 커밋 안 된 변경(추적 파일 수정·스테이징)이 있는가.
# node_modules 등은 .gitignore 대상이라 여기 안 잡힌다.
has_uncommitted_changes() {
  local out
  out=$(git -C "$1" status --porcelain --untracked-files=no 2>/dev/null) || return 1
  [ -n "$out" ]
}

[ $DRY_RUN -eq 1 ] && log "=== DRY RUN — 실제로 지우지 않음 ==="
log "기준: 최근 ${AGE_DAYS}일 내 활동 없고, 미커밋 변경 없고, 등록 안 된 것만"

freed=0; kept_reg=0; kept_dirty=0; kept_recent=0; kept_not_ignored=0
REPO_ROOTS=$(mktemp)     # 마지막에 worktree prune 을 돌릴 상위 저장소들

for root in "${WORKTREE_ROOTS[@]}"; do
  [ -d "$root" ] || continue
  for wt in "$root"/*/; do
    wt="${wt%/}"; [ -d "$wt" ] || continue

    # 상위 저장소 기록 (나중에 prune 용)
    git -C "$wt" rev-parse --path-format=absolute --git-common-dir 2>/dev/null >> "$REPO_ROOTS"

    if [ "$PURGE_REGISTERED" != "1" ] && is_linked_and_registered "$wt"; then
      kept_reg=$((kept_reg + 1)); continue
    fi
    if [ "$PROTECT_DIRTY" = "1" ] && has_uncommitted_changes "$wt"; then
      kept_dirty=$((kept_dirty + 1)); continue
    fi
    # .git 의 mtime 은 활동 신호로 쓰지 않는다. 이 스크립트가 워크트리마다
    # git 명령을 돌리는 것만으로 인덱스가 갱신돼 .git mtime 이 매번 현재시각이
    # 된다(2026-08-02 확인). 그러면 항상 "최근 활동"이 되어 영원히 정리되지
    # 않는다. 워크트리 디렉토리와 대상 디렉토리는 우리가 쓰지 않으므로 안전하다.
    if recently_active "$wt"; then
      kept_recent=$((kept_recent + 1)); continue
    fi

    # 깊이 무제한으로 찾는다. 저장소가 코드를 t_xxx/repo/ 하위에 두는 경우가
    # 많아 깊이 1만 보면 대부분을 놓친다(2026-08-02 확인: 2.4GB vs 9.5GB).
    # -prune 이라 node_modules 안으로는 내려가지 않는다.
    while IFS= read -r t; do
      [ -d "$t" ] || continue
      if recently_active "$t"; then kept_recent=$((kept_recent + 1)); continue; fi

      # 이름만 node_modules/venv라고 해서 지우지 않는다. 가장 가까운 Git 저장소가
      # 실제 ignore 대상으로 판정한 재생성 가능 디렉터리만 삭제한다.
      target_repo=$(git -C "$(dirname "$t")" rev-parse --show-toplevel 2>/dev/null) || {
        kept_not_ignored=$((kept_not_ignored + 1)); continue;
      }
      if ! git -C "$target_repo" check-ignore -q -- "$t" 2>/dev/null; then
        kept_not_ignored=$((kept_not_ignored + 1)); continue
      fi

      sz=$(du -sk "$t" 2>/dev/null | awk '{print $1}')
      if [ $DRY_RUN -eq 1 ]; then
        log "[예정] $t (${sz:-0}KB)"
      else
        rm -rf "$t" && log "삭제 $t (${sz:-0}KB)"
      fi
      freed=$((freed + ${sz:-0}))
    done < <(find "$wt" \( -name .git -o -name .worktrees \) -prune -o \
               \( -name node_modules -o -name .next -o -name venv -o -name .venv \) \
               -type d -prune -print 2>/dev/null)
  done
done

log "정리 약 $((freed/1024))MB | 보존: 등록됨 ${kept_reg} · 미커밋변경 ${kept_dirty} · 최근활동 ${kept_recent} · Git ignore 아님 ${kept_not_ignored}"

# 끊어진 워크트리 등록 정리 (위에서 수집한 저장소들에 대해서만)
if [ $DRY_RUN -eq 0 ]; then
  sort -u "$REPO_ROOTS" | while IFS= read -r gd; do
    [ -d "$gd" ] || continue
    git --git-dir="$gd" worktree prune 2>/dev/null
  done
fi

rm -f "$REPO_ROOTS"

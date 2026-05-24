#!/usr/bin/env bash
# setup_obsidian.sh — Obsidian Vault 배치
#   두 모드:
#     1) 솔로 모드 (TEAM_VAULT_REPO 비어있음) — 로컬 빈 Vault 디렉토리 생성
#     2) 팀 모드 (TEAM_VAULT_REPO 설정됨) — git clone 으로 팀 vault 받아오고
#        본인 호스트 (HOSTNAME_KIND) 하위 ouroboros mirror 디렉토리만 생성
set -euo pipefail

VAULT="${OBSIDIAN_VAULT:-$HOME/AgentStackVault}"
TEAM_VAULT_REPO="${TEAM_VAULT_REPO:-}"
TEAM_VAULT_BRANCH="${TEAM_VAULT_BRANCH:-main}"
HOSTNAME_KIND="${HOSTNAME_KIND:-$(scutil --get LocalHostName 2>/dev/null || hostname -s)}"

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
RST=$(tput sgr0 2>/dev/null || true)

echo ""
echo "→ Obsidian Vault 배치 (host=$HOSTNAME_KIND)"
echo "─────────────────────────────────────────"

if [ -n "$TEAM_VAULT_REPO" ]; then
  # ─── 팀 모드: git clone or pull ───────────────────────────────────────────
  echo "  ${BOLD}팀 모드${RST} — TEAM_VAULT_REPO=$TEAM_VAULT_REPO"
  if [ -d "$VAULT/.git" ]; then
    echo "  Vault 이미 존재 — git pull"
    (cd "$VAULT" && git pull --ff-only --branch "$TEAM_VAULT_BRANCH" 2>/dev/null \
                 || git pull --ff-only) \
      && echo "  pull 완료" \
      || echo "  ${DIM}pull 실패 — 기존 상태 유지${RST}"
  elif [ -d "$VAULT" ] && [ "$(ls -A "$VAULT" 2>/dev/null)" ]; then
    echo "  ${BOLD}[WARN]${RST} $VAULT 가 이미 존재하지만 git repo 가 아닙니다."
    echo "         수동 처리 필요. 다른 OBSIDIAN_VAULT 경로 사용을 고려하세요."
  else
    rm -rf "$VAULT" 2>/dev/null || true
    echo "  clone: $TEAM_VAULT_REPO → $VAULT"
    if git clone --branch "$TEAM_VAULT_BRANCH" "$TEAM_VAULT_REPO" "$VAULT" 2>/dev/null; then
      echo "  clone 완료"
    elif git clone "$TEAM_VAULT_REPO" "$VAULT"; then
      echo "  clone 완료 (기본 브랜치 사용)"
    else
      echo "  ${BOLD}[FAIL]${RST} clone 실패. SSH key 또는 권한을 확인하세요."
      echo "          'ssh -T git@github.com' 으로 인증 테스트 가능."
      exit 1
    fi
  fi

  # 본인 호스트 mirror 디렉토리만 생성 (충돌 방지: 호스트별 분리)
  mkdir -p "$VAULT/ouroboros/$HOSTNAME_KIND"/{specs,journal,evaluations,seeds}
  echo "  본인 mirror 영역: ouroboros/$HOSTNAME_KIND/{specs,journal,evaluations,seeds}"

  # 본인 per-user 디렉토리 (있으면 skip)
  USER_SLUG="${USER}"
  mkdir -p "$VAULT/per-user/$USER_SLUG"

else
  # ─── 솔로 모드: 로컬 빈 vault ────────────────────────────────────────────
  echo "  ${DIM}솔로 모드${RST} (TEAM_VAULT_REPO 미설정)"
  mkdir -p "$VAULT"/{specs,journal,evaluations,seeds}
  echo "  Vault 생성: $VAULT"
  echo "    specs/        — Ouroboros 가 결정화한 명세"
  echo "    journal/      — 실행 저널 (interview/run 로그)"
  echo "    evaluations/  — 3단계 검증 결과"
  echo "    seeds/        — seed.yaml 사본"

  # Vault 진입용 인덱스 노트 생성 (이미 있으면 보존)
  if [ ! -f "$VAULT/README.md" ]; then
    cat > "$VAULT/README.md" <<'EOF'
# Agent Stack Vault (Solo Mode)

Ouroboros 워크플로우의 출력이 이 Vault 로 미러링됩니다.

- `specs/` — interview 단계가 결정화한 불변 명세
- `journal/` — interview / run 단계의 실행 로그
- `evaluations/` — Mechanical → Semantic → Consensus 검증 결과
- `seeds/` — seed.yaml 사본 (재현용)

팀 공유 모드로 전환하려면 `.env` 에 `TEAM_VAULT_REPO` 를 설정하고
`make setup-obsidian` 을 다시 실행하세요.
EOF
    echo "  인덱스 노트 생성: $VAULT/README.md"
  fi
fi

# Obsidian 앱 설치 안내 — OS 감지
echo ""
echo "  ${BOLD}Obsidian 데스크톱 앱 설치 (수동 — GUI 앱)${RST}"
case "$(uname -s)" in
  Darwin)
    echo "    brew install --cask obsidian"
    echo "    또는 https://obsidian.md/download 에서 .dmg 다운로드" ;;
  Linux)
    echo "    https://obsidian.md/download 에서 AppImage / .deb / Flatpak 선택"
    echo "    Flatpak:  flatpak install flathub md.obsidian.Obsidian" ;;
  *)
    echo "    https://obsidian.md/download" ;;
esac
echo ""
echo "  설치 후 Obsidian 에서 'Open folder as vault' →"
echo "    $VAULT  선택"
if [ -n "$TEAM_VAULT_REPO" ]; then
  echo ""
  echo "  ${BOLD}팀 모드 추가 안내:${RST}"
  echo "  Obsidian Git 플러그인 (community plugin) 을 설치하면"
  echo "  자동으로 5분 주기로 pull/push 가 일어납니다."
  echo "  vault repo 안에 .obsidian/community-plugins.json 이 미리 들어 있으면"
  echo "  Obsidian 첫 실행 시 'Trust' 만 클릭하면 됩니다."
fi
echo ""

#!/usr/bin/env bash
# setup_obsidian.sh — Obsidian Vault 디렉토리 배치 + 앱 설치 안내
#   Obsidian 데스크톱 앱 자체는 GUI 앱이라 헤드리스 설치 불가.
#   이 스크립트는 Vault 디렉토리 구조만 만들고, 앱 설치는 링크로 안내한다.
set -euo pipefail

VAULT="${OBSIDIAN_VAULT:-$HOME/AgentStackVault}"

echo ""
echo "→ Obsidian Vault 배치"
echo "─────────────────────────────────────────"

# Vault 디렉토리 구조 생성.
#   Ouroboros 의 출력이 이 폴더들로 미러링된다.
mkdir -p "$VAULT"/{specs,journal,evaluations,seeds}
echo "  Vault 생성: $VAULT"
echo "    specs/        — Ouroboros 가 결정화한 명세"
echo "    journal/      — 실행 저널 (interview/run 로그)"
echo "    evaluations/  — 3단계 검증 결과"
echo "    seeds/        — seed.yaml 사본"

# Vault 진입용 인덱스 노트 생성 (이미 있으면 보존)
if [ ! -f "$VAULT/README.md" ]; then
  cat > "$VAULT/README.md" <<'EOF'
# Agent Stack Vault

Ouroboros 워크플로우의 출력이 이 Vault 로 미러링됩니다.

- `specs/` — interview 단계가 결정화한 불변 명세
- `journal/` — interview / run 단계의 실행 로그
- `evaluations/` — Mechanical → Semantic → Consensus 검증 결과
- `seeds/` — seed.yaml 사본 (재현용)

이 Vault 는 **읽기·검토용 지식 레이어**입니다.
실제 코드 실행은 Ouroboros + 런타임 CLI 가 담당합니다.
EOF
  echo "  인덱스 노트 생성: $VAULT/README.md"
fi

# Obsidian 앱 설치 안내 — OS 감지
echo ""
echo "  $(tput bold 2>/dev/null)Obsidian 데스크톱 앱 설치 (수동 — GUI 앱)$(tput sgr0 2>/dev/null)"
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
echo ""

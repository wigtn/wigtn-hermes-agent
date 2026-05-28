#!/usr/bin/env bash
# verify.sh — Hermes 설치 상태 검증 (Hermes-only)
#   --doctor 옵션: 진단 + 해결책
set -euo pipefail

MODE="verify"
if [ "${1:-}" = "--doctor" ]; then
  MODE="doctor"
fi

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
RST=$(tput sgr0 2>/dev/null || true)

ok()   { echo "  [${GREEN}OK${RST}]   $1"; }
miss() { echo "  [${BOLD}MISS${RST}] $1"; MISSING=$((MISSING+1)); }
warn() { echo "  [${BOLD}WARN${RST}] $1"; }
note() { echo "  ${DIM}[NOTE]${RST} $1"; }

MISSING=0
echo ""
echo "설치 검증 — Hermes Agent"
echo "─────────────────────────────────────────"

# --- Hermes CLI ---
if command -v hermes >/dev/null 2>&1; then
  ok "hermes CLI: $(command -v hermes)"
  HV=$(hermes --version 2>/dev/null | head -1 || echo unknown)
  note "$HV"
else
  miss "hermes CLI 미설치 → make install-hermes"
fi

# --- Hermes 인증 (openai-codex provider) ---
if command -v hermes >/dev/null 2>&1; then
  if hermes auth list 2>/dev/null | grep -q '^openai-codex'; then
    ok "hermes openai-codex provider 인증 완료 (ChatGPT Pro 쿼터)"
  elif hermes auth list 2>/dev/null | grep -qE '^(anthropic|openai|openrouter)\b'; then
    PROV=$(hermes auth list 2>/dev/null | grep -E '^(anthropic|openai|openrouter)' | head -1 | awk '{print $1}')
    warn "hermes 인증은 있으나 openai-codex 가 아님 (현재: $PROV) — Pro 쿼터 미사용"
  else
    miss "hermes 미인증 → make auth-hermes"
  fi
fi

echo "─────────────────────────────────────────"
if [ "$MISSING" -gt 0 ]; then
  echo "${BOLD}$MISSING 개 항목이 누락되었습니다.${RST} 위 [MISS] 항목을 처리하세요."
  if [ "$MODE" = "doctor" ]; then
    echo ""
    echo "${BOLD}진단 가이드:${RST}"
    echo ""
    echo "  • hermes CLI 가 없거나 PATH 에 안 잡힘:"
    echo "      → make install-hermes 재실행"
    echo "      → pipx ensurepath  후 새 터미널"
    echo "      → echo \$PATH 에 ~/.local/bin 포함 확인"
    echo ""
    echo "  • hermes auth list 에 openai-codex 가 안 보임:"
    echo "      → hermes auth add openai-codex --type oauth   (브라우저 OAuth)"
    echo "      → 또는 hermes model → \"OpenAI Codex\" 선택"
    echo "      → 양쪽 모두 ChatGPT Pro 계정으로 로그인 시 추가 결제 없음"
    echo ""
    echo "  • 인증은 했는데 다른 provider 가 잡힘:"
    echo "      → hermes auth list 로 등록 상태 확인"
    echo "      → hermes auth remove <provider>  로 정리 후 재인증"
    echo ""
  fi
  exit 1
fi

echo "${GREEN}모든 검증 통과.${RST}"
echo ""

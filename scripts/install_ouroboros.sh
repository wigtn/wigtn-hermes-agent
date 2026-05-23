#!/usr/bin/env bash
# install_ouroboros.sh — Ouroboros 설치 + MCP 서버 자동 등록
#   OUROBOROS_INSTALL_RUNTIME 환경변수로 런타임을 비대화식 지정.
set -euo pipefail

RUNTIME="${OUROBOROS_INSTALL_RUNTIME:-codex}"

echo ""
echo "→ Ouroboros 설치 (runtime=${RUNTIME})"
echo "─────────────────────────────────────────"

# 설치 방식 우선순위: pipx > pip --break-system-packages
install_pkg() {
  if command -v pipx >/dev/null 2>&1; then
    echo "  pipx 로 ouroboros-ai 설치 중..."
    pipx install "ouroboros-ai[mcp]" --force
  else
    echo "  pip 로 ouroboros-ai 설치 중 (--break-system-packages)..."
    python3 -m pip install --user --break-system-packages "ouroboros-ai[mcp]"
  fi
}

if command -v ouroboros >/dev/null 2>&1; then
  echo "  ouroboros 이미 설치됨 — 업그레이드 시도."
fi
install_pkg

if ! command -v ouroboros >/dev/null 2>&1; then
  echo "  [WARN] ouroboros 명령을 PATH 에서 찾을 수 없습니다."
  echo "         pipx ensurepath  또는  ~/.local/bin 을 PATH 에 추가하세요."
fi

# MCP 서버 등록.
#   codex / claude / hermes 는 공식 인스톨러가 자동 감지하지만,
#   여기서는 런타임을 명시적으로 넘겨 결정론적으로 등록한다.
echo ""
echo "  Ouroboros setup — MCP 서버 등록 (runtime=${RUNTIME})"
if command -v ouroboros >/dev/null 2>&1; then
  # codex/claude/hermes 는 --runtime 으로 명시 등록
  ouroboros setup --runtime "${RUNTIME}" \
    && echo "  MCP 서버 등록 완료." \
    || echo "  [WARN] setup 실패 — 'ouroboros setup --runtime ${RUNTIME}' 를 수동 실행하세요."
else
  echo "  [SKIP] ouroboros 미설치로 setup 건너뜀."
fi
echo ""

#!/usr/bin/env bash
# install_codex.sh — Codex CLI 설치 (인증은 별도, auth-codex 타겟 참조)
set -euo pipefail

echo ""
echo "→ Codex CLI 설치"
echo "─────────────────────────────────────────"

if command -v codex >/dev/null 2>&1; then
  echo "  codex CLI 이미 설치됨: $(codex --version 2>/dev/null || echo '버전 확인 불가')"
  echo "  재설치하려면 'npm uninstall -g @openai/codex' 후 다시 실행하세요."
  exit 0
fi

# 공식 설치 경로: npm. brew 가 있으면 brew 도 가능하나 npm 을 기본으로.
if command -v npm >/dev/null 2>&1; then
  echo "  npm 으로 @openai/codex 설치 중..."
  npm install -g @openai/codex
elif command -v brew >/dev/null 2>&1; then
  echo "  Homebrew 로 codex 설치 중..."
  brew install --cask codex
else
  echo "  [FAIL] npm 도 brew 도 없습니다."
  echo "         Node.js(nvm 권장) 를 설치한 뒤 다시 실행하세요."
  exit 1
fi

# 현재 셸의 PATH 캐시를 비워 방금 설치한 바이너리가 보이게 한다
hash -r 2>/dev/null || true

# npm global prefix 가 PATH 에 있는지 확인 (사용자 ~/.npmrc 가 prefix override 한 경우 대비)
NPM_BIN_DIR=""
if command -v npm >/dev/null 2>&1; then
  NPM_PREFIX="$(npm prefix -g 2>/dev/null || true)"
  [ -n "$NPM_PREFIX" ] && NPM_BIN_DIR="$NPM_PREFIX/bin"
fi

if command -v codex >/dev/null 2>&1; then
  echo "  설치 완료: $(codex --version 2>/dev/null || echo 'codex')"
  echo "  $(which codex)"
  echo ""
  echo "  $(tput bold 2>/dev/null)다음 단계:$(tput sgr0 2>/dev/null) 'make auth-codex' 로 ChatGPT 계정 인증"
  echo "  (인증 없이는 Ouroboros 가 codex 백엔드를 실행할 수 없습니다)"
elif [ -n "$NPM_BIN_DIR" ] && [ -x "$NPM_BIN_DIR/codex" ]; then
  # 바이너리는 있는데 PATH 에 없는 케이스 — 사용자 ~/.npmrc 의 custom prefix 등
  echo "  [WARN] codex 바이너리 발견: $NPM_BIN_DIR/codex"
  echo "         하지만 PATH 에 없어서 'codex' 명령으로 호출 안 됩니다."
  echo ""
  echo "         shell rc 에 아래를 추가하세요:"
  echo "           $(tput bold 2>/dev/null)export PATH=\"$NPM_BIN_DIR:\$PATH\"$(tput sgr0 2>/dev/null)"
  echo ""
  echo "         또는 새 터미널을 열고 다시 시도하세요."
  exit 1
else
  echo "  [FAIL] 설치 후에도 codex 명령을 찾을 수 없습니다."
  [ -n "$NPM_PREFIX" ] && echo "         npm global prefix: $NPM_PREFIX"
  echo "         npm root: $(npm root -g 2>/dev/null || echo 'unknown')"
  echo "         'npm ls -g @openai/codex' 로 설치 상태 확인 가능."
  exit 1
fi
echo ""

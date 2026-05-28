#!/usr/bin/env bash
# init.sh — wigtn-hermes-bootstrap 빈 Mac mini 부트스트랩 (Hermes-only)
# ---------------------------------------------------------------------------
# 빈 Mac mini 에서 Hermes Agent 가 동작 가능한 상태까지 한 번에 자동 진행.
# ChatGPT Pro 구독 쿼터를 Hermes 의 openai-codex provider 로 그대로 사용.
#
# 사용 1) curl | bash (가장 간단):
#   curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | bash
#
# 사용 2) 이미 clone 했다면 레포 안에서 직접 실행:
#   bash init.sh
#
# 환경변수 override:
#   REPO_URL  — 이 레포 git URL (기본: 공식 레포)
#   REPO_DIR  — clone 위치 (기본: $HOME/wigtn-hermes)
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/wigtn/wigtn-hermes-agent.git}"
REPO_DIR="${REPO_DIR:-$HOME/wigtn-hermes}"

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
RST=$(tput sgr0 2>/dev/null || true)

say()  { echo "${BOLD}$1${RST}"; }
ok()   { echo "  ${GREEN}✓${RST} $1"; }
info() { echo "  ${DIM}→${RST} $1"; }
warn() { echo "  ${YELLOW}!${RST} $1"; }
err()  { echo "  ${RED}✗${RST} $1" >&2; }

# curl|bash 환경에서도 사용자 키 입력 가능하도록 /dev/tty 사용
wait_enter() {
  echo "  ${YELLOW}완료 후 Enter 를 누르세요:${RST}"
  if [ -t 0 ]; then
    read -r _
  elif ( exec < /dev/tty ) 2>/dev/null; then
    read -r _ < /dev/tty
  else
    info "비대화 환경 감지 — 자동 진행 (Xcode CLT 설치가 완료되지 않았으면 다시 실행)"
  fi
}

has_tty() {
  [ -t 0 ] && return 0
  ( exec < /dev/tty ) 2>/dev/null
}

run_with_tty() {
  if [ -t 0 ]; then
    "$@"
  else
    "$@" < /dev/tty
  fi
}

echo ""
say "═══════════════════════════════════════════════════════════"
say " wigtn-hermes-bootstrap — 빈 Mac mini → Hermes 동작"
say "═══════════════════════════════════════════════════════════"
echo ""

# --------------------------------------------------------------------------
# 0. macOS check
# --------------------------------------------------------------------------
if [[ "$(uname -s)" != "Darwin" ]]; then
  err "macOS 전용 스크립트입니다. 현재 OS: $(uname -s)"
  exit 1
fi
MACOS_VER=$(sw_vers -productVersion)
MACOS_MAJOR=${MACOS_VER%%.*}
if [ "$MACOS_MAJOR" -lt 13 ]; then
  warn "macOS $MACOS_VER — macOS 13 (Ventura) 이상 권장"
else
  ok "macOS $MACOS_VER"
fi
echo ""

# --------------------------------------------------------------------------
# 1. Xcode Command Line Tools
# --------------------------------------------------------------------------
say "[1/6] Xcode Command Line Tools"
if ! xcode-select -p >/dev/null 2>&1; then
  info "설치 트리거 — GUI 팝업이 뜹니다"
  xcode-select --install 2>/dev/null || true
  echo ""
  echo "  ${YELLOW}대화상자에서 'Install' 을 클릭하고 설치 완료를 기다리세요.${RST}"
  wait_enter
  if ! xcode-select -p >/dev/null 2>&1; then
    err "Xcode CLT 설치가 확인되지 않았습니다. 설치 후 init.sh 재실행."
    exit 1
  fi
fi
ok "Xcode CLT: $(xcode-select -p)"
echo ""

# --------------------------------------------------------------------------
# 2. Homebrew
# --------------------------------------------------------------------------
say "[2/6] Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  info "Homebrew 설치 — sudo 비밀번호가 필요할 수 있습니다"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  # Apple Silicon: PATH 영구 등록
  if [[ "$(uname -m)" == "arm64" ]]; then
    BREW_BIN="/opt/homebrew/bin/brew"
    if [ -x "$BREW_BIN" ]; then
      eval "$($BREW_BIN shellenv)"
      SHELLRC="$HOME/.zprofile"
      [ "${SHELL##*/}" = "bash" ] && SHELLRC="$HOME/.bash_profile"
      if ! grep -q "brew shellenv" "$SHELLRC" 2>/dev/null; then
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$SHELLRC"
        info "$SHELLRC 에 brew shellenv 영구 등록"
      fi
    fi
  fi
fi
ok "brew: $(brew --version | head -1)"
echo ""

# --------------------------------------------------------------------------
# 3. 필수 brew 패키지 (Hermes 가 필요로 하는 것만)
# --------------------------------------------------------------------------
say "[3/6] 필수 패키지 (python@3.12, git, pipx, jq, gettext)"
BREW_PKGS=(python@3.12 git pipx jq gettext)
for pkg in "${BREW_PKGS[@]}"; do
  if brew list "$pkg" >/dev/null 2>&1; then
    ok "$pkg (이미 설치)"
  else
    info "$pkg 설치..."
    brew install "$pkg" || warn "$pkg 설치 실패 — 계속 진행"
  fi
done

# python@3.12 unversioned 심링크는 libexec/bin 에 있음.
PY312_PREFIX="$(brew --prefix python@3.12 2>/dev/null)"
if [ -d "$PY312_PREFIX" ]; then
  export PATH="$PY312_PREFIX/libexec/bin:$PY312_PREFIX/bin:$PATH"
  SHELLRC="$HOME/.zprofile"
  [ "${SHELL##*/}" = "bash" ] && SHELLRC="$HOME/.bash_profile"
  if ! grep -q "python@3.12/libexec/bin" "$SHELLRC" 2>/dev/null; then
    {
      echo ''
      echo '# python@3.12 unversioned shims (added by wigtn-hermes-bootstrap)'
      echo "export PATH=\"$PY312_PREFIX/libexec/bin:\$PATH\""
    } >> "$SHELLRC"
    info "$SHELLRC 에 python@3.12 PATH 영구 등록"
  fi
fi

# pipx PATH 등록 + 현재 셸 반영
if command -v pipx >/dev/null 2>&1; then
  pipx ensurepath >/dev/null 2>&1 || true
  export PATH="$HOME/.local/bin:$PATH"
fi
hash -r 2>/dev/null || true
echo ""

# --------------------------------------------------------------------------
# 4. 레포 준비 (clone 필요시)
# --------------------------------------------------------------------------
say "[4/6] 레포 준비"
if [ -f "./Makefile" ] && [ -f "./scripts/install_hermes.sh" ] && [ -f "./README.md" ]; then
  REPO_DIR="$(pwd)"
  ok "이미 레포 안: $REPO_DIR"
elif [ -d "$REPO_DIR/.git" ]; then
  info "$REPO_DIR 존재 — git pull"
  (cd "$REPO_DIR" && git pull --ff-only) || warn "git pull 실패 — 기존 상태 유지"
  ok "레포: $REPO_DIR"
else
  info "$REPO_URL → $REPO_DIR clone"
  git clone "$REPO_URL" "$REPO_DIR"
  ok "clone 완료: $REPO_DIR"
fi
cd "$REPO_DIR"
echo ""

# --------------------------------------------------------------------------
# 5. make install — Hermes 설치 + 검증
# --------------------------------------------------------------------------
say "[5/6] make install (preflight → install-hermes → verify)"
echo ""
if make install; then
  echo ""
  ok "${GREEN}make install 완료${RST}"
else
  warn "make install 일부 단계 실패 — 'make doctor' 로 진단"
  echo ""
fi
echo ""

# --------------------------------------------------------------------------
# 6. 인증 자동 트리거 — TTY 있고 아직 인증 안 됐으면 바로 띄움
# --------------------------------------------------------------------------
say "[6/6] Hermes openai-codex provider 인증"
HERMES_HAS_OPENAI_CODEX=0
if command -v hermes >/dev/null 2>&1; then
  hermes auth list 2>/dev/null | grep -q '^openai-codex' && HERMES_HAS_OPENAI_CODEX=1
fi

if [ "$HERMES_HAS_OPENAI_CODEX" = "1" ]; then
  ok "Hermes openai-codex provider 인증 이미 완료"
elif has_tty && command -v hermes >/dev/null 2>&1; then
  info "Hermes 인증 시작 (openai-codex OAuth — 브라우저가 열립니다)"
  run_with_tty hermes auth add openai-codex --type oauth \
    || warn "인증 실패/취소 — 나중에 'make auth-hermes' 로 재시도"
else
  warn "비대화 환경 — 인증을 직접 실행하세요: ${BOLD}make auth-hermes${RST}"
fi
echo ""

# --------------------------------------------------------------------------
# 완료 안내
# --------------------------------------------------------------------------
echo "${BOLD}═══════════════════════════════════════════════════════════${RST}"
echo "${BOLD}${GREEN} 부트스트랩 완료.${RST}"
echo "${BOLD}═══════════════════════════════════════════════════════════${RST}"
echo ""
echo "  ${BOLD}바로 사용:${RST}"
echo "       ${BOLD}hermes${RST}                       # 인터랙티브 셸"
echo "       ${BOLD}hermes chat -Q \"hello\"${RST}      # 한 줄 질문"
echo ""
echo "${DIM}검증: cd $REPO_DIR && make verify${RST}"
echo "${DIM}진단: cd $REPO_DIR && make doctor${RST}"
echo ""

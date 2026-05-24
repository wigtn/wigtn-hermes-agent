#!/usr/bin/env bash
# init.sh — agent-stack 빈 Mac mini 부트스트랩
# ---------------------------------------------------------------------------
# 빈 Mac mini 에서 동작 가능한 상태까지 한 번에 자동 진행한다.
#
# 사용 1) curl | bash (가장 간단):
#   curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh \
#     | ORG=wigtn bash
#
# 사용 2) 이미 clone 했다면 레포 안에서 직접 실행:
#   ORG=wigtn bash init.sh
#
# 환경변수 override:
#   ORG       — 조직 프로파일 (wigtn | brain-crew). 기본: wigtn
#   REPO_URL  — agent-stack git URL (기본: 공식 레포)
#   REPO_DIR  — agent-stack clone 위치 (기본: $HOME/agent-stack)
#   RUNTIME   — codex (기본) 또는 hermes — .env 에 자동 기록
#
#   TEAM_VAULT_REPO — 팀 vault git URL. ORG 프로파일이 자동 설정하지만 override 가능
#   OBSIDIAN_VAULT  — vault 로컬 경로. ORG 프로파일 기본값 사용
#   STACK_HOME      — agent-stack config 보관 위치. ORG 프로파일 기본값 사용
#   HOSTNAME_KIND   — vault 안의 ouroboros/<이 이름>/ 하위에 mirror. 기본: scutil hostname
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/wigtn/wigtn-hermes-agent.git}"
REPO_DIR="${REPO_DIR:-$HOME/agent-stack}"
RUNTIME="${RUNTIME:-codex}"
ORG="${ORG:-wigtn}"

# 조직 프로파일 — TEAM_VAULT_REPO / OBSIDIAN_VAULT / STACK_HOME 기본값 결정
# `:=` 패턴이라 사용자가 명시한 값이 우선
case "$ORG" in
  wigtn)
    : "${TEAM_VAULT_REPO:=git@github.com:wigtn/wigtn-team-wiki.git}"
    : "${OBSIDIAN_VAULT:=$HOME/WigtnVault}"
    : "${STACK_HOME:=$HOME/.wigtn-stack}"
    ;;
  brain-crew|braincrew)
    # 구현 예정 — 현재는 placeholder
    echo "  [WARN] ORG=brain-crew 는 아직 vault 레포가 준비되지 않았습니다." >&2
    echo "         TEAM_VAULT_REPO 환경변수로 수동 지정 필요." >&2
    : "${TEAM_VAULT_REPO:=}"
    : "${OBSIDIAN_VAULT:=$HOME/BraincrewVault}"
    : "${STACK_HOME:=$HOME/.braincrew-stack}"
    ;;
  *)
    echo "  [ERR] Unknown ORG: $ORG (wigtn | brain-crew 중 하나, 또는 모든 env 수동 지정)" >&2
    exit 1
    ;;
esac

# 호스트별 ouroboros mirror 분리용 (충돌 방지)
: "${HOSTNAME_KIND:=$(scutil --get LocalHostName 2>/dev/null || hostname -s)}"

export ORG TEAM_VAULT_REPO OBSIDIAN_VAULT STACK_HOME HOSTNAME_KIND

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
  elif [ -r /dev/tty ]; then
    read -r _ < /dev/tty
  else
    info "비대화 환경 감지 — 자동 진행 (Xcode CLT 설치가 완료되지 않았으면 다시 실행)"
  fi
}

echo ""
say "═══════════════════════════════════════════════════════════"
say " agent-stack 부트스트랩 — 빈 Mac mini → 동작 가능한 상태"
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
say "[1/8] Xcode Command Line Tools"
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
say "[2/8] Homebrew"
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
# 3. 필수 brew 패키지
# --------------------------------------------------------------------------
say "[3/8] 필수 패키지 (python@3.12, node, git, pipx, jq, gettext)"
BREW_PKGS=(python@3.12 node git pipx jq gettext)
for pkg in "${BREW_PKGS[@]}"; do
  if brew list "$pkg" >/dev/null 2>&1; then
    ok "$pkg (이미 설치)"
  else
    info "$pkg 설치..."
    brew install "$pkg" || warn "$pkg 설치 실패 — 계속 진행"
  fi
done

# python@3.12 의 unversioned python3 심링크는 libexec/bin 에 있음.
# 두 경로 모두 PATH 앞에 추가해서 'python3' 가 3.12 로 해석되게.
PY312_PREFIX="$(brew --prefix python@3.12 2>/dev/null)"
if [ -d "$PY312_PREFIX" ]; then
  export PATH="$PY312_PREFIX/libexec/bin:$PY312_PREFIX/bin:$PATH"
  # 영구 등록 (시스템 python3 보다 brew python@3.12 가 우선되도록)
  SHELLRC="$HOME/.zprofile"
  [ "${SHELL##*/}" = "bash" ] && SHELLRC="$HOME/.bash_profile"
  if ! grep -q "python@3.12/libexec/bin" "$SHELLRC" 2>/dev/null; then
    {
      echo ''
      echo '# python@3.12 unversioned shims (added by agent-stack init.sh)'
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
# 4. Obsidian (GUI 앱)
# --------------------------------------------------------------------------
say "[4/8] Obsidian 데스크톱 앱"
if [ -d "/Applications/Obsidian.app" ]; then
  ok "Obsidian.app 발견 (/Applications)"
elif brew list --cask obsidian >/dev/null 2>&1; then
  ok "Obsidian (brew cask 설치됨)"
else
  info "Obsidian 설치..."
  brew install --cask obsidian || warn "Obsidian 설치 실패 — 나중에 https://obsidian.md/download 에서 수동"
fi
echo ""

# --------------------------------------------------------------------------
# 5. 레포 위치 확인 (clone 필요시)
# --------------------------------------------------------------------------
say "[5/8] 레포 준비"
if [ -f "./Makefile" ] && [ -f "./scripts/install_codex.sh" ] && [ -f "./README.md" ]; then
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
# 6. .env 준비 — ORG 프로파일 + RUNTIME + TEAM_VAULT_REPO 기록
# --------------------------------------------------------------------------
say "[6/8] .env 준비 (ORG=$ORG, runtime=$RUNTIME, host=$HOSTNAME_KIND)"
if [ ! -f .env ]; then
  cp .env.example .env
fi

# upsert helper: .env 안의 KEY=... 라인을 교체 또는 추가
upsert_env() {
  local key="$1" val="$2"
  if grep -q "^$key=" .env 2>/dev/null; then
    sed -i.bak "s|^$key=.*|$key=$val|" .env && rm -f .env.bak
  else
    echo "$key=$val" >> .env
  fi
}

upsert_env OUROBOROS_RUNTIME "$RUNTIME"
upsert_env ORG "$ORG"
upsert_env HOSTNAME_KIND "$HOSTNAME_KIND"
[ -n "$TEAM_VAULT_REPO" ] && upsert_env TEAM_VAULT_REPO "$TEAM_VAULT_REPO"
upsert_env OBSIDIAN_VAULT "$OBSIDIAN_VAULT"
upsert_env STACK_HOME "$STACK_HOME"

ok ".env 작성 완료"
[ -n "$TEAM_VAULT_REPO" ] \
  && info "team vault: $TEAM_VAULT_REPO" \
  || info "solo 모드 (TEAM_VAULT_REPO 미설정 — 로컬 vault 만)"
echo ""

# --------------------------------------------------------------------------
# 7. make all — 자동 설치 본체
# --------------------------------------------------------------------------
say "[7/8] make all 실행 (preflight → CLI → ouroboros → configure → verify)"
echo ""
if make all; then
  echo ""
  ok "${GREEN}make all 완료${RST}"
else
  err "make all 일부 단계 실패 — 'make doctor' 로 진단"
  echo ""
fi
echo ""

# --------------------------------------------------------------------------
# 8. Obsidian Vault 디렉토리 생성
# --------------------------------------------------------------------------
say "[8/8] Obsidian Vault 디렉토리 생성"
make setup-obsidian
echo ""

# --------------------------------------------------------------------------
# 9. 남은 수동 단계 안내
# --------------------------------------------------------------------------
# set -e + pipefail 환경에서 grep 미매치가 스크립트를 죽이지 않도록 || true
FINAL_RUNTIME=$( (grep -E '^OUROBOROS_RUNTIME=' .env 2>/dev/null || true) | tail -1 | cut -d= -f2 | tr -d ' "')
FINAL_RUNTIME="${FINAL_RUNTIME:-codex}"

VAULT_PATH=$( (grep -E '^OBSIDIAN_VAULT=' .env 2>/dev/null || true) | tail -1 | cut -d= -f2 | tr -d ' "')
VAULT_PATH="${VAULT_PATH:-$HOME/AgentStackVault}"

echo ""
echo "${BOLD}═══════════════════════════════════════════════════════════${RST}"
echo "${BOLD}${GREEN} 부트스트랩 완료. 남은 수동 단계 2개:${RST}"
echo "${BOLD}═══════════════════════════════════════════════════════════${RST}"
echo ""
if [ "$FINAL_RUNTIME" = "hermes" ]; then
  echo "  ${BOLD}1) Hermes Codex provider 인증${RST} (device code)"
  echo "       cd $REPO_DIR"
  echo "       ${BOLD}make auth-hermes${RST}     # 안내 메시지"
  echo "       ${BOLD}hermes auth add codex-oauth${RST}     # 또는 직접 실행"
else
  echo "  ${BOLD}1) Codex 인증${RST} (브라우저 OAuth)"
  echo "       cd $REPO_DIR"
  echo "       ${BOLD}codex login${RST}"
fi
echo ""
echo "  ${BOLD}2) Obsidian 앱에서 Vault 열기${RST}"
echo "       ${BOLD}open -a Obsidian${RST}"
echo "       → 'Open folder as vault' → ${BOLD}$VAULT_PATH${RST}"
echo ""
echo "${DIM}검증: cd $REPO_DIR && make verify${RST}"
echo "${DIM}진단: cd $REPO_DIR && make doctor${RST}"
echo ""

#!/usr/bin/env bash
# verify.sh — 설치 상태 검증. --doctor 플래그 시 문제 진단 모드.
set -euo pipefail

STACK_HOME="${STACK_HOME:-$HOME/.agent-stack}"
VAULT="${OBSIDIAN_VAULT:-$HOME/AgentStackVault}"
RUNTIME="${OUROBOROS_RUNTIME:-codex}"
DOCTOR=0
[ "${1:-}" = "--doctor" ] && DOCTOR=1

BOLD=$(tput bold 2>/dev/null || true)
RST=$(tput sgr0 2>/dev/null || true)

ok()   { echo "  [OK]   $1"; }
warn() { echo "  [WARN] $1"; }
miss() { echo "  [MISS] $1"; MISSING=1; }
note() { echo "  [NOTE] $1"; }

MISSING=0
echo ""
echo "설치 검증 (runtime=${RUNTIME})"
echo "─────────────────────────────────────────"

# --- Ouroboros ---
command -v ouroboros >/dev/null 2>&1 \
  && ok "ouroboros: $(command -v ouroboros)" \
  || miss "ouroboros 미설치 → make install-ouroboros"

# --- 런타임별 CLI + 인증 ---
if [ "$RUNTIME" = "codex" ]; then
  if command -v codex >/dev/null 2>&1; then
    ok "codex CLI: $(command -v codex)"
  else
    miss "codex CLI 미설치 → make install-codex"
  fi
  if [ -f "$HOME/.codex/auth.json" ]; then
    ok "codex 인증 완료 (~/.codex/auth.json)"
  else
    miss "codex 미인증 → make auth-codex (codex login)"
  fi
elif [ "$RUNTIME" = "hermes" ]; then
  if command -v hermes >/dev/null 2>&1; then
    ok "hermes CLI: $(command -v hermes)"
  else
    miss "hermes CLI 미설치 → make install-hermes"
  fi
  # Hermes v0.14+ 는 'hermes auth list' 로 등록된 provider 를 본다.
  # (~/.hermes/auth.json 은 더 이상 단일 진실 공급원이 아님)
  if hermes auth list 2>/dev/null | grep -q '^openai-codex'; then
    ok "hermes openai-codex provider 인증 완료 (ChatGPT Pro 쿼터)"
  elif hermes auth list 2>/dev/null | grep -qE '^(anthropic|openai)\b'; then
    PROV=$(hermes auth list 2>/dev/null | grep -E '^(anthropic|openai)' | head -1 | awk '{print $1}')
    warn "hermes 인증은 있으나 openai-codex 가 아님 (현재: $PROV) — Pro 쿼터 미사용"
  else
    miss "hermes 미인증 → make auth-hermes"
  fi
else
  warn "알 수 없는 런타임: '${RUNTIME}' (codex 또는 hermes 만 지원)"
fi

# --- 구성 파일 ---
[ -f "$STACK_HOME/stack.yaml" ] \
  && ok "stack.yaml 존재" \
  || miss "stack.yaml 없음 → make configure"

# --- Obsidian Vault (솔로/팀 모드 자동 분기) ---
TEAM_VAULT_REPO="${TEAM_VAULT_REPO:-}"
HOSTNAME_KIND="${HOSTNAME_KIND:-$(scutil --get LocalHostName 2>/dev/null || hostname -s)}"

if [ -d "$VAULT" ]; then
  ok "Obsidian Vault: $VAULT"
  if [ -n "$TEAM_VAULT_REPO" ]; then
    # 팀 모드
    if [ -d "$VAULT/.git" ]; then
      ok "팀 vault (git): $(cd "$VAULT" && git remote get-url origin 2>/dev/null || echo 'no origin')"
      DIRTY=$(cd "$VAULT" && git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
      [ "$DIRTY" = "0" ] && ok "vault 깨끗함" || note "vault 변경 $DIRTY 건 (commit/push 필요)"
    else
      miss "$VAULT 가 git repo 가 아님 — make setup-obsidian 재실행"
    fi
    # 본인 호스트 ouroboros mirror 영역
    for d in specs journal evaluations seeds; do
      [ -d "$VAULT/ouroboros/$HOSTNAME_KIND/$d" ] \
        || warn "본인 mirror 영역 누락: ouroboros/$HOSTNAME_KIND/$d/"
    done
  else
    # 솔로 모드
    for d in specs journal evaluations seeds; do
      [ -d "$VAULT/$d" ] || warn "Vault 하위 폴더 누락: $d/"
    done
  fi
else
  miss "Obsidian Vault 없음 → make setup-obsidian"
fi

# Obsidian 앱 자체는 검증 불가(GUI 앱) — 안내만
note "Obsidian 데스크톱 앱 설치 여부는 자동 확인 불가."
echo "         설치 후 Vault 를 '$VAULT' 로 열어 두세요."

echo "─────────────────────────────────────────"

if [ "$MISSING" -eq 1 ]; then
  echo "일부 구성요소가 누락되었습니다. 위 [MISS] 항목을 처리하세요."
  if [ "$DOCTOR" -eq 1 ]; then
    echo ""
    echo "${BOLD}진단 — 흔한 원인${RST}"
    echo "  · ouroboros/codex/hermes 가 있는데 PATH 에 안 잡힘"
    echo "      → pipx ensurepath; export PATH=\$PATH:~/.local/bin"
    if [ "$RUNTIME" = "codex" ]; then
      echo "  · codex login 했는데 미인증으로 표시됨"
      echo "      → ~/.codex/auth.json 이 다른 \$HOME 에 생성됐을 수 있음. 같은 유저로 실행하세요."
    fi
    if [ "$RUNTIME" = "hermes" ]; then
      echo "  · hermes auth list 에 openai-codex 가 안 보임"
      echo "      → hermes auth add openai-codex --type oauth   (브라우저 OAuth)"
      echo "  · 이미 codex login 으로 codex CLI 는 인증돼 있어도"
      echo "      hermes 는 별도로 'hermes auth add openai-codex --type oauth' 필요"
      echo "      (양쪽 같은 ChatGPT Pro 계정이라 쿼터는 공유)"
    fi
    echo "  · ouroboros setup 이 MCP 등록 실패"
    echo "      → ouroboros setup --runtime ${RUNTIME} 를 수동 재실행"
  fi
  exit 1
fi
echo "전체 검증 통과. 'ooo interview \"...\"' 로 첫 워크플로우를 시작하세요."
echo ""

#!/usr/bin/env bash
# configure.sh — 구성요소를 연결하는 config 생성
#   Ouroboros 의 출력 경로를 Obsidian Vault 로 지정하고,
#   런타임 백엔드를 .env 의 OUROBOROS_RUNTIME 값으로 고정한다.
set -euo pipefail

STACK_HOME="${STACK_HOME:-$HOME/.agent-stack}"
VAULT="${OBSIDIAN_VAULT:-$HOME/AgentStackVault}"
RUNTIME="${OUROBOROS_RUNTIME:-codex}"
TEAM_VAULT_REPO="${TEAM_VAULT_REPO:-}"
HOSTNAME_KIND="${HOSTNAME_KIND:-$(scutil --get LocalHostName 2>/dev/null || hostname -s)}"
ORG="${ORG:-wigtn}"

# 팀 모드면 mirror 를 vault/ouroboros/<host>/ 아래로 (충돌 방지)
if [ -n "$TEAM_VAULT_REPO" ]; then
  MIRROR_BASE="${VAULT}/ouroboros/${HOSTNAME_KIND}"
  MODE="팀 (${ORG}, host=${HOSTNAME_KIND})"
else
  MIRROR_BASE="${VAULT}"
  MODE="솔로"
fi

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
RST=$(tput sgr0 2>/dev/null || true)

echo ""
echo "→ 구성 연결 (runtime=${RUNTIME}, mode=${MODE})"
echo "─────────────────────────────────────────"

mkdir -p "$STACK_HOME"

# stack-level config — 이 레포가 관리하는 단일 진실 공급원
cat > "$STACK_HOME/stack.yaml" <<EOF
# agent-stack 통합 구성 — make configure 가 생성
org: ${ORG}
runtime: ${RUNTIME}
hostname_kind: ${HOSTNAME_KIND}
obsidian_vault: ${VAULT}
team_vault_repo: ${TEAM_VAULT_REPO}
# Ouroboros Ledger 출력 mirror — 팀 모드면 호스트별 분리 디렉토리로
mirror:
  specs:       ${MIRROR_BASE}/specs
  journal:     ${MIRROR_BASE}/journal
  evaluations: ${MIRROR_BASE}/evaluations
  seeds:       ${MIRROR_BASE}/seeds
EOF
echo "  생성: $STACK_HOME/stack.yaml"

# Ouroboros 프로젝트 .env — 런타임을 codex 또는 hermes 로 고정
OURO_ENV="$STACK_HOME/ouroboros.env"
cat > "$OURO_ENV" <<EOF
# Ouroboros 런타임 백엔드.
#   codex  → Codex CLI (ChatGPT Pro 쿼터, 추가 결제 없음)
#   hermes → Hermes CLI (기본 "OpenAI Codex" provider 로 동일 Pro 쿼터)
OUROBOROS_RUNTIME=${RUNTIME}
EOF
echo "  생성: $OURO_ENV"

# 런타임별 사전 점검 안내
echo ""
if [ "$RUNTIME" = "codex" ]; then
  echo "  런타임: ${BOLD}codex${RST}"
  if command -v codex >/dev/null 2>&1; then
    if [ -f "$HOME/.codex/auth.json" ]; then
      echo "    codex CLI 설치됨, ~/.codex/auth.json 존재 — 인증 완료된 것으로 보입니다."
    else
      echo "    ${DIM}[할 일]${RST} codex CLI 는 있으나 인증 흔적이 없습니다."
      echo "            'make auth-codex' 안내대로 'codex login' 을 실행하세요."
    fi
  else
    echo "    ${DIM}[할 일]${RST} codex CLI 미설치 — 'make install-codex' 먼저 실행."
  fi
elif [ "$RUNTIME" = "hermes" ]; then
  echo "  런타임: ${BOLD}hermes${RST}"
  if command -v hermes >/dev/null 2>&1; then
    if hermes auth list 2>/dev/null | grep -q '^openai-codex'; then
      echo "    hermes openai-codex provider 인증 완료."
    else
      echo "    ${DIM}[할 일]${RST} hermes openai-codex 인증 없음:"
      echo "            ${BOLD}hermes auth add openai-codex --type oauth${RST}"
      echo "          또는 'make auth-hermes' 로 전체 안내 보기."
    fi
  else
    echo "    ${DIM}[할 일]${RST} hermes CLI 미설치 — 'make install-hermes' 먼저 실행."
  fi
else
  echo "  ${BOLD}[WARN]${RST} 알 수 없는 런타임: '${RUNTIME}' (codex 또는 hermes 만 지원)"
fi
echo ""
echo "  구성 완료. 'make verify' 로 전체 상태를 확인하세요."
echo ""

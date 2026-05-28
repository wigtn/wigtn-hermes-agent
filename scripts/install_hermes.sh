#!/usr/bin/env bash
# install_hermes.sh — Hermes CLI 설치
#   Hermes 는 50+ provider 를 지원. ChatGPT Pro 구독을 그대로 쓰려면
#   설치 후 "OpenAI Codex" provider 를 선택하면 codex CLI 와 같은
#   ChatGPT OAuth 통로를 거쳐 동일 Pro 쿼터를 소비한다.
#   다른 provider (OpenRouter, Nous Portal, OpenAI API 키 등)로 가면 별도 결제.
set -euo pipefail

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
RST=$(tput sgr0 2>/dev/null || true)

echo ""
echo "→ Hermes CLI 설치"
echo "─────────────────────────────────────────"

if command -v hermes >/dev/null 2>&1; then
  echo "  hermes CLI 이미 설치됨: $(hermes --version 2>/dev/null || echo '버전 확인 불가')"
  echo "  postinstall 재실행 (멱등):"
  hermes postinstall 2>/dev/null || true
else
  # 설치 방식 우선순위: pipx > pip > 공식 install.sh
  if command -v pipx >/dev/null 2>&1; then
    echo "  pipx 로 hermes-agent 설치 중..."
    pipx install hermes-agent --force
  elif command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; then
    echo "  pip 로 hermes-agent 설치 중 (--break-system-packages)..."
    python3 -m pip install --user --break-system-packages hermes-agent
  else
    echo "  ${DIM}pip/pipx 미설치 — 공식 install.sh 로 폴백.${RST}"
    if curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh 2>/dev/null | bash; then
      echo "  설치 스크립트 완료."
    else
      echo "  ${BOLD}[WARN]${RST} 자동 설치 실패 (네트워크 차단 또는 URL 변경 가능)."
      echo "          수동 설치: https://github.com/NousResearch/hermes-agent 참조"
      echo "          설치 후 'make verify' 로 확인하세요."
      exit 0
    fi
  fi

  if command -v hermes >/dev/null 2>&1; then
    echo "  postinstall 실행 (스킬·메모리·설정 초기화):"
    hermes postinstall || echo "  ${DIM}postinstall 실패 — 'hermes postinstall' 수동 재실행 가능.${RST}"
  fi
fi

if ! command -v hermes >/dev/null 2>&1; then
  echo "  ${BOLD}[WARN]${RST} hermes 명령을 PATH 에서 찾을 수 없습니다."
  echo "         pipx ensurepath  또는  ~/.local/bin 을 PATH 에 추가하세요."
  exit 0
fi

echo "  설치 완료: $(hermes --version 2>/dev/null || echo 'hermes')"
echo ""
echo "  ${BOLD}다음 단계:${RST} provider 인증"
echo ""
echo "  ChatGPT Pro 구독을 그대로 쓰려면 (권장, 추가 결제 없음):"
echo ""
echo "      ${BOLD}make auth-hermes${RST}"
echo ""
echo "  다른 provider (OpenRouter, Nous Portal, OpenAI API 키 등) 로 가려면:"
echo ""
echo "      ${BOLD}hermes model${RST}                        # 인터랙티브 메뉴"
echo "      ${BOLD}hermes config set model.provider <name>${RST}  # 비대화식"
echo ""
echo "  ${DIM}참고: Hermes v0.14+ 는 import 명령이 없습니다.${RST}"
echo "  ${DIM}      codex CLI 와 별도로 'hermes auth add openai-codex --type oauth' 필요.${RST}"
echo "  ${DIM}      양쪽 같은 ChatGPT Pro 계정이라 쿼터는 공유됩니다.${RST}"
echo ""

# ============================================================================
#  wigtn-hermes-bootstrap — Hermes Agent 설치 오케스트레이터 (Hermes-only)
# ----------------------------------------------------------------------------
#  설계 원칙:
#    - 자동화 가능한 것은 모두 자동화한다.
#    - 사람 손이 반드시 필요한 한 지점에서만 멈춘다:
#        OAuth 인증 — hermes auth add openai-codex --type oauth (브라우저)
#    - 토큰/시크릿은 절대 레포에 두지 않는다. .env 는 .gitignore 대상.
#    - Hermes 의 'openai-codex' provider 가 ChatGPT Pro 쿼터를 그대로 사용한다.
# ============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- 설정값: .env 가 있으면 읽어들인다 (없어도 동작) -------------------------
-include .env
export

# 색상 (TTY 일 때만)
BOLD := $(shell tput bold 2>/dev/null)
DIM  := $(shell tput dim 2>/dev/null)
RST  := $(shell tput sgr0 2>/dev/null)

.PHONY: help install preflight install-hermes auth-hermes verify doctor clean

## help: 사용 가능한 타겟 목록을 보여준다
help:
	@echo ""
	@echo "$(BOLD)wigtn-hermes-bootstrap$(RST) — Hermes Agent 설치 오케스트레이터"
	@echo ""
	@echo "$(DIM)  진입점:$(RST)"
	@echo "  $(BOLD)make install$(RST)         전체 설치 (빈 Mac 권장: $(BOLD)bash init.sh$(RST))"
	@echo ""
	@echo "$(DIM)  개별 단계:$(RST)"
	@echo "  $(BOLD)make preflight$(RST)       사전 점검 (python 3.12+, git, pipx)"
	@echo "  $(BOLD)make install-hermes$(RST)  Hermes CLI 설치"
	@echo "  $(BOLD)make auth-hermes$(RST)     Hermes openai-codex provider 인증 안내"
	@echo "  $(BOLD)make verify$(RST)          설치 검증"
	@echo "  $(BOLD)make doctor$(RST)          문제 진단"
	@echo ""

## install: 전체 설치 — preflight → install-hermes → verify
install:
	@$(MAKE) --no-print-directory preflight
	@$(MAKE) --no-print-directory install-hermes
	@$(MAKE) --no-print-directory verify || true
	@echo ""
	@echo "$(BOLD)═══════════════════════════════════════════════════════════$(RST)"
	@echo "$(BOLD) 자동 설치 완료. 남은 수동 단계 (1개):$(RST)"
	@echo "$(BOLD)═══════════════════════════════════════════════════════════$(RST)"
	@echo ""
	@echo " 1) Hermes openai-codex provider 인증 (브라우저 OAuth):"
	@echo "      $(BOLD)make auth-hermes$(RST)"
	@echo ""

## preflight: 필수 의존성을 점검한다
preflight:
	@bash scripts/preflight.sh

## install-hermes: Hermes CLI 를 설치한다
install-hermes:
	@bash scripts/install_hermes.sh

## auth-hermes: Hermes openai-codex provider 인증 절차 (자동화 불가 — 브라우저)
auth-hermes:
	@echo ""
	@echo "$(BOLD)Hermes openai-codex provider 인증 — 브라우저 OAuth$(RST)"
	@echo "$(DIM)이 단계는 자동화할 수 없습니다 (브라우저 로그인 필요).$(RST)"
	@echo ""
	@echo "  방법 A — 직접 명령:"
	@echo "      $(BOLD)hermes auth add openai-codex --type oauth$(RST)"
	@echo "      → 브라우저가 열리면 ChatGPT Pro 계정으로 로그인"
	@echo ""
	@echo "  방법 B — 인터랙티브 메뉴:"
	@echo "      $(BOLD)hermes model$(RST) → \"OpenAI Codex\" 선택 → OAuth 진행"
	@echo ""
	@echo "  → 인증 후 'hermes auth list' 로 등록된 provider 확인 가능"
	@echo "  → ChatGPT Pro 쿼터로 동작 (추가 결제 없음)"
	@echo ""
	@command -v hermes >/dev/null 2>&1 \
	  && echo "$(DIM)hermes CLI 감지됨. 위 명령 실행 후 'make verify' 로 확인하세요.$(RST)" \
	  || echo "먼저 'make install-hermes' 를 실행하세요."
	@echo ""

## verify: 설치 상태를 검증한다
verify:
	@bash scripts/verify.sh

## doctor: 흔한 문제를 진단하고 해결책을 제시한다
doctor:
	@bash scripts/verify.sh --doctor

## clean: 사용자 데이터는 건드리지 않고 안내만 (안전 정책)
clean:
	@echo ""
	@echo "$(DIM)이 타겟은 사용자 데이터(~/.hermes/)를 건드리지 않습니다.$(RST)"
	@echo "$(DIM)완전 제거하려면 수동으로:$(RST)"
	@echo "  pipx uninstall hermes-agent"
	@echo "  rm -rf ~/.hermes"
	@echo ""

# ============================================================================
#  agent-stack — Hermes + Ouroboros + Codex + Obsidian 통합 설치 오케스트레이터
# ----------------------------------------------------------------------------
#  설계 원칙:
#    - 자동화 가능한 것은 모두 자동화한다 (CLI 설치, MCP 등록, 디렉토리 배치)
#    - 사람 손이 반드시 필요한 두 지점에서는 멈추고 명령어만 안내한다:
#        (1) OAuth 인증   — 브라우저/device code 가 필요
#                          • codex 런타임:  codex login
#                          • hermes 런타임: hermes auth add codex-oauth
#        (2) Obsidian 앱  — GUI 데스크톱 앱, 헤드리스 설치 불가
#    - 토큰/시크릿은 절대 레포에 두지 않는다. .env 는 .gitignore 대상.
#    - codex / hermes 두 런타임 모두 ChatGPT Pro 구독 쿼터로 동작 가능하다.
#      (Hermes 의 "OpenAI Codex" provider 가 codex 와 같은 OAuth 통로 사용)
# ============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- 설정값: .env 가 있으면 읽어들인다 (없어도 동작) -------------------------
-include .env
export

# OUROBOROS_RUNTIME: codex(기본) | hermes
#   codex  → Codex CLI. ChatGPT Pro 쿼터, 추가 결제 없음.
#   hermes → Hermes CLI. 기본 "OpenAI Codex" provider 로 동일 Pro 쿼터 사용.
#            (다른 provider 로 갈아끼우면 별도 결제)
OUROBOROS_RUNTIME ?= codex
OBSIDIAN_VAULT    ?= $(HOME)/AgentStackVault
STACK_HOME        ?= $(HOME)/.agent-stack

# 색상 (TTY 일 때만)
BOLD := $(shell tput bold 2>/dev/null)
DIM  := $(shell tput dim 2>/dev/null)
RST  := $(shell tput sgr0 2>/dev/null)

.PHONY: help all dev preflight install-codex install-hermes install-ouroboros \
        install-runtime setup-obsidian configure verify sync-vault \
        auth-codex auth-hermes clean doctor

## help: 사용 가능한 타겟 목록을 보여준다
help:
	@echo ""
	@echo "$(BOLD)agent-stack$(RST) — 통합 설치 오케스트레이터"
	@echo ""
	@echo "$(DIM)  진입점 (목적별로 골라 쓰세요):$(RST)"
	@echo "  $(BOLD)make all$(RST)              전체 설치 — Mac 환경에 처음부터 다 (빈 Mac 권장: $(BOLD)bash init.sh$(RST))"
	@echo "  $(BOLD)make dev$(RST)              ${BOLD}개발자용 빠른 설치$(RST) — Ouroboros + config 만, prereqs 가정"
	@echo ""
	@echo "$(DIM)  개별 단계:$(RST)"
	@echo "  $(BOLD)make preflight$(RST)        사전 점검 (python, node, git, 디스크)"
	@echo "  $(BOLD)make install-codex$(RST)    Codex CLI 설치"
	@echo "  $(BOLD)make install-hermes$(RST)   Hermes CLI 설치"
	@echo "  $(BOLD)make install-ouroboros$(RST) Ouroboros 설치 + MCP 자동 등록"
	@echo "  $(BOLD)make setup-obsidian$(RST)   Obsidian Vault 디렉토리 배치 + 설치 안내"
	@echo "  $(BOLD)make configure$(RST)        config 파일 생성, 런타임/Vault 경로 연결"
	@echo "  $(BOLD)make auth-codex$(RST)       Codex 인증 안내 (codex login)"
	@echo "  $(BOLD)make auth-hermes$(RST)      Hermes Codex provider 인증 안내"
	@echo "  $(BOLD)make verify$(RST)           설치 검증"
	@echo "  $(BOLD)make doctor$(RST)           문제 진단"
	@echo "  $(BOLD)make sync-vault$(RST)       팀 vault pull/commit/push (TEAM_VAULT_REPO 설정 시)"
	@echo ""
	@echo "  현재 런타임: $(BOLD)$(OUROBOROS_RUNTIME)$(RST)   (.env 의 OUROBOROS_RUNTIME 으로 변경)"
	@echo ""

## all: 전체 스택을 순서대로 설치한다 (런타임에 따라 install-codex 또는 install-hermes 사용)
##      verify 실패 (예: codex 미인증) 와 무관하게 마지막 banner 까지 출력한다.
all:
	@$(MAKE) --no-print-directory preflight
	@$(MAKE) --no-print-directory install-runtime
	@$(MAKE) --no-print-directory install-ouroboros
	@$(MAKE) --no-print-directory configure
	@$(MAKE) --no-print-directory verify || true
	@echo ""
	@echo "$(BOLD)═══════════════════════════════════════════════════════════$(RST)"
	@echo "$(BOLD) 자동 설치 단계 완료. 남은 수동 단계:$(RST)"
	@echo "$(BOLD)═══════════════════════════════════════════════════════════$(RST)"
	@echo ""
	@if [ "$(OUROBOROS_RUNTIME)" = "hermes" ]; then \
	  echo " 1) Hermes Codex provider 인증 (device code):"; \
	  echo "      $(BOLD)make auth-hermes$(RST)"; \
	else \
	  echo " 1) Codex 인증 (브라우저 OAuth):"; \
	  echo "      $(BOLD)make auth-codex$(RST)"; \
	fi
	@echo ""
	@echo " 2) Obsidian 데스크톱 앱 설치 (GUI):"
	@echo "      $(BOLD)make setup-obsidian$(RST) 가 안내한 링크에서 직접 설치"
	@echo ""

## dev: 개발자용 빠른 설치 — prereqs (python3.12, node, codex/hermes) 가정.
##      preflight / CLI 설치 / Obsidian cask 모두 건너뜀. Ouroboros + config + verify 만.
##      verify 실패와 무관하게 마지막 banner 까지 출력.
dev:
	@$(MAKE) --no-print-directory install-ouroboros
	@$(MAKE) --no-print-directory configure
	@$(MAKE) --no-print-directory verify || true
	@echo ""
	@echo "$(BOLD)═══════════════════════════════════════════════════════════$(RST)"
	@echo "$(BOLD) dev 설치 완료.$(RST)"
	@echo "$(BOLD)═══════════════════════════════════════════════════════════$(RST)"
	@echo ""
	@echo "  $(DIM)추가로 필요한 게 있다면 개별 타겟으로:$(RST)"
	@if [ "$(OUROBOROS_RUNTIME)" = "hermes" ]; then \
	  command -v hermes >/dev/null 2>&1 \
	    || echo "  • Hermes CLI 미설치:   $(BOLD)make install-hermes$(RST)"; \
	  [ -f $$HOME/.hermes/auth.json ] \
	    || echo "  • Hermes 인증 안 됨:   $(BOLD)make auth-hermes$(RST)"; \
	else \
	  command -v codex >/dev/null 2>&1 \
	    || echo "  • Codex CLI 미설치:    $(BOLD)make install-codex$(RST)"; \
	  [ -f $$HOME/.codex/auth.json ] \
	    || echo "  • Codex 인증 안 됨:    $(BOLD)make auth-codex$(RST)"; \
	fi
	@[ -d "$(OBSIDIAN_VAULT)" ] \
	  || echo "  • Obsidian Vault 없음: $(BOLD)make setup-obsidian$(RST)"
	@echo "  $(DIM)모두 OK 면 'ooo interview \"...\"' 로 첫 워크플로우 시작.$(RST)"
	@echo ""

## install-runtime: 활성 런타임에 맞는 CLI 를 설치한다 (내부 타겟)
install-runtime:
	@if [ "$(OUROBOROS_RUNTIME)" = "hermes" ]; then \
	  $(MAKE) install-hermes; \
	else \
	  $(MAKE) install-codex; \
	fi

## preflight: 필수 의존성을 점검한다
preflight:
	@bash scripts/preflight.sh

## install-codex: Codex CLI 를 설치한다 (npm)
install-codex:
	@bash scripts/install_codex.sh

## install-hermes: Hermes CLI 를 설치한다
install-hermes:
	@bash scripts/install_hermes.sh

## install-ouroboros: Ouroboros 를 설치한다 (런타임 자동 감지 + MCP 등록)
install-ouroboros:
	@echo "$(DIM)→ Ouroboros 설치 (runtime=$(OUROBOROS_RUNTIME))$(RST)"
	@OUROBOROS_INSTALL_RUNTIME=$(OUROBOROS_RUNTIME) \
	  bash scripts/install_ouroboros.sh

## setup-obsidian: Obsidian Vault 를 배치하고 앱 설치를 안내한다
setup-obsidian:
	@STACK_HOME=$(STACK_HOME) OBSIDIAN_VAULT=$(OBSIDIAN_VAULT) \
	  bash scripts/setup_obsidian.sh

## configure: 모든 구성요소를 연결하는 config 를 생성한다
configure:
	@STACK_HOME=$(STACK_HOME) OBSIDIAN_VAULT=$(OBSIDIAN_VAULT) \
	  OUROBOROS_RUNTIME=$(OUROBOROS_RUNTIME) \
	  bash scripts/configure.sh

## auth-codex: Codex 인증 절차를 안내한다 (자동화 불가 — 사람이 실행)
auth-codex:
	@echo ""
	@echo "$(BOLD)Codex 인증 — 브라우저 OAuth 단계$(RST)"
	@echo "$(DIM)이 단계는 자동화할 수 없습니다 (브라우저 로그인 필요).$(RST)"
	@echo ""
	@echo "  아래 명령을 직접 실행하세요:"
	@echo ""
	@echo "      $(BOLD)codex login$(RST)"
	@echo ""
	@echo "  → 브라우저가 열리면 ChatGPT Pro 계정으로 로그인"
	@echo "  → 인증 토큰은 codex CLI 가 ~/.codex/auth.json 에 보관합니다"
	@echo "  → Ouroboros 는 이 토큰을 다루지 않습니다 (codex 바이너리만 호출)"
	@echo ""
	@command -v codex >/dev/null 2>&1 \
	  && echo "$(DIM)codex CLI 감지됨. 위 명령 실행 후 'make verify' 로 확인하세요.$(RST)" \
	  || echo "먼저 'make install-codex' 를 실행하세요."
	@echo ""

## auth-hermes: Hermes Codex provider 인증 절차를 안내한다 (자동화 불가)
auth-hermes:
	@echo ""
	@echo "$(BOLD)Hermes Codex provider 인증 — device code 단계$(RST)"
	@echo "$(DIM)이 단계는 자동화할 수 없습니다 (브라우저 device code 입력 필요).$(RST)"
	@echo ""
	@echo "  방법 A — 기존 codex 자격증명 자동 import (codex login 이미 했다면):"
	@echo "      $(BOLD)hermes auth import codex-cli$(RST)"
	@echo "      → ~/.codex/auth.json 을 읽어 ~/.hermes/auth.json 에 복사"
	@echo ""
	@echo "  방법 B — Hermes 에서 직접 device code 로 로그인:"
	@echo "      $(BOLD)hermes auth add codex-oauth$(RST)"
	@echo "      → URL 과 코드 표시 → 브라우저에서 ChatGPT 로그인 → 코드 입력"
	@echo ""
	@echo "  또는 인터랙티브 메뉴로:"
	@echo "      $(BOLD)hermes model$(RST) → \"OpenAI Codex\" 선택"
	@echo ""
	@echo "  → 인증 토큰은 hermes CLI 가 ~/.hermes/auth.json 에 보관합니다"
	@echo "  → ChatGPT Pro 쿼터를 codex CLI 와 동일하게 소비합니다"
	@echo ""
	@command -v hermes >/dev/null 2>&1 \
	  && echo "$(DIM)hermes CLI 감지됨. 위 중 하나 실행 후 'make verify' 로 확인하세요.$(RST)" \
	  || echo "먼저 'make install-hermes' 를 실행하세요."
	@echo ""

## verify: 설치 상태를 검증한다
verify:
	@STACK_HOME=$(STACK_HOME) OBSIDIAN_VAULT=$(OBSIDIAN_VAULT) \
	  OUROBOROS_RUNTIME=$(OUROBOROS_RUNTIME) \
	  bash scripts/verify.sh

## doctor: 흔한 문제를 진단하고 해결책을 제시한다
doctor:
	@STACK_HOME=$(STACK_HOME) OBSIDIAN_VAULT=$(OBSIDIAN_VAULT) \
	  OUROBOROS_RUNTIME=$(OUROBOROS_RUNTIME) \
	  bash scripts/verify.sh --doctor

## sync-vault: 팀 vault 수동 sync — pull → 본인 host mirror commit → push
##             평소엔 Obsidian Git 플러그인이 자동으로 하지만, 명시적으로 돌리고 싶을 때.
sync-vault:
	@if [ -z "$$TEAM_VAULT_REPO" ]; then \
	  echo "  ${DIM}TEAM_VAULT_REPO 미설정 — 솔로 모드라 sync 불필요${RST}"; \
	  exit 0; \
	fi
	@if [ ! -d "$(OBSIDIAN_VAULT)/.git" ]; then \
	  echo "  [WARN] $(OBSIDIAN_VAULT) 가 git repo 가 아님 — make setup-obsidian 먼저"; \
	  exit 1; \
	fi
	@echo "$(DIM)→ vault sync ($(OBSIDIAN_VAULT))$(RST)"
	@cd "$(OBSIDIAN_VAULT)" && git pull --rebase --autostash 2>&1 | sed 's/^/  /'
	@cd "$(OBSIDIAN_VAULT)" && git add "ouroboros/$$HOSTNAME_KIND" 2>/dev/null \
	  && git diff --cached --quiet \
	     || (git commit -m "vault: auto-sync from $$HOSTNAME_KIND" && git push)
	@echo "  $(BOLD)✓$(RST) sync 완료"

## clean: 생성된 config 를 제거한다 (CLI 설치본은 건드리지 않음)
clean:
	@echo "$(DIM)→ 생성된 config 제거 (CLI 설치본은 유지)$(RST)"
	@rm -rf $(STACK_HOME)
	@echo "완료. CLI 바이너리와 Obsidian Vault 는 그대로 두었습니다."

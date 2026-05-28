# agent-stack

Hermes · Ouroboros · Codex · Obsidian 을 한 번의 `make` 로 배치하는 통합 설치 오케스트레이터.

> **이 레포는 위 4개 도구의 fork/vendor 가 아닙니다.** 글루 코드(`init.sh`, `Makefile`, `scripts/*.sh`)만 들어 있고, 본체는 모두 공식 채널에서 받습니다:
> - **Hermes CLI** → PyPI [`hermes-agent`](https://pypi.org/project/hermes-agent/) (by [NousResearch](https://github.com/NousResearch/hermes-agent))
> - **Codex CLI** → npm [`@openai/codex`](https://www.npmjs.com/package/@openai/codex) (by OpenAI)
> - **Ouroboros** → PyPI [`ouroboros-ai`](https://pypi.org/project/ouroboros-ai/)
> - **Obsidian** → Homebrew cask `obsidian` (공식 .dmg 동일)
>
> 우리가 더하는 가치는 이 넷이 서로의 출력을 인식하도록 **config 를 한 곳에서 묶는 것**입니다 (`~/.wigtn-stack/stack.yaml`).

## 무엇을, 왜

이 레포는 네 구성요소를 하나의 워크플로우로 묶습니다. 각자의 역할은 같은 층이 아니라 위아래로 포개집니다.

- **Ouroboros** — Agent OS. `interview → seed → execute → evaluate → evolve` 의 명세 우선 워크플로우 레이어. 비결정적 에이전트 작업을 재현·관찰 가능한 실행 계약으로 바꿉니다.
- **Codex CLI / Hermes CLI** — Ouroboros 가 셸 아웃하는 실행 런타임. `OUROBOROS_RUNTIME` 설정값 하나로 둘 사이를 전환합니다. 둘은 경쟁재가 아니라 교체 가능한 백엔드입니다.
- **Obsidian** — 실행 주체가 아닌 지식 레이어. Ouroboros 의 명세·저널·검증 결과를 Markdown 으로 받아 사람이 검토합니다.

### Codex Pro 와의 관계 — 핵심

**Codex CLI 와 Hermes CLI 둘 다 ChatGPT Pro 구독 쿼터로 동작 가능합니다.** 인증 방식·도구 표면은 다르지만, 모두 같은 OAuth 통로(ChatGPT 로그인)를 거쳐 Codex 모델을 호출합니다.

| 런타임 | 인증 명령 | 확인 명령 | 추가 결제 |
| :--- | :--- | :--- | :--- |
| `codex`  | `codex login` (브라우저 OAuth) | `ls ~/.codex/auth.json` | 없음 (Pro 쿼터) |
| `hermes` | `hermes auth add openai-codex --type oauth` **또는** `hermes model` → "OpenAI Codex" | `hermes auth list` | 없음 (Pro 쿼터) |

Hermes v0.14+ 는 codex 자격증명 import 명령이 없습니다. codex CLI 와 hermes 는 각자 자기 토큰을 따로 보관하지만, **같은 ChatGPT Pro 계정으로 OAuth 하면 쿼터는 공유**됩니다 (양쪽 한 번씩 로그인 필요).

> ⚠️ Hermes 를 ChatGPT 가 아닌 다른 provider (OpenRouter, Nous Portal, OpenAI API 키 등)로 설정하면 그 때부터는 별도 결제가 발생하고 Codex Pro 와 무관해집니다. 기본 권장 구성은 두 런타임 모두 **"OpenAI Codex" provider** 입니다.

### 두 런타임의 진짜 차이

비용·인증이 같다면 무엇이 다른가요? **에이전트 셸의 동작**이 다릅니다.

| 항목 | Codex CLI | Hermes CLI |
| :--- | :--- | :--- |
| 설계 주체 | OpenAI | NousResearch |
| 도구 정책 | OpenAI 가 튜닝 (sandbox, exec 제한 등) | 사용자 정의 가능 |
| 자기개선 | 없음 | **세션 간 skill 자동 학습** |
| 영구 메모리 | 없음 | **`~/.hermes/memories/` 누적** |
| 다중 provider | OpenAI 모델만 | 50+ provider 전환 가능 |
| 비대화식 자동화 | `codex exec ...` | `hermes chat -Q ...` |

→ "가볍게 Pro 쿼터만 활용" 이면 `codex` 가 단순. "자기개선·메모리·다양한 모델 실험" 이면 `hermes`.

## 빠른 시작

### 시나리오 A — 빈 Mac mini (Xcode CLT, Homebrew, Python 다 없음)

**총 소요 시간 15~25분**, **사용자가 손 대는 횟수 3번.** 나머지는 전부 자동.

#### STEP 0. macOS 초기 설정 (Apple 박스 개봉)

```
박스 개봉 → macOS 첫 부팅 → Apple ID 로그인 → Wi-Fi 연결
Spotlight (⌘+Space) → "Terminal" → 터미널 열기
```

#### STEP 1. 한 줄 부트스트랩

**Codex 런타임 (기본):**
```bash
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | ORG=wigtn bash
```

**Hermes 런타임:**
```bash
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | ORG=wigtn RUNTIME=hermes bash
```

> `ORG=wigtn` 은 솔로 모드로 시작합니다 (vault SSH 의존 없음). 팀 vault 합류는 SSH key 등록 후 별도로 `TEAM_VAULT_REPO=git@github.com:wigtn/wigtn-team-wiki.git make setup-obsidian`.

#### STEP 2. init.sh 가 진행하는 8단계 + 손 댈 곳 3군데

```
[1/8] Xcode CLT 설치
       ▼
       ★ 손 댈 곳 1) GUI 다이얼로그 "Install" 클릭 + 설치 완료 대기 (5~10분)
                   완료 후 터미널에서 Enter

[2/8] Homebrew 설치
       ▼
       ★ 손 댈 곳 2) sudo 비밀번호 입력 (한 번)
                   Apple Silicon 이면 /opt/homebrew 자동 PATH 등록

[3/8] brew 패키지 (자동, 1~3분)
       └─ python@3.12, node, git, pipx, jq, gettext

[4/8] Obsidian.app 설치 (자동, brew cask)

[5/8] git clone (자동) → ~/agent-stack/

[6/8] .env 생성 (자동)
       └─ ORG=wigtn, OUROBOROS_RUNTIME=codex|hermes, HOSTNAME_KIND=<호스트이름>

[7/8] make all (자동, 2~5분)
       ├─ preflight       — 의존성 점검
       ├─ install-codex   — npm i -g @openai/codex     (RUNTIME=codex)
       │  또는 install-hermes — pipx install hermes-agent
       ├─ install-ouroboros — pipx + Codex MCP/rules/skills 자동 등록
       ├─ configure       — ~/.wigtn-stack/stack.yaml 생성
       └─ verify          — 인증 빼고 전부 OK 떠야 정상

[8/8] Obsidian Vault 디렉토리 생성
       └─ ~/WigtnVault/{specs,journal,evaluations,seeds}

       ▼
       ★ 손 댈 곳 3) 자동으로 인증 OAuth 띄움 — 브라우저에서 ChatGPT Pro 로그인
                   RUNTIME=codex  → codex login
                   RUNTIME=hermes → hermes auth add openai-codex --type oauth
```

#### STEP 3. 검증 + 첫 워크플로우

```bash
cd ~/agent-stack

# 1) 전부 OK 인지 확인
make verify
# → ouroboros, codex(또는 hermes), stack.yaml, Vault 전부 [OK] 여야 함

# 2) Obsidian 열어서 Vault 등록 (GUI — 한 번만)
open -a Obsidian
#   → "Open folder as vault" → /Users/<you>/WigtnVault 선택

# 3) 첫 워크플로우 — Ouroboros 가 명세→실행→검증→진화 한 사이클
ooo interview start "내가 만들고 싶은 거 한 줄로 설명"
```

#### 흔한 막힘 + 즉시 해결

| 증상 | 원인 | 처방 |
|---|---|---|
| `Xcode CLT not found` | GUI 다이얼로그 X 눌러서 닫음 | `xcode-select --install` 다시 실행 |
| `brew: command not found` (재로그인 후) | `.zprofile` 등록 안 됨 | `eval "$(/opt/homebrew/bin/brew shellenv)"` 그 후 새 터미널 |
| `codex` 실행 시 `ENOENT` (vendor 경로 깨짐) | npm postinstall 실패 — 묵은 캐시 | `npm uninstall -g @openai/codex && npm install -g @openai/codex` |
| `hermes auth list` 에 openai-codex 없음 | OAuth 안 끝남 | `hermes auth add openai-codex --type oauth` 재시도 |
| `make verify` 에서 codex/hermes 미인증 | OAuth 단계 skip 됨 | `codex login` 또는 `hermes auth add openai-codex --type oauth` |

#### 한 화면 요약

```
1. 터미널 열기
2. curl -fsSL .../init.sh | ORG=wigtn bash      (Hermes 면 + RUNTIME=hermes)
3. 손 댈 곳 3번:
   ┌── (a) Xcode CLT GUI Install 클릭
   ├── (b) sudo 비밀번호 (Homebrew)
   └── (c) 브라우저에서 ChatGPT Pro 로그인
4. make verify → 전부 [OK]
5. ooo interview start "..."
```

#### 보조: 그냥 짧은 형태가 필요하면

```bash
# Codex
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | bash

# Hermes
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | RUNTIME=hermes bash
```

### 시나리오 B — 개발 환경 이미 갖춘 Mac (Python 3.12+, Node, pipx 모두 있음)

`init.sh` 의 prereq 설치를 건너뛰고 바로 `make all` 부터.

```bash
git clone https://github.com/wigtn/wigtn-hermes-agent.git
cd wigtn-hermes-agent
cp .env.example .env      # 필요하면 OUROBOROS_RUNTIME, 경로 수정
make all                  # preflight → CLI → ouroboros → configure → verify
```

`make all` 이후 자동화 불가능한 수동 단계가 남습니다 (런타임에 따라 다름):

```bash
# 두 런타임 모두 공통:
make setup-obsidian       # Obsidian Vault 디렉토리 + 앱 설치 안내

# OUROBOROS_RUNTIME=codex 일 때:
make auth-codex           # codex login — 브라우저 OAuth

# OUROBOROS_RUNTIME=hermes 일 때:
make auth-hermes          # hermes auth add openai-codex --type oauth — 브라우저 OAuth
```

이 단계들이 수동인 이유는 명확합니다. 브라우저 OAuth 와 GUI 앱 설치는 헤드리스로 못 합니다. Makefile 로 억지로 자동화하는 대신, 멈춰서 정확한 명령어를 안내합니다 — 토큰을 레포에 박을 일도 없습니다.

### 시나리오 C — 이미 codex CLI 까지 다 있는 개발자 (핵심만 빨리)

`codex` 또는 `hermes` CLI 까지 이미 PATH 에 있고, **Ouroboros 만 깔아서 연결만 확인**하고 싶을 때.

```bash
git clone https://github.com/wigtn/wigtn-hermes-agent.git
cd wigtn-hermes-agent
cp .env.example .env
make dev                  # install-ouroboros → configure → verify
```

`make dev` 가 건너뛰는 것:
- ❌ preflight (이미 다 있다고 가정)
- ❌ install-codex / install-hermes (이미 깔려 있다고 가정)
- ❌ setup-obsidian (Obsidian 안 쓰는 워크플로우도 있으니 opt-in)

`make dev` 끝나면 **현재 환경에 빠진 것만** 골라서 안내합니다 — codex 미인증이면 `make auth-codex`, Vault 없으면 `make setup-obsidian` 식으로.

런타임을 hermes 로 시도하려면 `.env` 의 `OUROBOROS_RUNTIME=hermes` 로 바꾼 뒤 `make dev` 재실행.

## 타겟

| 타겟 | 하는 일 |
| :--- | :--- |
| `make all` | 자동화 가능한 전체 설치 |
| `make preflight` | python 3.12+, node, git, 디스크 점검 |
| `make install-codex` | Codex CLI 설치 (npm) |
| `make install-hermes` | Hermes CLI 설치 (선택적 런타임) |
| `make install-ouroboros` | Ouroboros 설치 + MCP 서버 등록 |
| `make setup-obsidian` | Vault 디렉토리 배치 + 앱 설치 안내 |
| `make configure` | 런타임·Vault 경로를 연결하는 config 생성 |
| `make auth-codex` | Codex 인증 절차 안내 (`codex login`) |
| `make auth-hermes` | Hermes openai-codex provider 인증 안내 (`hermes auth add openai-codex --type oauth`) |
| `make verify` | 설치 상태 검증 |
| `make doctor` | 흔한 문제 진단 |

## 런타임 전환

`.env` 의 `OUROBOROS_RUNTIME` 값만 바꾸고 `make configure && make verify` 를 다시 실행하면 됩니다.

```
OUROBOROS_RUNTIME=codex     # Codex CLI 사용. ChatGPT Pro 쿼터.
OUROBOROS_RUNTIME=hermes    # Hermes CLI 사용. 기본은 "OpenAI Codex" provider → 동일 Pro 쿼터.
```

`hermes` 런타임을 선택한 뒤 다른 provider 로 바꾸려면 `hermes model` 또는 `hermes config set model.provider <name>` 으로 갈아끼울 수 있습니다 (OpenRouter, Nous Portal, OpenAI API 키 등). 이 경우 결제 경로가 달라집니다.

## 팀 vault — N 대 Mac mini 공유

여러 Mac mini 가 **하나의 vault** 를 공유합니다. 각자 자기 호스트 디렉토리만 쓰므로 충돌 없음.

### 동작 방식

```
wigtn-team-wiki/                ← private GitHub repo
├── shared/                     ← 사람이 PR로 큐레이션
├── ouroboros/                  ← 봇 자동 출력 (path-guard CI 가 사람 PR 차단)
│   ├── harry-macmini/          ← Mac mini #1 만 씀
│   │   ├── specs/  journal/  evaluations/  seeds/
│   └── wigton-macmini/         ← Mac mini #2 만 씀
│       └── ...
├── per-user/                   ← 개인 스크래치패드
└── .obsidian/                  ← 공유 플러그인 설정 (obsidian-git 5분 sync 등)
```

### 새 Mac mini 추가

같은 한 줄. ORG 만 맞으면 됨:

```bash
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh \
  | ORG=wigtn bash
```

- vault 가 자동으로 `~/WigtnVault` 로 clone
- 본인 hostname 으로 `ouroboros/$HOSTNAME_KIND/` 디렉토리 자동 생성
- Ouroboros 가 spec/journal/eval 을 거기로 mirror
- Obsidian 열면 Git 플러그인이 5분 주기로 pull/push

### 수동 sync

```bash
make sync-vault     # 본인 호스트 mirror 만 commit + push
```

평소엔 Obsidian Git 플러그인이 자동이지만 명시적으로 트리거하고 싶을 때.

### 환경 변수

| 변수 | 의미 | 기본값 (ORG=wigtn) |
|------|------|--------------------|
| `ORG` | 조직 프로파일 | `wigtn` |
| `TEAM_VAULT_REPO` | vault git URL | `git@github.com:wigtn/wigtn-team-wiki.git` |
| `OBSIDIAN_VAULT` | vault 로컬 경로 | `$HOME/WigtnVault` |
| `STACK_HOME` | agent-stack config 위치 | `$HOME/.wigtn-stack` |
| `HOSTNAME_KIND` | 본인 mirror 디렉토리 이름 | `scutil --get LocalHostName` |

## 디렉토리

```
agent-stack/
├── init.sh               빈 Mac mini 한 줄 부트스트랩 (curl|bash)
├── Makefile              설치 오케스트레이션
├── .env.example          구성 템플릿 (.env 로 복사)
└── scripts/
    ├── preflight.sh       사전 점검
    ├── install_codex.sh   Codex CLI 설치
    ├── install_hermes.sh  Hermes CLI 설치
    ├── install_ouroboros.sh  Ouroboros 설치 + MCP 등록
    ├── setup_obsidian.sh  Vault 배치 + 앱 안내
    ├── configure.sh       구성 연결
    └── verify.sh          검증 + 진단
```

## 라이선스

MIT

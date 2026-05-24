# agent-stack

Hermes · Ouroboros · Codex · Obsidian 을 한 번의 `make` 로 배치하는 통합 설치 오케스트레이터.

## 무엇을, 왜

이 레포는 네 구성요소를 하나의 워크플로우로 묶습니다. 각자의 역할은 같은 층이 아니라 위아래로 포개집니다.

- **Ouroboros** — Agent OS. `interview → seed → execute → evaluate → evolve` 의 명세 우선 워크플로우 레이어. 비결정적 에이전트 작업을 재현·관찰 가능한 실행 계약으로 바꿉니다.
- **Codex CLI / Hermes CLI** — Ouroboros 가 셸 아웃하는 실행 런타임. `OUROBOROS_RUNTIME` 설정값 하나로 둘 사이를 전환합니다. 둘은 경쟁재가 아니라 교체 가능한 백엔드입니다.
- **Obsidian** — 실행 주체가 아닌 지식 레이어. Ouroboros 의 명세·저널·검증 결과를 Markdown 으로 받아 사람이 검토합니다.

### Codex Pro 와의 관계 — 핵심

**Codex CLI 와 Hermes CLI 둘 다 ChatGPT Pro 구독 쿼터로 동작 가능합니다.** 인증 방식·도구 표면은 다르지만, 모두 같은 OAuth 통로(ChatGPT 로그인)를 거쳐 Codex 모델을 호출합니다.

| 런타임 | 인증 명령 | 토큰 위치 | 추가 결제 |
| :--- | :--- | :--- | :--- |
| `codex`  | `codex login` (브라우저 OAuth) | `~/.codex/auth.json` | 없음 (Pro 쿼터) |
| `hermes` | `hermes auth add codex-oauth` (device code) **또는** `hermes model` → "OpenAI Codex" | `~/.hermes/auth.json` | 없음 (Pro 쿼터) |

Hermes 의 OpenAI Codex provider 는 `~/.codex/auth.json` 의 기존 자격증명을 **자동으로 import** 합니다. 따라서 `codex login` 을 이미 했다면 Hermes 쪽 인증이 즉시 끝납니다.

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

**한 줄로 끝.** init.sh 가 prereqs 부터 make all 까지 다 합니다.

```bash
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh \
  | ORG=wigtn bash
```

`ORG=wigtn` 이 자동으로 [wigtn-team-wiki](https://github.com/wigtn/wigtn-team-wiki) 를 vault 로 연결합니다. **2번째 Mac mini, 3번째 Mac mini 도 정확히 같은 한 줄**. 각자 `scutil --get LocalHostName` 결과로 자동 분리된 mirror 디렉토리를 갖습니다 (충돌 없음).

솔로 모드 (vault 공유 안 함) 로 시작하려면 ORG 생략:
```bash
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | bash
```

자동으로 진행되는 것:
1. Xcode CLT 설치 트리거 (GUI 팝업 → 'Install' 클릭만)
2. Homebrew 설치 + Apple Silicon PATH 영구 등록
3. `python@3.12 node git pipx jq gettext` brew 설치
4. Obsidian 데스크톱 앱 (cask) 설치
5. 레포 clone → `$HOME/agent-stack`
6. `.env` 생성
7. `make all` 실행 (preflight → CLI 설치 → Ouroboros → configure → verify)
8. `make setup-obsidian` (Vault 디렉토리 생성)

이후 남는 수동 단계 2개:
- `codex login` 또는 `make auth-hermes` — 브라우저 OAuth (본질적으로 사람 손)
- Obsidian 앱 첫 실행 + Vault 폴더 선택 — GUI

런타임을 hermes 로 시작하려면:
```bash
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
make auth-hermes          # hermes auth add codex-oauth — device code OAuth
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
| `make auth-hermes` | Hermes Codex provider 인증 안내 (`hermes auth add codex-oauth`) |
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

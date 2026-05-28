# wigtn-hermes-bootstrap

빈 Mac mini 에 **Hermes Agent** 를 한 줄로 깔고, **ChatGPT Pro 구독 쿼터**로 바로 동작시키는 부트스트랩.

> **이 레포는 Hermes Agent 의 fork 가 아닙니다.** 글루 코드(`init.sh`, `Makefile`, `scripts/*.sh`)만 들어 있고, Hermes 본체는 공식 채널에서 받습니다:
> - **Hermes CLI** → PyPI [`hermes-agent`](https://pypi.org/project/hermes-agent/) (by [NousResearch](https://github.com/NousResearch/hermes-agent))
>
> 우리가 더하는 가치는 **빈 Mac mini → 동작까지 한 줄**, 그리고 `openai-codex` provider 로 ChatGPT Pro OAuth 를 자동 트리거하는 것입니다.

## 무엇을, 왜

- **Hermes Agent** — 50+ provider 를 지원하는 self-improving 에이전트 셸. 영구 메모리(`~/.hermes/memories/`), 자동 학습되는 스킬, 비대화 자동화(`hermes chat -q "..."`) 가 기본 탑재.
- **ChatGPT Pro 쿼터 연결** — Hermes 의 `openai-codex` provider 는 OpenAI Codex CLI 와 같은 OAuth 통로(ChatGPT 로그인)를 거쳐 Codex 모델을 호출합니다. 추가 결제 없음.

> ⚠️ Hermes 를 다른 provider (OpenRouter, Nous Portal, OpenAI API 키 등) 로 설정하면 그 때부터는 별도 결제가 발생합니다. 기본 권장 구성은 `openai-codex` provider 입니다.

## 빠른 시작

### 시나리오 A — 빈 Mac mini (Xcode CLT, Homebrew, Python 다 없음)

**총 소요 시간 10~20분**, **사용자가 손 대는 횟수 3번.** 나머지는 전부 자동.

#### STEP 0. macOS 초기 설정 (Apple 박스 개봉)

```
박스 개봉 → macOS 첫 부팅 → Apple ID 로그인 → Wi-Fi 연결
Spotlight (⌘+Space) → "Terminal" → 터미널 열기
```

#### STEP 1. 한 줄 부트스트랩

```bash
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | bash
```

#### STEP 2. init.sh 가 진행하는 6단계 + 손 댈 곳 3군데

```
[1/6] Xcode CLT 설치
       ▼
       ★ 손 댈 곳 1) GUI 다이얼로그 "Install" 클릭 + 설치 완료 대기 (5~10분)
                   완료 후 터미널에서 Enter

[2/6] Homebrew 설치
       ▼
       ★ 손 댈 곳 2) sudo 비밀번호 입력 (한 번)
                   Apple Silicon 이면 /opt/homebrew 자동 PATH 등록

[3/6] brew 패키지 (자동, 1~2분)
       └─ python@3.12, git, pipx, jq, gettext

[4/6] git clone (자동) → ~/wigtn-hermes/

[5/6] make install (자동, 1~3분)
       ├─ preflight        — python 3.12+, git, pipx 점검
       ├─ install-hermes   — pipx install hermes-agent + postinstall
       └─ verify           — 인증 빼고 전부 OK 떠야 정상

[6/6] Hermes openai-codex provider 인증
       ▼
       ★ 손 댈 곳 3) 자동으로 OAuth 띄움 — 브라우저에서 ChatGPT Pro 로그인
                   (hermes auth add openai-codex --type oauth)
```

#### STEP 3. 검증 + 첫 사용

```bash
cd ~/wigtn-hermes

# 1) 전부 OK 인지 확인
make verify
# → hermes CLI, openai-codex provider 인증 모두 [OK]

# 2) 첫 질문 — Pro 쿼터로 동작 확인
hermes chat -q "say hello in one short sentence" -Q

# 3) 인터랙티브 셸
hermes
```

#### 흔한 막힘 + 즉시 해결

| 증상 | 원인 | 처방 |
|---|---|---|
| `Xcode CLT not found` | GUI 다이얼로그 X 눌러서 닫음 | `xcode-select --install` 다시 실행 |
| `brew: command not found` (재로그인 후) | `.zprofile` 등록 안 됨 | `eval "$(/opt/homebrew/bin/brew shellenv)"` 그 후 새 터미널 |
| `hermes: command not found` | pipx PATH 미적용 | `pipx ensurepath` 그 후 새 터미널 |
| `hermes auth list` 에 openai-codex 없음 | OAuth 안 끝남 | `hermes auth add openai-codex --type oauth` 재시도 |
| 다른 provider 가 잡혔음 | 실수로 다른 provider OAuth | `hermes auth remove <provider>` 후 재인증 |

#### 한 화면 요약

```
1. 터미널 열기
2. curl -fsSL .../init.sh | bash
3. 손 댈 곳 3번:
   ┌── (a) Xcode CLT GUI Install 클릭
   ├── (b) sudo 비밀번호 (Homebrew)
   └── (c) 브라우저에서 ChatGPT Pro 로그인
4. make verify → 전부 [OK]
5. hermes chat -q "..." -Q   또는   hermes
```

### 시나리오 B — 개발 환경 이미 갖춘 Mac (Python 3.12+, pipx 있음)

```bash
git clone https://github.com/wigtn/wigtn-hermes-agent.git ~/wigtn-hermes
cd ~/wigtn-hermes
make install              # preflight → install-hermes → verify
make auth-hermes          # 안내대로 OAuth (브라우저)
```

또는 더 짧게:

```bash
pipx install hermes-agent
hermes postinstall
hermes auth add openai-codex --type oauth
```

이 레포의 부가가치는 자동 인증 트리거 + verify/doctor 정도. 익숙한 개발자는 두 번째 형태로 충분.

## 타겟

| 타겟 | 설명 |
|---|---|
| `make install` | 전체 설치 — preflight → install-hermes → verify |
| `make preflight` | 사전 점검 (python 3.12+, git, pipx) |
| `make install-hermes` | Hermes CLI 설치 (pipx) |
| `make auth-hermes` | openai-codex provider 인증 안내 |
| `make verify` | 설치 상태 검증 |
| `make doctor` | 문제 진단 + 해결책 제시 |

## 환경 변수 (.env)

```bash
# 현재는 추가 설정 없이 동작합니다.
# .env 는 미래 확장 (Hermes 환경 override 등) 여지로 둡니다.
```

`.env` 파일이 없어도 무관. `.env.example` 참조.

## 디렉토리

```
wigtn-hermes-agent/
├── init.sh               빈 Mac mini 한 줄 부트스트랩 (curl|bash)
├── Makefile              5개 타겟 오케스트레이션
├── .env.example          (거의 비어 있음 — 향후 확장용)
├── scripts/
│   ├── preflight.sh      python/git/pipx 점검
│   ├── install_hermes.sh pipx install hermes-agent + postinstall
│   └── verify.sh         hermes 인증 검증 (+ --doctor 진단 모드)
└── README.md             이 파일
```

설치 후 사용자 머신:

```
~/wigtn-hermes/           이 레포 (init.sh 가 자동 clone)
~/.hermes/                Hermes 데이터 (memories, sessions, skills, hooks)
~/.local/bin/hermes       Hermes CLI 바이너리 (pipx)
```

## 라이선스

MIT. `LICENSE` 참조.

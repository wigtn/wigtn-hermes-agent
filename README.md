# wigtn-hermes

빈 Mac mini 에 **Hermes Agent** 를 깔고, 거기서 끝내지 않고 **계속 돌게** 만드는 글루 코드.

> **이 레포는 Hermes Agent 의 fork 가 아닙니다.** 글루 코드만 들어 있고 Hermes 본체는 공식 채널에서 받습니다.
> **Hermes CLI** → PyPI [`hermes-agent`](https://pypi.org/project/hermes-agent/) (by [NousResearch](https://github.com/NousResearch/hermes-agent))

레포가 다루는 것은 두 축입니다.

| | 무엇 | 어디 |
|---|---|---|
| **설치** | 빈 Mac mini → 동작까지 한 줄. ChatGPT Pro 구독 쿼터로 바로 씁니다 | `init.sh`, `Makefile`, `scripts/` |
| **운영** | 죽으면 되살리고, 쌓이면 치우고, PR 이 올라오면 자동으로 리뷰합니다 | `ops/`, [`docs/operations.md`](docs/operations.md) |

설치는 한 번이지만 운영은 계속입니다. **두 번째 축이 이 레포의 무게 중심**입니다.

---

# 1. 설치

## 시나리오 A — 빈 Mac mini

Xcode CLT, Homebrew, Python 아무것도 없는 상태에서 시작합니다.
**총 10~20분, 사람이 손 대는 곳 3번.** 나머지는 전부 자동입니다.

```bash
curl -fsSL https://raw.githubusercontent.com/wigtn/wigtn-hermes-agent/main/init.sh | bash
```

`init.sh` 가 도는 순서와 손 댈 지점입니다.

```
[1/6] Xcode CLT 설치
       ★ 손 댈 곳 1) GUI "Install" 클릭 + 완료 대기 (5~10분) → 터미널에서 Enter

[2/6] Homebrew 설치
       ★ 손 댈 곳 2) sudo 비밀번호 (한 번)

[3/6] brew 패키지 — python@3.12, git, pipx, jq, gettext
[4/6] git clone → ~/wigtn-hermes/
[5/6] make install — preflight → install-hermes → verify

[6/6] openai-codex provider 인증
       ★ 손 댈 곳 3) 브라우저에서 ChatGPT Pro 로그인
```

끝나면 확인합니다.

```bash
cd ~/wigtn-hermes
make verify                                  # 전부 [OK]
hermes chat -q "say hello in one sentence" -Q # 응답 오면 쿼터 연결 확인
hermes                                       # 인터랙티브 셸
```

## 시나리오 B — 개발 환경이 이미 있는 Mac

```bash
git clone https://github.com/wigtn/wigtn-hermes-agent.git ~/wigtn-hermes
cd ~/wigtn-hermes
make install
make auth-hermes
```

익숙하면 더 짧게도 됩니다. 이 경우 레포의 부가가치는 `verify` 와 `doctor` 정도입니다.

```bash
pipx install hermes-agent
hermes postinstall
hermes auth add openai-codex --type oauth
```

## 흔한 막힘

| 증상 | 원인 | 처방 |
|---|---|---|
| `Xcode CLT not found` | GUI 다이얼로그를 닫음 | `xcode-select --install` 다시 |
| `brew: command not found` | `.zprofile` 미등록 | `eval "$(/opt/homebrew/bin/brew shellenv)"` 후 새 터미널 |
| `hermes: command not found` | pipx PATH 미적용 | `pipx ensurepath` 후 새 터미널 |
| `hermes auth list` 에 openai-codex 없음 | OAuth 미완료 | `hermes auth add openai-codex --type oauth` |
| `No inference provider configured` | default provider 미선택 | `hermes model` → OpenAI Codex |
| 다른 provider 가 잡힘 | 실수로 다른 OAuth | `hermes auth remove <provider>` 후 재인증 |

## 비용

`openai-codex` provider 는 OpenAI Codex CLI 와 같은 OAuth 통로를 거칩니다. **ChatGPT 구독 쿼터로 동작하며 추가 결제가 없습니다.**

> ⚠️ 다른 provider (OpenRouter, Nous Portal, OpenAI API 키) 로 바꾸면 그때부터 별도 과금이 발생합니다.

---

# 2. 운영

설치가 끝나면 Hermes 는 게이트웨이 프로세스 하나로 Slack, webhook, 칸반, cron 을 전부 물고 돕니다.
문제는 **프로세스가 살아있는 채로 기능만 죽는 경우**입니다. launchd 의 `KeepAlive` 는 이것을 잡지 못합니다.

실제로 겪은 것들입니다.

| 증상 | 결과 |
|---|---|
| Slack 소켓이 끊긴 뒤 닫힌 세션으로 무한 재시도 | 프로세스는 정상, 응답만 35시간 중단 |
| 상태 파일이 부팅 시점 값을 유지 | 죽은 동안에도 `connected` 로 보고 |
| PR 리뷰 자동화가 조용히 멈춤 | 12일간 아무도 인지하지 못함 |
| 작업 워크트리 누적 | 디스크 6.9GB 점유 |

공통점은 **아무도 몰랐다**는 것입니다. 그래서 `ops/` 에 있는 것은 기능이 아니라 **지켜보는 장치**입니다.

| 스크립트 | 주기 | 하는 일 |
|---|---|---|
| `hermes-watchdog.py` | 2분 | 게이트웨이가 실제로 응답 가능한지 점검, 필요하면 재시작하고 Slack 보고 |
| `hermes-webhook-receiver.py` | 상주 | org 웹훅을 받아 PR 이 열리는 즉시 리뷰 태스크 생성 |
| `hermes-pr-scanner.py` | 3분 | 조직의 새 PR 을 훑는 안전망. 수신기가 놓친 것을 주워감 |
| `hermes-worktree-reaper.py` | 매일 | 병합된 PR 의 작업 워크트리 회수 |
| `hermes-pr-notifier.py` | 20초 | 끝난 리뷰를 판정·링크와 함께 Slack 으로 알림 |
| `hermes-metrics.py` | 상주 | 리뷰 건수·판정·소요 시간을 Prometheus 로 노출 |

표준 라이브러리만 씁니다. 추가 의존성이 없습니다.

```bash
GITHUB_ORG=myorg ALERT_CHANNEL=C0123456789 ./ops/install.sh

# 되돌리기
./ops/install.sh --uninstall
```

설계 근거, 환경 변수, 운영 중 확인법은 [`docs/operations.md`](docs/operations.md) 에 있습니다.

> **`hermes update` 뒤에는 `./ops/apply-local-patches.py` 를 돌리세요.** 장애 추적에 필요한
> 로그를 Hermes 패키지에 직접 넣어 둔 것이 있는데, 업그레이드하면 덮어써져 사라집니다.
> 사라진 것을 아무도 모르는 것이 문제라, 다음 장애 때 로그가 비어 있게 됩니다.

---

# 3. PR 자동 리뷰

PR 이 올라오면 리뷰 코멘트가 자동으로 달립니다. 형식적인 요약이 아니라 **검증 결과를 근거로**
판정합니다. 레포에 PR CI 가 있으면 그 결과를 읽고, 없는 레포에서만 워크트리에서 직접 돌립니다.

```
PR 열림
   ↓  org 웹훅 (즉시)  ·  스캐너 3분 주기 (안전망)
칸반 리뷰 태스크 생성
   ↓  디스패처가 워커 spawn, 전용 워크트리 체크아웃
CI 결과 확인 (CI 없는 레포면 로컬 실행) → 판정
   ↓
GitHub  요약 코멘트 1개 (재리뷰 시 수정) + Review API 판정 + 인라인 코멘트
Slack   ✅ [레포] PR #번호 <판정> — <링크>
```

동작 방식에서 신경 쓴 것들입니다.

- **소급 리뷰 방지** — 기준 시각을 처음 실행할 때 기록하고, 그 이후에 열린 PR 만 봅니다. 이미 열려 있던 PR 을 무더기로 리뷰하는 사고를 막습니다.
- **코멘트 누적 방지** — 요약은 마커가 달린 코멘트 하나를 계속 수정합니다. 여섯 번 리뷰해도 코멘트는 하나입니다.
- **중복 안전** — 웹훅과 스캐너를 같이 돌려도 칸반 idempotency key 가 같으면 태스크가 중복되지 않습니다.
- **모델 라우팅** — 문서만 바뀐 작은 PR 은 가벼운 모델로 돌립니다. 코드가 한 줄이라도 섞이면 기본 모델을 씁니다.
- **초안 제외** — draft PR 은 건너뜁니다. 리뷰받고 싶지 않으면 draft 로 열면 됩니다.
- **CI 우선 검증** — 레포별 검증 수단을 스캐너가 지시문에 박아 넣습니다(`REPO_VERIFY`).
  PR CI 가 있는 레포는 그 결과를 읽고 로컬 실행을 건너뜁니다. CI 가 더 정확하고 빠릅니다.
  결과는 요약 코멘트의 `### 검증` 블록에 방법·명령·결과·근거로 남습니다.
- **전용 스킬** — 워커에는 `hermes-pr-autoreview` 스킬만 붙습니다. 범용 코드리뷰 스킬은
  출력 형식과 판정 기준을 따로 지시해서 이 파이프라인의 계약을 덮어씁니다.
- **승인이 기본값** — 차단할 것이 없으면 승인합니다. 습관적으로 의견으로 내리지 않습니다.
  판정이 승인이 아니면 그 이유를 요약 코멘트 첫머리에 적습니다.
- **오탐 신고** — 요약 코멘트 끝에 👎 반응을 요청합니다. 오탐률은 자동 판정이 불가능해서
  사람 라벨링 말고 방법이 없습니다.
- **근거 기준** — 차단 사유로 올릴 수 있는 것은 확인한 것뿐입니다. 확인하지 못한 지적은 등급을 낮추되 버리지 않습니다. 확인 없이 단정해 생긴 오탐을 막기 위한 규칙이고, 자세한 배경은 [`docs/operations.md`](docs/operations.md#리뷰-지시문) 에 있습니다.

리뷰는 참고 자료이지 자동 차단 장치가 아닙니다. `REQUEST_CHANGES` 가 머지를 막게 두지 않는 편이 좋습니다.
오탐 하나에 멀쩡한 PR 이 멈추기 때문입니다. **판단은 사람이 합니다.**

전제 조건은 GitHub 토큰(classic PAT)입니다. 필요한 스코프는 `repo`, `workflow`, `read:org` 이고, org 웹훅까지 쓰려면 `admin:org_hook` 을 더합니다.

---

# 참고

## make 타겟

| 타겟 | 설명 |
|---|---|
| `make install` | 전체 설치 — preflight → install-hermes → verify |
| `make preflight` | 사전 점검 (python 3.12+, git, pipx) |
| `make install-hermes` | Hermes CLI 설치 (pipx) |
| `make auth-hermes` | openai-codex provider 인증 안내 |
| `make verify` | 설치 상태 검증 |
| `make doctor` | 문제 진단 + 해결책 제시 |

## 디렉토리

```
wigtn-hermes-agent/
├── init.sh                          빈 Mac mini 한 줄 부트스트랩 (curl|bash)
├── Makefile                         설치 타겟 오케스트레이션
├── scripts/
│   ├── preflight.sh                 python/git/pipx 점검
│   ├── install_hermes.sh            pipx install hermes-agent + postinstall
│   └── verify.sh                    인증 검증 (+ --doctor 진단 모드)
├── ops/                             운영 — 설치 이후 계속 돌게 만드는 부분
│   ├── install.sh                   운영 스크립트 launchd 등록/해제
│   ├── hermes-watchdog.py           게이트웨이 생존 감시 + 자동 복구
│   ├── hermes-webhook-receiver.py   org 웹훅 수신 → 리뷰 태스크
│   ├── hermes-pr-scanner.py         새 PR 폴링 (안전망)
│   ├── hermes-worktree-reaper.py    병합된 PR 의 워크트리 회수
│   ├── hermes-pr-notifier.py        완료 알림 Slack 발송 (20초)
│   ├── hermes-metrics.py            리뷰 실적 Prometheus 노출
│   ├── apply-local-patches.py       Hermes 패키지 로컬 패치 재적용
│   └── alertmanager.example.yml     Slack 알림 설정 예시
├── docs/
│   └── operations.md                운영 가이드
└── README.md                        이 파일
```

설치 후 사용자 머신:

```
~/wigtn-hermes/           이 레포 (init.sh 가 자동 clone)
~/.hermes/                Hermes 데이터 (memories, sessions, skills, hooks)
~/.local/bin/hermes       Hermes CLI 바이너리 (pipx)
~/hermes-ops/             운영 스크립트 · 로그 · 리뷰용 클론
```

## 환경 변수

설치 단계(`.env`)에는 필수 항목이 없습니다. 운영 단계의 환경 변수는
[`docs/operations.md`](docs/operations.md#환경-변수) 를 참고하세요.

## 라이선스

MIT. `LICENSE` 참조.

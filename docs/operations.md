# 운영 가이드 (day-2)

`init.sh` 로 Hermes 를 깔고 나면 "도는" 상태가 됩니다. 이 문서는 그 다음, **계속 돌게 만드는**
운영 장치를 다룹니다. 2026-08-14 에 맥미니 한 대에서 실제로 겪은 문제와 그 대응을 정리한 것입니다.

- [왜 필요한가](#왜-필요한가)
- [구성 요소](#구성-요소)
- [설치](#설치)
- [운영 중 확인법](#운영-중-확인법)
- [설계 메모](#설계-메모)

---

## 왜 필요한가

Hermes 는 게이트웨이 프로세스 하나로 Slack, webhook, 칸반, cron 을 전부 물고 돕니다.
프로세스가 죽으면 launchd 가 되살리지만, **프로세스가 살아있는 채로 기능만 죽는 경우**가 있습니다.

실제로 겪은 것:

| 증상 | 결과 |
|---|---|
| Slack 소켓이 끊긴 뒤 닫힌 세션으로 무한 재시도 | 프로세스는 정상, 응답만 35시간 중단 |
| 상태 파일(`gateway_state.json`)이 부팅 시점 값을 유지 | 죽은 동안에도 `"connected"` 로 보고 |
| PR 리뷰 자동화가 조용히 멈춤 | 12일간 아무도 인지하지 못함 |
| 작업용 워크트리가 계속 누적 | 디스크 6.9GB 점유, 정리 스크립트는 0MB 만 회수 |

공통점은 **아무도 몰랐다**는 것입니다. launchd 의 `KeepAlive` 는 종료된 프로세스만 되살리고,
알림 채널에는 상시 발동하는 오탐 규칙 하나뿐이라 아무도 보지 않았습니다.

그래서 필요한 것은 기능을 더 붙이는 게 아니라 **지켜보는 장치**입니다.

---

## 구성 요소

### 1. 워치독 — `ops/hermes-watchdog.py`

2분마다 게이트웨이가 실제로 일할 수 있는 상태인지 점검하고, 아니면 재시작한 뒤 Slack 으로 보고합니다.

점검 항목:

- 게이트웨이 프로세스 존재
- Slack 재연결 루프에 빠졌는지 (로그의 연결 실패 패턴을 최근 5분 구간에서 셈)
- webhook 포트 응답

`KeepAlive` 로는 두 번째 항목을 절대 잡을 수 없습니다. 프로세스가 살아 있기 때문입니다.

폭주 방지가 들어 있습니다. 15분 쿨다운을 두고, 2시간 안에 3회를 넘기면 재시작을 멈추고
사람을 부릅니다. 고장난 것을 무한히 재시작하는 것보다 조용히 멈추고 알리는 편이 낫습니다.

### 2. 워크트리 회수 — `ops/hermes-worktree-reaper.py`

완료된 PR 리뷰의 작업방을 회수합니다. 매일 한 번 돌면 충분합니다.

일반적인 정리 스크립트는 `node_modules` 같은 재생성 가능한 산출물만 지우고 워크트리 자체는
남깁니다. 게다가 `git worktree list` 에 등록된 것은 보호 대상이라 건드리지 않습니다.
그 결과 완료된 작업의 워크트리가 영구히 쌓입니다.

이 스크립트는 다섯 조건을 **모두** 만족할 때만 지웁니다.

1. 칸반 태스크가 `done`
2. **해당 PR 이 GitHub 에서 병합됨** — 병합됐으면 재리뷰가 없으므로 보관할 이유가 없다
3. 완료 후 유예 기간 경과 (기본 2일)
4. 허용된 워크트리 루트 아래에 있음 (경로 탈출 방지)
5. 추적 파일에 수정이 없음 — 미추적 리뷰 초안이나 락파일은 허용

지우기 전에 에이전트가 남긴 리뷰 초안은 아카이브로 옮깁니다. 초안의 내용은 이미 GitHub
코멘트로 게시되어 있어 사본이지만, 감사 추적용으로 남겨 둡니다.

### 3. PR 스캐너 — `ops/hermes-pr-scanner.py`

조직의 열린 PR 을 주기적으로 훑어 칸반 리뷰 태스크를 만듭니다.

webhook 으로 받은 이벤트를 에이전트가 해석해 태스크를 만드는 경로도 가능하지만,
**태스크 생성은 판단이 필요한 일이 아닙니다.** 결정적으로 처리하는 편이 안정적이고,
webhook 세션이 도구를 받지 못하는 환경에서도 동작합니다.

레포마다 webhook 을 걸 필요가 없다는 장점도 있습니다. 조직 전체를 훑으므로
**새로 만든 레포도 자동으로 포함**됩니다. org webhook 권한도 필요 없습니다.

동작 방식:

- 기준 시각(watermark)을 처음 실행할 때 기록하고, **그 이후에 새로 열린 PR만** 대상으로 삼습니다.
  이미 열려 있던 PR 을 소급해서 무더기로 리뷰하는 사고를 막습니다.
- 초안(draft) PR 은 건너뜁니다.
- 중복 방지 키는 `<레포>-pr-review:<번호>:<head sha>` 입니다. 새 커밋이 올라오면 sha 가 바뀌어
  다시 리뷰합니다.
- 리뷰용 원본 클론을 레포마다 하나 준비해 두고, 작업마다 거기서 워크트리를 뜹니다.

리뷰 자체는 칸반 워커가 합니다. 워커는 워크트리에서 빌드와 테스트를 실제로 실행한 뒤
GitHub 코멘트를 남기고, 칸반 알림 구독을 통해 Slack 으로 결과가 전달됩니다.

---

## 설치

세 스크립트는 표준 라이브러리만 씁니다. 별도 의존성이 없습니다.

```bash
# 1) 스크립트 배치
mkdir -p ~/hermes-ops/{logs,reviews,review-drafts}
cp ops/*.py ~/hermes-ops/
chmod +x ~/hermes-ops/*.py

# 2) GitHub 토큰 (classic PAT 권장)
#    필요한 스코프: repo, workflow, read:org
#    org webhook 까지 쓸 거라면 admin:org_hook 추가
printf '%s' 'ghp_...' > ~/.hermes/gh_token
chmod 600 ~/.hermes/gh_token
```

### 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `HERMES_HOME` | `~/.hermes` | Hermes 데이터 홈 |
| `HERMES_BIN` | `hermes` | Hermes CLI 실행 파일 |
| `HERMES_OPS_LOG_DIR` | `~/hermes-ops/logs` | 운영 스크립트 로그 |
| `HERMES_REVIEW_ROOT` | `~/hermes-ops/reviews` | 리뷰용 원본 클론 위치 |
| `HERMES_DRAFT_ARCHIVE` | `~/hermes-ops/review-drafts` | 리뷰 초안 보관 |
| `GITHUB_ORG` | (없음) | 대상 GitHub 조직. 스캐너에 필수 |
| `ALERT_CHANNEL` | (없음) | 알림 Slack 채널 ID. 비우면 알림 없음 |
| `HERMES_PR_DENYLIST` | (없음) | 리뷰에서 뺄 레포. 쉼표 구분 |
| `GH_TOKEN` / `GH_TOKEN_FILE` | `~/.hermes/gh_token` | GitHub 인증 |
| `GH_BIN` | `/opt/homebrew/bin/gh` | gh CLI 경로 |
| `HERMES_GATEWAY_LABEL` | `ai.hermes.gateway` | 게이트웨이 launchd 레이블 |

### launchd 등록

`ops/launchagents/` 의 템플릿에서 `__HOME__`, `__ORG__`, `__CHANNEL__` 을 채워
`~/Library/LaunchAgents/` 에 두고 등록합니다.

```bash
for f in hermes-watchdog hermes-worktree-reaper hermes-pr-scanner; do
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.wigtn.$f.plist
done
```

주기는 워치독 2분, 스캐너 3분, 회수 매일 04:30 입니다.

> **주의** — 스캐너를 처음 등록하면 첫 실행에서 기준 시각만 기록하고 아무 태스크도 만들지
> 않습니다. 이미 열려 있던 PR 을 건드리지 않기 위한 안전장치입니다. 의도적으로 기존 PR 까지
> 리뷰하고 싶다면 상태 파일의 `watermark` 를 과거로 바꾸면 됩니다.

---

## 운영 중 확인법

```bash
# 무엇이 등록돼 있나
launchctl list | grep hermes

# 스캐너가 돌고 있나 (3분 간격으로 한 줄씩 쌓임)
tail -5 ~/hermes-ops/logs/hermes-pr-scanner.log

# 워치독이 사고를 잡았나 (로그가 비어 있으면 사고가 없었다는 뜻)
tail -20 ~/hermes-ops/logs/hermes-watchdog.log

# 게이트웨이가 실제로 연결돼 있나 (상태 파일은 믿지 말 것)
lsof -nP -p "$(pgrep -f 'hermes_cli.main gateway' | head -1)" | grep ESTABLISHED
```

마지막 줄이 중요합니다. `gateway_state.json` 은 부팅 시점 값을 유지하는 경우가 있어
살아 있는지 판단하는 근거로 쓰면 안 됩니다. 실제 소켓을 봐야 합니다.

### 권장 알림 규칙

Prometheus 를 쓴다면 최소한 이 정도는 필요합니다. 임계값을 낮게 잡아 상시 발동하는 규칙은
채널을 오염시켜 **모든 알림을 무의미하게 만듭니다.** 실제로 CPU 5% 초과 규칙 하나가 18일간
계속 발동해 아무도 알림을 보지 않는 상태가 됐습니다.

| 규칙 | 조건 | 목적 |
|---|---|---|
| 디스크 여유 부족 | 15% 미만 10분 | 워크트리 누수 조기 경보 |
| 디스크 위험 | 7% 미만 5분 | 컨테이너 기동 실패 직전 |
| CPU 포화 | 85% 초과 15분 | 워커 과다 실행 |
| 수집 대상 다운 | `up == 0` 5분 | 관측 자체가 죽는 것을 감시 |

Slack 알림은 Incoming Webhook 대신 봇 토큰으로 `chat.postMessage` 를 호출할 수 있습니다.
Alertmanager 설정 예시는 `ops/alertmanager.example.yml` 을 참고하세요.

---

## 설계 메모

**하나의 인스턴스로 유지할 것.** Slack 만 컨테이너로 분리했다가 자동화 자산(칸반, 워크트리,
webhook)은 호스트에 남아 두 벌로 갈라진 적이 있습니다. 같은 버전인데 스킬이 10개 차이 나고,
한쪽에만 기억과 cron 이 쌓였습니다. 설정 파일만 봐서는 어느 쪽이 진짜인지 구분이 안 됩니다.

**동시 실행 상한을 명시할 것.** `kanban.max_spawn` 이 비어 있으면 상한이 없습니다.
PR 이 몰리면 워커가 무제한으로 뜹니다. 코어 수와 메모리를 보고 값을 정해 두세요.

```yaml
kanban:
  dispatch_in_gateway: true
  dispatch_interval_seconds: 60
  failure_limit: 2
  max_spawn: 6
  max_in_progress: 6
```

**GitHub 토큰은 환경변수로 고정할 것.** macOS 의 gh 는 토큰을 키체인으로 옮기면서 설정 파일에서
지우는 경우가 있습니다. 게이트웨이 재시작 시점에 인증이 조용히 깨집니다. classic PAT 를 발급해
launchd 환경변수(`GH_TOKEN`)로 주입하면 gh 가 건드리지 못합니다.

**자동 리뷰는 전용 계정으로.** 개인 계정 토큰을 쓰면 리뷰 코멘트가 그 사람 이름으로 올라갑니다.
팀원 입장에서는 사람이 쓴 리뷰로 보여서, 자동화가 돌고 있다는 사실 자체를 아무도 모르게 됩니다.

**워크트리 정리를 파이프라인에 넣을 것.** 태스크 생명주기가 `created → claimed → spawned →
completed` 로 끝나면 작업방은 그대로 남습니다. 정리 단계가 없으면 하루 수십 건 규모에서
한 달이면 디스크가 찹니다.

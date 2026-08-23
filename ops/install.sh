#!/usr/bin/env bash
# 운영 스크립트 세 개를 launchd 에 등록한다.
#
# 사용법:
#   GITHUB_ORG=myorg ALERT_CHANNEL=C0123456789 ./ops/install.sh
#
# 되돌리기:
#   ./ops/install.sh --uninstall
set -euo pipefail

OPS_DIR="${HERMES_OPS_DIR:-$HOME/hermes-ops}"
LA_DIR="$HOME/Library/LaunchAgents"
PREFIX="${LAUNCHD_PREFIX:-com.local}"
JOBS=(hermes-watchdog hermes-pr-scanner hermes-worktree-reaper hermes-worktree-gc hermes-metrics hermes-webhook-receiver hermes-pr-notifier)

if [ "${1:-}" = "--uninstall" ]; then
  for j in "${JOBS[@]}"; do
    launchctl bootout "gui/$(id -u)/$PREFIX.$j" 2>/dev/null || true
    rm -f "$LA_DIR/$PREFIX.$j.plist"
    echo "제거: $PREFIX.$j"
  done
  exit 0
fi

: "${GITHUB_ORG:?GITHUB_ORG 를 지정하세요 (예: GITHUB_ORG=myorg)}"
ALERT_CHANNEL="${ALERT_CHANNEL:-}"
DENYLIST="${HERMES_PR_DENYLIST:-}"
HERMES_BIN="${HERMES_BIN:-$(command -v hermes || echo hermes)}"
GH_BIN="${GH_BIN:-$(command -v gh || echo /opt/homebrew/bin/gh)}"

TOKEN_FILE="${GH_TOKEN_FILE:-${HERMES_HOME:-$HOME/.hermes}/gh_token}"
if [ ! -s "$TOKEN_FILE" ] && [ -z "${GH_TOKEN:-}" ]; then
  echo "경고: GitHub 토큰이 없습니다."
  echo "  $TOKEN_FILE 에 classic PAT 를 넣으세요 (스코프: repo, workflow, read:org)"
  echo "    printf '%s' 'ghp_...' > $TOKEN_FILE && chmod 600 $TOKEN_FILE"
  echo "  토큰 없이도 등록은 되지만 스캐너는 아무 PR 도 조회하지 못합니다."
  echo
fi

mkdir -p "$OPS_DIR"/{logs,reviews,review-drafts} "$LA_DIR"

for f in hermes-watchdog.py hermes-pr-scanner.py hermes-worktree-reaper.py \
         hermes-worktree-gc.sh hermes-metrics.py hermes-webhook-receiver.py \
         hermes-pr-notifier.py apply-local-patches.py hermes-review-audit.py; do
  install -m 0755 "$(dirname "$0")/$f" "$OPS_DIR/$f"
done
echo "스크립트 배치: $OPS_DIR"

# 실행 방법. 대부분 파이썬이지만 gc 는 셸 스크립트다.
# 여기를 분기하지 않으면 python3 hermes-worktree-gc.py 를 부르게 된다.
program_for() {
  case "$1" in
    hermes-worktree-gc)
      echo "    <string>/bin/bash</string>"
      echo "    <string>$OPS_DIR/$1.sh</string>" ;;
    *)
      echo "    <string>/usr/bin/python3</string>"
      echo "    <string>$OPS_DIR/$1.py</string>" ;;
  esac
}

# 주기: 워치독 2분, 스캐너 3분, 회수 매일 04:30, 산출물 정리 매일 04:00
schedule_for() {
  case "$1" in
    hermes-watchdog)        echo "<key>StartInterval</key><integer>120</integer>" ;;
    hermes-pr-scanner)      echo "<key>StartInterval</key><integer>180</integer>" ;;
    hermes-worktree-reaper) echo "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>30</integer></dict>" ;;
    hermes-worktree-gc)     echo "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>" ;;
    hermes-metrics|hermes-webhook-receiver|hermes-pr-notifier)
      # 주기 실행이 아니라 상주한다. 죽으면 launchd 가 되살린다.
      echo "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>" ;;
  esac
}

for j in "${JOBS[@]}"; do
  cat > "$LA_DIR/$PREFIX.$j.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PREFIX.$j</string>
  <key>ProgramArguments</key>
  <array>
$(program_for "$j")
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HERMES_HOME</key><string>${HERMES_HOME:-$HOME/.hermes}</string>
    <key>HERMES_BIN</key><string>$HERMES_BIN</string>
    <key>GH_BIN</key><string>$GH_BIN</string>
    <key>HERMES_OPS_LOG_DIR</key><string>$OPS_DIR/logs</string>
    <key>HERMES_REVIEW_ROOT</key><string>$OPS_DIR/reviews</string>
    <key>HERMES_DRAFT_ARCHIVE</key><string>$OPS_DIR/review-drafts</string>
    <key>GITHUB_ORG</key><string>$GITHUB_ORG</string>
    <key>ALERT_CHANNEL</key><string>$ALERT_CHANNEL</string>
    <key>HERMES_PR_DENYLIST</key><string>$DENYLIST</string>
    <key>GH_TOKEN_FILE</key><string>$TOKEN_FILE</string>
    <key>HERMES_LIGHT_MODEL</key><string>${HERMES_LIGHT_MODEL:-}</string>
    <key>HERMES_LIGHT_MAX_CHANGES</key><string>${HERMES_LIGHT_MAX_CHANGES:-200}</string>
    <key>WEBHOOK_SECRET_FILE</key><string>${WEBHOOK_SECRET_FILE:-${HERMES_HOME:-$HOME/.hermes}/webhook_secret}</string>
    <key>NOTIFY_INTERVAL</key><string>${NOTIFY_INTERVAL:-20}</string>
  </dict>
  $(schedule_for "$j")
  <key>StandardOutPath</key><string>$OPS_DIR/logs/$j.out.log</string>
  <key>StandardErrorPath</key><string>$OPS_DIR/logs/$j.err.log</string>
</dict>
</plist>
PLIST
  plutil -lint "$LA_DIR/$PREFIX.$j.plist" >/dev/null
  launchctl bootout "gui/$(id -u)/$PREFIX.$j" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LA_DIR/$PREFIX.$j.plist"
  echo "등록: $PREFIX.$j"
done

echo
echo "등록된 작업:"
launchctl list | grep -E "$(IFS='|'; echo "${JOBS[*]}")" || true
echo
echo "알림은 처음에 한 번 --seed 로 돌려 과거 리뷰가 다시 날아가지 않게 하세요:"
echo "  $OPS_DIR/hermes-pr-notifier.py --seed"
echo
echo "스캐너는 첫 실행에서 기준 시각만 기록하고 태스크를 만들지 않습니다."
echo "이미 열려 있던 PR 은 대상에서 제외됩니다."

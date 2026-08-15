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
JOBS=(hermes-watchdog hermes-pr-scanner hermes-worktree-reaper)

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

mkdir -p "$OPS_DIR"/{logs,reviews,review-drafts} "$LA_DIR"

for f in hermes-watchdog.py hermes-pr-scanner.py hermes-worktree-reaper.py; do
  install -m 0755 "$(dirname "$0")/$f" "$OPS_DIR/$f"
done
echo "스크립트 배치: $OPS_DIR"

# 주기: 워치독 2분, 스캐너 3분, 회수 매일 04:30
schedule_for() {
  case "$1" in
    hermes-watchdog)        echo "<key>StartInterval</key><integer>120</integer>" ;;
    hermes-pr-scanner)      echo "<key>StartInterval</key><integer>180</integer>" ;;
    hermes-worktree-reaper) echo "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>30</integer></dict>" ;;
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
    <string>/usr/bin/python3</string>
    <string>$OPS_DIR/$j.py</string>
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
echo "스캐너는 첫 실행에서 기준 시각만 기록하고 태스크를 만들지 않습니다."
echo "이미 열려 있던 PR 은 대상에서 제외됩니다."

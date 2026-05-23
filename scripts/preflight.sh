#!/usr/bin/env bash
# preflight.sh — 설치 전 필수 의존성 점검
set -euo pipefail

ok()   { echo "  [OK]   $1"; }
warn() { echo "  [WARN] $1"; }
fail() { echo "  [FAIL] $1"; FAILED=1; }

FAILED=0
echo ""
echo "사전 점검 — 필수 의존성"
echo "─────────────────────────────────────────"

# Python 3.12+ : Ouroboros 요구사항
# python3 가 3.12 미만이어도 python3.12 가 있으면 OK 처리 (brew python@3.12 일반 케이스)
check_python_version() {
  local bin="$1"
  command -v "$bin" >/dev/null 2>&1 || return 1
  local ver maj min
  ver=$("$bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || return 1
  maj=${ver%.*}; min=${ver#*.}
  [ "$maj" -ge 3 ] && [ "$min" -ge 12 ] && echo "$ver"
}
PY_OK=""
for candidate in python3 python3.12 python3.13; do
  if v=$(check_python_version "$candidate"); then
    PY_OK="$candidate $v"
    break
  fi
done
if [ -n "$PY_OK" ]; then
  ok "python $PY_OK (Ouroboros 는 3.12+ 요구)"
else
  if command -v python3 >/dev/null 2>&1; then
    CUR=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo unknown)
    fail "python3 $CUR — 3.12 이상 필요. 'brew install python@3.12' 또는 pyenv 사용."
  else
    fail "python3 미설치 — 'brew install python@3.12'"
  fi
fi

# Node.js : Codex CLI / Hermes 일부 의존
if command -v node >/dev/null 2>&1; then
  ok "node $(node --version)"
else
  warn "node 미설치 — Codex CLI npm 설치에 필요. nvm 권장."
fi

# git : Ouroboros 가 lineage 추적에 사용
command -v git >/dev/null 2>&1 && ok "git $(git --version | awk '{print $3}')" \
  || fail "git 미설치"

# pipx : 격리 설치 권장
command -v pipx >/dev/null 2>&1 && ok "pipx 사용 가능" \
  || warn "pipx 미설치 — pip --break-system-packages 로 대체 가능하나 pipx 권장"

# 디스크 여유 (대략 2GB 권장)
AVAIL=$(df -Pk "$HOME" | awk 'NR==2{print int($4/1024/1024)}')
if [ "${AVAIL:-0}" -ge 2 ]; then
  ok "디스크 여유 ${AVAIL}GB"
else
  warn "디스크 여유 ${AVAIL}GB — 2GB 이상 권장"
fi

echo "─────────────────────────────────────────"
if [ "$FAILED" -eq 1 ]; then
  echo "사전 점검 실패. 위 [FAIL] 항목을 해결한 뒤 다시 실행하세요."
  exit 1
fi
echo "사전 점검 통과."
echo ""

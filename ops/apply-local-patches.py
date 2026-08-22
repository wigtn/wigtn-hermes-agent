#!/usr/bin/env python3
"""
설치된 Hermes 패키지에 우리 로컬 패치를 다시 넣는다.

왜 필요한가.
`hermes update` 나 `pipx upgrade hermes-agent` 를 돌리면 pip 이 site-packages 를
새 버전으로 덮어쓴다. 그때 아래 패치가 조용히 사라진다. 사라진 것을 아무도
모르는 것이 진짜 문제다. 다음에 같은 장애가 나면 로그가 비어 있고 원인 추적을
처음부터 다시 해야 한다.

그래서 업그레이드 직후 이 스크립트를 돌린다. 여러 번 돌려도 안전하다.

무엇을 넣는가.

1. gateway/run.py — 사용자에게 정형 문구가 나가기 전에 원본 오류를 로그로 남긴다.
   Slack 등 채팅 화면에는 "Provider authentication failed" 같은 정형 문구만 가고
   원본은 어디에도 남지 않는다. 표시된 원인과 실제 원인이 달라도 확인할 방법이 없다.

2. gateway/platforms/base.py — 메시지가 게이트웨이에 닿는 순간을 남기고,
   활성 세션 가드에 걸리는 경우도 남긴다. 이것이 없어서 2026-08-16 22:57 사례에서
   응답은 나갔는데 로그가 한 줄도 없는 상황이 생겼다.

사용법:
  apply-local-patches.py [--site-packages <경로>] [--check] [--revert]

  --check   적용 여부만 확인하고 끝낸다 (종료코드 0=적용됨, 1=미적용)
  --revert  백업에서 원본을 되돌린다
"""

import argparse
import ast
import io
import os
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
BACKUP_DIR = os.environ.get(
    "HERMES_PATCH_BACKUP", os.path.join(HOME, "hermes-ops", "patch-backups"))


def find_site_packages():
    """hermes 실행 파일에서 site-packages 를 역추적한다."""
    env = os.environ.get("HERMES_SITE_PACKAGES")
    if env:
        return env
    # 실제로 돌고 있는 게이트웨이의 site-packages 를 먼저 본다.
    # shutil.which("hermes") 는 PATH 에 잡히는 아무 설치나 고르는데,
    # 이 호스트에는 hermes 가 둘 깔려 있어서 launchd 가 안 쓰는 쪽을
    # 검사하고 "적용됨" 이라고 답하는 사고가 실제로 있었다.
    try:
        import json as _json
        with io.open(os.path.join(HOME, ".hermes", "gateway.pid"),
                     encoding="utf-8") as _f:
            _argv = (_json.load(_f).get("argv") or [""])[0]
        _marker = os.sep + "site-packages" + os.sep
        if _marker in _argv:
            return _argv.split(_marker)[0] + os.sep + "site-packages"
    except Exception:
        pass
    hermes = shutil.which(os.environ.get("HERMES_BIN", "hermes"))
    if not hermes:
        return None
    # <venv>/bin/hermes -> <venv>/lib/python3.x/site-packages
    venv = os.path.dirname(os.path.dirname(os.path.realpath(hermes)))
    lib = os.path.join(venv, "lib")
    if not os.path.isdir(lib):
        return None
    for d in sorted(os.listdir(lib)):
        cand = os.path.join(lib, d, "site-packages")
        if os.path.isdir(cand):
            return cand
    return None


# ── 패치 정의 ──────────────────────────────────────────────────────────
# marker: 이미 적용됐는지 판단하는 문자열
# old/new: 치환 쌍

PATCHES = [
    {
        "file": "gateway/run.py",
        "marker": "user saw mapped text",
        "why": "정형 문구로 바뀌기 전 원본 오류를 남긴다",
        "old": '''def _gateway_provider_error_reply(text: str) -> str:
    """Map raw provider/API errors to a short user-safe Telegram reply."""
    if _GATEWAY_AUTH_ERROR_RE.search(text):''',
        "new": '''def _gateway_provider_error_reply(text: str) -> str:
    """Map raw provider/API errors to a short user-safe Telegram reply."""
    # 사용자에게는 정형 문구만 나가므로, 원문은 여기서 남긴다.
    # 남기지 않으면 표시된 원인과 실제 원인이 달라도 확인할 방법이 없다.
    try:
        logger.warning(
            "gateway provider error (user saw mapped text) raw=%r",
            (text or "")[:2000],
        )
    except Exception:
        pass
    if _GATEWAY_AUTH_ERROR_RE.search(text):''',
    },
    {
        "file": "gateway/platforms/base.py",
        "marker": "platform inbound:",
        "why": "메시지가 게이트웨이에 닿는 순간을 남긴다",
        "old": '''        if not self._message_handler:
            return

        coerce_plaintext_gateway_command(event)''',
        "new": '''        if not self._message_handler:
            return

        # 진입 로그. 여기부터 남겨야 어디서 걸러지든 흔적이 남는다.
        try:
            _src = getattr(event, "source", None)
            logger.info(
                "platform inbound: platform=%s chat=%s user=%s thread=%s text=%r",
                getattr(_src, "platform", "?"),
                getattr(_src, "chat_id", "?"),
                getattr(_src, "user_name", "?"),
                getattr(_src, "thread_id", None),
                (getattr(event, "text", "") or "")[:100],
            )
        except Exception:
            pass

        coerce_plaintext_gateway_command(event)''',
    },
    {
        "file": "gateway/platforms/base.py",
        "marker": "active-session guard:",
        "why": "이전 세션이 안 풀려 막히는 경우를 남긴다",
        "old": '''        # Check if there's already an active handler for this session
        if session_key in self._active_sessions:''',
        "new": '''        # Check if there's already an active handler for this session
        if session_key in self._active_sessions:
            try:
                logger.info("active-session guard: session=%s (이미 처리 중)", session_key)
            except Exception:
                pass''',
    },
    {
        "file": "gateway/run.py",
        "marker": "401 은 HTTP 상태 문맥일 때만",
        "why": "401 오탐으로 모든 실패가 인증 실패로 표시되는 것을 막는다",
        "old": r"""_GATEWAY_AUTH_ERROR_RE = re.compile(
    r"(provider\s+authentication\s+failed|incorrect\s+api\s+key|invalid\s+api\s+key|\b401\b)",
    re.IGNORECASE,
)""",
        "new": r"""_GATEWAY_AUTH_ERROR_RE = re.compile(
    # 원래 `\b401\b` 였다. 그러면 스레드풀을 거친 모든 실패의 트레이스백에
    # 반드시 들어가는 `concurrent/futures/_base.py", line 401` 이 걸려서,
    # 타임아웃도 500 도 503 도 전부 "인증 실패" 로 표시됐다.
    # 401 은 HTTP 상태 문맥일 때만 인증 실패로 본다.
    r"(provider\s+authentication\s+failed|incorrect\s+api\s+key|invalid\s+api\s+key"
    r"|(?:code|status|statuscode|http)\s*[:=]?\s*401\b)",
    re.IGNORECASE,
)""",
    },
]


def backup(path):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = os.path.basename(path) + ".orig-" + time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, name)
    shutil.copy2(path, dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-packages")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    sp = args.site_packages or find_site_packages()
    if not sp or not os.path.isdir(sp):
        print("site-packages 를 찾지 못했습니다. --site-packages 로 지정하세요.")
        return 2
    print("대상: %s" % sp)

    if args.revert:
        if not os.path.isdir(BACKUP_DIR):
            print("백업 디렉터리가 없습니다: %s" % BACKUP_DIR)
            return 2
        files = sorted(os.listdir(BACKUP_DIR))
        if not files:
            print("되돌릴 백업이 없습니다.")
            return 2
        print("가장 최근 백업으로 되돌리려면 수동으로 복사하세요:")
        for f in files[-4:]:
            print("  %s" % os.path.join(BACKUP_DIR, f))
        return 0

    missing = []
    applied = []
    for pt in PATCHES:
        path = os.path.join(sp, pt["file"])
        if not os.path.exists(path):
            print("  건너뜀 (파일 없음): %s" % pt["file"])
            continue
        s = io.open(path, encoding="utf-8").read()
        if pt["marker"] in s:
            applied.append(pt["marker"])
            continue
        missing.append((pt, path, s))

    if args.check:
        print("적용됨 %d개, 미적용 %d개" % (len(applied), len(missing)))
        for pt, _, _ in missing:
            print("  미적용: %s (%s)" % (pt["marker"], pt["why"]))
        return 0 if not missing else 1

    if not missing:
        print("모두 적용된 상태입니다. 할 일 없음.")
        return 0

    touched = set()
    for pt, path, s in missing:
        if pt["old"] not in s:
            print("  실패: %s — 앵커를 찾지 못했습니다." % pt["marker"])
            print("        Hermes 버전이 바뀌어 코드가 달라졌을 수 있습니다. 수동 확인이 필요합니다.")
            return 1
        if path not in touched:
            b = backup(path)
            print("  백업: %s" % b)
            touched.add(path)
        s = s.replace(pt["old"], pt["new"], 1)
        io.open(path, "w", encoding="utf-8").write(s)
        ast.parse(io.open(path, encoding="utf-8").read())
        print("  적용: %s — %s" % (pt["marker"], pt["why"]))

    print()
    print("적용 완료. 게이트웨이를 재시작해야 반영됩니다.")
    print("  launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway")
    return 0


if __name__ == "__main__":
    sys.exit(main())

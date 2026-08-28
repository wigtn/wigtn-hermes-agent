#!/usr/bin/env python3
"""손상된 칸반 보드를 검증된 복구본으로 되돌린다. 멱등.

워치독이 손상을 감지하면 복구본을 만들어 두고 이 명령을 알린다.
넣는 것은 사람이 한다. `.recover` 는 손상된 페이지 안의 행을 살리지 못해
데이터가 줄 수 있다. 2026-08-27 손상에서 태스크 1건이 사라졌다.

본체만 갈아끼우면 안 된다. 게이트웨이가 붙들고 있는 -wal / -shm 이 새 파일과
맞지 않아 즉시 손상으로 보인다. 셋을 한 묶음으로 다룬다.

사용법:
    hermes-kanban-restore.py <복구본.db>      되돌린다
    hermes-kanban-restore.py --check          지금 보드가 성한지만 본다
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))
DB = os.path.join(HERMES_HOME, "kanban.db")
# 손상본과 복구본을 두는 자리. 워치독과 같은 변수를 본다. 둘이 어긋나면
# 워치독이 안내한 경로를 이 도구가 찾지 못한다.
KEEP = os.environ.get("HERMES_RECOVER_DIR",
                      os.path.join(HOME, "hermes-ops", "kanban-recover"))
HERMES_BIN = os.environ.get("HERMES_BIN", "hermes")


def integrity(path):
    """quick_check 결과. 열 수조차 없으면 그 사유를 문자열로 준다."""
    # 실패의 종류를 가린다. 무조건 immutable 로 넘어가면 WAL 이 깨진 보드를
    # `ok` 로 본다. immutable 은 WAL 을 통째로 무시하기 때문이다.
    # 열지 못한 것(OperationalError)은 -wal 도 -shm 도 없을 때이고, 그때는
    # WAL 에 든 내용 자체가 없으므로 본체만 봐도 잃는 것이 없다.
    try:
        c = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=20)
        try:
            return c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.OperationalError as e:
        if "unable to open" not in str(e):
            return "열 수 없음: %s" % e
    except sqlite3.DatabaseError as e:
        return "열 수 없음: %s" % e
    # immutable 은 WAL 을 무시한다. `-wal` 이 있는데도 여기까지 왔다면(읽기
    # 전용 디렉터리 등) 본체만 본 결과를 정상이라고 할 수 없다. WAL 안의
    # 손상도, 그 안의 커밋도 보지 못한 채 `ok` 를 내게 된다.
    wal = path + "-wal"
    if os.path.exists(wal) and os.path.getsize(wal) > 0:
        return ("열 수 없음: -wal 이 있는데 읽기 전용으로 열지 못했습니다.")
    try:
        c = sqlite3.connect("file:%s?immutable=1" % path, uri=True, timeout=20)
        try:
            return c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error as e:
        return "열 수 없음: %s" % e


def counts(path):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=20)
    except sqlite3.Error:
        c = sqlite3.connect("file:%s?immutable=1" % path, uri=True, timeout=20)
    try:
        return (c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM task_events").fetchone()[0])
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", help="넣을 복구본 경로")
    ap.add_argument("--check", action="store_true",
                    help="보드가 성한지만 본다 (0=성함, 1=손상)")
    args = ap.parse_args()

    cur = integrity(DB)
    if args.check:
        print("현재 보드: %s" % cur)
        return 0 if cur == "ok" else 1

    if not args.source:
        ap.error("복구본 경로가 필요합니다 (또는 --check)")

    print("=== 1. 복구본 검증 ===")
    if not os.path.exists(args.source):
        sys.exit("복구본이 없습니다: %s" % args.source)
    r = integrity(args.source)
    print("  %s  ->  %s" % (os.path.basename(args.source), r))
    if r != "ok":
        sys.exit("복구본이 성하지 않습니다. 넣지 않습니다.")
    t, e = counts(args.source)
    print("  tasks=%d  task_events=%d" % (t, e))
    # 빈 보드는 성하다. 성한 것과 쓸 만한 것은 다르다.
    if t == 0:
        sys.exit("복구본에 태스크가 한 건도 없습니다. 넣지 않습니다.")

    print("=== 2. 현재 보드 ===")
    print("  %s" % cur)
    if cur == "ok":
        print("  이미 성합니다. 교체하지 않습니다.")
        return 0

    print("=== 3. 손상본 보존 ===")
    os.makedirs(KEEP, exist_ok=True)
    keep = os.path.join(KEEP, "kanban.db.corrupt-%s"
                        % time.strftime("%Y%m%d-%H%M%S"))
    # `-wal` 까지 보존한다. 본체만 두면 나중에 이 손상본에서 더 파내려 할 때
    # 아직 체크포인트되지 않았던 커밋이 이미 없다.
    # 여기서 실패하면 마지막 방어선이 없는 채로 교체하게 된다. 그러느니 멈춘다.
    try:
        shutil.copy2(DB, keep)
        if os.path.exists(DB + "-wal"):
            shutil.copy2(DB + "-wal", keep + "-wal")
    except OSError as e:
        sys.exit("손상본을 보존하지 못했습니다: %s\n"
                 "보존 없이는 교체하지 않습니다. 보드는 그대로입니다." % e)
    print("  %s" % keep)

    print("=== 4. 교체 (-wal / -shm 포함) ===")
    # 실패할 수 있는 일을 먼저 끝낸다. 여기까지는 되돌릴 수 있다 — 보드도
    # 사이드카도 손대지 않았다.
    tmp = DB + ".incoming"
    if os.path.exists(tmp) and not os.path.isfile(tmp):
        sys.exit("%s 가 파일이 아닙니다. 치우고 다시 실행하세요." % tmp)
    try:
        shutil.copy2(args.source, tmp)
        os.chmod(tmp, 0o644)
    except OSError as e:
        try:
            if os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        sys.exit("복구본을 준비하지 못했습니다: %s\n"
                 "보드와 -wal / -shm 은 그대로입니다. 손상본은 %s 에 있습니다."
                 % (e, keep))

    # 원본과 사이드카를 **지우지 않고 옆으로 치운다**. 검증까지 끝난 뒤에
    # 지운다. 지워 버리면 어느 단계에서 실패하든 되돌릴 것이 없고, "실패하면
    # 보드와 사이드카는 그대로" 라는 이 도구의 계약이 깨진다. 복구 도구가
    # 장애를 키우는 것은 고치려던 것보다 나쁘다.
    PREVIOUS = DB + ".previous"
    moved = []                      # [(치운 자리, 제자리)]

    def rollback():
        """치운 것을 전부 제자리로. 되돌리지 못한 것의 목록을 준다.

        되돌리기도 실패할 수 있다. 교체가 권한이나 파일시스템 문제로 실패했다면
        같은 자리로 되돌리는 것도 같은 이유로 실패한다. 그때 "되돌렸습니다" 라고
        말하면 안 된다. 사람은 그 말을 믿고 손을 뗀다. 무엇이 어디 남았는지
        알려야 손으로 살릴 수 있다.
        """
        left = []
        try:
            if os.path.isfile(DB):
                os.unlink(DB)
        except OSError:
            pass
        for away, p in moved:
            try:
                os.replace(away, p)
            except OSError:
                left.append((away, p))
        return left


    def bail(headline):
        """되돌린 결과를 정직하게 알리고 멈춘다."""
        left = rollback()
        try:
            if os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        if left:
            sys.exit("%s\n"
                     "**되돌리지 못한 것이 있습니다.** 손으로 옮겨야 합니다.\n%s\n"
                     "손상본은 %s 에 있습니다."
                     % (headline,
                        "\n".join("  %s  ->  %s" % (a, p) for a, p in left),
                        keep))
        sys.exit("%s\n보드와 -wal / -shm 을 되돌렸습니다. 손상본은 %s 에 있습니다."
                 % (headline, keep))

    try:
        for suffix in ("-wal", "-shm"):
            p = DB + suffix
            if os.path.exists(p):
                away = p + ".replacing"
                if os.path.exists(away):
                    os.unlink(away)          # 이전 실패의 잔재
                os.replace(p, away)
                moved.append((away, p))
                print("  치움 %s" % os.path.basename(p))
        if os.path.exists(PREVIOUS):
            os.unlink(PREVIOUS)
        os.replace(DB, PREVIOUS)             # 원본을 옆으로
        moved.append((PREVIOUS, DB))
        os.replace(tmp, DB)                  # 같은 파일시스템 -> 원자적
    except OSError as e:
        bail("교체하지 못했습니다: %s" % e)
    print("  교체 완료 (검증 전)")

    print("=== 5. 검증 ===")
    r = integrity(DB)
    print("  quick_check: %s" % r)
    if r != "ok":
        bail("교체 후 검증에 실패했습니다: %s" % r)
    try:
        t, e = counts(DB)
    except sqlite3.Error as exc:
        bail("교체 후 보드를 읽지 못했습니다: %s" % exc)
    print("  tasks=%d  task_events=%d" % (t, e))

    # 검증까지 끝났다. 이제 치워 둔 것을 지운다.
    for away, _ in moved:
        try:
            os.unlink(away)
        except OSError:
            pass

    print("=== 6. Hermes 가 여는지 ===")
    p = subprocess.run([HERMES_BIN, "kanban", "list", "--status", "running"],
                       capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, HERMES_HOME=HERMES_HOME))
    print("  exit=%d" % p.returncode)
    out = (p.stdout or p.stderr).strip()
    print("  " + ("\n  ".join(out.splitlines()[:5]) if out else "(running 없음)"))

    print("=== 7. Hermes 가 남긴 손상 백업 정리 ===")
    n = 0
    for f in sorted(os.listdir(HERMES_HOME)):
        if f.startswith("kanban.db") and ".corrupt." in f:
            os.unlink(os.path.join(HERMES_HOME, f))
            n += 1
    print("  %d 개 제거 (원본은 %s 에 보존)" % (n, KEEP))

    print()
    print("게이트웨이 디스패처는 파일이 바뀐 것을 보고 스스로 재개합니다.")
    print("확인: tail -f ~/.hermes/logs/gateway.log | grep dispatcher")
    return 0


if __name__ == "__main__":
    sys.exit(main())

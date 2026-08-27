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
    # mode=ro 로 열어 보고, 못 열면 immutable=1 로 본다. WAL 보드는 -shm 이
    # 없으면 읽기 전용으로 열리지 않는다. 그것은 손상이 아니라 아무도 보드를
    # 열고 있지 않다는 뜻이다. immutable 은 WAL 안의 미체크포인트 커밋을
    # 보지 못하지만, 여기서 보려는 것은 본체의 페이지 구조다.
    r = None
    for uri in ("file:%s?mode=ro" % path, "file:%s?immutable=1" % path):
        try:
            c = sqlite3.connect(uri, uri=True, timeout=20)
            try:
                return c.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                c.close()
        except sqlite3.Error as e:
            r = "열 수 없음: %s" % e
    return r


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
    shutil.copy2(DB, keep)
    if os.path.exists(DB + "-wal"):
        shutil.copy2(DB + "-wal", keep + "-wal")
    print("  %s" % keep)

    print("=== 4. 교체 (-wal / -shm 포함) ===")
    # 실패할 수 있는 일을 먼저 끝낸다. 여기까지는 되돌릴 수 있다 — 보드도
    # sidecar 도 손대지 않았다. 순서가 거꾸로면(sidecar 를 먼저 지우면)
    # 복사가 실패했을 때 손상된 보드는 그대로인데 WAL 만 사라진다. 거기
    # 있던 미체크포인트 태스크는 그 순간 복구 불가능해진다.
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

    # 여기서부터 되돌릴 수 없다. 옛 WAL 을 치우고 원자적으로 바꾼다.
    # 남겨 두면 새 본체를 옛 WAL 로 해석한다.
    for suffix in ("-wal", "-shm"):
        p = DB + suffix
        if os.path.exists(p):
            os.unlink(p)
            print("  제거 %s" % os.path.basename(p))
    os.replace(tmp, DB)          # 같은 파일시스템 -> 원자적 교체
    print("  교체 완료")

    print("=== 5. 검증 ===")
    r = integrity(DB)
    print("  quick_check: %s" % r)
    if r != "ok":
        sys.exit("교체 후에도 성하지 않습니다.")
    t, e = counts(DB)
    print("  tasks=%d  task_events=%d" % (t, e))

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

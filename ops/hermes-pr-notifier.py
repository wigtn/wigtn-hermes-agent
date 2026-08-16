#!/usr/bin/env python3
"""
끝난 리뷰를 Slack 으로 알린다.

원래 이 일은 PR 스캐너가 겸했다. 그런데 스캐너는 3분 주기라 알림도 3분까지
늦어졌다. 리뷰 시작은 웹훅으로 즉시인데 완료 알림만 폴링이면 앞뒤가 맞지 않는다.

그래서 알림만 떼어 짧은 주기로 돈다. 칸반 DB 만 읽고 모델을 부르지 않으므로
20초 주기로 돌려도 부담이 없다.

칸반 기본 알림을 쓰지 않는 이유는 형식이다. 기본 알림은 태스크 제목만 실어서
판정과 코멘트 URL 이 빠진다. 워커가 남긴 완료 요약의 첫 줄이 이미 원하는
형식이므로 그것을 그대로 보낸다.

사용법: hermes-pr-notifier.py [--interval 20] [--once] [--dry-run]
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))
KANBAN_DB = os.path.join(HERMES_HOME, "kanban.db")
STATE_PATH = os.path.join(HERMES_HOME, "pr-notifier-state.json")
LOG_DIR = os.environ.get("HERMES_OPS_LOG_DIR", os.path.join(HOME, "hermes-ops", "logs"))
LOG_PATH = os.path.join(LOG_DIR, "hermes-pr-notifier.log")
ALERT_CHANNEL = os.environ.get("ALERT_CHANNEL", "")
CREATED_BY = os.environ.get("HERMES_TASK_CREATOR", "pr-scanner")


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def slack_token():
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if tok:
        return tok
    try:
        with open(os.path.join(HERMES_HOME, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SLACK_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def slack_post(text):
    if not ALERT_CHANNEL:
        return False
    token = slack_token()
    if not token:
        return False
    data = urllib.parse.urlencode({"channel": ALERT_CHANNEL, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=data,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        log("  Slack 발송 예외: %s" % e)
        return False


def db_snapshot(path):
    # 파일 복사로는 WAL 에 있는 최근 커밋이 빠진다. backup() 은 일관된 상태를 뜬다.
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(path, timeout=15)
    try:
        dst = sqlite3.connect(tmp.name)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return tmp.name


def load_announced():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return set(json.load(f).get("announced", []))
    except (OSError, ValueError):
        return set()


def save_announced(ids):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"announced": sorted(ids)[-3000:]}, f)
    except OSError:
        pass


def tick(announced, dry_run):
    if not os.path.exists(KANBAN_DB):
        return announced, 0
    try:
        db = db_snapshot(KANBAN_DB)
    except sqlite3.Error as e:
        log("  칸반 스냅샷 실패, 다음 주기에 재시도: %s" % e)
        return announced, 0

    sent = 0
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, status, title FROM tasks "
            "WHERE created_by = ? AND status IN ('done', 'blocked')",
            (CREATED_BY,)).fetchall()
        for r in rows:
            if r["id"] in announced:
                continue
            ev = conn.execute(
                "SELECT payload FROM task_events WHERE task_id = ? "
                "AND kind IN ('completed', 'blocked', 'gave_up') "
                "ORDER BY rowid DESC LIMIT 1", (r["id"],)).fetchone()
            summary = ""
            if ev and ev["payload"]:
                try:
                    summary = (json.loads(ev["payload"]).get("summary") or "").strip()
                except (ValueError, AttributeError):
                    summary = ""
            first = summary.splitlines()[0] if summary else ""
            if not first:
                first = "%s %s" % ("✅" if r["status"] == "done" else "⏸", r["title"])
            if dry_run:
                log("  [dry-run] %s" % first[:110])
                continue
            # 보낸 뒤에만 기록한다. 실패하면 다음 주기에 다시 시도한다.
            if slack_post(first):
                announced.add(r["id"])
                sent += 1
                log("  알림: %s" % first[:110])
            else:
                log("  발송 실패, 재시도 예정: %s" % r["id"])
    finally:
        conn.close()
        os.unlink(db)
    return announced, sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int,
                    default=int(os.environ.get("NOTIFY_INTERVAL", "20")))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", action="store_true",
                    help="현재 끝난 태스크를 모두 발송 완료로 표시하고 종료")
    args = ap.parse_args()

    announced = load_announced()

    if args.seed:
        db = db_snapshot(KANBAN_DB)
        try:
            c = sqlite3.connect(db)
            ids = [r[0] for r in c.execute(
                "SELECT id FROM tasks WHERE created_by = ? "
                "AND status IN ('done','blocked')", (CREATED_BY,))]
            c.close()
        finally:
            os.unlink(db)
        announced |= set(ids)
        save_announced(announced)
        log("기존 완료 태스크 %d건을 발송 완료로 표시" % len(ids))
        return 0

    if args.once:
        announced, sent = tick(announced, args.dry_run)
        if not args.dry_run:
            save_announced(announced)
        log("1회 실행: 발송 %d건" % sent)
        return 0

    log("알림 감시 시작 (%d초 주기, 채널 %s)" % (args.interval, ALERT_CHANNEL or "미설정"))
    while True:
        try:
            announced, sent = tick(announced, args.dry_run)
            if sent:
                save_announced(announced)
        except Exception as e:
            log("오류: %s: %s" % (type(e).__name__, e))
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())

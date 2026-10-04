"""Tell the owner when data stops flowing.

Called at the end of every cron run (success or failure). Two ways to be "down":
  * ingest has not succeeded for DOWN_AFTER_MIN minutes (the source API is failing), or
  * ingest succeeds but no station has reported for STALE_AFTER_H hours (the source serves old data).
The owner gets one LINE message when it goes down, a reminder every REMIND_H hours while it stays
down, and one message when it recovers. Needs env ADMIN_LINE_ID (the owner's LINE user id; send the
bot "ไอดีของฉัน" to read it) - without it nothing is sent.
"""
import os
import sys
from datetime import timedelta

import core
import notify

DOWN_AFTER_MIN = 60
STALE_AFTER_H = 6
REMIND_H = 6
KEY = "ingest_alert"


def _iso(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def problem(conn, at):
    """A short Thai description of what is wrong, or None when everything is fine."""
    ok = conn.execute("SELECT MAX(ts) AS m FROM ingest_runs WHERE ok=1").fetchone()["m"]
    last = conn.execute("SELECT ok, error FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
    if last is None:
        return None
    if not last["ok"]:
        age = int((at - core.parse(ok)).total_seconds() // 60) if ok else None
        if age is None or age >= DOWN_AFTER_MIN:
            since = f"ล่าสุดสำเร็จเมื่อ {age} นาทีก่อน" if age is not None else "ยังไม่เคยดึงสำเร็จ"
            return f"ดึงข้อมูลจาก ThaiWater ไม่สำเร็จ ({since})\nข้อผิดพลาด: {(last['error'] or '')[:200]}"
        return None
    newest = conn.execute("SELECT MAX(ts) AS m FROM readings").fetchone()["m"]
    if newest and at - core.parse(newest) >= timedelta(hours=STALE_AFTER_H):
        hours = int((at - core.parse(newest)).total_seconds() // 3600)
        return f"ดึงข้อมูลได้ แต่ไม่มีสถานีไหนอัปเดตมา {hours} ชั่วโมงแล้ว (ต้นทางอาจส่งข้อมูลเก่า)"
    return None


def _state(conn):
    row = conn.execute("SELECT ts FROM alert_state WHERE name=?", (KEY,)).fetchone()
    return row["ts"] if row else None


def check(conn, at, send=None):
    """Send an alert / reminder / recovery message if due. Returns what was sent: 'down',
    'remind', 'recovered' or None. Never raises."""
    try:
        target = os.environ.get("ADMIN_LINE_ID", "").strip()
        if not target:
            return None
        send = send or notify.send_line
        bad, since = problem(conn, at), _state(conn)
        if bad and (since is None or at - core.parse(since) >= timedelta(hours=REMIND_H)):
            kind = "down" if since is None else "remind"
            send(target, f"⚠️ น้ำใกล้บ้านฉัน: ระบบมีปัญหา\n{bad}")
            conn.execute("DELETE FROM alert_state WHERE name=?", (KEY,))
            conn.execute("INSERT INTO alert_state(name, ts) VALUES(?,?)", (KEY, _iso(at)))
            conn.commit()
            return kind
        if not bad and since is not None:
            send(target, "✅ น้ำใกล้บ้านฉัน: ระบบกลับมาดึงข้อมูลได้ปกติแล้ว")
            conn.execute("DELETE FROM alert_state WHERE name=?", (KEY,))
            conn.commit()
            return "recovered"
    except Exception as e:  # monitoring must never break the ingest it watches
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"watchdog failed: {type(e).__name__}: {e}", file=sys.stderr)
    return None


# ---- database size -------------------------------------------------------------------------
# Neon's free plan stops accepting writes when the database is full, and nothing warns you first.
STORAGE_KEY = "storage_alert"
STORAGE_WARN = 0.8
STORAGE_REMIND_H = 24


def db_limit_mb():
    """Size of the plan in MB (env DB_LIMIT_MB, default 512 = Neon free)."""
    try:
        return max(1.0, float(os.environ.get("DB_LIMIT_MB", "512")))
    except ValueError:
        return 512.0


def db_size_mb(conn):
    if getattr(conn, "pg", False):
        row = conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()
        return row["n"] / 1048576
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    size = conn.execute("PRAGMA page_size").fetchone()[0]
    return pages * size / 1048576


def check_storage(conn, at, send=None, size_mb=None):
    """Warn the owner when the database passes STORAGE_WARN of its plan; repeat once a day while
    it stays above. Returns 'warn' when a message was sent, else None. Never raises."""
    try:
        target = os.environ.get("ADMIN_LINE_ID", "").strip()
        if not target:
            return None
        used, limit = (db_size_mb(conn) if size_mb is None else size_mb), db_limit_mb()
        since = conn.execute("SELECT ts FROM alert_state WHERE name=?", (STORAGE_KEY,)).fetchone()
        since = since["ts"] if since else None
        if used < limit * STORAGE_WARN:
            if since is not None:  # back under the line: re-arm
                conn.execute("DELETE FROM alert_state WHERE name=?", (STORAGE_KEY,))
                conn.commit()
            return None
        if since is not None and at - core.parse(since) < timedelta(hours=STORAGE_REMIND_H):
            return None
        send = send or notify.send_line
        send(target, f"⚠️ น้ำใกล้บ้านฉัน: ฐานข้อมูลใช้ไป {used:.0f} จาก {limit:.0f} MB ({used / limit * 100:.0f}%)\n"
                     "ถ้าเต็ม ระบบจะเขียนข้อมูลใหม่ไม่ได้ ให้เข้า Neon ตรวจพื้นที่ หรืออัปเกรดแพ็กเกจ")
        conn.execute("DELETE FROM alert_state WHERE name=?", (STORAGE_KEY,))
        conn.execute("INSERT INTO alert_state(name, ts) VALUES(?,?)", (STORAGE_KEY, _iso(at)))
        conn.commit()
        return "warn"
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"storage check failed: {type(e).__name__}: {e}", file=sys.stderr)
    return None

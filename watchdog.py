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


# ---- LINE message quota ---------------------------------------------------------------------
# The free LINE plan allows a fixed number of pushed messages a month; past it, pushes fail until the month turns.
QUOTA_KEY, QUOTA_CHECKED_KEY = "line_quota_alert", "line_quota_checked"
QUOTA_WARN = 0.8
QUOTA_CHECK_EVERY_H = 6
QUOTA_REMIND_H = 24


def check_line_quota(conn, at, send=None, fetch=None):
    """Ask LINE how much of this month's quota is used (at most every QUOTA_CHECK_EVERY_H hours) and warn the owner
    from QUOTA_WARN on, repeating daily. Returns 'warn' when a message was sent. Never raises."""
    try:
        target = os.environ.get("ADMIN_LINE_ID", "").strip()
        if not target:
            return None
        row = conn.execute("SELECT ts FROM alert_state WHERE name=?", (QUOTA_CHECKED_KEY,)).fetchone()
        if row and at - core.parse(row["ts"]) < timedelta(hours=QUOTA_CHECK_EVERY_H):
            return None
        conn.execute("DELETE FROM alert_state WHERE name=?", (QUOTA_CHECKED_KEY,))
        conn.execute("INSERT INTO alert_state(name, ts) VALUES(?,?)", (QUOTA_CHECKED_KEY, _iso(at)))
        conn.commit()
        limit, used = (fetch or notify.line_quota)()
        since = conn.execute("SELECT ts FROM alert_state WHERE name=?", (QUOTA_KEY,)).fetchone()
        since = since["ts"] if since else None
        if limit is None or used < limit * QUOTA_WARN:
            if since is not None:
                conn.execute("DELETE FROM alert_state WHERE name=?", (QUOTA_KEY,))
                conn.commit()
            return None
        if since is not None and at - core.parse(since) < timedelta(hours=QUOTA_REMIND_H):
            return None
        left = max(limit - used, 0)
        (send or notify.send_line)(target, f"⚠️ น้ำใกล้บ้านฉัน: โควตาข้อความ LINE เดือนนี้ใช้ไป {used} จาก {limit} ({used / limit * 100:.0f}%) "
                                           f"เหลือ {left}\nถ้าเต็ม ข้อความเตือนผู้ใช้จะส่งไม่ได้จนกว่าจะขึ้นเดือนใหม่ ลดความถี่สรุป (DIGEST_EVERY_H) หรืออัปเกรดแพ็กเกจ")
        conn.execute("DELETE FROM alert_state WHERE name=?", (QUOTA_KEY,))
        conn.execute("INSERT INTO alert_state(name, ts) VALUES(?,?)", (QUOTA_KEY, _iso(at)))
        conn.commit()
        return "warn"
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"line quota check failed: {type(e).__name__}: {e}", file=sys.stderr)
    return None


# ---- agencies / provinces that go quiet together -----------------------------------------------
# watchdog.check already notices when NOTHING reports. This catches one agency (or one province's stations from
# one agency) going silent while the rest keep reporting.
SILENT_H = 6
SILENT_MIN_STATIONS = 3          # a province needs this many stations from one agency to count as a group
AGENCY_MIN_STATIONS, AGENCY_SILENT_SHARE = 10, 0.25
SILENT_PREFIX = "silent:"
SILENT_LIST_MAX = 6


def silent_groups(conn, at):
    """Labels of groups that stopped reporting: every station of one agency in one province, or at least a quarter of an agency."""
    cut = core.iso(at - timedelta(hours=SILENT_H))
    rows = conn.execute("SELECT s.source, s.province, MAX(r.ts) AS ts FROM stations s "
                        "LEFT JOIN readings r ON r.station_id = s.id GROUP BY s.id, s.source, s.province").fetchall()
    by_agency, by_prov = {}, {}
    for r in rows:
        quiet = r["ts"] is None or r["ts"] < cut
        for table, key in ((by_agency, r["source"]), (by_prov, (r["source"], r["province"]))):
            if key and (key[0] if isinstance(key, tuple) else key):
                table.setdefault(key, []).append(quiet)
    out = []
    for src, v in by_agency.items():
        if len(v) >= AGENCY_MIN_STATIONS and sum(v) / len(v) >= AGENCY_SILENT_SHARE:
            out.append(f"หน่วยงาน {src} ({sum(v)} จาก {len(v)} สถานี)")
    for (src, prov), v in by_prov.items():
        if prov and len(v) >= SILENT_MIN_STATIONS and all(v):
            out.append(f"{src} จ.{prov} ({len(v)} สถานี)")
    return sorted(out)


def check_silent(conn, at, send=None):
    """Tell the owner when a group of stations goes quiet, and when it is back. One message per change, no repeats.
    Skipped while the whole feed is broken (check() already reports that). Returns 'silent', 'back' or None."""
    try:
        target = os.environ.get("ADMIN_LINE_ID", "").strip()
        if not target or problem(conn, at):
            return None
        now = set(silent_groups(conn, at))
        before = {r["name"][len(SILENT_PREFIX):] for r in conn.execute(
            "SELECT name FROM alert_state WHERE name LIKE ?", (SILENT_PREFIX + "%",)).fetchall()}
        new, back = sorted(now - before), sorted(before - now)
        send = send or notify.send_line
        kind = None
        if new:
            more = f"\n…และอีก {len(new) - SILENT_LIST_MAX} กลุ่ม" if len(new) > SILENT_LIST_MAX else ""
            send(target, f"⚠️ น้ำใกล้บ้านฉัน: สถานีกลุ่มนี้ไม่มีข้อมูลใหม่เกิน {SILENT_H} ชั่วโมง ขณะที่สถานีอื่นยังอัปเดตปกติ\n"
                         + "\n".join("• " + g for g in new[:SILENT_LIST_MAX]) + more)
            kind = "silent"
        if back:
            send(target, "✅ น้ำใกล้บ้านฉัน: กลับมามีข้อมูลแล้ว\n" + "\n".join("• " + g for g in back[:SILENT_LIST_MAX]))
            kind = kind or "back"
        for g in new:
            conn.execute("INSERT INTO alert_state(name, ts) VALUES(?,?)", (SILENT_PREFIX + g, _iso(at)))
        for g in back:
            conn.execute("DELETE FROM alert_state WHERE name=?", (SILENT_PREFIX + g,))
        conn.commit()
        return kind
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"silence check failed: {type(e).__name__}: {e}", file=sys.stderr)
    return None

"""Flood spots reported by people on the ground (web map and LINE). Unverified by nature, so:
  - every report expires after TTL_H hours,
  - one person (an IP / LINE user, stored only as a salted hash) is limited per hour and per place,
  - 3 different people flagging a report as untrue hides it,
  - reports near each other from different people count as "confirmed by N".
No photos, no names; only the point, the level, an optional short note and the time."""
import hashlib
import os
from datetime import timedelta

from core import km

LEVELS = {1: "เปียกแฉะ", 2: "ท่วมถึงข้อเท้า", 3: "ท่วมถึงเข่า", 4: "ท่วมเกินเอว"}
TTL_H = 12
HIDE_AFTER_FLAGS = 3
MAX_PER_HOUR = 5
DUP_KM, DUP_MIN = 0.1, 10      # the same person re-reporting the same spot within 10 min is ignored
CONFIRM_KM = 0.3
NOTE_MAX = 140
PENDING_MIN = 10               # LINE: how long the bot waits for the location after the level


class Rejected(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def who(raw):
    """Salted hash of an IP / LINE userId: lets us rate-limit without keeping the identity."""
    salt = os.environ.get("REPORT_SALT") or os.environ.get("LINE_CHANNEL_SECRET") or "nkb"
    return hashlib.sha256(f"{salt}|{raw}".encode()).hexdigest()[:16]


def in_thailand(lat, lng):
    return 5 <= lat <= 21 and 97 <= lng <= 106


def add(conn, lat, lng, level, note, src, person, at):
    if level not in LEVELS:
        raise Rejected(422, "ระดับน้ำไม่ถูกต้อง")
    if not in_thailand(lat, lng):
        raise Rejected(400, "ตำแหน่งนี้อยู่นอกประเทศไทย")
    note = " ".join((note or "").split())[:NOTE_MAX] or None
    hour_ago, dup_ago = _iso(at - timedelta(hours=1)), _iso(at - timedelta(minutes=DUP_MIN))
    mine = conn.execute("SELECT lat, lng, created_at FROM flood_reports WHERE who=? AND created_at>=?",
                        (person, hour_ago)).fetchall()
    if len(mine) >= MAX_PER_HOUR:
        raise Rejected(429, "ส่งรายงานบ่อยเกินไป ลองใหม่ภายหลัง")
    if any(r["created_at"] >= dup_ago and km(lat, lng, r["lat"], r["lng"]) <= DUP_KM for r in mine):
        raise Rejected(429, "คุณเพิ่งรายงานจุดนี้ไปแล้ว")
    conn.execute("INSERT INTO flood_reports(lat,lng,level,note,src,who,created_at,flags) VALUES(?,?,?,?,?,?,?,0)",
                 (round(lat, 5), round(lng, 5), level, note, src, person, _iso(at)))
    conn.commit()


def active(conn, at):
    """Reports still shown on the map, with how many different people reported nearby."""
    since = _iso(at - timedelta(hours=TTL_H))
    rows = [dict(r) for r in conn.execute(
        "SELECT id, lat, lng, level, note, src, who, created_at, flags FROM flood_reports "
        "WHERE created_at>=? AND flags<? ORDER BY created_at DESC LIMIT 500", (since, HIDE_AFTER_FLAGS)).fetchall()]
    out = []
    for r in rows:
        people = {o["who"] for o in rows if km(r["lat"], r["lng"], o["lat"], o["lng"]) <= CONFIRM_KM}
        age = max(0, int((at - _parse(r["created_at"])).total_seconds() // 60))
        out.append({"id": r["id"], "lat": r["lat"], "lng": r["lng"], "level": r["level"], "label": LEVELS[r["level"]],
                    "note": r["note"], "src": r["src"], "age_min": age, "confirmed": len(people)})
    return out


def _parse(s):
    from datetime import datetime, timezone
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def flag(conn, report_id, person):
    """Mark a report as untrue. One vote per person; returns False if the report does not exist."""
    if not conn.execute("SELECT 1 FROM flood_reports WHERE id=?", (report_id,)).fetchone():
        return False
    conn.execute("INSERT INTO flood_flags(report_id, who) VALUES(?,?) ON CONFLICT DO NOTHING", (report_id, person))
    conn.execute("UPDATE flood_reports SET flags=(SELECT COUNT(*) FROM flood_flags WHERE report_id=?) WHERE id=?",
                 (report_id, report_id))
    conn.commit()
    return True


def set_pending(conn, target, level, at):
    conn.execute("DELETE FROM line_pending WHERE target=?", (target,))
    conn.execute("INSERT INTO line_pending(target, level, ts) VALUES(?,?,?)", (target, level, _iso(at)))
    conn.commit()


def pop_pending(conn, target, at):
    """The level the user chose a moment ago, or None if they did not (or it is too old)."""
    row = conn.execute("SELECT level, ts FROM line_pending WHERE target=?", (target,)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM line_pending WHERE target=?", (target,))
    conn.commit()
    return row["level"] if row["ts"] >= _iso(at - timedelta(minutes=PENDING_MIN)) else None


def prune(conn, at, keep_h=48):
    cutoff = _iso(at - timedelta(hours=keep_h))
    conn.execute("DELETE FROM flood_flags WHERE report_id IN (SELECT id FROM flood_reports WHERE created_at<?)", (cutoff,))
    n = conn.execute("DELETE FROM flood_reports WHERE created_at<?", (cutoff,)).rowcount
    conn.execute("DELETE FROM line_pending WHERE ts<?", (cutoff,))
    conn.commit()
    return n

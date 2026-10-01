"""LINE bot webhook: users subscribe by sending their location to the official account.

  location message -> save/replace their subscription (userId + coordinates)
  "ยกเลิก" / "stop"  -> delete it
  "สถานะ"            -> current status of the station nearest their saved location
  anything else    -> short instructions

Requests are authenticated with LINE's X-Line-Signature (HMAC-SHA256 of the raw body with
the channel secret), so only LINE can register subscriptions. We store nothing but the
LINE userId and the coordinates the user chose to send.
"""
import base64
import hashlib
import hmac
import os

import core
from ingest import utc_now
from notify import message

MAX_SUBSCRIBERS = int(os.environ.get("MAX_SUBSCRIBERS", "500"))
CANCEL = {"ยกเลิก", "stop", "cancel", "unsubscribe"}
STATUS = {"สถานะ", "status"}
HELP = ("น้ำใกล้บ้านฉัน: ส่งตำแหน่งบ้านของคุณมาที่นี่ (กด + แล้วเลือก \"ตำแหน่งที่ตั้ง\") "
        "เพื่อรับแจ้งเตือนเมื่อสถานีวัดน้ำที่ใกล้ที่สุดเปลี่ยนสถานะ\n"
        "พิมพ์ \"สถานะ\" เพื่อดูค่าล่าสุด หรือ \"ยกเลิก\" เพื่อหยุดรับแจ้งเตือนและลบตำแหน่งที่เก็บไว้\n"
        "ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น ให้ยึดประกาศ ปภ. เป็นหลัก")


def valid_signature(secret, body, signature):
    if not secret or not signature:
        return False
    mac = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(mac, signature)


def _in_thailand(lat, lng):
    return 5 <= lat <= 21 and 97 <= lng <= 106


def _nearest(conn, lat, lng, at):
    near = core.nearest(core.latest(conn, at), lat, lng, 1, fresh_only=True)
    return near[0] if near else None


def handle_event(conn, ev, at, reply):
    """Process one LINE event. `reply(token, text)` sends the answer."""
    user = (ev.get("source") or {}).get("userId")
    if not user:
        return
    if ev.get("type") == "unfollow":  # user blocked us: forget them
        conn.execute("DELETE FROM subscriptions WHERE channel='line' AND target=?", (user,))
        conn.commit()
        return
    token = ev.get("replyToken")
    if not token:
        return
    if ev.get("type") == "follow":
        return reply(token, HELP)
    if ev.get("type") != "message":
        return
    m = ev.get("message") or {}
    if m.get("type") == "location":
        return _subscribe(conn, user, token, m, at, reply)
    text = (m.get("text") or "").strip().lower() if m.get("type") == "text" else ""
    if text in CANCEL:
        conn.execute("DELETE FROM subscriptions WHERE channel='line' AND target=?", (user,))
        conn.commit()
        return reply(token, "หยุดแจ้งเตือนและลบตำแหน่งของคุณแล้ว ส่งตำแหน่งมาใหม่ได้ทุกเมื่อ")
    if text in STATUS:
        sub = conn.execute("SELECT * FROM subscriptions WHERE channel='line' AND target=?", (user,)).fetchone()
        if not sub:
            return reply(token, "ยังไม่มีตำแหน่งของคุณ ส่งตำแหน่งบ้านมาก่อนได้เลย")
        st = _nearest(conn, sub["lat"], sub["lng"], at)
        return reply(token, message(sub, st) if st else "ตอนนี้ไม่มีสถานีใกล้บ้านที่ข้อมูลล่าสุด ลองใหม่ภายหลัง")
    reply(token, HELP)


def _subscribe(conn, user, token, m, at, reply):
    try:
        lat, lng = float(m["latitude"]), float(m["longitude"])
    except (KeyError, TypeError, ValueError):
        return reply(token, "อ่านตำแหน่งไม่ได้ ลองส่งใหม่อีกครั้ง")
    if not _in_thailand(lat, lng):
        return reply(token, "ตำแหน่งนี้อยู่นอกประเทศไทย ระบบรองรับเฉพาะสถานีในไทย")
    existing = conn.execute("SELECT 1 AS x FROM subscriptions WHERE channel='line' AND target=?", (user,)).fetchone()
    if not existing:
        total = conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"]
        if total >= MAX_SUBSCRIBERS:
            return reply(token, "ขออภัย ตอนนี้รับผู้ใช้เต็มแล้ว")
    st = _nearest(conn, lat, lng, at)
    conn.execute("DELETE FROM subscriptions WHERE channel='line' AND target=?", (user,))
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,last_status,last_notified) "
                 "VALUES('line',?,?,?,?,?,?)",
                 (user, lat, lng, "บ้านของคุณ", st["status"] if st else None, utc_now() if st else None))
    conn.commit()
    head = "บันทึกตำแหน่งแล้ว จะแจ้งเตือนเมื่อสถานีใกล้บ้านเปลี่ยนสถานะ (เก็บเฉพาะตำแหน่งนี้ พิมพ์ \"ยกเลิก\" เพื่อลบ)\n\n"
    reply(token, head + (message({"label": "บ้านของคุณ"}, st) if st else "ตอนนี้ยังไม่มีสถานีใกล้บ้านที่ข้อมูลล่าสุด"))

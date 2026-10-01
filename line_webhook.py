"""LINE bot webhook: users subscribe by sending their location to the official account.

  location message   -> save/replace their subscription (userId + coordinates)
  "ยกเลิก" / "stop"    -> delete it
  "สถานะ"              -> current status of the station nearest their saved location (card)
  "รายงาน"             -> province situation report now (also sent every morning at 07:00)
  "ตั้งค่า"             -> show settings with tap-to-change buttons:
        "แจ้งทุกระดับ" / "แจ้งเฉพาะเตือนภัย"   which status changes trigger a message
        "ไม่รบกวนกลางคืน" / "แจ้งกลางคืน"       hold non-alert messages between 22:00 and 06:00
        "ปิดสรุป" / "เปิดสรุป"                  the 07:00 morning report
  anything else      -> short instructions

In a LINE group or room the bot only reacts to commands (and stays silent otherwise):
  "ติดตาม <จังหวัด>"  -> send that province's morning report to the group every day
  "รายงาน", "ปิดสรุป", "เปิดสรุป", "ยกเลิก", "ช่วยเหลือ"

Requests are authenticated with LINE's X-Line-Signature (HMAC-SHA256 of the raw body with
the channel secret), so only LINE can register subscriptions. We store nothing but the
LINE userId (or groupId) and the coordinates / province the user chose to give.
"""
import base64
import hashlib
import hmac
import os

import cards
import core
import report
from ingest import utc_now
from notify import _invoke, message, province_of, report_link

MAX_SUBSCRIBERS = int(os.environ.get("MAX_SUBSCRIBERS", "50"))
CANCEL = {"ยกเลิก", "stop", "cancel", "unsubscribe"}
STATUS = {"สถานะ", "status"}
REPORT = {"รายงาน", "report", "สรุป"}
DIGEST_OFF = {"ปิดสรุป", "หยุดสรุป"}
DIGEST_ON = {"เปิดสรุป"}
SETTINGS = {"ตั้งค่า", "settings"}
GROUP_HELP_WORDS = {"ช่วยเหลือ", "help"}
MENU = ["สถานะ", "รายงาน", "ตั้งค่า"]
# command -> (column, value, confirmation)
CHANGES = {
    "แจ้งทุกระดับ": ("notify_level", "all", "จะแจ้งเตือนทุกครั้งที่สถานะสถานีใกล้บ้านเปลี่ยน"),
    "แจ้งเฉพาะเตือนภัย": ("notify_level", "alert", "จะแจ้งเตือนเฉพาะตอนที่ถึงหรือพ้นระดับเตือนภัย"),
    "ไม่รบกวนกลางคืน": ("quiet", 1, "ช่วง 22:00–06:00 จะไม่ส่งข้อความ ยกเว้นระดับเตือนภัย (ข้อความที่ค้างจะส่งหลัง 06:00)"),
    "แจ้งกลางคืน": ("quiet", 0, "จะแจ้งเตือนตลอด 24 ชม."),
}
HELP = ("น้ำใกล้บ้านฉัน: ส่งตำแหน่งบ้านของคุณมาที่นี่ (กด + แล้วเลือก \"ตำแหน่งที่ตั้ง\") "
        "เพื่อรับแจ้งเตือนเมื่อสถานีวัดน้ำที่ใกล้ที่สุดเปลี่ยนสถานะ\n"
        "พิมพ์ \"สถานะ\" ดูค่าล่าสุด · \"รายงาน\" ดูสรุปทั้งจังหวัด · \"ตั้งค่า\" เลือกระดับแจ้งเตือน/ช่วงไม่รบกวน · "
        "\"ปิดสรุป\" หยุดสรุปทุกเช้า 07:00 · \"ยกเลิก\" หยุดทุกอย่างและลบตำแหน่งที่เก็บไว้\n"
        "ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น ให้ยึดประกาศ ปภ. เป็นหลัก")
GROUP_HELP = ("น้ำใกล้บ้านฉัน: พิมพ์ \"ติดตาม <ชื่อจังหวัด>\" เช่น ติดตาม อยุธยา "
              "เพื่อให้บอตส่งสรุปสถานการณ์น้ำของจังหวัดนั้นเข้ากลุ่มนี้ทุกเช้า 07:00\n"
              "คำสั่งอื่น: \"รายงาน\" ดูสรุปตอนนี้ · \"ปิดสรุป\" / \"เปิดสรุป\" · \"ยกเลิก\" ลบกลุ่มนี้ออกจากระบบ\n"
              "บอตจะไม่ตอบข้อความทั่วไปในกลุ่ม ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น")


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


def _reply(reply, token, text, flex=None, quick=None):
    """`reply` may be a simple (token, text) callable (tests) or accept flex=/quick= (real sender)."""
    _invoke(reply, token, text, flex=flex, quick=quick)


def _sub(conn, target):
    return conn.execute("SELECT * FROM subscriptions WHERE channel='line' AND target=?", (target,)).fetchone()


def _delete(conn, target):
    conn.execute("DELETE FROM subscriptions WHERE channel='line' AND target=?", (target,))
    conn.commit()


def _full(conn, existing):
    return not existing and conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"] >= MAX_SUBSCRIBERS


def handle_event(conn, ev, at, reply):
    """Process one LINE event. `reply(token, text[, flex=, quick=])` sends the answer."""
    src = ev.get("source") or {}
    gid = src.get("groupId") or src.get("roomId")
    if gid:
        return _handle_group(conn, gid, ev, at, reply)
    user = src.get("userId")
    if not user:
        return
    if ev.get("type") == "unfollow":  # user blocked us: forget them
        return _delete(conn, user)
    token = ev.get("replyToken")
    if not token:
        return
    if ev.get("type") == "follow":
        return _reply(reply, token, HELP, quick=cards.quick(MENU))
    if ev.get("type") != "message":
        return
    m = ev.get("message") or {}
    if m.get("type") == "location":
        return _subscribe(conn, user, token, m, at, reply)
    text = (m.get("text") or "").strip().lower() if m.get("type") == "text" else ""
    if text in CANCEL:
        _delete(conn, user)
        return _reply(reply, token, "หยุดแจ้งเตือนและลบตำแหน่งของคุณแล้ว ส่งตำแหน่งมาใหม่ได้ทุกเมื่อ")
    if text in STATUS | REPORT | SETTINGS | set(CHANGES) | DIGEST_OFF | DIGEST_ON:
        sub = _sub(conn, user)
        if not sub:
            return _reply(reply, token, "ยังไม่มีตำแหน่งของคุณ ส่งตำแหน่งบ้านมาก่อนได้เลย")
        if text in STATUS:
            st = _nearest(conn, sub["lat"], sub["lng"], at)
            if not st:
                return _reply(reply, token, "ตอนนี้ไม่มีสถานีใกล้บ้านที่ข้อมูลล่าสุด ลองใหม่ภายหลัง")
            return _reply(reply, token, message(sub, st), cards.station_card(sub["label"], st, report_link(st["province"])),
                          cards.quick(MENU))
        if text in REPORT:
            prov = province_of(core.latest(conn, at), sub["lat"], sub["lng"])
            return _send_report(conn, reply, token, prov, at)
        if text in SETTINGS:
            return _settings(reply, token, sub)
        if text in CHANGES:
            col, val, confirm = CHANGES[text]
            conn.execute(f"UPDATE subscriptions SET {col}=? WHERE channel='line' AND target=?", (val, user))
            conn.commit()
            return _settings(reply, token, _sub(conn, user), confirm)
        on = 1 if text in DIGEST_ON else 0
        conn.execute("UPDATE subscriptions SET digest=? WHERE channel='line' AND target=?", (on, user))
        conn.commit()
        return _reply(reply, token, "จะส่งสรุปสถานการณ์ให้ทุกเช้า 07:00 (พิมพ์ \"ปิดสรุป\" เพื่อหยุด)" if on
                      else "หยุดส่งสรุปทุกเช้าแล้ว ยังแจ้งเตือนเมื่อสถานะเปลี่ยนตามเดิม (พิมพ์ \"เปิดสรุป\" เพื่อเปิดใหม่)",
                      quick=cards.quick(MENU))
    _reply(reply, token, HELP, quick=cards.quick(MENU))


def _send_report(conn, reply, token, prov, at):
    rep = report.build(conn, prov, at) if prov else None
    if not rep:
        return _reply(reply, token, "ยังไม่มีข้อมูลจังหวัดของคุณ")
    url = report_link(prov)
    _reply(reply, token, report.digest_text(rep, at, url), cards.digest_card(rep, report.thai_date(at), url),
           cards.quick(MENU))


def _settings(reply, token, sub, confirm=None):
    alert_only = sub["notify_level"] == "alert"
    quiet = bool(sub["quiet"])
    digest = bool(sub["digest"])
    lines = ([confirm, ""] if confirm else []) + [
        "การตั้งค่าของคุณ",
        "• แจ้งเตือน: " + ("เฉพาะตอนถึง/พ้นระดับเตือนภัย" if alert_only else "ทุกครั้งที่สถานะเปลี่ยน"),
        "• กลางคืน 22:00–06:00: " + ("ไม่ส่ง ยกเว้นเตือนภัย" if quiet else "ส่งตามปกติ"),
        "• สรุปทุกเช้า 07:00: " + ("เปิด" if digest else "ปิด"),
        "แตะปุ่มด้านล่างเพื่อเปลี่ยน"]
    buttons = ["แจ้งทุกระดับ" if alert_only else "แจ้งเฉพาะเตือนภัย",
               "แจ้งกลางคืน" if quiet else "ไม่รบกวนกลางคืน",
               "ปิดสรุป" if digest else "เปิดสรุป", "สถานะ", "ยกเลิก"]
    _reply(reply, token, "\n".join(lines), quick=cards.quick(buttons))


def _subscribe(conn, user, token, m, at, reply):
    try:
        lat, lng = float(m["latitude"]), float(m["longitude"])
    except (KeyError, TypeError, ValueError):
        return _reply(reply, token, "อ่านตำแหน่งไม่ได้ ลองส่งใหม่อีกครั้ง")
    if not _in_thailand(lat, lng):
        return _reply(reply, token, "ตำแหน่งนี้อยู่นอกประเทศไทย ระบบรองรับเฉพาะสถานีในไทย")
    existing = conn.execute("SELECT digest, notify_level, quiet FROM subscriptions WHERE channel='line' AND target=?",
                            (user,)).fetchone()
    if _full(conn, existing):
        return _reply(reply, token, "ขออภัย ตอนนี้รับผู้ใช้เต็มแล้ว")
    st = _nearest(conn, lat, lng, at)
    _delete(conn, user)
    keep = existing or {"digest": 1, "notify_level": "all", "quiet": 0}  # re-sending a location keeps the settings
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,last_status,last_notified,digest,notify_level,quiet) "
                 "VALUES('line',?,?,?,?,?,?,?,?,?)",
                 (user, lat, lng, "บ้านของคุณ", st["status"] if st else None, utc_now() if st else None,
                  keep["digest"], keep["notify_level"], keep["quiet"]))
    conn.commit()
    head = ("บันทึกตำแหน่งแล้ว จะแจ้งเตือนเมื่อสถานีใกล้บ้านเปลี่ยนสถานะ และส่งสรุปสถานการณ์ทุกเช้า 07:00 "
            "(เก็บเฉพาะตำแหน่งนี้ พิมพ์ \"ตั้งค่า\" เปลี่ยนระดับแจ้งเตือน/ช่วงไม่รบกวน \"ปิดสรุป\" หยุดสรุปเช้า "
            "หรือ \"ยกเลิก\" เพื่อลบ)\n\n")
    _reply(reply, token, head + (message({"label": "บ้านของคุณ"}, st) if st else "ตอนนี้ยังไม่มีสถานีใกล้บ้านที่ข้อมูลล่าสุด"),
           quick=cards.quick(MENU))


# ---- groups and rooms ---------------------------------------------------------------------------

def _handle_group(conn, gid, ev, at, reply):
    kind = ev.get("type")
    if kind == "leave":  # the bot was removed: forget the group
        return _delete(conn, gid)
    token = ev.get("replyToken")
    if not token:
        return
    if kind == "join":
        return _reply(reply, token, GROUP_HELP)
    m = ev.get("message") or {}
    if kind != "message" or m.get("type") != "text":
        return
    raw = (m.get("text") or "").strip()
    text = raw.lower()
    if raw.startswith("ติดตาม"):
        return _group_follow(conn, gid, token, raw[len("ติดตาม"):].strip(), at, reply)
    if text in GROUP_HELP_WORDS:
        return _reply(reply, token, GROUP_HELP)
    if text in CANCEL | REPORT | DIGEST_OFF | DIGEST_ON:
        sub = _sub(conn, gid)
        if not sub:
            return _reply(reply, token, "กลุ่มนี้ยังไม่ได้ตั้งจังหวัด พิมพ์ \"ติดตาม <ชื่อจังหวัด>\"")
        if text in CANCEL:
            _delete(conn, gid)
            return _reply(reply, token, "เลิกส่งสรุปเข้ากลุ่มนี้และลบข้อมูลของกลุ่มแล้ว")
        if text in REPORT:
            return _send_report(conn, reply, token, (sub["label"] or "")[2:], at)
        on = 1 if text in DIGEST_ON else 0
        conn.execute("UPDATE subscriptions SET digest=? WHERE channel='line' AND target=?", (on, gid))
        conn.commit()
        return _reply(reply, token, "จะส่งสรุปเข้ากลุ่มทุกเช้า 07:00" if on else "หยุดส่งสรุปเข้ากลุ่มแล้ว")
    # anything else is ordinary chat: stay silent


def _group_follow(conn, gid, token, query, at, reply):
    rows = core.latest(conn, at)
    provinces = sorted({r["province"] for r in rows if r["province"]})
    if not query:
        return _reply(reply, token, "พิมพ์ชื่อจังหวัดต่อท้าย เช่น ติดตาม อยุธยา")
    found = [query] if query in provinces else [p for p in provinces if query in p]
    if not found:
        return _reply(reply, token, f"ไม่พบจังหวัด \"{query}\" ที่มีสถานีวัดน้ำ ลองพิมพ์ชื่อจังหวัดให้ครบ")
    if len(found) > 1:
        return _reply(reply, token, "พบหลายจังหวัด: " + ", ".join(found[:6]) + " พิมพ์ชื่อให้ชัดขึ้นอีกนิด")
    prov = found[0]
    existing = conn.execute("SELECT digest FROM subscriptions WHERE channel='line' AND target=?", (gid,)).fetchone()
    if _full(conn, existing):
        return _reply(reply, token, "ขออภัย ตอนนี้รับผู้ใช้เต็มแล้ว")
    pts = [(r["lat"], r["lng"]) for r in rows if r["province"] == prov]
    lat, lng = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    _delete(conn, gid)
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,digest) VALUES('line',?,?,?,?,?)",
                 (gid, lat, lng, "จ." + prov, existing["digest"] if existing else 1))
    conn.commit()
    head = (f"ตั้งค่าแล้ว: จะส่งสรุปสถานการณ์น้ำ จ.{prov} เข้ากลุ่มนี้ทุกเช้า 07:00 "
            "(พิมพ์ \"ปิดสรุป\" หยุดชั่วคราว \"ยกเลิก\" ลบกลุ่มนี้ออกจากระบบ)\n\n")
    rep = report.build(conn, prov, at, rows=rows)
    url = report_link(prov)
    _reply(reply, token, head + report.digest_text(rep, at, url))

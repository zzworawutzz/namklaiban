"""LINE bot webhook: users subscribe by sending their location to the official account.

  location message   -> save/replace their subscription (userId + coordinates)
  "ยกเลิก" / "stop"    -> delete it
  "สถานะ"              -> current status of the station nearest their saved location (card)
  "รายงาน"             -> province situation report now (also sent automatically, see report.digest_label())
  "ตั้งค่า"             -> show settings with tap-to-change buttons:
        "แจ้งทุกระดับ" / "แจ้งเฉพาะเตือนภัย"   which status changes trigger a message
        "ไม่รบกวนกลางคืน" / "แจ้งกลางคืน"       hold non-alert messages between 22:00 and 06:00
        "ปิดสรุป" / "เปิดสรุป"                  the periodic situation summary
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
import urllib.parse
from datetime import datetime, timedelta, timezone

import budget
import cards
import core
import floodreports
import report
import shelters
import suggest
from ingest import TZ_TH, utc_now
from notify import _invoke, message, province_of, public_url, report_link

MAX_SUBSCRIBERS = int(os.environ.get("MAX_SUBSCRIBERS", "50"))   # people, not places
MAX_PLACES = 3
NEW_USER_DIGEST = 1 if os.environ.get("NEW_USER_DIGEST", "0") == "1" else 0   # new followers start without the daily summary: it is ~30 of the 300 monthly messages each
PLACE_LABELS = ["บ้านของคุณ", "จุดที่ 2", "จุดที่ 3"]
SAME_PLACE_KM = 0.2
PLACES_CMD = {"ตำแหน่งของฉัน", "จุดที่บันทึก", "places"}
CANCEL = {"ยกเลิก", "stop", "cancel", "unsubscribe"}
STATUS = {"สถานะ", "status"}
REPORT = {"รายงาน", "report", "สรุป"}
DIGEST_OFF = {"ปิดสรุป", "หยุดสรุป"}
DIGEST_ON = {"เปิดสรุป"}
SETTINGS = {"ตั้งค่า", "settings"}
MY_ID_CMD = {"ไอดีของฉัน", "my id"}
GROUP_HELP_WORDS = {"ช่วยเหลือ", "help"}
MENU = ["สถานะ", "รายงาน", "แจ้งน้ำท่วม", "ศูนย์พักพิง", "ตำแหน่งของฉัน", "ตั้งค่า", "วิธีใช้"]
SNOOZE_CMD = {"พักแจ้งเตือน", "พักแจ้งเตือน 6 ชม.", "พัก"}
SNOOZE_OFF = {"เลิกพัก", "เลิกพักแจ้งเตือน"}
SNOOZE_H = 6
SHELTER_CMD = {"ศูนย์พักพิง", "ที่พักพิง", "shelter"}
MANUAL_CMD = {"วิธีใช้", "คู่มือ", "help", "ช่วยเหลือ"}
FLOOD_CMD = "แจ้งน้ำท่วม"
FLOOD_BUTTONS = {"ท่วม" + label.replace("ท่วมถึง", "").replace("ท่วม", ""): lvl for lvl, label in floodreports.LEVELS.items()}
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
        "พิมพ์ชื่อตำบล/อำเภอ/จังหวัด/สถานี เช่น \"บางบัวทอง\" เพื่อดูสถานการณ์ที่นั่น · \"พักแจ้งเตือน\" หยุดแจ้ง 6 ชม. · \"แจ้งน้ำท่วม\" รายงานจุดที่ท่วมแถวคุณ · \"ตำแหน่งของฉัน\" ดู/ลบจุดที่ติดตาม (ได้สูงสุด 3 จุด) · \"ศูนย์พักพิง\" ดู 3 แห่งที่ใกล้ที่สุด · \"ปิดสรุป\" หยุดสรุปสถานการณ์อัตโนมัติ · \"ยกเลิก\" หยุดทุกอย่างและลบตำแหน่งที่เก็บไว้\n"
        "ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น ให้ยึดประกาศ ปภ. เป็นหลัก")
GROUP_HELP = ("น้ำใกล้บ้านฉัน: พิมพ์ \"ติดตาม <ชื่อจังหวัด>\" เช่น ติดตาม อยุธยา "
              "เพื่อให้บอตส่งสรุปสถานการณ์น้ำของจังหวัดนั้นเข้ากลุ่มนี้ทุกเช้า 07:00\n"
              "คำสั่งอื่น: \"รายงาน\" ดูสรุปตอนนี้ · \"ปิดสรุป\" / \"เปิดสรุป\" · \"ยกเลิก\" ลบกลุ่มนี้ออกจากระบบ\n"
              "บอตจะไม่ตอบข้อความทั่วไปในกลุ่ม ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น")


def _with_manual(text):
    """Append the link to the full manual when the public URL of this deployment is known."""
    base = public_url()
    return text + (f"\n\nคู่มือการใช้งานฉบับเต็ม: {base}/help.html\nนโยบายความเป็นส่วนตัว: {base}/privacy.html" if base else "")


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


def _places(conn, target):
    """All saved places of a LINE user (oldest first); the first one is the primary place."""
    return conn.execute("SELECT * FROM subscriptions WHERE channel='line' AND target=? ORDER BY id", (target,)).fetchall()


def _sub(conn, target):
    p = _places(conn, target)
    return p[0] if p else None


def _delete(conn, target):
    conn.execute("DELETE FROM subscriptions WHERE channel='line' AND target=?", (target,))
    conn.commit()


def _full(conn, existing):
    return not existing and conn.execute("SELECT COUNT(DISTINCT target) AS n FROM subscriptions").fetchone()["n"] >= MAX_SUBSCRIBERS


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
        return _reply(reply, token, _with_manual(HELP), quick=cards.quick(MENU))
    if ev.get("type") == "postback":
        return _postback(conn, user, token, (ev.get("postback") or {}).get("data") or "", at, reply)
    if ev.get("type") != "message":
        return
    m = ev.get("message") or {}
    if m.get("type") == "location":
        level = floodreports.pop_pending(conn, user, at)
        if level:
            return _flood_report(conn, user, token, m, level, at, reply)
        return _subscribe(conn, user, token, m, at, reply)
    text = (m.get("text") or "").strip().lower() if m.get("type") == "text" else ""
    if text == FLOOD_CMD:
        return _reply(reply, token, "ตอนนี้น้ำท่วมระดับไหน? เลือกด้านล่าง แล้วส่งตำแหน่งจุดที่ท่วมมา\n"
                      "(รายงานจะแสดงบนแผนที่ 12 ชม. เป็นข้อมูลจากผู้ใช้ ยังไม่ผ่านการตรวจสอบ)",
                      quick=cards.quick(list(FLOOD_BUTTONS)))
    if text in FLOOD_BUTTONS:
        floodreports.set_pending(conn, user, FLOOD_BUTTONS[text], at)
        return _reply(reply, token, "รับทราบ กดปุ่มด้านล่างแล้วเลือกจุดที่ท่วม (ภายใน 10 นาที)",
                      quick=[cards.locate_button()])
    if text in CANCEL:
        _delete(conn, user)
        return _reply(reply, token, "หยุดแจ้งเตือนและลบตำแหน่งของคุณแล้ว ส่งตำแหน่งมาใหม่ได้ทุกเมื่อ")
    if text in MY_ID_CMD:
        return _reply(reply, token, f"ไอดี LINE ของคุณ:\n{user}\n(ใช้ตั้งค่า ADMIN_LINE_ID เพื่อรับแจ้งเตือนเมื่อระบบดึงข้อมูลไม่ได้ อย่าแชร์ให้ใคร)")
    if text in MANUAL_CMD:
        return _reply(reply, token, _with_manual(HELP), quick=cards.quick(MENU))
    if text in PLACES_CMD:
        return _list_places(conn, user, token, reply)
    if text.startswith("ลบจุดที่"):
        return _delete_place(conn, user, token, text[len("ลบจุดที่"):].strip(), reply)
    if text in SHELTER_CMD:
        sub = _sub(conn, user)
        if not sub:
            return _reply(reply, token, "ส่งตำแหน่งของคุณมาก่อน (กด + แล้วเลือก \"ตำแหน่งที่ตั้ง\") แล้วพิมพ์ \"ศูนย์พักพิง\" อีกครั้ง")
        near = shelters.nearest(sub["lat"], sub["lng"], 3, 50)
        return _reply(reply, token, shelters.line_text(near) if near else
                      "ไม่พบศูนย์พักพิงในรัศมี 50 กม. จากตำแหน่งที่บันทึกไว้ ฉุกเฉินโทร 1784 (ปภ.)", quick=cards.quick(MENU))
    if text in SNOOZE_CMD | SNOOZE_OFF:
        return _snooze(conn, user, token, text in SNOOZE_CMD, at, reply)
    if text in STATUS | REPORT | SETTINGS | set(CHANGES) | DIGEST_OFF | DIGEST_ON:
        sub = _sub(conn, user)
        if not sub:
            return _reply(reply, token, "ยังไม่มีตำแหน่งของคุณ ส่งตำแหน่งบ้านมาก่อนได้เลย")
        if text in STATUS:
            places = _places(conn, user)
            found = [(p, _nearest(conn, p["lat"], p["lng"], at)) for p in places]
            if not any(st for _, st in found):
                return _reply(reply, token, "ตอนนี้ไม่มีสถานีใกล้บ้านที่ข้อมูลล่าสุด ลองใหม่ภายหลัง")
            if len(places) == 1:
                p, st = found[0]
                return _reply(reply, token, message(p, st), cards.station_card(p["label"], st, report_link(st["province"])),
                              cards.quick(MENU))
            return _reply(reply, token, "\n\n".join(message(p, st) if st else f"{p['label']}: ยังไม่มีสถานีใกล้ที่ข้อมูลล่าสุด"
                                                    for p, st in found), quick=cards.quick(MENU))
        if text in REPORT:
            prov = province_of(core.latest(conn, at), sub["lat"], sub["lng"])
            return _send_report(conn, reply, token, prov, at)
        if text in SETTINGS:
            return _settings(reply, token, sub, at=at)
        if text in CHANGES:
            col, val, confirm = CHANGES[text]
            conn.execute(f"UPDATE subscriptions SET {col}=? WHERE channel='line' AND target=?", (val, user))
            conn.commit()
            return _settings(reply, token, _sub(conn, user), confirm, at)
        on = 1 if text in DIGEST_ON else 0
        conn.execute("UPDATE subscriptions SET digest=? WHERE channel='line' AND target=?", (on, user))
        conn.commit()
        return _reply(reply, token, f"จะส่งสรุปสถานการณ์ให้{report.digest_label()} (พิมพ์ \"ปิดสรุป\" เพื่อหยุด)\n" + _quota_note(conn, at).strip() if on
                      else "หยุดส่งสรุปแล้ว ยังแจ้งเตือนเมื่อสถานะเปลี่ยนตามเดิม (พิมพ์ \"เปิดสรุป\" เพื่อเปิดใหม่)",
                      quick=cards.quick(MENU))
    if text and _lookup(conn, user, token, text, at, reply):
        return
    _reply(reply, token, _with_manual(HELP), quick=cards.quick(MENU))


LOOKUP_CHOICES = 5
LOOKUP_MIN_CHARS = 3


def _lookup(conn, user, token, text, at, reply):
    """A place name typed in the chat ("บางบัวทอง", "อยุธยา", a station's name): answer for that place.
    Returns False when the text matches nothing, so the caller can fall back to the instructions."""
    if len(suggest.norm(text)) < LOOKUP_MIN_CHARS:   # "น้ำ" or "ok" is chatter, not a place
        return False
    rows = core.latest(conn, at)
    found = [m for m in suggest.search(text, stations=rows, limit=LOOKUP_CHOICES * 2) if m["type"] != "ศูนย์พักพิง"][:LOOKUP_CHOICES]
    if not found:
        return False
    if len(found) == 1 or suggest.norm(found[0]["name"]) == suggest.norm(text):
        return _show_place(conn, user, token, found[0], at, reply)
    items = [(m["label"], _look_data(m)) for m in found]
    _reply(reply, token, f"พบหลายที่ชื่อ \"{text[:30]}\" เลือกที่ต้องการดู", quick=cards.quick_postback(items))
    return True


def _look_data(m):
    if m["type"] == "จังหวัด":
        return "act=prov&" + urllib.parse.urlencode({"p": m["province"]})
    return f"act=look&lat={m['lat']}&lng={m['lng']}&" + urllib.parse.urlencode({"n": m["name"][:40]})


def _show_place(conn, user, token, m, at, reply):
    if m["type"] == "จังหวัด":
        _send_report(conn, reply, token, m["province"], at)
        return True
    st = _nearest(conn, m["lat"], m["lng"], at)
    label = m["name"]
    if not st:
        _reply(reply, token, f"{label}: ตอนนี้ไม่มีสถานีใกล้ที่ข้อมูลล่าสุด ฉุกเฉินโทร 1784 (ปภ.)", quick=cards.quick(MENU))
        return True
    text = message({"label": label}, st)
    if not _sub(conn, user):
        text += "\n\nอยากให้บอตแจ้งเตือนเอง ส่งตำแหน่งบ้านของคุณมา (กด + แล้วเลือก \"ตำแหน่งที่ตั้ง\")"
    _reply(reply, token, text, cards.station_card(label, st, report_link(st["province"])), cards.quick(MENU))
    return True


def _flood_report(conn, user, token, m, level, at, reply):
    try:
        lat, lng = float(m["latitude"]), float(m["longitude"])
    except (KeyError, TypeError, ValueError):
        return _reply(reply, token, "อ่านตำแหน่งไม่ได้ ลองแจ้งใหม่อีกครั้ง", quick=cards.quick(MENU))
    try:
        floodreports.add(conn, lat, lng, level, None, "line", floodreports.who("line:" + user), at)
    except floodreports.Rejected as e:
        return _reply(reply, token, e.message, quick=cards.quick(MENU))
    _reply(reply, token, f"ขอบคุณ บันทึกแล้ว: {floodreports.LEVELS[level]} จะแสดงบนแผนที่ 12 ชม. "
                         "(ข้อมูลจากผู้ใช้ ยังไม่ผ่านการตรวจสอบ)", quick=cards.quick(MENU))


def _send_report(conn, reply, token, prov, at):
    rep = report.build(conn, prov, at) if prov else None
    if not rep:
        return _reply(reply, token, "ยังไม่มีข้อมูลจังหวัดของคุณ")
    url = report_link(prov)
    _reply(reply, token, report.digest_text(rep, at, url), cards.digest_card(rep, report.thai_date(at), url),
           cards.quick(MENU))


def _quota_note(conn, at):
    """Said to someone who has just signed up (or turned the summary on) while this month's message quota is nearly used up."""
    if budget.Budget(conn, at).allow(False):
        return ""
    return "⚠ โควตาข้อความของบอตเดือนนี้ใกล้เต็ม จึงแจ้งเฉพาะระดับเตือนภัยจนถึงต้นเดือนหน้า (สรุปและการเตือนอื่นๆ พักไว้ก่อน)\n\n"


def _snooze_text(sub, at):
    """"พักถึง 18:30 น." while a pause is running, otherwise None."""
    until = sub["snooze_until"]
    if not until or until <= at.strftime("%Y-%m-%dT%H:%M:%SZ"):
        return None
    return "พักถึง " + datetime.strptime(until, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TZ_TH).strftime("%H:%M") + " น."


def _snooze(conn, user, token, on, at, reply):
    if not _sub(conn, user):
        return _reply(reply, token, "ยังไม่มีตำแหน่งของคุณ ส่งตำแหน่งบ้านมาก่อนได้เลย")
    until = (at + timedelta(hours=SNOOZE_H)).strftime("%Y-%m-%dT%H:%M:%SZ") if on else None
    conn.execute("UPDATE subscriptions SET snooze_until=? WHERE channel='line' AND target=?", (until, user))
    conn.commit()
    if on:
        when = _snooze_text(_sub(conn, user), at)
        return _reply(reply, token, f"{when} จะไม่ส่งแจ้งเตือนสถานะที่เปลี่ยน (ระดับเตือนภัยและสรุปประจำวันยังส่งตามปกติ)\n"
                      "ถ้าสถานะยังต่างจากตอนนี้เมื่อพักครบ จะแจ้งให้ทราบ พิมพ์ \"เลิกพัก\" เพื่อกลับมาแจ้งทันที", quick=cards.quick(MENU))
    _reply(reply, token, "กลับมาแจ้งเตือนตามปกติแล้ว", quick=cards.quick(MENU))


def _settings(reply, token, sub, confirm=None, at=None):
    alert_only = sub["notify_level"] == "alert"
    quiet = bool(sub["quiet"])
    digest = bool(sub["digest"])
    lines = ([confirm, ""] if confirm else []) + [
        "การตั้งค่าของคุณ",
        "• แจ้งเตือน: " + ("เฉพาะตอนถึง/พ้นระดับเตือนภัย" if alert_only else "ทุกครั้งที่สถานะเปลี่ยน"),
        "• กลางคืน 22:00–06:00: " + ("ไม่ส่ง ยกเว้นเตือนภัย" if quiet else "ส่งตามปกติ"),
        f"• สรุป{report.digest_label()}: " + ("เปิด" if digest else "ปิด")]
    paused = _snooze_text(sub, at or datetime.now(timezone.utc))
    if paused:
        lines.append("• พักแจ้งเตือน: " + paused)
    lines.append("แตะปุ่มด้านล่างเพื่อเปลี่ยน")
    buttons = ["แจ้งทุกระดับ" if alert_only else "แจ้งเฉพาะเตือนภัย",
               "แจ้งกลางคืน" if quiet else "ไม่รบกวนกลางคืน",
               "ปิดสรุป" if digest else "เปิดสรุป", "เลิกพัก" if paused else "พักแจ้งเตือน 6 ชม.", "สถานะ", "ยกเลิก"]
    _reply(reply, token, "\n".join(lines), quick=cards.quick(buttons))


def _subscribe(conn, user, token, m, at, reply):
    try:
        lat, lng = float(m["latitude"]), float(m["longitude"])
    except (KeyError, TypeError, ValueError):
        return _reply(reply, token, "อ่านตำแหน่งไม่ได้ ลองส่งใหม่อีกครั้ง")
    if not _in_thailand(lat, lng):
        return _reply(reply, token, "ตำแหน่งนี้อยู่นอกประเทศไทย ระบบรองรับเฉพาะสถานีในไทย")
    places = _places(conn, user)
    if _full(conn, places):
        return _reply(reply, token, "ขออภัย ตอนนี้รับผู้ใช้เต็มแล้ว")
    if not places:
        st = _nearest(conn, lat, lng, at)
        _insert_place(conn, user, lat, lng, PLACE_LABELS[0], st, {"digest": NEW_USER_DIGEST, "notify_level": "all", "quiet": 0})
        if NEW_USER_DIGEST:
            head = ("บันทึกตำแหน่งแล้ว จะแจ้งเตือนเมื่อสถานีใกล้บ้านเปลี่ยนสถานะ และส่งสรุปสถานการณ์ "
                    f"{report.digest_label()} "
                    "(เก็บเฉพาะตำแหน่งนี้ พิมพ์ \"ตั้งค่า\" เปลี่ยนระดับแจ้งเตือน/ช่วงไม่รบกวน \"ปิดสรุป\" หยุดสรุป "
                    "\"ส่งตำแหน่งอีกจุดเพื่อติดตามเพิ่มได้สูงสุด 3 จุด\" หรือ \"ยกเลิก\" เพื่อลบ)\n\n")
        else:
            head = ("บันทึกตำแหน่งแล้ว จะแจ้งเตือนเมื่อสถานีใกล้บ้านเปลี่ยนสถานะ ส่วนสรุปสถานการณ์ปิดไว้ก่อนเพื่อไม่ให้ข้อความเยอะ "
                    f"พิมพ์ \"เปิดสรุป\" ถ้าต้องการรับสรุป{report.digest_label()} "
                    "(เก็บเฉพาะตำแหน่งนี้ พิมพ์ \"ตั้งค่า\" เปลี่ยนระดับแจ้งเตือน/ช่วงไม่รบกวน "
                    "\"ส่งตำแหน่งอีกจุดเพื่อติดตามเพิ่มได้สูงสุด 3 จุด\" หรือ \"ยกเลิก\" เพื่อลบ)\n\n")
        head += _quota_note(conn, at)
        return _reply(reply, token, head + (message({"label": PLACE_LABELS[0]}, st) if st else
                                            "ตอนนี้ยังไม่มีสถานีใกล้บ้านที่ข้อมูลล่าสุด"), quick=cards.quick(MENU))
    for p in places:
        if core.km(lat, lng, p["lat"], p["lng"]) <= SAME_PLACE_KM:
            return _reply(reply, token, f"จุดนี้บันทึกไว้แล้วเป็น \"{p['label']}\" พิมพ์ \"ตำแหน่งของฉัน\" เพื่อดูหรือลบจุดที่บันทึก",
                          quick=cards.quick(MENU))
    pos = f"lat={lat:.6f}&lng={lng:.6f}"
    items = [(f"แทนที่ {p['label']}", f"act=rep&id={p['id']}&{pos}") for p in places]
    if len(places) < MAX_PLACES:
        items.insert(0, (f"เพิ่มเป็น{_free_label(places)}", f"act=add&{pos}"))
    _reply(reply, token, f"คุณบันทึกไว้ {len(places)} จุดแล้ว ตำแหน่งใหม่นี้จะให้ทำอย่างไร? (ติดตามได้สูงสุด {MAX_PLACES} จุด)",
           quick=cards.quick_postback(items))


def _free_label(places):
    used = {p["label"] for p in places}
    return next((l for l in PLACE_LABELS if l not in used), PLACE_LABELS[-1])


def _insert_place(conn, user, lat, lng, label, st, keep):
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,last_status,last_notified,digest,notify_level,quiet) "
                 "VALUES('line',?,?,?,?,?,?,?,?,?)",
                 (user, lat, lng, label, st["status"] if st else None, utc_now() if st else None,
                  keep["digest"], keep["notify_level"], keep["quiet"]))
    conn.commit()


def _postback(conn, user, token, data, at, reply):
    q = {k: v[0] for k, v in urllib.parse.parse_qs(data).items()}
    if q.get("act") in ("snooze", "unsnooze"):
        return _snooze(conn, user, token, q["act"] == "snooze", at, reply)
    if q.get("act") == "prov" and q.get("p"):
        return _send_report(conn, reply, token, q["p"][:60], at)
    if q.get("act") == "look":
        try:
            lat, lng = float(q["lat"]), float(q["lng"])
        except (KeyError, ValueError):
            return
        if _in_thailand(lat, lng):
            _show_place(conn, user, token, {"type": "สถานที่", "name": (q.get("n") or "จุดที่เลือก")[:40], "lat": lat, "lng": lng}, at, reply)
        return
    try:
        lat, lng = float(q["lat"]), float(q["lng"])
    except (KeyError, ValueError):
        return
    if not _in_thailand(lat, lng) or q.get("act") not in ("add", "rep"):
        return
    places = _places(conn, user)
    if not places:
        return _reply(reply, token, "ยังไม่มีตำแหน่งของคุณ ส่งตำแหน่งบ้านมาก่อนได้เลย")
    st = _nearest(conn, lat, lng, at)
    if q["act"] == "add":
        if len(places) >= MAX_PLACES:
            return _reply(reply, token, f"บันทึกครบ {MAX_PLACES} จุดแล้ว เลือก \"แทนที่\" หรือพิมพ์ \"ตำแหน่งของฉัน\" เพื่อลบจุดเดิมก่อน",
                          quick=cards.quick(MENU))
        label = _free_label(places)
        _insert_place(conn, user, lat, lng, label, st, places[0])   # same settings as the first place
        done = f"เพิ่ม \"{label}\" แล้ว"
    else:
        target = next((p for p in places if str(p["id"]) == q.get("id")), None)
        if not target:
            return _reply(reply, token, "ไม่พบจุดที่จะแทนที่ ลองส่งตำแหน่งใหม่อีกครั้ง")
        conn.execute("UPDATE subscriptions SET lat=?, lng=?, last_status=?, last_notified=?, last_early=NULL, last_report_alert=NULL WHERE id=?",
                     (lat, lng, st["status"] if st else None, utc_now() if st else None, target["id"]))
        conn.commit()
        label, done = target["label"], f"แทนที่ \"{target['label']}\" ด้วยตำแหน่งใหม่แล้ว"
    _reply(reply, token, done + "\n\n" + (message({"label": label}, st) if st else "ตอนนี้ยังไม่มีสถานีใกล้จุดนี้ที่ข้อมูลล่าสุด"),
           quick=cards.quick(MENU))


def _list_places(conn, user, token, reply):
    places = _places(conn, user)
    if not places:
        return _reply(reply, token, "ยังไม่มีตำแหน่งที่บันทึกไว้ ส่งตำแหน่งบ้านมาได้เลย (กด + แล้วเลือก \"ตำแหน่งที่ตั้ง\")")
    lines = [f"ตำแหน่งที่ติดตามอยู่ ({len(places)}/{MAX_PLACES})"] + [
        f"{i}. {p['label']} ({p['lat']:.4f}, {p['lng']:.4f})" for i, p in enumerate(places, 1)]
    lines.append("ส่งตำแหน่งใหม่เพื่อเพิ่มหรือแทนที่ หรือแตะปุ่มด้านล่างเพื่อลบ")
    _reply(reply, token, "\n".join(lines), quick=cards.quick([f"ลบจุดที่ {i}" for i in range(1, len(places) + 1)] + ["สถานะ"]))


def _delete_place(conn, user, token, n, reply):
    places = _places(conn, user)
    try:
        target = places[int(n) - 1] if int(n) >= 1 else None
    except (ValueError, IndexError):
        target = None
    if not target:
        return _reply(reply, token, "ไม่พบจุดนั้น พิมพ์ \"ตำแหน่งของฉัน\" เพื่อดูรายการ", quick=cards.quick(MENU))
    conn.execute("DELETE FROM subscriptions WHERE id=?", (target["id"],))
    conn.commit()
    left = len(places) - 1
    _reply(reply, token, f"ลบ \"{target['label']}\" แล้ว" + (f" เหลือ {left} จุด" if left else " ตอนนี้ไม่มีจุดที่ติดตามแล้ว ส่งตำแหน่งใหม่ได้ทุกเมื่อ"),
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
        return _reply(reply, token, _with_manual(GROUP_HELP))
    m = ev.get("message") or {}
    if kind != "message" or m.get("type") != "text":
        return
    raw = (m.get("text") or "").strip()
    text = raw.lower()
    if raw.startswith("ติดตาม"):
        return _group_follow(conn, gid, token, raw[len("ติดตาม"):].strip(), at, reply)
    if text in GROUP_HELP_WORDS:
        return _reply(reply, token, _with_manual(GROUP_HELP))
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

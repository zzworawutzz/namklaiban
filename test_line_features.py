import io
import json
import os
import urllib.error
from datetime import timedelta

import pytest

import cards, core, db, ingest as ig, line_webhook as lw, notify, report
from richmenu.menu import CHAT_BAR_TEXT, COLS, H, LABELS, W, build_menu
from test_report import AT, PROV, seed

NIGHT = AT - timedelta(hours=8)   # 23:30 in Thailand


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "f.db"))
    seed(c)
    return c


def nodes(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from nodes(v)
    elif isinstance(x, list):
        for v in x:
            yield from nodes(v)


def check_flex(card):
    """The rules LINE enforces that we could plausibly break."""
    for n in nodes(card):
        if n.get("type") == "box":
            assert isinstance(n["contents"], list)
        if isinstance(n.get("width"), str) and n["width"].endswith("%"):
            assert 1 <= int(n["width"][:-1]) <= 100
        if n.get("type") == "text":
            assert n["text"] != ""
        if n.get("type") == "uri":
            assert n["uri"].startswith("https://")
    assert len(json.dumps(card)) < 30000


def test_cards_are_valid_flex(conn):
    rows = core.latest(conn, AT)
    st = core.nearest(rows, 14.35, 100.55, 1)[0]
    c1 = cards.station_card("บ้านของคุณ", st, "https://x.example/report.html?province=a")
    c2 = cards.station_card(None, {**st, "pct_of_bank": None, "trend": None, "status": "unknown"}, None)
    c3 = cards.digest_card(report.build(conn, PROV, AT, rows=rows), "2 ต.ค. 2569 07:30 น.", "https://x.example/r")
    for c in (c1, c2, c3):
        check_flex(c)
    assert "footer" in c1 and "footer" not in c2          # a link only when it is https
    assert cards.quick(["a"] * 20)[:1][0]["action"]["text"] == "a" and len(cards.quick(["a"] * 20)) == 13


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"details"))


def test_card_rejected_falls_back_to_plain_text(monkeypatch):
    sent = []

    def fake(url, payload, timeout):
        sent.append(payload["messages"][0])
        if payload["messages"][0]["type"] == "flex":
            raise http_error(400)
    monkeypatch.setattr(notify, "_line_post", fake)
    notify.reply_line("tok", "hello", flex={"type": "bubble"}, quick=cards.quick(["สถานะ"]))
    assert [m["type"] for m in sent] == ["flex", "text"] and sent[1]["text"] == "hello" and "quickReply" in sent[1]
    sent.clear()
    notify.send_line("U1", "hi", flex=None)
    assert [m["type"] for m in sent] == ["text"]


def test_other_errors_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(notify, "_line_post", lambda *a: (_ for _ in ()).throw(http_error(500)))
    with pytest.raises(urllib.error.HTTPError):
        notify.send_line("U1", "hi", flex={"type": "bubble"})
    monkeypatch.setattr(notify, "_line_post", lambda *a: (_ for _ in ()).throw(http_error(400)))
    with pytest.raises(urllib.error.HTTPError):        # a plain-text 400 is a real error: no retry loop
        notify.send_line("U1", "hi")


def set_a(conn, pct, mins_ago):
    conn.execute("INSERT INTO readings VALUES('A',?,1.0,?,?)", (core.iso(AT - timedelta(minutes=mins_ago)), pct, ig.status_of(pct)))
    conn.commit()


def add_sub(conn, **kw):
    cols = {"channel": "stdout", "target": "U1", "lat": 14.35, "lng": 100.55, "label": "บ้าน", "last_status": "normal", **kw}
    conn.execute(f"INSERT INTO subscriptions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values()))
    conn.commit()


def status_of_sub(conn):
    return conn.execute("SELECT last_status FROM subscriptions").fetchone()["last_status"]


def test_alert_only_users_hear_about_alert_transitions_only(conn):
    add_sub(conn, notify_level="alert")
    out = []
    ok = {"stdout": lambda t, m: out.append(m)}
    set_a(conn, 75, 25)
    assert notify.run(conn, AT, ok) == (0, 0) and status_of_sub(conn) == "watch"     # silent, state remembered
    set_a(conn, 95, 20)
    assert notify.run(conn, AT, ok) == (1, 0)                                          # reached alert
    set_a(conn, 75, 15)
    assert notify.run(conn, AT, ok) == (1, 0)                                          # left alert
    set_a(conn, 50, 10)
    assert notify.run(conn, AT, ok) == (0, 0) and status_of_sub(conn) == "normal"     # watch -> normal: not their business


def test_quiet_hours_hold_back_non_alert_until_morning(conn):
    add_sub(conn, quiet=1)
    out = []
    ok = {"stdout": lambda t, m: out.append(m)}
    set_a(conn, 75, 25)
    assert notify.run(conn, NIGHT, ok) == (0, 0) and status_of_sub(conn) == "normal"  # held, not forgotten
    assert notify.run(conn, AT, ok) == (1, 0) and status_of_sub(conn) == "watch"      # sent after 06:00
    set_a(conn, 95, 20)
    assert notify.run(conn, NIGHT, ok) == (1, 0)                                       # alerts always go out
    assert notify.in_quiet_hours(NIGHT) and not notify.in_quiet_hours(AT)


def test_senders_receive_cards_and_simple_senders_still_work(conn):
    add_sub(conn, target="U1", last_status="normal")
    got = {}
    notify.run(conn, AT, {"stdout": lambda t, m, flex=None: got.update(flex=flex)})
    assert got["flex"]["type"] == "bubble"
    conn.execute("UPDATE subscriptions SET last_status='normal'"); conn.commit()
    assert notify.run(conn, AT, {"stdout": lambda t, m: None}) == (1, 0)              # 2-argument sender


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, token, text, flex=None, quick=None):
        self.calls.append(dict(text=text, flex=flex, quick=[q["action"].get("text") or q["action"]["label"] for q in quick] if quick else None))

    @property
    def last(self):
        return self.calls[-1]


def user_event(m, user="U1"):
    return {"type": "message", "replyToken": "r", "source": {"userId": user}, "message": m}


def say(conn, rec, text, user="U1"):
    lw.handle_event(conn, user_event({"type": "text", "text": text}, user), AT, rec)
    return rec.last if rec.calls else None


def test_settings_menu_and_changes(conn):
    rec = Recorder()
    assert "ยังไม่มีตำแหน่ง" in say(conn, rec, "ตั้งค่า")["text"]
    lw.handle_event(conn, user_event({"type": "location", "latitude": 14.36, "longitude": 100.55}), AT, rec)
    assert rec.last["quick"] == lw.MENU
    s = say(conn, rec, "ตั้งค่า")
    assert "ทุกครั้งที่สถานะเปลี่ยน" in s["text"] and {"แจ้งเฉพาะเตือนภัย", "ไม่รบกวนกลางคืน", "ปิดสรุป"} <= set(s["quick"])
    s = say(conn, rec, "แจ้งเฉพาะเตือนภัย")
    assert "เฉพาะตอนที่ถึง" in s["text"] and "แจ้งทุกระดับ" in s["quick"]
    say(conn, rec, "ไม่รบกวนกลางคืน")
    row = conn.execute("SELECT notify_level, quiet FROM subscriptions").fetchone()
    assert (row["notify_level"], row["quiet"]) == ("alert", 1)
    lw.handle_event(conn, user_event({"type": "location", "latitude": 14.9, "longitude": 100.9}), AT, rec)  # a new place
    lw.handle_event(conn, {"type": "postback", "replyToken": "r", "source": {"userId": "U1"},
                           "postback": {"data": "act=add&lat=14.9&lng=100.9"}}, AT, rec)
    rows = conn.execute("SELECT notify_level, quiet FROM subscriptions").fetchall()
    assert len(rows) == 2 and all((r["notify_level"], r["quiet"]) == ("alert", 1) for r in rows)   # settings are shared
    say(conn, rec, "แจ้งทุกระดับ"); say(conn, rec, "แจ้งกลางคืน")
    rows = conn.execute("SELECT notify_level, quiet FROM subscriptions").fetchall()
    assert all((r["notify_level"], r["quiet"]) == ("all", 0) for r in rows)    # a change applies to every place


def test_status_and_report_replies_carry_cards_and_buttons(conn):
    rec = Recorder()
    lw.handle_event(conn, user_event({"type": "location", "latitude": 14.36, "longitude": 100.55}), AT, rec)
    st, rp = say(conn, rec, "สถานะ"), say(conn, rec, "รายงาน")
    assert st["flex"]["type"] == "bubble" and "เตือนภัย" in st["text"] and st["quick"] == lw.MENU
    assert rp["flex"]["type"] == "bubble" and "สรุปสถานการณ์น้ำ จ.พระนครศรีอยุธยา" in rp["text"]
    check_flex(st["flex"]); check_flex(rp["flex"])


def group_event(kind, text=None, gid="C123", **extra):
    ev = {"type": kind, "replyToken": "r", "source": {"type": "group", "groupId": gid}, **extra}
    if text is not None:
        ev["message"] = {"type": "text", "text": text}
    return ev


def test_group_flow(conn):
    rec = Recorder()
    h = lambda ev: lw.handle_event(conn, ev, AT, rec)
    h(group_event("join"))
    assert "ติดตาม" in rec.last["text"]
    n = len(rec.calls)
    h(group_event("message", "สวัสดีทุกคน")); h(group_event("message", "ไปกินข้าวกัน"))
    assert len(rec.calls) == n                                                         # ordinary chat: silence
    h(group_event("message", "ติดตาม ภูเก็ต")); assert "ไม่พบจังหวัด" in rec.last["text"]
    h(group_event("message", "ติดตาม")); assert "ชื่อจังหวัด" in rec.last["text"]
    conn.execute("INSERT INTO stations(id,name,province,lat,lng) VALUES('E','x','นครศรีธรรมราช',8.4,99.9)"); conn.commit()
    h(group_event("message", "ติดตาม นครศรี")); assert "พบหลายจังหวัด" in rec.last["text"]
    h(group_event("message", "ติดตาม อยุธยา"))
    sub = conn.execute("SELECT * FROM subscriptions WHERE target='C123'").fetchone()
    assert sub["label"] == "จ.พระนครศรีอยุธยา" and 14 < sub["lat"] < 15 and "สรุปสถานการณ์น้ำ" in rec.last["text"]
    h(group_event("message", "รายงาน")); assert "จ.พระนครศรีอยุธยา" in rec.last["text"] and rec.last["flex"]
    assert notify.run(conn, AT, {"line": lambda t, m: 1 / 0}) == (0, 0)               # no status-change pushes to groups
    out = []
    assert notify.run_digest(conn, AT, {"line": lambda t, m, flex=None: out.append((t, m))}) == (1, 0)
    assert out[0][0] == "C123" and "จ.พระนครศรีอยุธยา" in out[0][1]
    h(group_event("message", "ปิดสรุป"))
    assert conn.execute("SELECT digest FROM subscriptions").fetchone()["digest"] == 0
    h(group_event("leave"))
    assert conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"] == 0


def test_group_cap_and_rooms(conn, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(lw, "MAX_SUBSCRIBERS", 1)
    lw.handle_event(conn, group_event("message", "ติดตาม อยุธยา", gid="C1"), AT, rec)
    lw.handle_event(conn, group_event("message", "ติดตาม อยุธยา", gid="C2"), AT, rec)
    assert "เต็ม" in rec.last["text"]
    room = {"type": "message", "replyToken": "r", "source": {"type": "room", "roomId": "R9"},
            "message": {"type": "text", "text": "ติดตาม เชียงใหม่"}}
    monkeypatch.setattr(lw, "MAX_SUBSCRIBERS", 5)
    lw.handle_event(conn, room, AT, rec)
    assert conn.execute("SELECT label FROM subscriptions WHERE target='R9'").fetchone()["label"] == "จ.เชียงใหม่"
    assert notify.is_group({"channel": "line", "target": "R9"}) and not notify.is_group({"channel": "line", "target": "U1"})


def test_rich_menu_definition_and_image():
    m = build_menu()
    assert len(CHAT_BAR_TEXT) <= 14 and m["size"] == {"width": W, "height": H} and len(m["areas"]) == COLS == len(LABELS)
    xs = [(a["bounds"]["x"], a["bounds"]["x"] + a["bounds"]["width"]) for a in m["areas"]]
    assert xs[0][0] == 0 and xs[-1][1] == W and all(xs[i][1] == xs[i + 1][0] for i in range(len(xs) - 1))
    texts = {a["action"].get("text") for a in m["areas"]}
    assert {"สถานะ", "รายงาน", "ตั้งค่า"} <= texts                                    # buttons send commands the bot knows
    assert "สถานะ" in lw.STATUS and "รายงาน" in lw.REPORT and "ตั้งค่า" in lw.SETTINGS
    assert next(a for a in m["areas"] if a["action"]["type"] == "uri")["action"]["uri"].startswith("https://line.me/")
    png = os.path.join(os.path.dirname(__file__), "richmenu", "richmenu.png")
    assert os.path.getsize(png) < 1024 * 1024
    assert open(png, "rb").read(8) == b"\x89PNG\r\n\x1a\n"

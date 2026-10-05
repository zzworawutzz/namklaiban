import base64, hashlib, hmac, json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api, db, ingest as ig, line_webhook as lw
from conftest import real_rows

AT = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)  # station 505018 (alert) is fresh
SECRET = "test-secret"


def sign(body: bytes, secret=SECRET):
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def loc(lat=14.2, lng=99.0, user="U1"):
    return {"type": "message", "replyToken": "r", "source": {"userId": user},
            "message": {"type": "location", "latitude": lat, "longitude": lng}}


def text(t, user="U1"):
    return {"type": "message", "replyToken": "r", "source": {"userId": user}, "message": {"type": "text", "text": t}}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "l.db"))
    ig.init_db(c); ig.save(c, real_rows())
    return c


def run(conn, ev):
    out = []
    lw.handle_event(conn, ev, AT, lambda tok, t: out.append(t))
    return out


def subs(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM subscriptions")]


def test_signature_check():
    body = b'{"events":[]}'
    assert lw.valid_signature(SECRET, body, sign(body))
    assert not lw.valid_signature(SECRET, body, sign(body, "other"))
    assert not lw.valid_signature(SECRET, body, None) and not lw.valid_signature("", body, sign(body))


def test_location_subscribes_replies_with_current_status_and_does_not_double_send(conn):
    out = run(conn, loc())
    assert len(subs(conn)) == 1 and subs(conn)[0]["target"] == "U1"
    assert "บันทึกตำแหน่งแล้ว" in out[0] and "เตือนภัย" in out[0] and "บ้านปากแซง" in out[0]
    import notify
    assert notify.run(conn, AT, {"line": lambda t, m: 1 / 0}) == (0, 0)  # baseline == current: stays quiet


def test_resend_location_asks_before_changing_anything(conn):
    run(conn, loc(14.2, 99.0))
    out = run(conn, loc(14.5, 101.0))
    assert "จะให้ทำอย่างไร" in out[0]
    s = subs(conn)
    assert len(s) == 1 and s[0]["lat"] == 14.2                      # nothing changed yet


def test_cancel_status_help_and_unfollow(conn):
    assert "ยังไม่มีตำแหน่ง" in run(conn, text("สถานะ"))[0]
    run(conn, loc())
    assert "เตือนภัย" in run(conn, text("สถานะ"))[0]
    assert "ลบตำแหน่ง" in run(conn, text("ยกเลิก"))[0] and subs(conn) == []
    assert "ส่งตำแหน่งบ้าน" in run(conn, text("สวัสดี"))[0]
    run(conn, loc())
    lw.handle_event(conn, {"type": "unfollow", "source": {"userId": "U1"}}, AT, lambda *a: 1 / 0)
    assert subs(conn) == []


def test_rejects_bad_coordinates_and_caps_subscribers(conn, monkeypatch):
    assert "นอกประเทศไทย" in run(conn, loc(40.0, 100.0))[0] and subs(conn) == []
    assert "อ่านตำแหน่งไม่ได้" in run(conn, {**loc(), "message": {"type": "location"}})[0]
    monkeypatch.setattr(lw, "MAX_SUBSCRIBERS", 1)
    run(conn, loc(user="U1"))
    assert "เต็ม" in run(conn, loc(user="U2"))[0]
    assert "จะให้ทำอย่างไร" in run(conn, loc(14.5, 101.0, user="U1"))[0]  # existing user can still add or replace


def test_endpoint_signature_and_end_to_end(tmp_path, monkeypatch):
    db_path = tmp_path / "e.db"
    c = db.connect(str(db_path)); ig.init_db(c); ig.save(c, real_rows()); c.commit(); c.close()
    monkeypatch.setenv("WATER_DB", str(db_path))
    monkeypatch.setattr(api, "now", lambda: AT)
    sent = []
    monkeypatch.setattr(api.notify, "reply_line", lambda tok, t, flex=None, quick=None: sent.append(t))
    cl = TestClient(api.app)
    body = json.dumps({"events": [loc()]}).encode()
    assert cl.post("/api/line/webhook", content=body).status_code == 503            # not configured
    monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
    assert cl.post("/api/line/webhook", content=body).status_code == 401            # no signature
    assert cl.post("/api/line/webhook", content=body, headers={"X-Line-Signature": sign(body, "x")}).status_code == 401
    ok = cl.post("/api/line/webhook", content=body, headers={"X-Line-Signature": sign(body)})
    assert ok.status_code == 200 and "บันทึกตำแหน่งแล้ว" in sent[0]
    empty = b'{"events":[]}'                                                          # LINE console "Verify" button
    assert cl.post("/api/line/webhook", content=empty, headers={"X-Line-Signature": sign(empty)}).status_code == 200
    assert cl.post("/api/line/webhook", content=b"{", headers={"X-Line-Signature": sign(b"{")}).status_code == 400


def test_endpoint_passes_cards_and_buttons_through_to_line(tmp_path, monkeypatch):
    """Regression: the real endpoint must hand flex cards and quick-reply buttons to reply_line
    (they were silently dropped once, so users only ever saw plain text)."""
    db_path = tmp_path / "x.db"
    c = db.connect(str(db_path)); ig.init_db(c); ig.save(c, real_rows()); c.commit(); c.close()
    monkeypatch.setenv("WATER_DB", str(db_path))
    monkeypatch.setattr(api, "now", lambda: AT)
    monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
    got = []
    monkeypatch.setattr(api.notify, "reply_line", lambda tok, t, flex=None, quick=None: got.append((t, flex, quick)))
    cl = TestClient(api.app)

    def post(ev):
        body = json.dumps({"events": [ev]}).encode()
        assert cl.post("/api/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).status_code == 200
    post(loc())
    post(text("สถานะ"))
    post(text("ตั้งค่า"))
    status, settings = got[1], got[2]
    assert status[1]["type"] == "bubble" and [q["action"]["text"] for q in status[2]] == ["สถานะ", "รายงาน", "แจ้งน้ำท่วม", "ศูนย์พักพิง", "แชร์ให้ญาติ", "ตำแหน่งของฉัน", "ตั้งค่า", "วิธีใช้"]
    assert settings[1] is None and "แจ้งเฉพาะเตือนภัย" in [q["action"]["text"] for q in settings[2]]


def test_add_friend_link_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "a.db"))
    cl = TestClient(api.app)
    monkeypatch.setattr(api.notify, "bot_basic_id", lambda: None)
    assert cl.get("/api/line/add-friend").status_code == 404
    monkeypatch.setattr(api.notify, "bot_basic_id", lambda: "@123abcde")
    r = cl.get("/api/line/add-friend")
    assert r.status_code == 200 and r.json() == {"url": "https://line.me/R/ti/p/%40123abcde"}
    assert "max-age" in r.headers["cache-control"]


def test_bot_basic_id_is_cached_and_failures_are_not(monkeypatch):
    import io
    import notify
    notify._basic_id_cache.clear()
    monkeypatch.setenv("LINE_CHANNEL_TOKEN", "t" * 40)
    calls = []

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: (calls.append(1), R(b'{"basicId": "@abc"}'))[1])
    assert notify.bot_basic_id() == "@abc" and notify.bot_basic_id() == "@abc" and len(calls) == 1
    notify._basic_id_cache.clear()
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert notify.bot_basic_id() is None and "id" not in notify._basic_id_cache
    notify._basic_id_cache.clear()


def test_my_id_command_shows_the_senders_id():
    out = []
    lw.handle_event(None, {"type": "message", "replyToken": "t", "source": {"userId": "U123"},
                                     "message": {"type": "text", "text": "ไอดีของฉัน"}}, None,
                              lambda tok, text, flex=None, quick=None: out.append(text))
    assert "U123" in out[0] and "ADMIN_LINE_ID" in out[0]


def run_full(conn, ev):
    out = []
    lw.handle_event(conn, ev, AT, lambda tok, t, flex=None, quick=None: out.append({"text": t, "flex": flex, "quick": quick}))
    return out


def test_typing_a_station_name_answers_for_that_place(conn):
    (r,) = run_full(conn, text("บ้านปากแซง"))
    assert "บ้านปากแซง" in r["text"] and "เตือนภัย" in r["text"] and r["flex"]["type"] == "bubble"
    assert "ส่งตำแหน่งบ้านของคุณมา" in r["text"]                      # nobody saved yet: tell them how to get alerts
    assert not subs(conn)                                              # looking a place up saves nothing


def test_typing_a_province_sends_the_province_report(conn):
    (r,) = run_full(conn, text("กาญจนบุรี"))
    assert "กาญจนบุรี" in r["text"] and r["flex"]["type"] == "bubble"


def test_several_matches_become_buttons_and_tapping_one_shows_the_place(conn):
    (r,) = run_full(conn, text("บาง"))
    assert r["quick"] and len(r["quick"]) <= 5 and "เลือกที่ต้องการดู" in r["text"]
    data = r["quick"][0]["action"]["data"]
    assert data.startswith("act=look&") and len(data) < 300
    (r2,) = run_full(conn, {"type": "postback", "replyToken": "r", "source": {"userId": "U1"}, "postback": {"data": data}})
    assert r2["flex"] or "ไม่มีสถานีใกล้" in r2["text"]


def test_chatter_and_unknown_text_still_get_the_instructions(conn):
    for t in ("สวัสดี", "ok", "xyzzy"):
        (r,) = run_full(conn, text(t))
        assert "ส่งตำแหน่งบ้านของคุณมา" in r["text"] and r["flex"] is None


def test_lookup_ignores_bad_postbacks(conn):
    for d in ("act=look&lat=abc&lng=1", "act=look&lat=40&lng=10", "act=prov"):
        assert run_full(conn, {"type": "postback", "replyToken": "r", "source": {"userId": "U1"}, "postback": {"data": d}}) == []


def test_snooze_command_button_and_cancel(conn):
    assert "ยังไม่มีตำแหน่ง" in run(conn, text("พักแจ้งเตือน"))[0]
    run(conn, loc())
    (r,) = run_full(conn, text("พักแจ้งเตือน 6 ชม."))
    assert "พักถึง 05:30 น." in r["text"] and "เตือนภัย" in r["text"]            # 16:30Z + 6 h = 22:30Z = 05:30 Thai time
    assert subs(conn)[0]["snooze_until"] == "2026-10-01T22:30:00Z"
    (s,) = run_full(conn, text("ตั้งค่า"))
    assert "พักแจ้งเตือน: พักถึง" in s["text"] and any(q["action"]["text"] == "เลิกพัก" for q in s["quick"])
    run(conn, {"type": "postback", "replyToken": "r", "source": {"userId": "U1"}, "postback": {"data": "act=snooze"}})
    assert subs(conn)[0]["snooze_until"] == "2026-10-01T22:30:00Z"
    assert "กลับมาแจ้งเตือน" in run(conn, text("เลิกพัก"))[0] and subs(conn)[0]["snooze_until"] is None


def test_new_followers_start_without_the_daily_summary_and_can_turn_it_on(conn):
    (r,) = run_full(conn, loc())
    assert subs(conn)[0]["digest"] == 0
    assert "ปิดไว้ก่อน" in r["text"] and 'พิมพ์ "เปิดสรุป"' in r["text"]
    assert "จะส่งสรุปสถานการณ์" in run(conn, text("เปิดสรุป"))[0] and subs(conn)[0]["digest"] == 1


def test_the_old_default_is_one_switch_away(conn, monkeypatch):
    monkeypatch.setattr(lw, "NEW_USER_DIGEST", 1)
    (r,) = run_full(conn, loc())
    assert subs(conn)[0]["digest"] == 1 and "ปิดไว้ก่อน" not in r["text"] and 'พิมพ์ "เปิดสรุป"' not in r["text"]


def test_a_signup_while_the_quota_is_nearly_gone_is_told_so(conn, monkeypatch):
    import notify
    monkeypatch.setattr(notify, "line_quota", lambda: (300, 296))
    (r,) = run_full(conn, loc())
    assert "โควตาข้อความของบอตเดือนนี้ใกล้เต็ม" in r["text"] and "เฉพาะระดับเตือนภัย" in r["text"]
    assert "ใกล้เต็ม" in run(conn, text("เปิดสรุป"))[0]                                    # turning the summary on says the same
    conn.execute("DELETE FROM subscriptions"); conn.execute("DELETE FROM alert_state"); conn.commit()
    monkeypatch.setattr(notify, "line_quota", lambda: (300, 20))
    (r,) = run_full(conn, loc())
    assert "ใกล้เต็ม" not in r["text"]


def test_share_with_family_gives_a_link_and_a_button_per_saved_place(conn, monkeypatch):
    import urllib.parse
    monkeypatch.setenv("PUBLIC_URL", "https://nkb.example")
    assert "ส่งตำแหน่งบ้านของคุณมาก่อน" in run(conn, text("แชร์ให้ญาติ"))[0]               # nobody saved yet
    run(conn, loc(14.2, 99.0))
    (r,) = run_full(conn, text("แชร์ให้ญาติ"))
    assert "https://nkb.example/?lat=14.200&lng=99.000" in r["text"] and "ปัดราว 100 ม." in r["text"]
    buttons = [n for n in r["flex"]["footer"]["contents"] if n["type"] == "button"]
    assert len(buttons) == 1 and buttons[0]["action"]["type"] == "uri"
    uri = buttons[0]["action"]["uri"]
    assert uri.startswith("https://line.me/R/share?text=") and len(uri) < 1000
    assert "https://nkb.example/?lat=14.200&lng=99.000" in urllib.parse.unquote(uri)
    assert "แชร์ให้ญาติ" in lw.MENU and r["quick"] is not None
    # a second place gets its own button
    run(conn, loc(14.9, 100.9))
    run(conn, {"type": "postback", "replyToken": "r", "source": {"userId": "U1"}, "postback": {"data": "act=add&lat=14.9&lng=100.9"}})
    (r2,) = run_full(conn, text("แชร์"))
    assert len([n for n in r2["flex"]["footer"]["contents"] if n["type"] == "button"]) == 2 and "lat=14.900&lng=100.900" in r2["text"]


def test_share_says_what_to_do_instead_when_the_web_address_is_not_set(conn, monkeypatch):
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
    run(conn, loc())
    assert "แชร์ลิงก์จุดนี้" in run(conn, text("แชร์ให้ญาติ"))[0]

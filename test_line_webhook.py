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
    assert status[1]["type"] == "bubble" and [q["action"]["text"] for q in status[2]] == ["สถานะ", "รายงาน", "แจ้งน้ำท่วม", "ศูนย์พักพิง", "ตำแหน่งของฉัน", "ตั้งค่า"]
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

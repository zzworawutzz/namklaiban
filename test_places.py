from datetime import timedelta

import pytest

import db, ingest as ig, line_webhook as lw, notify
from conftest import real_rows
from test_floodreports import AT


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "p.db")); ig.init_db(c); ig.save(c, real_rows())
    return c


class Rec:
    def __init__(self): self.calls = []
    def __call__(self, token, text, flex=None, quick=None): self.calls.append((text, flex, quick))
    @property
    def last(self): return self.calls[-1]


def msg(m, user="U1"):
    return {"type": "message", "replyToken": "r", "source": {"userId": user}, "message": m}


def loc(lat, lng, user="U1"):
    return msg({"type": "location", "latitude": lat, "longitude": lng}, user)


def say(conn, rec, t, user="U1"):
    lw.handle_event(conn, msg({"type": "text", "text": t}, user), AT, rec)


def pick(conn, rec, n, user="U1"):
    """Tap the n-th quick-reply button of the last bot message (a postback)."""
    data = rec.last[2][n]["action"]["data"]
    lw.handle_event(conn, {"type": "postback", "replyToken": "r", "source": {"userId": user}, "postback": {"data": data}}, AT, rec)


def places(conn, user="U1"):
    return [dict(r) for r in conn.execute("SELECT * FROM subscriptions WHERE target=? ORDER BY id", (user,))]


def test_second_location_offers_add_or_replace_and_add_copies_settings(conn):
    rec = Rec()
    lw.handle_event(conn, loc(14.2, 99.0), AT, rec)
    say(conn, rec, "ไม่รบกวนกลางคืน"); say(conn, rec, "ปิดสรุป")
    lw.handle_event(conn, loc(14.5, 100.6), AT, rec)
    labels = [q["action"]["label"] for q in rec.last[2]]
    assert labels == ["เพิ่มเป็นจุดที่ 2", "แทนที่ บ้านของคุณ"] and rec.last[2][0]["action"]["type"] == "postback"
    pick(conn, rec, 0)
    p = places(conn)
    assert [x["label"] for x in p] == ["บ้านของคุณ", "จุดที่ 2"] and (p[1]["quiet"], p[1]["digest"]) == (1, 0)
    assert "เพิ่ม \"จุดที่ 2\" แล้ว" in rec.last[0]


def test_replace_moves_the_chosen_place_and_resets_its_state(conn):
    rec = Rec()
    lw.handle_event(conn, loc(14.2, 99.0), AT, rec)
    conn.execute("UPDATE subscriptions SET last_early='2026-10-01T10:00:00Z'"); conn.commit()
    lw.handle_event(conn, loc(14.5, 100.6), AT, rec)
    pick(conn, rec, 1)                                                  # "แทนที่ บ้านของคุณ"
    p = places(conn)
    assert len(p) == 1 and p[0]["lat"] == 14.5 and p[0]["last_early"] is None


def test_limit_of_three_places_and_same_spot_is_not_duplicated(conn):
    rec = Rec()
    lw.handle_event(conn, loc(14.2, 99.0), AT, rec)
    for lat in (14.5, 15.0):
        lw.handle_event(conn, loc(lat, 100.6), AT, rec); pick(conn, rec, 0)
    assert len(places(conn)) == 3
    lw.handle_event(conn, loc(16.0, 101.0), AT, rec)
    assert [q["action"]["label"] for q in rec.last[2]] == ["แทนที่ บ้านของคุณ", "แทนที่ จุดที่ 2", "แทนที่ จุดที่ 3"]
    lw.handle_event(conn, loc(14.2005, 99.0005), AT, rec)               # ~70 m from home
    assert "บันทึกไว้แล้ว" in rec.last[0] and len(places(conn)) == 3
    # a stale "add" button tapped after the limit is reached does not add a fourth
    lw.handle_event(conn, {"type": "postback", "replyToken": "r", "source": {"userId": "U1"},
                           "postback": {"data": "act=add&lat=17.0&lng=102.0"}}, AT, rec)
    assert len(places(conn)) == 3 and "ครบ 3 จุด" in rec.last[0]


def test_postback_cannot_replace_someone_elses_place_or_use_bad_data(conn):
    rec = Rec()
    lw.handle_event(conn, loc(14.2, 99.0, "U1"), AT, rec); lw.handle_event(conn, loc(14.3, 99.1, "U2"), AT, rec)
    other = places(conn, "U2")[0]["id"]
    for data in (f"act=rep&id={other}&lat=14.9&lng=100.0", "act=rep&lat=abc&lng=1", "act=zzz&lat=14.9&lng=100.0",
                 "act=add&lat=40.0&lng=100.0"):
        lw.handle_event(conn, {"type": "postback", "replyToken": "r", "source": {"userId": "U1"}, "postback": {"data": data}}, AT, rec)
    assert places(conn, "U2")[0]["lat"] == 14.3 and len(places(conn, "U1")) == 1 and places(conn, "U1")[0]["lat"] == 14.2


def test_list_delete_and_status_cover_every_place(conn):
    rec = Rec()
    lw.handle_event(conn, loc(14.2, 99.0), AT, rec)
    lw.handle_event(conn, loc(14.5, 100.6), AT, rec); pick(conn, rec, 0)
    say(conn, rec, "ตำแหน่งของฉัน")
    assert "(2/3)" in rec.last[0] and "จุดที่ 2" in rec.last[0] and "ลบจุดที่ 2" in [q["action"]["label"] for q in rec.last[2]]
    say(conn, rec, "สถานะ")
    assert rec.last[0].count("น้ำใกล้บ้านฉัน") == 2 and rec.last[1] is None       # both places, plain text
    say(conn, rec, "ลบจุดที่ 9"); assert "ไม่พบจุดนั้น" in rec.last[0] and len(places(conn)) == 2
    say(conn, rec, "ลบจุดที่ 1"); assert len(places(conn)) == 1 and places(conn)[0]["label"] == "จุดที่ 2"
    say(conn, rec, "ยกเลิก"); assert places(conn) == []


def test_user_cap_counts_people_not_places(conn, monkeypatch):
    monkeypatch.setattr(lw, "MAX_SUBSCRIBERS", 1)
    rec = Rec()
    lw.handle_event(conn, loc(14.2, 99.0), AT, rec)
    lw.handle_event(conn, loc(14.5, 100.6), AT, rec); pick(conn, rec, 0)
    assert len(places(conn)) == 2                                            # one person, two places: still allowed
    lw.handle_event(conn, loc(14.2, 99.0, "U2"), AT, rec)
    assert "เต็ม" in rec.last[0]


def test_alerts_use_the_label_of_each_place_and_morning_report_is_sent_once_per_province(conn):
    rec = Rec()
    lw.handle_event(conn, loc(14.2, 99.0), AT, rec)
    lw.handle_event(conn, loc(14.25, 99.05), AT, rec); pick(conn, rec, 0)   # a second place ~7 km away, same province
    conn.execute("UPDATE subscriptions SET last_status=NULL"); conn.commit()
    out = []
    sent, failed = notify.run(conn, AT, {"line": lambda t, m: out.append(m)})
    assert sent == 2 and any("(บ้านของคุณ)" in m for m in out) and any("(จุดที่ 2)" in m for m in out)
    morning = AT.replace(hour=1, minute=0) + timedelta(days=1)             # 08:00 Thai
    digests = []
    notify.run_digest(conn, morning, {"line": lambda t, m: digests.append(m)})
    assert len(digests) == 1                                                  # one report for the province, not one per place
    assert all(p["last_digest"] for p in places(conn))


def test_manual_command_replies_with_help_and_a_link(conn, monkeypatch):
    rec = Rec()
    monkeypatch.setenv("PUBLIC_URL", "https://example.test")
    say(conn, rec, "วิธีใช้")
    assert "https://example.test/help.html" in rec.last[0] and "ส่งตำแหน่งบ้าน" in rec.last[0]
    monkeypatch.delenv("PUBLIC_URL", raising=False); monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
    say(conn, rec, "คู่มือ")
    assert "help.html" not in rec.last[0]            # no public URL configured: help text only


def test_manual_link_is_in_the_welcome_fallback_and_group_messages(conn, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://example.test")
    rec = Rec()
    lw.handle_event(conn, {"type": "follow", "replyToken": "r", "source": {"userId": "U1"}}, AT, rec)
    assert "https://example.test/help.html" in rec.last[0]
    say(conn, rec, "สวัสดี")                                          # anything we do not understand
    assert "https://example.test/help.html" in rec.last[0]
    group = {"replyToken": "r", "source": {"groupId": "C1"}}
    lw.handle_event(conn, {**group, "type": "join"}, AT, rec)
    assert "https://example.test/help.html" in rec.last[0]
    lw.handle_event(conn, {**group, "type": "message", "message": {"type": "text", "text": "ช่วยเหลือ"}}, AT, rec)
    assert "https://example.test/help.html" in rec.last[0]

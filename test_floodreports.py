from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api, db, floodreports as fr, ingest as ig, line_webhook as lw
from conftest import real_rows

AT = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "f.db"))
    ig.init_db(c)
    return c


def test_add_and_list_expires_after_12_hours(conn):
    fr.add(conn, 14.35, 100.57, 3, "  น้ำ   ท่วมหน้าตลาด ", "web", "a", AT)
    got = fr.active(conn, AT + timedelta(minutes=5))
    assert len(got) == 1 and got[0]["label"] == "ท่วมถึงเข่า" and got[0]["note"] == "น้ำ ท่วมหน้าตลาด"
    assert got[0]["age_min"] == 5 and got[0]["confirmed"] == 1
    assert "who" not in got[0]
    assert fr.active(conn, AT + timedelta(hours=12, minutes=1)) == []


def test_rejects_bad_level_outside_thailand_and_limits(conn):
    for args in [(14.3, 100.5, 9), (51.5, 0.1, 2)]:
        with pytest.raises(fr.Rejected):
            fr.add(conn, *args, None, "web", "a", AT)
    fr.add(conn, 14.3, 100.5, 2, None, "web", "a", AT)
    with pytest.raises(fr.Rejected) as e:   # same person, same spot, minutes later
        fr.add(conn, 14.3001, 100.5001, 2, None, "web", "a", AT + timedelta(minutes=2))
    assert e.value.code == 429
    for i in range(4):  # five per hour in total, each at a different place
        fr.add(conn, 14.4 + i * 0.1, 100.5, 2, None, "web", "a", AT + timedelta(minutes=20 + i))
    with pytest.raises(fr.Rejected):
        fr.add(conn, 15.9, 100.5, 2, None, "web", "a", AT + timedelta(minutes=30))
    fr.add(conn, 15.9, 100.5, 2, None, "web", "b", AT + timedelta(minutes=30))  # someone else is unaffected


def test_nearby_reports_from_different_people_confirm_each_other(conn):
    fr.add(conn, 14.3500, 100.5700, 2, None, "web", "a", AT)
    fr.add(conn, 14.3510, 100.5705, 3, None, "web", "b", AT)   # ~120 m away
    fr.add(conn, 14.9000, 100.9000, 3, None, "web", "c", AT)   # far away
    near = {r["lat"]: r["confirmed"] for r in fr.active(conn, AT)}
    assert near[14.35] == 2 and near[14.351] == 2 and near[14.9] == 1


def test_three_different_flags_hide_a_report_and_votes_are_unique(conn):
    fr.add(conn, 14.3, 100.5, 2, None, "web", "a", AT)
    rid = fr.active(conn, AT)[0]["id"]
    assert fr.flag(conn, rid, "x") and fr.flag(conn, rid, "x")   # the same person twice counts once
    assert len(fr.active(conn, AT)) == 1
    fr.flag(conn, rid, "y"); fr.flag(conn, rid, "z")
    assert fr.active(conn, AT) == []
    assert fr.flag(conn, 999, "x") is False


def test_prune_removes_old_reports(conn):
    fr.add(conn, 14.3, 100.5, 2, None, "web", "a", AT - timedelta(hours=60))
    fr.add(conn, 14.4, 100.5, 2, None, "web", "a", AT)
    assert fr.prune(conn, AT) == 1


def test_api_post_list_flag_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(api, "now", lambda: AT)
    cl = TestClient(api.app)
    ok = cl.post("/api/flood-reports", json={"lat": 14.35, "lng": 100.57, "level": 2, "note": "x" * 100})
    assert ok.status_code == 201
    assert cl.post("/api/flood-reports", json={"lat": 14.35, "lng": 100.57, "level": 7}).status_code == 422
    assert cl.post("/api/flood-reports", json={"lat": 40.0, "lng": 100.57, "level": 2}).status_code == 400
    assert cl.post("/api/flood-reports", json={"lat": 14.35, "lng": 100.57, "level": 2}).status_code == 429  # duplicate
    rows = cl.get("/api/flood-reports").json()
    assert len(rows) == 1 and "who" not in rows[0]
    assert cl.post(f"/api/flood-reports/{rows[0]['id']}/flag").status_code == 200
    assert cl.post("/api/flood-reports/12345/flag").status_code == 404


def test_api_uses_forwarded_ip_as_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(api, "now", lambda: AT)
    cl = TestClient(api.app)
    body = {"lat": 14.35, "lng": 100.57, "level": 2}
    assert cl.post("/api/flood-reports", json=body, headers={"x-forwarded-for": "1.1.1.1"}).status_code == 201
    assert cl.post("/api/flood-reports", json=body, headers={"x-forwarded-for": "1.1.1.1, 9.9.9.9"}).status_code == 429
    assert cl.post("/api/flood-reports", json=body, headers={"x-forwarded-for": "2.2.2.2"}).status_code == 201


# ---- LINE ----

def ev(msg, user="U9"):
    return {"type": "message", "replyToken": "r", "source": {"userId": user}, "message": msg}


def say(conn, m):
    out = []
    lw.handle_event(conn, ev(m), AT, lambda tok, t, flex=None, quick=None: out.append((t, quick)))
    return out[0]


def test_line_flow_level_then_location_records_a_report_and_does_not_subscribe(conn):
    ig.save(conn, real_rows())
    t, q = say(conn, {"type": "text", "text": "แจ้งน้ำท่วม"})
    assert [b["action"]["text"] for b in q] == ["ท่วมเปียกแฉะ", "ท่วมข้อเท้า", "ท่วมเข่า", "ท่วมเกินเอว"]
    t, q = say(conn, {"type": "text", "text": "ท่วมเข่า"})
    assert q[0]["action"]["type"] == "location"
    t, q = say(conn, {"type": "location", "latitude": 14.35, "longitude": 100.57})
    assert "ท่วมถึงเข่า" in t
    assert fr.active(conn, AT)[0]["level"] == 3 and fr.active(conn, AT)[0]["src"] == "line"
    assert conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"] == 0


def test_line_location_without_pending_level_still_subscribes_and_pending_expires(conn):
    ig.save(conn, real_rows())
    say(conn, {"type": "text", "text": "ท่วมเกินเอว"})
    fr.set_pending(conn, "U9", 4, AT - timedelta(minutes=30))     # too old
    say(conn, {"type": "location", "latitude": 14.2, "longitude": 99.0})
    assert fr.active(conn, AT) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"] == 1

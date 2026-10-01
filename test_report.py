from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api, core, db, ingest as ig, line_webhook as lw, notify, report

AT = datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc)  # 07:30 in Thailand
PROV = "พระนครศรีอยุธยา"


def seed(conn, extra_provinces=()):
    """A=alert now (was 80% a day ago, so newly over bank), B=watch & steady, C=normal, D=other province."""
    ig.init_db(conn)
    stations = [("A", "สะพานหัวเวียง", PROV, 14.35, 100.55), ("B", "บางบาล", PROV, 14.40, 100.50),
                ("C", "ท่าเรือ", PROV, 14.60, 100.70), ("D", "เมืองเชียงใหม่", "เชียงใหม่", 18.79, 98.98)]
    for sid, name, prov, lat, lng in stations:
        conn.execute("INSERT INTO stations(id,name,province,lat,lng) VALUES(?,?,?,?,?)", (sid, name, prov, lat, lng))

    def add(sid, hours_ago, pct):
        ts = core.iso(AT - timedelta(hours=hours_ago, minutes=30 if hours_ago == 0 else 0))
        conn.execute("INSERT INTO readings VALUES(?,?,?,?,?)", (sid, ts, 1.0, pct, ig.status_of(pct)))

    for h in range(0, 49):  # hourly for two days
        add("A", h, 120 - h * 1.6)      # 120 now, 81.6 a day ago (watch), rising 1.6%/h
        add("B", h, 75)
        add("C", h, 30)
        add("D", h, 50)
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "r.db"))
    seed(c)
    return c


def test_build_report_numbers(conn):
    rep = report.build(conn, PROV, AT)
    assert rep["stations"] == 3 and rep["counts"] == {"alert": 1, "watch": 1, "normal": 1, "unknown": 0}
    assert rep["over_bank"] == 1 and [s["id"] for s in rep["newly_over_bank"]] == ["A"]
    assert rep["delta_24h"] == {"alert": 1, "watch": -1}   # A moved watch -> alert; B unchanged
    assert [t["id"] for t in rep["top"]] == ["A", "B", "C"]
    assert rep["rising"][0]["id"] == "A" and rep["rising"][0]["trend_pct_per_hr"] > 0.5
    assert len(rep["series"]) == 14 and rep["series"][-1]["day"] == "2026-10-02"
    assert rep["series"][-1]["reporting"] == 3 and rep["series"][-1]["alert"] == 1
    assert report.build(conn, "ไม่มีจังหวัดนี้", AT) is None


def test_delta_vs_yesterday_counts_stations_that_crossed(conn):
    rep = report.build(conn, PROV, AT)
    assert rep["delta_24h"] == {"alert": 1, "watch": -1}
    conn.execute("DELETE FROM readings WHERE ts < ?", (core.iso(AT - timedelta(hours=3)),))  # no history -> no comparison
    assert report.build(conn, PROV, AT)["delta_24h"] is None


def test_text_has_headline_delta_and_top(conn):
    text = report.build(conn, PROV, AT)["text"]
    for part in ("สรุปสถานการณ์น้ำ จ.พระนครศรีอยุธยา", "2 ต.ค. 2569 07:30 น.", "เตือนภัย 1", "ล้นตลิ่งแล้ว 1",
                 "1. สะพานหัวเวียง 120%", "กำลังสูงขึ้นเร็ว", "1784"):
        assert part in text, part
    assert "เทียบ 24 ชม. ก่อน" in text and len(text) < 4000


def test_digest_window_once_per_day_retry_and_opt_out(conn):
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('stdout','U1',14.36,100.55,'บ้าน')")
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,digest) VALUES('stdout','U2',14.36,100.55,'x',0)")
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('stdout','U3',18.8,98.98,'cm')")
    conn.commit()
    out = []
    ok = {"stdout": lambda t, m: out.append((t, m))}
    early = AT - timedelta(minutes=31)   # 06:59 Thai
    late = AT + timedelta(hours=3, minutes=30)  # 11:00 Thai
    assert notify.run_digest(conn, early, ok) == (0, 0) and notify.run_digest(conn, late, ok) == (0, 0)
    assert notify.run_digest(conn, AT, ok) == (2, 0)                      # U1 + U3; U2 opted out
    assert {t for t, _ in out} == {"U1", "U3"}
    assert "จ.พระนครศรีอยุธยา" in dict(out)["U1"] and "จ.เชียงใหม่" in dict(out)["U3"]
    assert notify.run_digest(conn, AT + timedelta(minutes=20), ok) == (0, 0)  # already sent today
    nxt = AT + timedelta(days=1)
    assert notify.run_digest(conn, nxt, {"stdout": lambda t, m: 1 / 0}) == (0, 2)   # fails -> not marked
    assert notify.run_digest(conn, nxt + timedelta(minutes=20), ok) == (2, 0)       # retried


def test_digest_link_uses_deployment_domain(conn, monkeypatch):
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "example.vercel.app")
    assert notify.report_link(PROV) == "https://example.vercel.app/report.html?province=%E0%B8%9E%E0%B8%A3%E0%B8%B0%E0%B8%99%E0%B8%84%E0%B8%A3%E0%B8%A8%E0%B8%A3%E0%B8%B5%E0%B8%AD%E0%B8%A2%E0%B8%B8%E0%B8%98%E0%B8%A2%E0%B8%B2"
    monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL")
    assert notify.report_link(PROV) is None


def loc(user="U1", lat=14.36, lng=100.55):
    return {"type": "message", "replyToken": "r", "source": {"userId": user},
            "message": {"type": "location", "latitude": lat, "longitude": lng}}


def text(t, user="U1"):
    return {"type": "message", "replyToken": "r", "source": {"userId": user}, "message": {"type": "text", "text": t}}


def test_line_commands_report_and_digest_toggle(conn):
    out = []
    run = lambda ev: (out.clear(), lw.handle_event(conn, ev, AT, lambda tok, t: out.append(t)), list(out))[2]
    assert "ยังไม่มีตำแหน่ง" in run(text("รายงาน"))[0]
    assert "07:00" in run(loc())[0]
    assert "สรุปสถานการณ์น้ำ จ.พระนครศรีอยุธยา" in run(text("รายงาน"))[0]
    assert "หยุดส่งสรุป" in run(text("ปิดสรุป"))[0]
    assert conn.execute("SELECT digest FROM subscriptions").fetchone()["digest"] == 0
    run(loc(lat=14.37))                                   # re-sending the location keeps the opt-out
    assert conn.execute("SELECT digest FROM subscriptions").fetchone()["digest"] == 0
    assert "จะส่งสรุป" in run(text("เปิดสรุป"))[0]
    assert conn.execute("SELECT digest FROM subscriptions").fetchone()["digest"] == 1


def test_report_endpoint(tmp_path, monkeypatch):
    p = tmp_path / "e.db"
    c = db.connect(str(p)); seed(c); c.commit(); c.close()
    monkeypatch.setenv("WATER_DB", str(p))
    monkeypatch.setattr(api, "now", lambda: AT)
    cl = TestClient(api.app)
    r = cl.get("/reports/" + PROV)
    assert r.status_code == 200 and r.json()["stations"] == 3 and len(r.json()["series"]) == 14
    assert len(cl.get("/reports/" + PROV, params={"days": 5}).json()["series"]) == 5
    assert cl.get("/reports/ไม่มี").status_code == 404 and cl.get("/reports/" + PROV, params={"days": 99}).status_code == 422
    assert r.headers["cache-control"].startswith("public")


def test_init_db_adds_digest_columns_to_old_sqlite(tmp_path):
    c = db.connect(str(tmp_path / "old.db"))
    c.executescript("CREATE TABLE subscriptions(id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL,"
                    "target TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL, label TEXT, last_status TEXT, last_notified TEXT);")
    c.execute("INSERT INTO subscriptions(channel,target,lat,lng) VALUES('line','U',1,1)")
    ig.init_db(c)
    row = c.execute("SELECT digest, last_digest FROM subscriptions").fetchone()
    assert row["digest"] == 1 and row["last_digest"] is None  # existing subscribers keep getting the report


def test_snapshots_values_gaps_and_filters(conn):
    s = core.snapshots(conn, AT, hours=24, step_h=6)
    assert len(s["times"]) == 5 and s["times"][-1] == core.iso(AT) and s["step_h"] == 6
    a = s["stations"]["A"]                       # A = 120 - 1.6 * hours_ago (readings at AT-30min, AT-1h, ...)
    assert a[-1] == 120.0 and a[0] == pytest.approx(120 - 1.6 * 24, abs=0.1) and a[0] < a[-1]
    assert set(s["stations"]) == {"A", "B", "C", "D"}
    only = core.snapshots(conn, AT, hours=24, step_h=6, province=PROV)
    assert set(only["stations"]) == {"A", "B", "C"}
    conn.execute("DELETE FROM readings WHERE station_id='B' AND ts > ?", (core.iso(AT - timedelta(hours=10)),))
    b = core.snapshots(conn, AT, hours=24, step_h=6)["stations"]["B"]
    assert b[0] is not None and b[-1] is None    # newest slots have no reading within 150 min -> null, not carried forever


def test_snapshots_endpoint(tmp_path, monkeypatch):
    p = tmp_path / "s.db"
    c = db.connect(str(p)); seed(c); c.commit(); c.close()
    monkeypatch.setenv("WATER_DB", str(p))
    monkeypatch.setattr(api, "now", lambda: AT)
    cl = TestClient(api.app)
    r = cl.get("/history/snapshots", params={"hours": 12, "step": 3, "province": PROV})
    assert r.status_code == 200 and len(r.json()["times"]) == 5 and set(r.json()["stations"]) == {"A", "B", "C"}
    assert cl.get("/history/snapshots", params={"hours": 500}).status_code == 422
    assert cl.get("/history/snapshots", params={"step": 0}).status_code == 422
    assert r.headers["cache-control"].startswith("public")

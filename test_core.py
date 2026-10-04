import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api, core, ingest as ig
from conftest import real_rows

AT = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


def pts(*pcts, step_min=60):
    return [(AT - timedelta(minutes=step_min * (len(pcts) - 1 - i)), p) for i, p in enumerate(pcts)]


def test_trend_labels_and_min_span():
    assert core.trend_of(pts(60, 66)) == ("rising", 6.0)
    assert core.trend_of(pts(66, 60)) == ("falling", -6.0)
    assert core.trend_of(pts(60, 60.2))[0] == "steady"
    assert core.trend_of(pts(60, 70, step_min=20)) == (None, None)  # span 20 min < 45
    assert core.trend_of(pts(60)) == (None, None)


def test_eta_only_when_rising_below_bank_and_within_two_days():
    assert core.eta_to_bank_h(88, 6) == 2.0
    assert core.eta_to_bank_h(88, -3) is None
    assert core.eta_to_bank_h(105, 5) is None
    assert core.eta_to_bank_h(10, 0.5) is None  # 180 h away -> not worth saying


def test_advice_covers_states():
    base = dict(status="normal", stale=False, pct_of_bank=40, trend=None)
    assert "ปกติ" in core.advice(base)
    assert "ล้นตลิ่ง" in core.advice({**base, "status": "alert", "pct_of_bank": 120})
    assert "สูงขึ้น" in core.advice({**base, "status": "watch", "pct_of_bank": 75, "trend": "rising"})
    assert "1784" in core.advice({**base, "stale": True})
    assert "ประเมินสถานะไม่ได้" in core.advice({**base, "status": "unknown"})


def make_db(path, rows):
    c = sqlite3.connect(path)
    ig.init_db(c)
    ig.save(c, rows)
    c.row_factory = sqlite3.Row
    return c


def reading(sid, ts, pct):
    return dict(station_id=sid, ts=ts, water_level=1.0, pct_of_bank=pct, status=ig.status_of(pct))


def test_latest_attaches_trend_from_history(tmp_path):
    c = make_db(tmp_path / "t.db", real_rows())
    for ts, pct in [("2026-10-01T14:00:00Z", 140.0), ("2026-10-01T15:00:00Z", 145.0)]:
        c.execute("INSERT INTO readings VALUES(:station_id,:ts,:water_level,:pct_of_bank,:status)",
                  reading("505018", ts, pct))
    row = {r["id"]: r for r in core.latest(c, AT)}["505018"]
    assert row["trend"] == "rising" and row["pct_of_bank"] == pytest.approx(148.9, abs=0.1)
    assert row["advice"] and row["watch_pct"] == 70.0


def test_prune_and_init_db_migrates_old_schema(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE stations(id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT, province TEXT,"
                    "lat REAL NOT NULL, lng REAL NOT NULL, bank_level REAL, ground_level REAL, updated_at TEXT);")
    ig.init_db(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(stations)")}
    assert {"river", "basin", "watch_pct", "alert_pct"} <= cols
    ig.save(c, real_rows())
    old = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c.execute("INSERT INTO readings VALUES('505018',?,1,50,'normal')", (old,))
    assert ig.prune(c, 45) == 1
    assert c.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2


def test_per_station_thresholds_change_status():
    raw = real_rows()[0]
    raw["station"]["ground_level"], raw["station"]["min_bank"], raw["waterlevel_msl"] = 0.0, 10.0, "6.0"
    assert ig.normalise(raw)[1]["status"] == "normal"
    st, rd = ig.normalise(raw, {"505018": {"watch": 50, "alert": 80}})
    assert rd["status"] == "watch" and st["watch_pct"] == 50.0


def test_related_splits_upstream_downstream_by_bed_elevation():
    def s(id, ground, lat, river="แม่น้ำแควน้อย"):
        return dict(id=id, name=id, province="x", status="normal", pct_of_bank=50, trend=None,
                    trend_pct_per_hr=None, stale=False, lat=lat, lng=99.0, ground_level=ground, river=river,
                    source="RID")
    rows = [s("me", 30, 14.0), s("twin", 31, 14.0001), s("up", 60, 14.3), s("down", 10, 13.8),
            s("far", 90, 17.0), s("other", 70, 14.1, river="คลองอื่น"), s("nolevel", None, 14.1)]
    r = core.related(rows, "me")
    assert [x["id"] for x in r["upstream"]] == ["up"] and [x["id"] for x in r["downstream"]] == ["down"]
    assert [x["id"] for x in r["colocated"]] == ["twin"]  # not mislabelled as upstream/downstream
    assert core.related(rows, "nolevel")["upstream"] == []


def test_ingest_cli_logs_runs_and_health_reports_it(tmp_path, monkeypatch, capsys):
    import json
    db, dump = tmp_path / "w.db", tmp_path / "d.json"
    dump.write_text(json.dumps({"data": real_rows()}), encoding="utf-8")
    assert ig.main(["--db", str(db), "--file", str(dump)]) == 0
    assert ig.main(["--db", str(db), "--file", str(tmp_path / "missing.json")]) == 1
    monkeypatch.setenv("WATER_DB", str(db))
    monkeypatch.setattr(api, "now", lambda: datetime.now(timezone.utc))
    h = TestClient(api.app).get("/health").json()
    assert h["ingest_ok"] is True and "FileNotFoundError" in h["last_ingest_error"]


def test_status_filter_in_sql_and_related_endpoint_404(tmp_path, monkeypatch):
    make_db(tmp_path / "a.db", real_rows()).close()
    monkeypatch.setenv("WATER_DB", str(tmp_path / "a.db"))
    c = TestClient(api.app)
    assert len(c.get("/stations", params={"status": "alert"}).json()) == 2
    assert c.get("/stations", params={"status": "normal"}).json() == []
    assert c.get("/stations/505018/related").status_code == 200
    assert c.get("/stations/nope/related").status_code == 404


def test_twin_conflict_flagged_but_statuses_untouched():
    def s(id, src, status, lat=14.0, stale=False):
        return dict(id=id, source=src, status=status, pct_of_bank=50, stale=stale, lat=lat, lng=99.0)
    rows = core.attach_twins([s("a", "RID", "alert"), s("b", "EGAT", "normal"), s("c", "RID", "normal", lat=15.0),
                              s("d", "X", "alert", stale=True), s("e", "Y", "normal", lat=16.0), s("f", "Z", "normal", lat=16.0)])
    by = {r["id"]: r for r in rows}
    assert by["a"]["twin_conflict"] and by["b"]["twin_conflict"] and by["a"]["status"] == "alert"
    assert by["c"]["twins"] == [] and not by["c"]["twin_conflict"]
    assert by["e"]["twins"][0]["id"] == "f" and not by["e"]["twin_conflict"]  # twins agree


def test_rise_of_needs_enough_clean_data():
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    lv = lambda *v: [(t0 + timedelta(minutes=30 * i), x) for i, x in enumerate(v)]
    assert core.rise_of(lv(1.0, 1.2, 1.5, 1.7)) == (0.7, False)
    assert core.rise_of(lv(1.0, 1.4)) == (None, False)            # two points: not enough
    assert core.rise_of(lv(1.0, 1.1, 1.2)) == (None, False)       # span of one hour: too short
    assert core.rise_of(lv(1.0, 1.1, 4.5, 4.6)) == (None, True)   # 3.4 m step: sensor glitch
    assert core.rise_of(lv(5.0, 4.0, 3.0, 2.5)) == (-2.5, False)  # falling is fine, just not "fast rising"


def test_fast_risers_ranks_by_metres_and_skips_duplicates():
    mk = lambda i, rise, lat=14.0: {"id": i, "lat": lat, "lng": 100.0 + len(i), "rise_3h_m": rise}
    rows = [mk("a", 0.5), mk("bb", 0.9), mk("ccc", 0.2), mk("dddd", None), {**mk("ee", 0.4), "lng": 100.0 + 2}]  # "ee" shares bb's site
    assert [r["id"] for r in core.fast_risers(rows)] == ["bb", "a"]


def _series(fn, hours=72, step_min=30):
    t0 = AT - timedelta(hours=hours)
    return [(t0 + timedelta(minutes=step_min * i), fn(step_min * i / 60)) for i in range(int(hours * 60 / step_min) + 1)]


def test_tide_is_told_apart_from_a_flood_wave():
    import math
    tide = _series(lambda h: 1.0 * math.sin(2 * math.pi * h / 12.4))           # +-1 m twice a day
    assert core.is_tidal(tide)
    flood = _series(lambda h: min(h, 40) * 0.05 + max(0, h - 40) * -0.02)       # one slow rise, one slow fall
    assert not core.is_tidal(flood)
    two_storms = _series(lambda h: 2.0 * math.exp(-((h - 20) / 8) ** 2) + 2.5 * math.exp(-((h - 55) / 8) ** 2))
    assert not core.is_tidal(two_storms)                                       # rain pulses are days apart, not hours
    assert not core.is_tidal(tide[:6])                                         # too little history to say


def _store(c, levels):
    for t, v in levels:
        c.execute("INSERT OR REPLACE INTO readings VALUES('505018',?,?,?,?)", (core.iso(t), v, 60.0, "normal"))
    c.commit()


def test_latest_drops_the_rise_of_a_tidal_gauge_but_keeps_a_real_one(tmp_path):
    import math
    c = make_db(tmp_path / "t.db", real_rows())
    tide = _series(lambda h: 1.0 * math.sin(2 * math.pi * h / 12.4 - 4.57))   # ends in the middle of a climb of over a metre in 3 h
    _store(c, tide)
    row = {r["id"]: r for r in core.latest(c, AT)}["505018"]
    assert row["rise_3h_m"] is None and row["rise_tidal"] is True

    c.execute("DELETE FROM readings WHERE station_id='505018'")
    wave = _series(lambda h: 1.0 if h < 68.5 else 1.0 + (h - 68.5) * 0.5)    # flat for days, then climbs 0.5 m/h
    _store(c, wave)
    row = {r["id"]: r for r in core.latest(c, AT)}["505018"]
    assert row["rise_3h_m"] and row["rise_3h_m"] >= 0.5 and row["rise_tidal"] is False

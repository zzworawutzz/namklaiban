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


def _ob(**kw):
    d = {"stale": False, "water_level": 5.5, "bank_level": 2.75, "pct_of_bank": 129.5}
    d.update(kw)
    return core.over_bank_cm(d)


def test_over_bank_cm_is_level_minus_bank_in_cm_rounded_to_5():
    assert _ob(water_level=3.57, bank_level=2.75, pct_of_bank=107.0) == 80       # 82 cm -> 80
    assert _ob(water_level=3.58, bank_level=2.75, pct_of_bank=107.0) == 85       # 83 cm -> 85
    assert _ob(water_level=2.40, bank_level=2.75, pct_of_bank=95.0) == -35        # 35 cm below the bank: within a metre, so shown
    assert _ob(water_level=2.75, bank_level=2.75, pct_of_bank=100.0) == 0


def test_over_bank_cm_is_none_when_it_should_not_be_trusted_or_shown():
    assert _ob(stale=True) is None                                                # old reading
    assert _ob(bank_level=None) is None and _ob(water_level=None) is None
    assert _ob(water_level=6.5, bank_level=2.75) is None                          # 3.75 m above: probably a datum error
    assert _ob(water_level=1.0, bank_level=2.75, pct_of_bank=40.0) is None        # far below the bank: not news
    assert _ob(water_level=3.5, bank_level=2.75, pct_of_bank=80.0) is None        # above the bank but the % says below: figures disagree
    assert _ob(water_level=2.0, bank_level=2.75, pct_of_bank=120.0) is None       # the other way round


def test_over_bank_text():
    assert core.over_bank_text(80) == "สูงกว่าตลิ่งราว 80 ซม."
    assert core.over_bank_text(-35) == "ต่ำกว่าตลิ่งราว 35 ซม."
    assert core.over_bank_text(0) == "น้ำเสมอระดับตลิ่ง" and core.over_bank_text(None) == ""


def test_bank_data_quality_sorts_stations_into_who_is_fine_and_who_is_doubtful():
    def r(i, lv, bk, gd, pct, stale=False):
        return {"id": i, "name": "s" + str(i), "province": "p", "water_level": lv, "bank_level": bk, "ground_level": gd, "pct_of_bank": pct, "stale": stale}
    rows = [r(1, 3.57, 2.75, -6.0, 107.0),            # fine: 82 cm above
            r(2, 6.9, 2.75, -6.0, 130.0),             # 4.15 m above the bank: too high
            r(3, 3.5, 2.75, -6.0, 80.0),              # above the bank but the % says below: disagree
            r(4, 3.0, 3.0, 3.5, 50.0),                # bank not above the river bed
            r(5, 1.0, 2.75, -6.0, 40.0),              # far below the bank: normal
            r(6, None, 2.75, 0.0, None),              # no level: missing
            r(7, 9.0, 2.75, -6.0, 200.0, stale=True)] # old reading: not looked at
    q = core.bank_data_quality(rows)
    assert (q["fresh"], q["usable"], q["far_below"], q["missing"]) == (6, 1, 1, 1)   # 6 fresh = 1 usable + 1 far below + 1 missing + 3 doubtful
    assert [i["id"] for i in q["too_high"]] == [2] and q["too_high"][0]["cm"] == 415
    assert [i["id"] for i in q["disagree"]] == [3] and [i["id"] for i in q["bank_not_above_bed"]] == [4]
    assert q["too_high_count"] == q["disagree_count"] == q["bank_not_above_bed_count"] == 1


def test_bank_data_quality_lists_are_capped_but_counted():
    rows = [{"id": i, "name": "s", "province": "p", "water_level": 9.0 + i, "bank_level": 2.0, "ground_level": -5.0, "pct_of_bank": 150.0, "stale": False} for i in range(25)]
    q = core.bank_data_quality(rows)
    assert q["too_high_count"] == 25 and len(q["too_high"]) == 10 and q["too_high"][0]["id"] == 24      # the worst first


def test_a_gauge_with_no_thresholds_recorded_gets_no_cm_and_counts_as_missing_not_doubtful():
    d = {"stale": False, "water_level": 0.4, "bank_level": 0.0, "ground_level": 0.0, "pct_of_bank": None}      # bank 0 / bed 0, e.g. a reservoir gauge
    assert core.over_bank_cm(d) is None                                                                         # would otherwise read "40 cm above the bank"
    assert core.over_bank_cm(dict(d, bank_level=2.0, ground_level=2.5)) is None                                 # bank below the bed: nonsense
    q = core.bank_data_quality([{"id": 1, "name": "a", "province": "p", **{k: d[k] for k in ("water_level", "bank_level", "ground_level", "pct_of_bank")}, "stale": False}])
    assert q["missing"] == 1 and q["bank_not_above_bed_count"] == 0                                             # an empty record is not a data error


def test_the_database_side_summary_gives_the_same_trend_and_rise_as_going_through_every_reading(tmp_path):
    """recent_series() sends two numbers per station instead of every reading; trend_of and rise_of must not notice."""
    import sqlite3, ingest as ig
    c = sqlite3.connect(tmp_path / "s.db"); c.row_factory = sqlite3.Row; ig.init_db(c)
    def put(sid, minutes_ago, level, pct):
        ts = (AT - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        c.execute("INSERT INTO readings(station_id,ts,water_level,pct_of_bank,status) VALUES(?,?,?,?,'normal')", (sid, ts, level, pct))
    for sid in "ABCDEFGH":
        c.execute("INSERT INTO stations(id,name,source,lat,lng) VALUES(?,?,?,?,?)", (sid, sid, "HII", 14.0, 100.0))
    for k in range(40):                                                          # 10 hours, every 15 minutes: some before the 6 h window, some inside the 3 h one
        put("A", 15 * k, 1.0 + k / 10, 50.0 + k)                                 # smooth, falling towards now
        put("B", 15 * k, None if k % 3 == 0 else 2.0, 60.0 if k % 2 else None)   # gaps in either column
        put("C", 15 * k, None, None)                                             # nothing usable
        put("D", 15 * k, 1.0 if k > 5 else 3.5, 40.0)                            # one 2.5 m jump 90 minutes ago: a faulty sensor
    for k, m in enumerate((150, 100, 50, 0)):
        put("E", m, 1.0 + k * 1.2, 20.0 + k)                                     # steps of 1.2 m that add up to 3.6 m: no spike step, but over the total limit
    put("F", 10, 2.0, 70.0)                                                      # a single reading
    put("G", 30, 2.0, 70.0); put("G", 0, 2.4, 71.0)                              # two readings, too close together for a trend or a rise
    put("H", 170, 2.0, 70.0); put("H", 90, 2.2, 71.0); put("H", 0, 2.5, 72.0)    # three readings spread over almost 3 h
    c.commit()
    old_pts, old_lv = core.recent_points(c, AT), core.recent_levels(c, AT)
    pts, lv = core.recent_series(c, AT)
    assert set(pts) == set(old_pts) and set(lv) == set(old_lv)
    for sid in old_pts:
        assert core.trend_of(pts[sid]) == core.trend_of(old_pts[sid]), sid
    for sid in old_lv:
        assert core.rise_of(lv[sid]) == core.rise_of(old_lv[sid]), sid
    assert core.rise_of(lv["D"]) == (None, True) and core.rise_of(lv["E"]) == (None, True)   # the glitch rules survived the move
    assert core.rise_of(lv["A"])[0] is not None and core.trend_of(pts["A"])[0] == "falling"   # (k counts back in time, so the older the higher)
    assert "C" not in pts and "C" not in lv and len(pts["A"]) == 2                           # two entries a station at most


def test_freshness_agrees_with_latest_about_age_and_staleness(tmp_path):
    import sqlite3, ingest as ig
    from conftest import real_rows
    c = sqlite3.connect(tmp_path / "f.db"); c.row_factory = sqlite3.Row; ig.init_db(c); ig.save(c, real_rows())
    full = {r["id"]: r for r in core.latest(c, AT)}
    light = {r["id"]: r for r in core.freshness(c, AT)}
    assert set(full) == set(light) and light
    for i, r in light.items():
        assert (r["age_min"], r["stale"], r["source"], r["ts"]) == (full[i]["age_min"], full[i]["stale"], full[i]["source"], full[i]["ts"])


def test_latest_near_computes_trends_only_around_the_given_places_and_agrees_with_the_full_answer_there(tmp_path):
    import sqlite3, ingest as ig
    c = sqlite3.connect(tmp_path / "n.db"); c.row_factory = sqlite3.Row; ig.init_db(c)
    for i in range(30):                                                          # 30 stations on a line, one degree of longitude apart
        sid = f"S{i}"
        c.execute("INSERT INTO stations(id,name,source,province,lat,lng,bank_level,ground_level) VALUES(?,?,?,?,?,?,?,?)", (sid, sid, "HII", "ป", 14.0, 90.0 + i * 0.1, 5.0, 1.0))
        for k in range(24):                                                      # every 15 min for 6 hours, rising
            ts = (AT - timedelta(minutes=15 * k)).strftime("%Y-%m-%dT%H:%M:%SZ")
            c.execute("INSERT INTO readings(station_id,ts,water_level,pct_of_bank,status) VALUES(?,?,?,?,'normal')", (sid, ts, 2.0 - k * 0.02, round((1.0 - k * 0.02) / 4 * 100, 1)))
    c.commit()
    full = {r["id"]: r for r in core.latest(c, AT)}
    near = {r["id"]: r for r in core.latest(c, AT, near=[(14.0, 90.0)])}       # a person right at the first station
    assert set(near) == set(full) and len(near) == 30                            # still every station, so counts and agency shares stay right
    for sid in ("S0", "S1", "S7"):                                               # the nearest eight: identical to the full computation
        for k in ("trend", "trend_pct_per_hr", "rise_3h_m", "eta_to_bank_h", "status", "pct_of_bank", "age_min", "stale"):
            assert near[sid][k] == full[sid][k], (sid, k)
    assert full["S20"]["trend"] == "rising" and near["S20"]["trend"] is None     # far away: nobody asked, so it is left empty
    assert near["S20"]["status"] == full["S20"]["status"] and near["S20"]["pct_of_bank"] == full["S20"]["pct_of_bank"]


def test_the_nearest_fresh_station_gets_its_trend_even_when_the_nearest_ones_are_stale(tmp_path):
    import sqlite3, ingest as ig
    c = sqlite3.connect(tmp_path / "m.db"); c.row_factory = sqlite3.Row; ig.init_db(c)
    for i in range(12):
        sid = f"S{i}"
        c.execute("INSERT INTO stations(id,name,source,province,lat,lng,bank_level,ground_level) VALUES(?,?,?,?,?,?,?,?)", (sid, sid, "HII", "ป", 14.0, 90.0 + i * 0.1, 5.0, 1.0))
        newest = AT - timedelta(hours=9 if i < 10 else 0)                        # the ten nearest are silent, the eleventh reports
        for k in range(24):
            ts = (newest - timedelta(minutes=15 * k)).strftime("%Y-%m-%dT%H:%M:%SZ")
            c.execute("INSERT INTO readings(station_id,ts,water_level,pct_of_bank,status) VALUES(?,?,?,?,'normal')", (sid, ts, 2.0 - k * 0.02, round((1.0 - k * 0.02) / 4 * 100, 1)))
    c.commit()
    near = {r["id"]: r for r in core.latest(c, AT, near=[(14.0, 90.0)])}
    assert near["S10"]["stale"] is False and near["S10"]["trend"] == "rising"

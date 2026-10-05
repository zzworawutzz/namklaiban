import sqlite3
from datetime import datetime, timezone

import dams
import ingest as ig

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)      # 11:00 Thai time


def dam(name, prov, basin, pct, date="2026-10-05", released=5.0, normal=100.0):
    return {"dam_date": date, "dam_storage": 80.0, "dam_storage_percent": pct, "dam_inflow": 2.0, "dam_released": released,
            "dam": {"dam_name": {"th": name}, "dam_lat": 15.0, "dam_long": 100.0, "normal_storage": normal, "id": 1},
            "agency": {"agency_shortname": {"th": "ชป. "}}, "basin": {"basin_name": {"th": basin}},
            "geocode": {"province_name": {"th": prov}}}


PAYLOAD = {"data": {"dam_daily": [dam("ป่าสัก", "สระบุรี", "ลุ่มน้ำป่าสัก", 109.8, released=43.2), dam("ภูมิพล", "ตาก", "ลุ่มน้ำปิง", 61.0),
                                  dam("สิริกิติ์", "อุตรดิตถ์", "ลุ่มน้ำน่าน", 91.0), dam("ลำตะคอง", "นครราชสีมา", "ลุ่มน้ำมูล", 50.0),
                                  dam("เก่า", "ตาก", "ลุ่มน้ำปิง", 99.0, date="2026-09-20"),         # not reported for two weeks
                                  dam("ไม่มีค่า", "ตาก", "ลุ่มน้ำปิง", None), {"dam": {}}],
                     "dam_hourly": [{"dam_date": "2020-11-09 14:00"}]}}                                 # years old: never used


def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ig.init_db(c)
    for i, (prov, basin) in enumerate([("พระนครศรีอยุธยา", "ลุ่มน้ำเจ้าพระยา")] * 3 + [("พระนครศรีอยุธยา", "ลุ่มน้ำป่าสัก")] * 2 + [("พระนครศรีอยุธยา", "ลุ่มน้ำท่าจีน")]):
        c.execute("INSERT INTO stations(id,name,province,lat,lng,basin) VALUES(?,?,?,?,?,?)", (f"s{i}", f"ส{i}", prov, 14.3, 100.5, basin))
    return c


def test_parse_keeps_recent_valid_large_dams_only():
    rows = dams.parse(PAYLOAD, NOW)
    assert [r["name"] for r in rows] == ["ป่าสัก", "ภูมิพล", "สิริกิติ์", "ลำตะคอง"]
    top = rows[0]
    assert top["pct"] == 109.8 and top["released"] == 43.2 and top["basin"] == "ลุ่มน้ำป่าสัก" and top["agency"] == "ชป." and top["date"] == "2026-10-05"


def test_a_province_gets_the_dams_in_its_basins_and_upstream_fullest_first():
    c = conn()
    assert dams.basins_of(c, "พระนครศรีอยุธยา") == ["ลุ่มน้ำเจ้าพระยา", "ลุ่มน้ำป่าสัก"]   # the single Tha Chin station does not count
    g = dams.for_province(c, "พระนครศรีอยุธยา", dams.parse(PAYLOAD, NOW))
    assert [d["name"] for d in g["dams"]] == ["ป่าสัก", "สิริกิติ์", "ภูมิพล"]              # Pasak, plus Nan and Ping above the Chao Phraya; not the Mun
    assert g["as_of"] == "2026-10-05"
    assert dams.for_province(c, "ไม่มีจังหวัดนี้", dams.parse(PAYLOAD, NOW)) is None
    assert dams.for_province(c, "พระนครศรีอยุธยา", []) is None


def test_the_summary_line_only_names_dams_above_the_threshold(monkeypatch):
    c = conn()
    monkeypatch.setattr(dams, "get", lambda: dams.parse(PAYLOAD, NOW))
    high = dams.high_in(c, "พระนครศรีอยุธยา")
    assert [d["name"] for d in high] == ["ป่าสัก", "สิริกิติ์"]                              # 61 % Ping stays out
    text = dams.line(high)
    assert "ป่าสัก 110% ระบาย 43 ล้าน ลบ.ม./วัน" in text and "ไม่ได้บอกว่าจะท่วม" in text
    monkeypatch.setenv("DAM_ALERT_PCT", "100")
    assert [d["name"] for d in dams.high_in(c, "พระนครศรีอยุธยา")] == ["ป่าสัก"]
    monkeypatch.setattr(dams, "get", lambda: None)
    assert dams.high_in(c, "พระนครศรีอยุธยา") is None                                       # feed down: no line, no error


def test_feed_is_cached_and_a_failure_falls_back_to_the_last_copy(monkeypatch):
    dams._cache.update(at=0.0, rows=None)
    calls, t = [], [1000.0]
    def fetcher():
        calls.append(1)
        if len(calls) > 1:
            raise OSError("down")
        return PAYLOAD
    assert len(dams.get(fetcher, lambda: t[0], NOW)) == 4 and len(dams.get(fetcher, lambda: t[0], NOW)) == 4 and len(calls) == 1
    t[0] += dams.CACHE_S + 1
    assert len(dams.get(fetcher, lambda: t[0], NOW)) == 4                                  # failed refresh: the recent copy
    t[0] += 7 * 3600
    assert dams.get(fetcher, lambda: t[0], NOW) is None                                    # too old to trust
    dams._cache.update(at=0.0, rows=None)


def test_dams_endpoint_lists_the_related_dams_and_says_so_when_the_feed_is_down(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api
    monkeypatch.setenv("WATER_DB", str(tmp_path / "d.db"))
    api._ready.clear()
    client = TestClient(api.app)
    with api.conn() as c:
        for i, basin in enumerate(["ลุ่มน้ำเจ้าพระยา"] * 2 + ["ลุ่มน้ำป่าสัก"] * 2):
            c.execute("INSERT INTO stations(id,name,province,lat,lng,basin) VALUES(?,?,?,?,?,?)", (f"s{i}", f"ส{i}", "พระนครศรีอยุธยา", 14.3, 100.5, basin))
        c.commit()
    monkeypatch.setattr(dams, "get", lambda: dams.parse(PAYLOAD, NOW))
    r = client.get("/api/dams", params={"province": "พระนครศรีอยุธยา", "limit": 2})
    j = r.json()
    assert r.status_code == 200 and [d["name"] for d in j["dams"]] == ["ป่าสัก", "สิริกิติ์"] and j["total"] == 3 and j["as_of"] == "2026-10-05"
    assert "max-age" in r.headers["cache-control"]
    assert client.get("/api/dams", params={"province": "ไม่มีจังหวัดนี้"}).json() == {"dams": [], "total": 0}
    assert client.get("/api/dams").status_code == 422
    monkeypatch.setattr(dams, "get", lambda: None)
    assert client.get("/api/dams", params={"province": "พระนครศรีอยุธยา"}).status_code == 503


def test_the_morning_summary_carries_the_dam_line_only_when_a_dam_is_nearly_full(tmp_path, monkeypatch):
    import db, notify
    from conftest import real_rows
    c = db.connect(str(tmp_path / "m.db"))
    ig.init_db(c); ig.save(c, real_rows())
    c.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,digest) VALUES('line','U1',14.2,99.0,'บ้าน',1)")
    c.commit()
    at = datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc)                    # 07:30 Thai time
    monkeypatch.setattr(notify, "line_quota", lambda: (300, 10))
    out = []
    send = {"line": lambda t, m, **kw: out.append(m)}
    monkeypatch.setattr(dams, "high_in", lambda conn, prov: dams.parse(PAYLOAD, NOW)[:1])
    assert notify.run_digest(c, at, send) == (1, 0) and "🏞 เขื่อนใหญ่ในลุ่มน้ำนี้และต้นน้ำ" in out[0] and "ป่าสัก 110%" in out[0]
    c.execute("UPDATE subscriptions SET last_digest=NULL")
    out.clear()
    monkeypatch.setattr(dams, "high_in", lambda conn, prov: None)
    assert notify.run_digest(c, at, send) == (1, 0) and "🏞" not in out[0]


def test_a_dam_listed_twice_keeps_its_newest_report_and_a_zero_percent_with_water_is_dropped():
    rid = dam("วชิราลงกรณ", "กาญจนบุรี", "ลุ่มน้ำแม่กลอง", 99.0, date="2026-10-05")
    egat = dam("วชิราลงกรณ", "กาญจนบุรี", "ลุ่มน้ำแม่กลอง", 84.59, date="2026-10-04")
    bad = dam("ศรีนครินทร์", "กาญจนบุรี", "ลุ่มน้ำแม่กลอง", 0)                    # storage 80 in the helper: cannot be 0 %
    rows = dams.parse({"data": {"dam_daily": [egat, rid, bad]}}, NOW)
    assert [(r["name"], r["pct"]) for r in rows] == [("วชิราลงกรณ", 99.0)]


def test_the_line_names_big_reservoirs_first_and_leaves_out_a_release_of_nothing(monkeypatch):
    c = conn()
    small = dam("เล็ก", "สระบุรี", "ลุ่มน้ำป่าสัก", 99.0, released=0.2, normal=50.0)
    big = dam("ใหญ่", "สระบุรี", "ลุ่มน้ำป่าสัก", 95.0, released=12.0, normal=900.0)
    monkeypatch.setattr(dams, "get", lambda: dams.parse({"data": {"dam_daily": [small, big]}}, NOW))
    high = dams.high_in(c, "พระนครศรีอยุธยา")
    assert [d["name"] for d in high] == ["ใหญ่", "เล็ก"]
    text = dams.line(high)
    assert "ใหญ่ 95% ระบาย 12 ล้าน ลบ.ม./วัน" in text and "เล็ก 99% " in text and "เล็ก 99% ระบาย" not in text


def graph(storages, year=2026, start="2026-09-28"):
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return {"data": {"graph_data": [{"year": year, "data": [{"date": f"{(d0 + timedelta(days=i)).isoformat()}T00:00:00+07:00", "value": v}
                                                          for i, v in enumerate(storages)]}]}}


def test_seven_day_change_is_in_percentage_points_of_normal_storage():
    d = {"id": 5, "date": "2026-10-05", "storage_mcm": 957.13, "normal_mcm": 872.0}
    fetch = lambda dam_id, year: graph([870.0, 880, 890, 900, 920, 940, 950, 957.13])     # 2026-09-28 .. 2026-10-05
    c = dams.change_7d(d, fetch)
    assert c == round((957.13 - 870.0) / 872.0 * 100, 1) == 10.0
    assert dams.trend_text(c) == "▲ +10.0 จุดใน 7 วัน" and dams.trend_text(-3.04) == "▼ -3.0 จุดใน 7 วัน"
    assert dams.trend_text(0.4) == "" and dams.trend_text(None) == ""                    # steady or unknown: nothing said


def test_a_missing_day_uses_the_neighbour_and_no_series_means_no_figure():
    d = {"id": 6, "date": "2026-10-05", "storage_mcm": 900.0, "normal_mcm": 1000.0}
    gappy = graph([800.0, 810, 820, 830, 840, 850, 860, 870])
    gappy["data"]["graph_data"][0]["data"].pop(0)                                         # 2026-09-28 is missing: 09-29 (810) is used
    assert dams.change_7d(d, lambda i, y: gappy) == 9.0
    dams._graph_cache.clear()
    assert dams.change_7d(d, lambda i, y: (_ for _ in ()).throw(OSError("down"))) is None
    assert dams.change_7d({**d, "id": None}, lambda i, y: gappy) is None and dams.change_7d({**d, "storage_mcm": None}, lambda i, y: gappy) is None


def test_around_new_year_both_years_are_read_and_series_are_cached():
    calls = []
    def fetch(dam_id, year):
        calls.append(year)
        return graph([100.0] * 5, start="2025-12-26") if year == 2025 else graph([110.0] * 5, start="2026-01-01")
    d = {"id": 7, "date": "2026-01-03", "storage_mcm": 120.0, "normal_mcm": 200.0}
    assert dams.change_7d(d, fetch) == 10.0 and sorted(calls) == [2025, 2026]           
    dams.change_7d(d, fetch)
    assert sorted(calls) == [2025, 2026]                                                  # the second call came from the cache


def test_the_summary_line_shows_the_week_when_known(monkeypatch):
    c = conn()
    monkeypatch.setattr(dams, "get", lambda: dams.parse({"data": {"dam_daily": [dam("ใหญ่", "สระบุรี", "ลุ่มน้ำป่าสัก", 95.0, released=12.0, normal=900.0)]}}, NOW))
    monkeypatch.setattr(dams, "fetch_graph", lambda dam_id, year: graph([700.0, 720, 740, 760, 780, 800, 820, 840], start="2026-09-28"))
    high = dams.high_in(c, "พระนครศรีอยุธยา")
    assert high[0]["change_7d"] == round((80.0 - 700.0) / 900.0 * 100, 1)                 # the helper's dam holds 80 now: the figure follows the data it is given
    assert "▼" in dams.line(high)

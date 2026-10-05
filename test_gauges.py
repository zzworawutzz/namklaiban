from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import api
import gauges

NOW = datetime(2026, 10, 4, 14, 30, tzinfo=timezone.utc)   # 21:30 in Thailand


def row(name, prov, lat, lng, mm24, at="2026-10-04 21:00", mm1=0.0):
    return {"rain_24h": mm24, "rain_1h": mm1, "rainfall_datetime": at, "station": {"id": 1, "tele_station_name": {"th": name},
            "tele_station_lat": lat, "tele_station_long": lng}, "geocode": {"province_name": {"th": prov}}}


PAYLOAD = {"data": [row("ก", "ปทุมธานี", 14.03, 100.73, 62.5, mm1=12), row("ข", "ปทุมธานี", 14.10, 100.60, 8.0),
                    row("ค", "ปทุมธานี", 14.20, 100.50, 41.0), row("ง", "นนทบุรี", 13.86, 100.51, 3.0),
                    row("เก่า", "ปทุมธานี", 14.0, 100.7, 99.0, at="2026-10-04 09:00"),      # last reported 12 h ago: ignored
                    row("ไม่มีค่า", "ปทุมธานี", 14.0, 100.7, None), row("ติดลบ", "ปทุมธานี", 14.0, 100.7, -1.0),
                    {"station": {}}]}                                                         # malformed


def test_parse_keeps_only_fresh_valid_readings():
    rows = gauges.parse(PAYLOAD, NOW)
    assert [r["name"] for r in rows] == ["ก", "ข", "ค", "ง"]
    assert rows[0]["mm24"] == 62.5 and rows[0]["mm1"] == 12 and rows[0]["age_min"] == 30 and rows[0]["at"] == "2026-10-04T14:00:00Z"


def test_province_summary_and_nearest():
    rows = gauges.parse(PAYLOAD, NOW)
    g = gauges.for_province(rows, "ปทุมธานี")
    assert g["stations"] == 3 and g["max"]["name"] == "ก" and g["over_35"] == 2 and g["mean_mm"] == 37.2
    assert [r["name"] for r in g["top"]] == ["ก", "ค", "ข"] and gauges.for_province(rows, "ไม่มีจังหวัดนี้") is None
    near = gauges.nearest(rows, 14.03, 100.73, 2)
    assert [r["name"] for r in near] == ["ก", "ข"] and near[0]["distance_km"] == 0.0
    assert gauges.nearest(rows, 18.8, 98.9) == []                                             # nothing within 25 km


def test_feed_is_cached_and_a_failure_falls_back_to_the_last_copy():
    calls, t = [], [1000.0]
    ok = lambda: calls.append(1) or PAYLOAD
    assert len(gauges.get(ok, lambda: t[0], NOW)) == 4 and len(gauges.get(ok, lambda: t[0], NOW)) == 4 and len(calls) == 1
    t[0] += 700                                                                              # cache expired, feed now down
    assert len(gauges.get(lambda: 1 / 0, lambda: t[0], NOW)) == 4                            # keeps the recent copy
    t[0] += 4 * 3600
    assert gauges.get(lambda: 1 / 0, lambda: t[0], NOW) is None                              # too old to trust


def test_api_endpoint_province_nearby_and_unavailable(monkeypatch):
    monkeypatch.setattr(gauges, "get", lambda *a, **k: gauges.parse(PAYLOAD, NOW))
    c = TestClient(api.app)
    r = c.get("/api/rain", params={"province": "ปทุมธานี"})
    assert r.status_code == 200 and r.json()["max"]["name"] == "ก" and "s-maxage=600" in r.headers["cache-control"]
    assert c.get("/api/rain", params={"lat": 14.03, "lng": 100.73}).json()["nearby"][0]["name"] == "ก"
    assert c.get("/api/rain", params={"province": "ไม่มี"}).json() == {"stations": 0}
    assert c.get("/api/rain").status_code == 422
    monkeypatch.setattr(gauges, "get", lambda *a, **k: None)
    assert c.get("/api/rain", params={"province": "ปทุมธานี"}).status_code == 503


def test_summary_line_only_when_the_gauges_measured_heavy_rain(monkeypatch):
    monkeypatch.setattr(gauges, "get", lambda *a, **k: gauges.parse(PAYLOAD, NOW))
    g = gauges.heavy_in("ปทุมธานี")
    assert g and "62" in gauges.line(g) and "ที่ ก" in gauges.line(g) and "ไม่ใช่พยากรณ์" in gauges.line(g)
    assert gauges.heavy_in("นนทบุรี") is None                       # 3 mm: nothing to say
    monkeypatch.setattr(gauges, "get", lambda *a, **k: None)
    assert gauges.heavy_in("ปทุมธานี") is None                      # feed down: the summary goes out without the line


def test_digest_text_and_card_carry_the_gauge_line():
    import cards, report
    from test_report import AT
    rep = {"province": "ปทุมธานี", "stations": 3, "counts": {"alert": 0, "watch": 0, "normal": 3, "unknown": 0}, "stale": 0, "over_bank": 0,
           "newly_over_bank": [], "delta_24h": None, "top": [], "rising": [], "fast": [], "series": [],
           "gauge": gauges.for_province(gauges.parse(PAYLOAD, NOW), "ปทุมธานี")}
    text = report.digest_text(rep, AT)
    assert "เครื่องวัดฝนจริง 24 ชม. สูงสุด 62 มม. ที่ ก" in text and "ไม่ใช่พยากรณ์" in text
    assert "เครื่องวัดฝนจริง" in str(cards.digest_card(rep, "วันนี้"))


def row3(name, prov, lat, lng, mm, sid, end="2026-10-04"):
    return {"rain_3d": mm, "rainfall_start_date": "2026-10-02", "rainfall_end_date": end, "rainfall_datetime": "2026-10-05",
            "station": {"id": sid, "tele_station_name": {"th": name}, "tele_station_lat": lat, "tele_station_long": lng}, "geocode": {"province_name": {"th": prov}}}


PAYLOAD3 = {"data": [row3("ก", "ปทุมธานี", 14.03, 100.73, 120.0, 1), row3("ข", "ปทุมธานี", 14.10, 100.60, 20.0, 2),
                     row3("ง", "นนทบุรี", 13.86, 100.51, 5.0, 4),
                     row3("เก่า", "ปทุมธานี", 14.0, 100.7, 300.0, 5, end="2026-09-20"),          # the period ended two weeks ago: ignored
                     row3("ไม่มีค่า", "ปทุมธานี", 14.0, 100.7, None, 6), {"station": {}}]}


def test_three_day_totals_keep_only_recent_valid_periods_and_summarise_a_province():
    rows3 = gauges.parse3(PAYLOAD3, NOW)
    assert [r["name"] for r in rows3] == ["ก", "ข", "ง"]
    g = gauges.province_3d(rows3, "ปทุมธานี")
    assert g == {"stations": 2, "mean_mm": 70.0, "max": {"name": "ก", "mm3d": 120.0}, "over_100": 1, "start": "2026-10-02", "end": "2026-10-04"}
    assert gauges.province_3d(rows3, "ไม่มี") is None


def test_nearby_gauges_get_their_three_day_total_by_station_id():
    rows = gauges.parse({"data": [{**row("ก", "ปทุมธานี", 14.03, 100.73, 62.5), "station": {"id": 1, "tele_station_name": {"th": "ก"}, "tele_station_lat": 14.03, "tele_station_long": 100.73}},
                                  {**row("ค", "ปทุมธานี", 14.10, 100.60, 41.0), "station": {"id": 7, "tele_station_name": {"th": "ค"}, "tele_station_lat": 14.10, "tele_station_long": 100.60}}]}, NOW)
    near = gauges.nearest(rows, 14.03, 100.73, 3)
    period = gauges.with_3d(near, gauges.parse3(PAYLOAD3, NOW))
    assert [(g["name"], g["mm3d"]) for g in near] == [("ก", 120.0), ("ค", None)]                 # gauge 7 has no 3-day figure
    assert period == {"start": "2026-10-02", "end": "2026-10-04"}
    assert gauges.with_3d(near, None) is None and near[0]["mm3d"] is None


def test_three_day_feed_is_cached_and_the_endpoint_adds_it_when_available(monkeypatch):
    calls, t = [], [1000.0]
    ok = lambda: calls.append(1) or PAYLOAD3
    assert len(gauges.get3(ok, lambda: t[0], NOW)) == 3 and len(gauges.get3(ok, lambda: t[0], NOW)) == 3 and len(calls) == 1
    t[0] += gauges.CACHE3_S + 1
    assert len(gauges.get3(lambda: 1 / 0, lambda: t[0], NOW)) == 3                               # a failed refresh keeps the recent copy
    t[0] += 13 * 3600
    assert gauges.get3(lambda: 1 / 0, lambda: t[0], NOW) is None
    gauges._cache3.update(at=0.0, rows=None)
    monkeypatch.setattr(gauges, "get", lambda *a, **k: gauges.parse(PAYLOAD, NOW))
    monkeypatch.setattr(gauges, "get3", lambda *a, **k: gauges.parse3(PAYLOAD3, NOW))
    c = TestClient(api.app)
    j = c.get("/api/rain", params={"province": "ปทุมธานี"}).json()
    assert j["three_day"]["max"]["mm3d"] == 120.0 and j["max"]["name"] == "ก"
    monkeypatch.setattr(gauges, "get3", lambda *a, **k: None)                                    # the 3-day feed is down: the 24 h answer is unchanged
    j = c.get("/api/rain", params={"province": "ปทุมธานี"}).json()
    assert "three_day" not in j and j["max"]["name"] == "ก"
    assert "three_day_period" not in c.get("/api/rain", params={"lat": 14.03, "lng": 100.73}).json()

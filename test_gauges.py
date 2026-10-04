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

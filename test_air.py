# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import air
import api

NOW = datetime(2026, 11, 19, 13, 30, tzinfo=timezone.utc)   # 20:30 in Thailand


def st(sid, name, area, lat, lng, pm, date="2026-11-19", time="20:00"):
    return {"stationID": sid, "nameTH": name, "areaTH": area, "lat": str(lat), "long": str(lng),
            "AQILast": {"date": date, "time": time, "PM25": {"value": str(pm), "aqi": "1", "color_id": "1"}}}


PAYLOAD = {"stations": [
    st("a", "สวนสาธารณะ", "ต.ปากน้ำ อ.เมือง, กระบี่", 8.05, 98.92, 7.2),
    st("b", "เขตบึงกุ่ม", "แขวงคลองกุ่ม เขตบึงกุ่ม, กรุงเทพฯ", 13.78, 100.65, 30.1),
    st("c", "สวนลุมพินี", "เขตปทุมวัน, กรุงเทพมหานคร", 13.73, 100.54, 52.0),
    st("d", "ค้างนาน", "อ.เมือง, ลำปาง", 18.3, 99.5, 80.0, date="2026-11-19", time="12:00"),   # 8.5 h old: ignored
    st("e", "ไม่มีค่า", "อ.เมือง, ลำพูน", 18.5, 99.0, -1),
    {"stationID": "x"},                                                                       # malformed
]}


def test_parse_keeps_only_fresh_valid_readings_and_levels():
    rows = air.parse(PAYLOAD, NOW)
    assert [r["id"] for r in rows] == ["a", "b", "c"]
    assert [r["level"] for r in rows] == [1, 3, 4] and rows[0]["province"] == "กระบี่"
    assert rows[1]["province"] == rows[2]["province"] == "กรุงเทพมหานคร" and rows[0]["at"] == "2026-11-19T13:00:00Z" and rows[0]["age_min"] == 30


def test_province_names_are_cleaned_up():
    assert air.province_of("ต.ในเมือง อ.เมือง, จ.เชียงใหม่") == "เชียงใหม่" and air.province_of("อ.เมือง, กาฬสินธ์ุ") == "กาฬสินธุ์"
    assert air.province_of("อ.ประจวบ, ประจวบคิรีขันธ์") == "ประจวบคีรีขันธ์" and air.province_of("สวนลุมพินี เขตปทุมวัน") == "กรุงเทพมหานคร"
    assert air.province_of("แขวงคลองกุ่ม เขตบึงกุ่ม") == "กรุงเทพมหานคร" and air.province_of("") == ""


def test_tls_context_adds_the_missing_intermediates_and_still_verifies():
    import ssl
    plain = ssl.create_default_context()
    ctx = air.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    names = " ".join(str(c.get("subject")) for c in ctx.get_ca_certs())
    assert "YR1" in names and "Root YR" in names and len(ctx.get_ca_certs()) >= len(plain.get_ca_certs()) + 2


def test_fetch_uses_that_context_and_a_missing_bundle_does_not_crash(monkeypatch, tmp_path):
    import io
    seen = {}

    class Resp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout=None, context=None):
        seen["ctx"] = context
        return Resp(b'{"stations": []}')
    import importlib.util
    spec = importlib.util.spec_from_file_location("air_real", air.__file__)      # the conftest replaces air.fetch; load an untouched copy
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    monkeypatch.setattr(real.urllib.request, "urlopen", fake_open)
    assert real.fetch() == {"stations": []} and seen["ctx"].check_hostname is True
    monkeypatch.setattr(real, "CHAIN", tmp_path / "nope.pem")
    assert real.tls_context().verify_mode.name == "CERT_REQUIRED"


def test_level_boundaries_follow_the_thai_bands():
    assert [air.level(v)[0] for v in (0, 15, 15.1, 25, 25.1, 37.5, 37.6, 75, 75.1, 300)] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]


def test_province_summary_and_fallback_to_nearest_stations():
    rows = air.parse(PAYLOAD, NOW)
    g = air.for_province(rows, "กรุงเทพมหานคร")
    assert g["stations"] == 2 and g["mean"] == 41.0 and g["level"] == 4 and g["max"]["id"] == "c" and g["nearby"] is False
    assert air.for_province(rows, "ไม่มีจังหวัดนี้") is None
    near = air.for_province(rows, "นนทบุรี", centre=(13.86, 100.51))       # no station of its own: the closest ones, flagged
    assert near["nearby"] is True and near["max"]["id"] == "c"
    assert air.for_province(rows, "เชียงราย", centre=(19.9, 99.8)) is None   # nothing within 50 km either
    assert [r["id"] for r in air.nearest(rows, 13.73, 100.54, 2)] == ["c", "b"] and air.nearest(rows, 18.8, 98.9) == []


def test_feed_is_cached_and_a_failure_falls_back_to_the_last_copy():
    calls, t = [], [1000.0]

    def good():
        calls.append(1)
        return PAYLOAD

    def bad():
        raise OSError("down")
    assert len(air.get(good, lambda: t[0], NOW)) == 3 and air.get(good, lambda: t[0], NOW) and len(calls) == 1
    t[0] += air.CACHE_S + 1
    assert len(air.get(bad, lambda: t[0], NOW)) == 3                       # stale copy beats nothing
    t[0] += 4 * 3600
    assert air.get(bad, lambda: t[0], NOW) is None


def test_api_province_nearby_and_unavailable(monkeypatch):
    c = TestClient(api.app)
    assert c.get("/api/air").status_code == 422
    monkeypatch.setattr(air, "get", lambda: None)
    assert c.get("/api/air?province=กระบี่").status_code == 503
    monkeypatch.setattr(air, "get", lambda: air.parse(PAYLOAD, NOW))
    r = c.get("/api/air?province=กระบี่")
    assert r.status_code == 200 and r.json()["max"]["id"] == "a" and "max-age=300" in r.headers["cache-control"]
    n = c.get("/api/air?lat=13.73&lng=100.54&limit=2").json()["nearby"]
    assert [x["id"] for x in n] == ["c", "b"]
    assert c.get("/api/air?province=ไม่มี").json() == {"stations": 0}


def test_api_lists_every_fresh_station_for_the_map(monkeypatch):
    c = TestClient(api.app)
    monkeypatch.setattr(air, "get", lambda: None)
    assert c.get("/api/air/stations").status_code == 503
    monkeypatch.setattr(air, "get", lambda: air.parse(PAYLOAD, NOW))
    r = c.get("/api/air/stations")
    j = r.json()
    assert r.status_code == 200 and "max-age=300" in r.headers["cache-control"] and j["as_of"] == "2026-11-19T13:00:00Z"
    assert [s["id"] for s in j["stations"]] == ["a", "b", "c"] and set(j["stations"][0]) == {"id", "name", "province", "lat", "lng", "pm25", "level", "label", "at"}
    monkeypatch.setattr(air, "get", lambda: [])
    assert c.get("/api/air/stations").json() == {"as_of": None, "stations": []}

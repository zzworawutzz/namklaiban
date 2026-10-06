from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api, ews

NOW = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)   # 14:00 Thai time

ROWS = [
    {"stn": "STN1", "name": "บ้านก ", "stn_type": "RF", "province": "ตาก", "amphoe": "เมือง", "tambon": "ก", "latitude": "16.9", "longitude": "99.1",
     "status": "3", "rain": "7.0", "rain12h": "20.5", "wl": "0.00", "date": "02/10/69 13:15 น."},
    {"stn": "STN2", "name": "บ้านข*", "stn_type": "wl", "province": "กาญจนบุรี", "amphoe": "ข", "tambon": "ข", "latitude": "14.2", "longitude": "98.9",
     "status": 2, "rain": "0.0", "wl": "3.52", "date": "27/09/69 23:30 น."},
    {"stn": "STN3", "name": "ปกติ", "stn_type": "RF", "latitude": "15", "longitude": "100", "status": "0", "date": "02/10/69 13:15 น."},
    {"stn": "STN4", "name": "ไม่ทราบ", "stn_type": "RF", "latitude": "15", "longitude": "100", "status": "9", "date": "02/10/69 13:15 น."},
    {"stn": "STN5", "name": "ไม่มีพิกัด", "status": "1", "latitude": "", "longitude": "", "date": "02/10/69 13:15 น."},
    {"stn": "STN6", "name": "วันที่เสีย", "stn_type": "RF", "latitude": "15", "longitude": "100", "status": "1", "date": "???", "wl": "N/A"},
]


def test_parse_date_thai_time_buddhist_year():
    assert ews.parse_date("02/10/69 13:15 น.") == datetime(2026, 10, 2, 6, 15, tzinfo=timezone.utc)
    assert ews.parse_date("???") is None and ews.parse_date(None) is None


def test_only_flagged_stations_with_coordinates_most_severe_first():
    out = ews.warnings(ROWS, NOW)
    assert [s["id"] for s in out] == ["STN1", "STN2", "STN6"]
    a, b, c = out
    assert a["label"] == "วิกฤต" and a["name"] == "บ้านก" and a["kind"] == "rain" and a["rain"] == 7.0 and a["wl"] == 0.0
    assert a["age_h"] == 0.8 and a["stale"] is False and a["updated"] == "2026-10-02T06:15:00Z"
    assert b["kind"] == "wl" and b["stale"] is True and b["wl"] == 3.52          # flag older than 24 h
    assert c["stale"] is True and c["updated"] is None and c["wl"] is None      # unreadable date counts as stale


def test_current_caches_and_serves_last_good_copy_when_source_fails():
    ews._cache.update(at=0.0, data=None)
    calls = []
    def loader():
        calls.append(1); return ROWS
    first = ews.current(loader, now=lambda: 1000.0)
    assert first["total"] == 6 and len(first["stations"]) == 3 and first["cached"] is False
    ews.current(loader, now=lambda: 1100.0)
    assert len(calls) == 1                                                       # within 10 min: no second fetch
    def broken():
        raise OSError("down")
    again = ews.current(broken, now=lambda: 5000.0)
    assert again["cached"] is True and len(again["stations"]) == 3
    ews._cache.update(at=0.0, data=None)
    with pytest.raises(OSError):
        ews.current(broken, now=lambda: 9000.0)


def test_endpoint(monkeypatch):
    monkeypatch.setenv("EWS_ENABLED", "1")
    ews._cache.update(at=0.0, data=None)
    monkeypatch.setattr(ews, "fetch", lambda: ROWS)
    r = TestClient(api.app).get("/api/ews/warnings")
    assert r.status_code == 200 and len(r.json()["stations"]) == 3 and "stale-while-revalidate" in r.headers["cache-control"]
    ews._cache.update(at=0.0, data=None)
    monkeypatch.setattr(ews, "fetch", lambda: (_ for _ in ()).throw(OSError("down")))
    r = TestClient(api.app).get("/api/ews/warnings")
    assert r.status_code == 502 and "down" not in r.text and "OSError" not in r.text      # nothing about the failure reaches visitors
    ews._cache.update(at=0.0, data=None)

from datetime import datetime, timezone
import sqlite3
import pytest
from fastapi.testclient import TestClient

import api, ingest as ig
from conftest import real_rows


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("WATER_DB", str(db))
    monkeypatch.setattr(api, "now", lambda: datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc))  # 23:30 TH
    c = sqlite3.connect(db); c.executescript(ig.SCHEMA)
    ig.save(c, real_rows()); c.close()
    return TestClient(api.app)


def test_stations_flags_stale_by_reading_time(client):
    by = {s["id"]: s for s in client.get("/stations").json()}
    assert by["505018"]["stale"] is False and by["505018"]["age_min"] == 30
    assert by["1098950"]["stale"] is True  # 13:00 TH is ~10.5h old


def test_filter_and_bad_status_rejected(client):
    assert len(client.get("/stations", params={"province": "กาญจน"}).json()) == 1
    assert len(client.get("/stations", params={"status": "alert"}).json()) == 2
    assert client.get("/stations", params={"status": "bogus"}).status_code == 422


def test_nearby_orders_by_distance_and_validates(client):
    r = client.get("/stations/nearby", params={"lat": 14.2, "lng": 99.0, "limit": 2}).json()
    assert [x["id"] for x in r] == ["505018", "1098950"] and r[0]["distance_km"] < 10
    assert client.get("/stations/nearby", params={"lat": 14.2, "lng": 99.0, "fresh_only": True}).json()[0]["id"] == "505018"
    assert len(client.get("/stations/nearby", params={"lat": 14.2, "lng": 99.0, "fresh_only": True}).json()) == 1
    assert client.get("/stations/nearby", params={"lat": 999, "lng": 0}).status_code == 422


def test_readings_and_404(client):
    r = client.get("/stations/505018/readings", params={"days": 7}).json()
    assert len(r) == 1 and r[0]["status"] == "alert"
    assert client.get("/stations/nope/readings").status_code == 404


def test_health_and_empty_db(client, tmp_path, monkeypatch):
    h = client.get("/health").json()
    assert (h["stations"], h["stale"], h["latest_reading"]) == (2, 1, "2026-10-01T16:00:00Z")
    assert h["ingest_ok"] is False and h["last_ingest_age_min"] is None  # no ingest_runs row yet
    monkeypatch.setenv("WATER_DB", str(tmp_path / "empty.db"))
    assert client.get("/health").json()["stations"] == 0

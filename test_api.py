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
    assert h["latest_reading_age_min"] == 30                             # fixture clock is 30 min after the newest reading
    monkeypatch.setenv("WATER_DB", str(tmp_path / "empty.db"))
    assert client.get("/health").json()["stations"] == 0


class _FakeResp:
    def __init__(self, body=b"\x89PNG", ctype="image/png"):
        self._b, self.headers = body, {"Content-Type": ctype}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_gistda_layer_is_off_without_a_key(client, monkeypatch):
    monkeypatch.delenv("GISTDA_API_KEY", raising=False)
    assert client.get("/api/gistda/status").json()["enabled"] is False
    assert client.get("/api/gistda/flood/7days/8/200/120").status_code == 404


def test_gistda_tile_proxy_keeps_key_server_side(client, monkeypatch):
    monkeypatch.setenv("GISTDA_API_KEY", "secret-key")
    seen = {}
    def fake(url, timeout=0):
        seen["url"] = url; return _FakeResp()
    monkeypatch.setattr(api.urllib.request, "urlopen", fake)
    assert client.get("/api/gistda/status").json()["enabled"] is True
    r = client.get("/api/gistda/flood/7days/8/200/120")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "secret-key" in seen["url"] and "/flood/7days/tms/8/200/120" in seen["url"]
    assert "secret-key" not in r.text and "secret-key" not in str(r.headers)


def test_gistda_tile_rejects_bad_period_and_upstream_errors(client, monkeypatch):
    monkeypatch.setenv("GISTDA_API_KEY", "k")
    assert client.get("/api/gistda/flood/1year/8/200/120").status_code == 404
    import urllib.error
    def boom(url, timeout=0): raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)
    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    assert client.get("/api/gistda/flood/7days/8/200/120").status_code == 502
    def html(url, timeout=0): return _FakeResp(b"<html>", "text/html")
    monkeypatch.setattr(api.urllib.request, "urlopen", html)
    assert client.get("/api/gistda/flood/7days/8/200/120").status_code == 502


def test_gistda_tiles_are_only_served_for_thailand(client, monkeypatch):
    monkeypatch.setenv("GISTDA_API_KEY", "k")
    calls = []
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda url, timeout=0: calls.append(url) or _FakeResp())
    assert client.get("/api/gistda/flood/7days/8/200/120").status_code == 200        # around Bangkok
    assert client.get("/api/gistda/flood/7days/6/50/30").status_code == 200          # a coarser tile that still covers Thailand
    for path in ("7days/8/1/1",                                                      # open sea off Alaska
                 "7days/8/200/150",                                                  # south of Thailand
                 "7days/8/120/120",                                                  # western India
                 "7days/3/5/3", "7days/16/52000/30000",                              # zoom outside what the layer uses
                 "7days/22/4000000/2000000", "7days/-1/0/0"):
        assert client.get("/api/gistda/flood/" + path).status_code in (404, 422), path
    assert len(calls) == 2                                                           # nothing outside the box reached GISTDA


def test_tile_in_thailand_edges():
    assert api.tile_in_thailand(8, 200, 120) and not api.tile_in_thailand(8, 0, 0) and not api.tile_in_thailand(4, 12, 7)


def test_docs_and_the_schema_are_off_unless_asked_for(client):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404, p


def test_docs_can_be_switched_on_for_local_work(monkeypatch):
    import importlib
    monkeypatch.setenv("ENABLE_DOCS", "1")
    app = importlib.reload(api).app
    try:
        from fastapi.testclient import TestClient
        assert TestClient(app).get("/openapi.json").status_code == 200
    finally:
        monkeypatch.delenv("ENABLE_DOCS")
        importlib.reload(api)


def test_forwarded_for_is_only_believed_behind_a_trusted_proxy(monkeypatch):
    class Req:
        headers = {"x-forwarded-for": "9.9.9.9, 1.1.1.1"}
        client = type("C", (), {"host": "10.0.0.5"})()
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    assert api.request_ip(Req) == "10.0.0.5"                                         # a plain deployment: the header is the visitor's to forge
    monkeypatch.setenv("VERCEL", "1")
    assert api.request_ip(Req) == "9.9.9.9"                                          # Vercel rewrites it
    monkeypatch.delenv("VERCEL")
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert api.request_ip(Req) == "9.9.9.9"


def test_a_forged_forwarded_for_does_not_dodge_the_rate_limit_without_a_proxy(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    c = TestClient(api.app)
    codes = [c.get("/stations", headers={"x-forwarded-for": f"7.7.7.{i}"}).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]                         # a new invented address per request changes nothing

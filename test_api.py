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


def test_cross_site_writes_are_refused_but_our_own_pages_and_tools_can_post(client):
    body = {"lat": 14.2, "lng": 100.5, "level": 2}
    assert client.post("/api/flood-reports", json=body, headers={"origin": "https://evil.example", "x-forwarded-for": "4.4.4.1"}).status_code == 403
    assert client.post("/api/flood-reports/1/flag", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/flood-reports", json=body, headers={"origin": "http://testserver", "x-forwarded-for": "4.4.4.2"}).status_code == 201   # our own page
    assert client.post("/api/flood-reports", json=body, headers={"x-forwarded-for": "4.4.4.3"}).status_code == 201                                     # a script: no Origin at all
    assert client.get("/stations", headers={"origin": "https://evil.example"}).status_code == 200                                                        # reads stay open


def test_a_cross_site_preflight_for_a_post_is_not_granted(client):
    r = client.options("/api/flood-reports", headers={"origin": "https://evil.example", "access-control-request-method": "POST"})
    assert r.status_code == 400 or "POST" not in r.headers.get("access-control-allow-methods", "")


def _ingest_ran(minutes_ago, ok=1, error=None):
    from datetime import timedelta
    with api.conn() as c:
        c.execute("INSERT INTO ingest_runs(ts,ok,stations,readings,skipped,error) VALUES(?,?,?,?,?,?)",
                  (api.core.iso(api.now() - timedelta(minutes=minutes_ago)), ok, 2, 2, 0, error))
        c.commit()


def test_health_monitor_is_503_until_ingest_has_run_and_200_when_data_flows(client):
    r = client.get("/health/monitor")
    assert r.status_code == 503 and r.json()["ok"] is False and "ingest has not succeeded recently" in r.json()["problems"][0]
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/health").status_code == 200                                  # /health itself never fails: the page reads it
    _ingest_ran(5)
    r = client.get("/health/monitor")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["problems"] == [] and r.json()["stations"] == 2


def test_health_monitor_says_why_it_fails(client):
    _ingest_ran(300, ok=1)                                                           # last success five hours ago
    r = client.get("/health/monitor")
    assert r.status_code == 503 and "ingest has not succeeded recently" in r.json()["problems"][0]
    _ingest_ran(1, ok=0, error="ConnectionError: boom")
    assert "boom" in client.get("/health/monitor").json()["problems"][0]


def test_health_monitor_fails_when_no_reading_is_newer_than_three_hours(client, monkeypatch):
    from datetime import timedelta
    _ingest_ran(5)
    assert client.get("/health/monitor").status_code == 200
    monkeypatch.setattr(api, "now", lambda: datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc) + timedelta(hours=4))
    _ingest_ran(5)                                                                   # ingest keeps succeeding, the source has nothing new
    r = client.get("/health/monitor")
    assert r.status_code == 503 and any("newest reading is" in p for p in r.json()["problems"])


def test_health_monitor_does_not_fail_just_because_many_stations_are_old(client):
    _ingest_ran(5)
    j = client.get("/health/monitor").json()
    assert j["stale"] >= 1 and j["ok"] is True                                       # one of two stations is hours old: that is not an outage


def test_health_monitor_with_an_empty_database_says_no_stations(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "empty2.db"))
    api._ready.clear()
    r = TestClient(api.app).get("/health/monitor")
    assert r.status_code == 503 and "no stations" in r.json()["problems"]


def _fake_rows(agencies, now_ts="2026-10-01T16:00:00Z"):
    """agencies: {name: (stations, how many of them have a reading 10 min old; the rest are 5 h old)}"""
    rows = []
    for name, (n, recent) in agencies.items():
        for i in range(n):
            fresh = i < recent
            rows.append({"id": f"{name}{i}", "source": name, "ts": now_ts if fresh else "2026-10-01T11:00:00Z", "stale": not fresh,
                         "age_min": 10 if fresh else 300})
    return rows


def test_health_monitor_fails_when_a_whole_agency_goes_quiet_while_others_report(client, monkeypatch):
    _ingest_ran(5)
    monkeypatch.setattr(api.core, "freshness", lambda c, at, *a, **k: _fake_rows({"HII": (330, 6), "FOP": (89, 2), "RID": (314, 300), "EGAT": (72, 70)}))
    r = client.get("/health/monitor")
    assert r.status_code == 503 and r.json()["ok"] is False
    probs = " | ".join(r.json()["problems"])
    assert "agency HII has gone quiet: 6 of 330" in probs and "agency FOP has gone quiet: 2 of 89" in probs and "RID" not in probs and "EGAT" not in probs
    assert r.json()["agencies"]["RID"] == {"stations": 314, "recent": 300}
    assert r.json()["ingest_ok"] is True and r.json()["latest_reading_age_min"] is not None        # the old checks alone would have said 200


def test_health_monitor_tolerates_the_normal_share_of_dead_stations_and_small_agencies(client, monkeypatch):
    _ingest_ran(5)
    monkeypatch.setattr(api.core, "freshness", lambda c, at, *a, **k: _fake_rows({"HII": (330, 295), "FOP": (89, 88), "RID": (314, 295), "EGAT": (72, 69), "TINY": (6, 0)}))
    r = client.get("/health/monitor")
    assert r.status_code == 200 and r.json()["problems"] == []                                       # ~11 % dead HII stations are normal; a 6-station agency is too small to judge


def test_health_lists_agencies_that_have_gone_quiet_for_the_web_notice(client, monkeypatch):
    monkeypatch.setattr(api.core, "freshness", lambda c, at, *a, **k: _fake_rows({"HII": (330, 6), "RID": (314, 300), "TINY": (6, 0)}))
    assert client.get("/health").json()["quiet_agencies"] == ["HII"]                  # too-small agencies are not judged
    monkeypatch.setattr(api.core, "freshness", lambda c, at, *a, **k: _fake_rows({"HII": (330, 295), "RID": (314, 300)}))
    assert client.get("/health").json()["quiet_agencies"] == []


def test_a_second_scheduler_call_within_minutes_of_a_good_ingest_is_skipped_but_still_needs_the_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret")
    monkeypatch.setenv("INGEST_MIN_GAP_MIN", "8")
    H = {"Authorization": "Bearer s3cret-s3cret-s3cret"}
    ran = []
    monkeypatch.setattr(api.ingest, "run_ingest", lambda c, thresholds=None: ran.append(1) or (2, 2, 0, 0))
    _ingest_ran(3)                                                               # a good ingest 3 minutes ago (helper from the monitor tests)
    r = client.get("/api/cron/ingest", headers=H)
    skipped = lambda j: isinstance(j.get("skipped"), str)                         # a real run also has "skipped": a count of readings
    assert r.status_code == 200 and skipped(r.json()) and "3 min ago" in r.json()["skipped"] and ran == []
    assert client.get("/api/cron/ingest", headers={"Authorization": "Bearer wrong"}).status_code == 401           # the secret is still checked first
    with api.conn() as c:
        c.execute("DELETE FROM ingest_runs"); c.commit()
    _ingest_ran(12)                                                              # ...but 12 minutes ago is long enough: it runs
    monkeypatch.setattr(api.notify, "run", lambda c, at: (0, 0))
    monkeypatch.setattr(api.notify, "run_digest", lambda c, at: (0, 0))
    r = client.get("/api/cron/ingest", headers=H)
    assert r.status_code == 200 and not skipped(r.json()) and ran == [1], r.json()
    monkeypatch.setenv("INGEST_MIN_GAP_MIN", "0")
    _ingest_ran(1)
    assert not skipped(client.get("/api/cron/ingest", headers=H).json()) and ran == [1, 1]                      # 0 turns the rule off


def test_nearby_still_gives_trends_for_the_nearest_stations_but_a_long_list_falls_back_to_the_full_read(client):
    short = client.get("/stations/nearby", params={"lat": 14.35, "lng": 100.57, "limit": 2}).json()
    assert len(short) == 2 and all("trend" in s for s in short)
    long_ = client.get("/stations/nearby", params={"lat": 14.35, "lng": 100.57, "limit": 20}).json()
    assert len(long_) >= 2 and [s["id"] for s in long_[:2]] == [s["id"] for s in short]

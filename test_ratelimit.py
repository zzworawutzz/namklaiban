from fastapi.testclient import TestClient

import api
import ratelimit
from ratelimit import Limiter


def test_window_counts_resets_and_reports_retry_after():
    t = [1000.0]
    lim = Limiter(clock=lambda: t[0])
    assert [lim.check("a", 3)[0] for _ in range(4)] == [True, True, True, False]
    ok, retry = lim.check("a", 3)
    assert not ok and 1 <= retry <= 60
    assert lim.check("b", 3)[0]                    # someone else is unaffected
    t[0] += 61
    assert lim.check("a", 3)[0]                    # a new minute starts fresh
    assert lim.check("a", 0) == (True, 0)          # a limit of 0 means "off"


def test_old_visitors_are_forgotten_when_the_table_grows(monkeypatch):
    t = [0.0]
    lim = Limiter(clock=lambda: t[0])
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 5)
    for i in range(5):
        lim.check(f"ip{i}", 10)
    t[0] += 120
    lim.check("new", 10)
    lim.check("new2", 10)
    assert len(lim.hits) <= 3


def test_which_paths_are_limited():
    assert ratelimit.applies("/stations") and ratelimit.applies("/stations/5/readings") and ratelimit.applies("/api/suggest")
    assert not ratelimit.applies("/health") and not ratelimit.applies("/index.html") and not ratelimit.applies("/api/cron/ingest")
    assert not ratelimit.applies("/api/line/webhook")


def test_api_answers_429_after_the_limit_per_visitor(monkeypatch, tmp_path):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    api._ready.clear()
    c = TestClient(api.app)
    me = {"x-forwarded-for": "1.2.3.4"}
    assert [c.get("/stations", headers=me).status_code for _ in range(3)] == [200, 200, 200]
    r = c.get("/stations", headers=me)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "ถี่เกินไป" in r.json()["detail"]
    assert "content-security-policy" in r.headers                      # even the refusal carries the security headers
    assert c.get("/stations", headers={"x-forwarded-for": "5.6.7.8"}).status_code == 200   # another visitor
    for _ in range(6):                                                 # health checks and cron are never limited
        assert c.get("/health", headers=me).status_code == 200
        assert c.get("/api/cron/test-alert", headers=me).status_code != 429


def test_limit_can_be_switched_off(monkeypatch, tmp_path):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    api._ready.clear()
    c = TestClient(api.app)
    assert all(c.get("/stations").status_code == 200 for _ in range(30))

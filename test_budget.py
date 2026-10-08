import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import budget, notify, ingest as ig

AT = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "b.db")
    c.row_factory = sqlite3.Row
    ig.init_db(c)
    return c


def test_only_alert_level_news_goes_out_once_the_reserve_is_all_that_is_left(conn):
    b = budget.Budget(conn, AT, lambda: (300, 292))               # 8 left, reserve is 15
    assert b.allow(False) is False and b.allow(True) is True
    assert budget.Budget(conn, AT + timedelta(minutes=31), lambda: (300, 200)).allow(False) is True


def test_reserve_is_five_percent_of_the_limit_and_at_least_ten():
    assert budget.reserve(300) == 15 and budget.reserve(100) == 10 and budget.reserve(1000) == 50


def test_messages_sent_in_this_run_count_against_the_budget(conn):
    b = budget.Budget(conn, AT, lambda: (300, 284))               # 16 left: one more than the reserve
    assert b.allow(False) is True
    b.spent()
    assert b.allow(False) is False


def test_unknown_or_unlimited_never_holds_anything(conn):
    def boom():
        raise RuntimeError("HTTP 500")
    assert budget.Budget(conn, AT, boom).allow(False) is True       # LINE cannot be asked
    assert budget.Budget(conn, AT, lambda: (None, 5000)).allow(False) is True   # no cap on this plan


def test_line_is_asked_at_most_every_half_hour(conn):
    calls = []
    def fetch():
        calls.append(1)
        return 300, 10
    budget.Budget(conn, AT, fetch).allow(False)
    budget.Budget(conn, AT + timedelta(minutes=29), fetch).allow(False)
    assert len(calls) == 1
    budget.Budget(conn, AT + timedelta(minutes=31), fetch).allow(False)
    assert len(calls) == 2


def _line_run(conn, monkeypatch, used, status, prev, at=AT):
    monkeypatch.setattr(notify, "line_quota", lambda: (300, used))
    st = {"id": "s", "name": "สถานีทดสอบ", "province": "P", "status": status, "pct_of_bank": 95.0 if status == "alert" else 40.0,
          "distance_km": 1.0, "advice": "x", "trend": None, "trend_pct_per_hr": None, "eta_to_bank_h": None,
          "stale": False, "age_min": 5, "twin_conflict": False, "twins": [], "rise_3h_m": None, "lat": 14.2, "lng": 99.0,
          "water_level": 1.0, "source": "RID"}
    monkeypatch.setattr(notify.core, "latest", lambda c, a, **kw: [st])
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,last_status) VALUES('line','U1',14.2,99.0,'บ้าน',?)", (prev,))
    conn.commit()
    out = []
    n = notify.run(conn, at, {"line": lambda t, m, **kw: out.append(m)})
    return n, out


def test_alert_goes_out_but_a_normal_change_waits_when_the_quota_is_nearly_gone(conn, monkeypatch):
    n, out = _line_run(conn, monkeypatch, 292, "alert", "watch")
    assert n == (1, 0) and len(out) == 1                              # alert level is never held
    conn.execute("DELETE FROM subscriptions")
    n, out = _line_run(conn, monkeypatch, 292, "normal", "watch", AT + timedelta(hours=1))
    assert n == (0, 0) and out == []
    assert conn.execute("SELECT last_status FROM subscriptions").fetchone()[0] == "watch"   # still pending: sent later


def test_a_normal_change_is_sent_when_there_is_plenty_of_quota(conn, monkeypatch):
    n, out = _line_run(conn, monkeypatch, 100, "normal", "watch")
    assert n == (1, 0)


def test_the_summary_waits_when_the_quota_is_nearly_gone_and_goes_out_otherwise(conn, monkeypatch):
    from conftest import real_rows
    ig.save(conn, real_rows())
    at = datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc)            # 07:30 Thai time
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('line','U1',14.2,99.0,'บ้าน')")
    conn.commit()
    monkeypatch.setattr(notify.rainalert, "for_province", lambda rows, prov: None)
    monkeypatch.setattr(notify.gauges, "heavy_in", lambda prov: None)
    out = []
    send = {"line": lambda t, m, **kw: out.append(m)}
    monkeypatch.setattr(notify, "line_quota", lambda: (300, 295))
    assert notify.run_digest(conn, at, send) == (0, 0) and out == []     # held, not failed
    monkeypatch.setattr(notify, "line_quota", lambda: (300, 50))
    conn.execute("DELETE FROM alert_state")                              # forget the cached answer
    assert notify.run_digest(conn, at + timedelta(minutes=20), send) == (1, 0) and len(out) == 1

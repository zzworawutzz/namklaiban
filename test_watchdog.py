import sqlite3
from datetime import datetime, timedelta, timezone

import ingest as ig
import watchdog
from conftest import real_rows

AT = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


def setup(tmp_path, monkeypatch, admin="UADMIN"):
    if admin:
        monkeypatch.setenv("ADMIN_LINE_ID", admin)
    else:
        monkeypatch.delenv("ADMIN_LINE_ID", raising=False)
    c = sqlite3.connect(tmp_path / "w.db")
    c.row_factory = sqlite3.Row
    ig.init_db(c)
    ig.save(c, real_rows())  # newest reading 2026-10-01T16:00Z -> fresh at AT
    return c


def run(c, ts, ok, error=None):
    c.execute("INSERT INTO ingest_runs(ts,ok,error) VALUES(?,?,?)", (ts.strftime("%Y-%m-%dT%H:%M:%SZ"), int(ok), error))
    c.commit()


def test_healthy_is_silent(tmp_path, monkeypatch):
    c, out = setup(tmp_path, monkeypatch), []
    run(c, AT - timedelta(minutes=5), True)
    assert watchdog.check(c, AT, lambda t, m: out.append((t, m))) is None and out == []


def test_short_failure_is_not_reported(tmp_path, monkeypatch):
    c, out = setup(tmp_path, monkeypatch), []
    run(c, AT - timedelta(minutes=30), True)
    run(c, AT - timedelta(minutes=10), False, "URLError: timed out")
    assert watchdog.check(c, AT, lambda t, m: out.append(m)) is None and out == []


def test_long_failure_alerts_once_then_reminds_then_recovers(tmp_path, monkeypatch):
    c, out = setup(tmp_path, monkeypatch), []
    send = lambda t, m: out.append((t, m))
    run(c, AT - timedelta(minutes=90), True)
    run(c, AT - timedelta(minutes=10), False, "URLError: timed out")
    assert watchdog.check(c, AT, send) == "down"
    assert out[0][0] == "UADMIN" and "ไม่สำเร็จ" in out[0][1] and "timed out" in out[0][1]
    assert watchdog.check(c, AT + timedelta(minutes=20), send) is None      # same outage: quiet
    assert watchdog.check(c, AT + timedelta(hours=6), send) == "remind"     # still down 6 h later
    run(c, AT + timedelta(hours=6, minutes=5), True)
    c.execute("UPDATE readings SET ts=?", ((AT + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
    c.commit()
    assert watchdog.check(c, AT + timedelta(hours=6, minutes=10), send) == "recovered"
    assert "กลับมา" in out[-1][1]
    assert watchdog.check(c, AT + timedelta(hours=7), send) is None         # and stays quiet


def test_never_succeeded_alerts(tmp_path, monkeypatch):
    c, out = setup(tmp_path, monkeypatch), []
    run(c, AT, False, "ValueError: no rows")
    assert watchdog.check(c, AT, lambda t, m: out.append(m)) == "down" and "ยังไม่เคยดึงสำเร็จ" in out[0]


def test_stale_data_alerts_even_when_ingest_succeeds(tmp_path, monkeypatch):
    c, out = setup(tmp_path, monkeypatch), []
    late = AT + timedelta(hours=8)
    run(c, late - timedelta(minutes=5), True)
    assert watchdog.check(c, late, lambda t, m: out.append(m)) == "down" and "ไม่มีสถานีไหนอัปเดต" in out[0]


def test_no_admin_id_sends_nothing(tmp_path, monkeypatch):
    c = setup(tmp_path, monkeypatch, admin=None)
    run(c, AT, False, "boom")
    assert watchdog.check(c, AT, lambda t, m: 1 / 0) is None


def test_failed_send_is_retried_and_never_raises(tmp_path, monkeypatch):
    c, out = setup(tmp_path, monkeypatch), []
    run(c, AT, False, "boom")
    assert watchdog.check(c, AT, lambda t, m: 1 / 0) is None   # LINE down: swallowed, state not saved
    assert watchdog.check(c, AT + timedelta(minutes=20), lambda t, m: out.append(m)) == "down"


def test_test_alert_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import api, notify
    sent = []
    monkeypatch.setattr(notify, "send_line", lambda t, m, flex=None: sent.append((t, m)))
    monkeypatch.setenv("CRON_SECRET", "s")
    c = TestClient(api.app)
    assert c.get("/api/cron/test-alert").status_code == 401
    monkeypatch.delenv("ADMIN_LINE_ID", raising=False)
    assert c.get("/api/cron/test-alert", headers={"Authorization": "Bearer s"}).status_code == 400
    monkeypatch.setenv("ADMIN_LINE_ID", "UADMIN")
    r = c.get("/api/cron/test-alert", headers={"Authorization": "Bearer s"})
    assert r.status_code == 200 and sent[0][0] == "UADMIN" and "ทดสอบ" in sent[0][1]

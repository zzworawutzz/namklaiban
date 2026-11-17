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


def test_storage_warns_once_a_day_and_rearms(tmp_path, monkeypatch):
    c, out = setup(tmp_path, monkeypatch), []
    send = lambda t, m: out.append(m)
    assert watchdog.check_storage(c, AT, send, size_mb=300) is None and out == []   # 59%: quiet
    assert watchdog.check_storage(c, AT, send, size_mb=420) == "warn" and "82%" in out[0]
    assert watchdog.check_storage(c, AT + timedelta(hours=3), send, size_mb=430) is None  # same day
    assert watchdog.check_storage(c, AT + timedelta(hours=25), send, size_mb=440) == "warn"
    assert watchdog.check_storage(c, AT + timedelta(hours=26), send, size_mb=100) is None  # re-arm
    assert watchdog.check_storage(c, AT + timedelta(hours=27), send, size_mb=420) == "warn"
    assert len(out) == 3


def test_storage_needs_admin_and_reads_real_size(tmp_path, monkeypatch):
    c = setup(tmp_path, monkeypatch, admin=None)
    assert watchdog.check_storage(c, AT, lambda t, m: 1 / 0, size_mb=500) is None
    assert watchdog.db_size_mb(c) > 0


# ---- station counts per agency -------------------------------------------------------------------

def feed(c, sizes, at):
    """Pretend the latest ingest saved `sizes` = {agency: n} stations (the earlier ones keep their older updated_at)."""
    c.execute("DELETE FROM stations")
    stamp = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    for agency, n in sizes.items():
        for i in range(n):
            c.execute("INSERT INTO stations(id,name,source,lat,lng,updated_at,first_seen) VALUES(?,?,?,?,?,?,?)",
                      (f"{agency}{i}", "x", agency, 14.0, 100.0, stamp, stamp))
    c.commit()


def feed_setup(tmp_path, monkeypatch, admin="UADMIN"):
    c = setup(tmp_path, monkeypatch, admin)
    run(c, AT - timedelta(minutes=5), True)
    c.execute("DELETE FROM readings")   # keep the "no station reported" check out of these tests
    c.execute("INSERT INTO readings(station_id,ts,water_level,pct_of_bank,status) SELECT id,?,1,10,'normal' FROM stations LIMIT 1",
              ((AT - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
    c.commit()
    return c


def test_feed_size_first_run_only_records(tmp_path, monkeypatch):
    c, out = feed_setup(tmp_path, monkeypatch), []
    feed(c, {"RID": 300, "HII": 320}, AT)
    assert watchdog.check_feed_changes(c, AT, lambda t, m: out.append(m)) == [] and out == []
    assert watchdog._marks(c, watchdog.FEED_PREFIX) == {"RID": 300, "HII": 320}


def test_feed_size_small_moves_are_ignored(tmp_path, monkeypatch):
    c, out = feed_setup(tmp_path, monkeypatch), []
    feed(c, {"RID": 300, "HII": 320}, AT)
    watchdog.check_feed_changes(c, AT, lambda t, m: out.append(m))
    feed(c, {"RID": 296, "HII": 330}, AT + timedelta(minutes=15))   # -4 stations; +10 but only 3%
    for i in (1, 2, 3):
        assert watchdog.check_feed_changes(c, AT + timedelta(minutes=15 * i), lambda t, m: out.append(m)) == []
    assert out == []


def test_feed_drop_is_reported_after_two_runs_and_only_once(tmp_path, monkeypatch):
    c, out = feed_setup(tmp_path, monkeypatch), []
    send = lambda t, m: out.append((t, m))
    feed(c, {"RID": 300, "HII": 320}, AT)
    watchdog.check_feed_changes(c, AT, send)
    feed(c, {"RID": 200, "HII": 320}, AT + timedelta(minutes=15))
    assert watchdog.check_feed_changes(c, AT + timedelta(minutes=15), send) == [] and out == []   # seen once: wait
    assert watchdog.check_feed_changes(c, AT + timedelta(minutes=30), send) == ["RID"]
    assert out[0][0] == "UADMIN" and "RID: 300 → 200" in out[0][1] and "−100" in out[0][1] and "HII" not in out[0][1]
    assert watchdog.check_feed_changes(c, AT + timedelta(minutes=45), send) == [] and len(out) == 1   # 200 is the reference now


def test_one_bad_response_is_not_reported(tmp_path, monkeypatch):
    c, out = feed_setup(tmp_path, monkeypatch), []
    send = lambda t, m: out.append(m)
    feed(c, {"RID": 300}, AT)
    watchdog.check_feed_changes(c, AT, send)
    feed(c, {"RID": 100}, AT + timedelta(minutes=15))
    watchdog.check_feed_changes(c, AT + timedelta(minutes=15), send)
    feed(c, {"RID": 300}, AT + timedelta(minutes=30))              # the next run is back to normal
    assert watchdog.check_feed_changes(c, AT + timedelta(minutes=30), send) == [] and out == []
    assert watchdog._marks(c, watchdog.FEED_PENDING) == {}


def test_agency_vanishing_and_new_agency_reported(tmp_path, monkeypatch):
    c, out = feed_setup(tmp_path, monkeypatch), []
    send = lambda t, m: out.append(m)
    feed(c, {"RID": 300, "HII": 320}, AT)
    watchdog.check_feed_changes(c, AT, send)
    for i in (1, 2):
        feed(c, {"RID": 300, "NEW": 40}, AT + timedelta(minutes=15 * i))
        done = watchdog.check_feed_changes(c, AT + timedelta(minutes=15 * i), send)
    assert done == ["HII", "NEW"] and "HII: 320 → 0" in out[0] and "NEW: 0 → 40" in out[0] and "+40" in out[0]


def test_new_stations_are_counted_in_the_message(tmp_path, monkeypatch):
    c, out = feed_setup(tmp_path, monkeypatch), []
    send = lambda t, m: out.append(m)
    feed(c, {"RID": 300}, AT)
    watchdog.check_feed_changes(c, AT, send)
    for i in (1, 2):
        feed(c, {"RID": 330}, AT + timedelta(minutes=15 * i))       # 330 rows all first seen just now
        watchdog.check_feed_changes(c, AT + timedelta(minutes=15 * i), send)
    assert "RID: 300 → 330" in out[0] and "เพิ่งพบครั้งแรกใน 24 ชม.: 330" in out[0]


def test_feed_size_without_admin_id_still_moves_reference(tmp_path, monkeypatch):
    c = feed_setup(tmp_path, monkeypatch, admin=None)
    feed(c, {"RID": 300}, AT)
    watchdog.check_feed_changes(c, AT)
    for i in (1, 2):
        feed(c, {"RID": 200}, AT + timedelta(minutes=15 * i))
        done = watchdog.check_feed_changes(c, AT + timedelta(minutes=15 * i))
    assert done == ["RID"] and watchdog._marks(c, watchdog.FEED_PREFIX) == {"RID": 200}


def test_feed_size_is_silent_while_ingest_is_down(tmp_path, monkeypatch):
    c, out = feed_setup(tmp_path, monkeypatch), []
    feed(c, {"RID": 300}, AT)
    watchdog.check_feed_changes(c, AT, lambda t, m: out.append(m))
    run(c, AT - timedelta(minutes=1), False, "URLError: timed out")
    run(c, AT - timedelta(minutes=2), False, "URLError: timed out")
    c.execute("DELETE FROM ingest_runs WHERE ok=1")
    c.commit()
    feed(c, {"RID": 100}, AT)
    assert watchdog.problem(c, AT) and watchdog.check_feed_changes(c, AT, lambda t, m: out.append(m)) == [] and out == []

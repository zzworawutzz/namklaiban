import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
import core
import ingest as ig
import notify
import sendlog
import watchdog

AT = datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)   # 10:00 in Thailand


def make(tmp_path, admin="UADMIN", monkeypatch=None):
    if monkeypatch is not None:
        if admin:
            monkeypatch.setenv("ADMIN_LINE_ID", admin)
        else:
            monkeypatch.delenv("ADMIN_LINE_ID", raising=False)
    c = sqlite3.connect(tmp_path / "o.db")
    c.row_factory = sqlite3.Row
    ig.init_db(c)
    return c


def add_station(c, sid, src, prov, age_h):
    c.execute("INSERT INTO stations(id,name,source,province,lat,lng) VALUES(?,?,?,?,?,?)", (sid, sid, src, prov, 14.0, 100.0))
    if age_h is not None:
        c.execute("INSERT INTO readings VALUES(?,?,?,?,?)", (sid, core.iso(AT - timedelta(hours=age_h)), 1.0, 50.0, "normal"))
    c.commit()


def ingest_ok(c):
    c.execute("INSERT INTO ingest_runs(ts,ok) VALUES(?,1)", (core.iso(AT - timedelta(minutes=5)),))
    c.commit()


# ---- send log ----

def test_send_log_summary_and_prune(tmp_path):
    c = make(tmp_path)
    for kind, prov, ok, hours in [("early", "A", 1, 1), ("fast", "A", 1, 2), ("fast", "B", 0, 3), ("digest", "A", 1, 30)]:
        sendlog.record(c, AT - timedelta(hours=hours), kind, prov, bool(ok))
    sendlog.record(c, AT - timedelta(days=70), "status", "A")
    c.commit()
    s = sendlog.summary(c, AT, 7)
    today = next(d for d in s["days"] if d["day"] == "2026-10-04")
    assert today["total"] == 3 and today["failed"] == 1 and today["by_kind"] == {"early": 1, "fast": 2}
    assert s["top_provinces"][0] == {"province": "A", "messages": 3}
    sendlog.prune(c, AT)
    assert c.execute("SELECT COUNT(*) FROM send_log").fetchone()[0] == 4   # the 70-day-old row is gone


def test_notify_run_logs_what_it_sends(tmp_path, monkeypatch):
    import test_notify as tn
    st = tn._station(trend="steady", eta_to_bank_h=None, status="normal", pct_of_bank=60.0, rise_3h_m=0.62)
    c, out, go = tn._run(tmp_path, monkeypatch, st)
    assert go() == (1, 0)
    row = c.execute("SELECT kind, province, ok FROM send_log").fetchone()
    assert (row["kind"], row["province"], row["ok"]) == ("fast", "อยุธยา", 1)


# ---- LINE quota ----

def test_line_quota_warns_at_80_percent_checks_rarely_and_rearms(tmp_path, monkeypatch):
    c, out = make(tmp_path, monkeypatch=monkeypatch), []
    send = lambda t, m: out.append(m)
    assert watchdog.check_line_quota(c, AT, send, lambda: (200, 100)) is None and out == []       # 50%
    later = AT + timedelta(hours=7)
    assert watchdog.check_line_quota(c, later, send, lambda: (200, 170)) == "warn" and "170 จาก 200" in out[0] and "เหลือ 30" in out[0]
    assert watchdog.check_line_quota(c, later + timedelta(hours=1), send, lambda: 1 / 0) is None    # not asked again so soon
    assert watchdog.check_line_quota(c, later + timedelta(hours=7), send, lambda: (200, 180)) is None  # warned < 24 h ago
    assert watchdog.check_line_quota(c, later + timedelta(hours=25), send, lambda: (200, 190)) == "warn"
    assert watchdog.check_line_quota(c, later + timedelta(hours=40), send, lambda: (None, 5000)) is None  # no cap on this plan
    assert len(out) == 2


def test_line_quota_failure_or_no_admin_is_silent(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch=monkeypatch)
    assert watchdog.check_line_quota(c, AT, lambda t, m: 1 / 0, lambda: (_ for _ in ()).throw(OSError("down"))) is None
    monkeypatch.delenv("ADMIN_LINE_ID", raising=False)
    assert watchdog.check_line_quota(c, AT + timedelta(days=1), lambda t, m: 1 / 0, lambda: (10, 10)) is None


# ---- silent groups ----

def test_silent_group_is_reported_once_and_again_when_back(tmp_path, monkeypatch):
    c, out = make(tmp_path, monkeypatch=monkeypatch), []
    send = lambda t, m: out.append(m)
    for i in range(12):
        add_station(c, f"h{i}", "HII", f"P{i % 4}", 0.5)                  # a healthy agency
    for i in range(3):
        add_station(c, f"r{i}", "RID", "สุราษฎร์ธานี", 9)                 # one province's RID stations all quiet
    for i in range(5):
        add_station(c, f"e{i}", "RID", "นครสวรรค์", 0.5)
    ingest_ok(c)
    assert watchdog.silent_groups(c, AT) == ["RID จ.สุราษฎร์ธานี (3 สถานี)"]
    assert watchdog.check_silent(c, AT, send) == "silent" and "RID จ.สุราษฎร์ธานี" in out[0]
    assert watchdog.check_silent(c, AT + timedelta(hours=1), send) is None      # no repeat
    c.execute("INSERT INTO readings VALUES('r0',?,1.0,50,'normal')", (core.iso(AT + timedelta(hours=1)),))
    c.execute("INSERT INTO readings VALUES('r1',?,1.0,50,'normal')", (core.iso(AT + timedelta(hours=1)),))
    c.execute("INSERT INTO readings VALUES('r2',?,1.0,50,'normal')", (core.iso(AT + timedelta(hours=1)),))
    c.commit()
    assert watchdog.check_silent(c, AT + timedelta(hours=2), send) == "back" and "กลับมามีข้อมูลแล้ว" in out[1]


def test_whole_agency_going_quiet_is_one_group_and_total_outage_is_left_to_the_other_check(tmp_path, monkeypatch):
    c, out = make(tmp_path, monkeypatch=monkeypatch), []
    for i in range(10):
        add_station(c, f"a{i}", "RID", f"P{i}", 12 if i < 4 else 0.5)      # 40% of an agency silent, spread over provinces
    ingest_ok(c)
    assert watchdog.silent_groups(c, AT) == ["หน่วยงาน RID (4 จาก 10 สถานี)"]
    c.execute("DELETE FROM ingest_runs")
    c.execute("INSERT INTO ingest_runs(ts,ok) VALUES(?,1)", (core.iso(AT - timedelta(hours=5)),))
    c.execute("INSERT INTO ingest_runs(ts,ok,error) VALUES(?,0,'down')", (core.iso(AT - timedelta(hours=3)),))
    c.commit()
    assert watchdog.check_silent(c, AT, lambda t, m: out.append(m)) is None and out == []   # feed failure: check() reports it


# ---- the owner's stats endpoint ----

def test_stats_endpoint_needs_the_cron_secret_and_reports_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("CRON_SECRET", "sekret")
    monkeypatch.setattr(notify, "line_quota", lambda: (200, 12))
    api._ready.clear()
    client = TestClient(api.app)
    assert client.get("/api/cron/stats").status_code == 401
    with api.conn() as c:
        sendlog.record(c, api.now(), "digest", "ปทุมธานี")
    r = client.get("/api/cron/stats", headers={"Authorization": "Bearer sekret"})
    j = r.json()
    assert r.status_code == 200 and j["line_quota"] == {"limit": 200, "used": 12}
    assert j["days"][-1]["by_kind"] == {"digest": 1} and j["db_mb"] > 0 and j["silent_groups"] == []


# ---- monthly backup reminder ----

def test_backup_reminder_waits_a_month_then_repeats(tmp_path, monkeypatch):
    c, out = make(tmp_path, monkeypatch=monkeypatch), []
    send = lambda t, m: out.append(m)
    c.execute("INSERT INTO subscriptions(channel,target,lat,lng) VALUES('line','U1',14,100)")
    c.commit()
    assert watchdog.check_backup_reminder(c, AT, send) is None and out == []                      # clock starts, no message
    assert watchdog.check_backup_reminder(c, AT + timedelta(days=29), send) is None
    assert watchdog.check_backup_reminder(c, AT + timedelta(days=31), send) == "remind"
    assert "1 รายการ" in out[0] and "backup_export.py" in out[0]
    assert watchdog.check_backup_reminder(c, AT + timedelta(days=40), send) is None              # next one in a month
    assert watchdog.check_backup_reminder(c, AT + timedelta(days=62), send) == "remind"


def test_backup_reminder_can_be_turned_off(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setenv("BACKUP_REMIND", "0")
    assert watchdog.check_backup_reminder(c, AT, lambda t, m: 1 / 0) is None
    assert watchdog.check_backup_reminder(c, AT + timedelta(days=90), lambda t, m: 1 / 0) is None

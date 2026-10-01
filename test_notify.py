import sqlite3
from datetime import datetime, timezone

import notify, ingest as ig
from conftest import real_rows

AT = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)  # station 505018 is 30 min old -> fresh


def setup(tmp_path):
    c = sqlite3.connect(tmp_path / "n.db")
    c.row_factory = sqlite3.Row
    ig.init_db(c)
    ig.save(c, real_rows())
    c.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('stdout','U1',14.2,99.0,'บ้าน')")
    c.commit()
    return c


def test_first_run_notifies_alert_then_stays_quiet_then_notifies_on_change(tmp_path):
    c, out = setup(tmp_path), []
    senders = {"stdout": lambda t, m: out.append((t, m))}
    assert notify.run(c, AT, senders) == (1, 0)
    assert "เตือนภัย" in out[0][1] and "บ้านปากแซง" in out[0][1]
    assert notify.run(c, AT, senders) == (0, 0)  # same status -> silent
    c.execute("UPDATE subscriptions SET last_status='normal'")
    assert notify.run(c, AT, senders) == (1, 0)  # escalation


def test_normal_first_run_is_silent_baseline(tmp_path):
    c = setup(tmp_path)
    c.execute("UPDATE readings SET status='normal', pct_of_bank=10")
    assert notify.run(c, AT, {"stdout": lambda t, m: 1 / 0}) == (0, 0)
    assert c.execute("SELECT last_status FROM subscriptions").fetchone()[0] == "normal"


def test_failed_send_is_retried(tmp_path):
    c = setup(tmp_path)
    assert notify.run(c, AT, {"stdout": lambda t, m: 1 / 0}) == (0, 1)
    assert c.execute("SELECT last_status FROM subscriptions").fetchone()[0] is None
    assert notify.run(c, AT, {"stdout": lambda t, m: None}) == (1, 0)


def test_line_requires_token(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_TOKEN", raising=False)
    try:
        notify.send_line("U1", "x")
    except RuntimeError as e:
        assert "LINE_CHANNEL_TOKEN" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_message_mentions_twin_disagreement():
    st = dict(name="x", province="y", distance_km=1, status="alert", pct_of_bank=120, trend=None, eta_to_bank_h=None,
              advice="a", twin_conflict=True, twins=[dict(source="EGAT", status="normal")])
    m = notify.message(dict(label=""), st)
    assert "EGAT ปกติ" in m and "ตรวจกับหน่วยงาน" in m


def test_line_rejects_placeholder_token(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_TOKEN", "วาง token ที่เทอร์มินัลของคุณ")
    try:
        notify.send_line("U1", "x")
    except RuntimeError as e:
        assert "placeholder" in str(e)
    else:
        raise AssertionError("expected RuntimeError")

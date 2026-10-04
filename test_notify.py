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


# ---- early warning and nearby user reports ----

import floodreports as fr
from datetime import timedelta


def _station(**kw):
    base = dict(id="s1", name="สถานีทดสอบ", province="อยุธยา", lat=14.2, lng=99.0, status="watch", pct_of_bank=85.0,
                trend="rising", trend_pct_per_hr=4.0, eta_to_bank_h=3.8, stale=False, age_min=10, source="x", twins=[],
                twin_conflict=False, advice="เฝ้าระวัง", river="r", watch_pct=70.0, alert_pct=90.0, water_level=1.0,
                bank_level=2.0, ground_level=0.0, ts="2026-10-01T16:20:00Z", basin="b")
    base.update(kw)
    return base


_n = iter(range(1000))


def _run(tmp_path, monkeypatch, station, sub_sql="", at=AT, reports=()):
    d = tmp_path / str(next(_n)); d.mkdir()          # a fresh database per scenario
    c = setup(d)
    if sub_sql:
        c.execute("UPDATE subscriptions SET " + sub_sql)
    c.execute("UPDATE subscriptions SET last_status=?", (station["status"],))   # status already known: no change message
    c.commit()
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [station])
    for who, lat, lng in reports:
        fr.add(c, lat, lng, 3, None, "web", who, at)
    out = []
    return c, out, (lambda: notify.run(c, at, {"stdout": lambda t, m: out.append(m)}))


def test_early_warning_once_then_cooldown(tmp_path, monkeypatch):
    c, out, go = _run(tmp_path, monkeypatch, _station())
    assert go() == (1, 0) and "เตือนล่วงหน้า" in out[0] and "~3.8 ชม." in out[0]
    assert go() == (0, 0)                                           # cooldown
    c.execute("UPDATE subscriptions SET last_early='2026-10-01T05:00:00Z'")
    assert go() == (1, 0)                                           # six hours later it may warn again


def test_no_early_warning_when_far_falling_normal_or_user_wants_alerts_only(tmp_path, monkeypatch):
    for kw, sql in [(dict(eta_to_bank_h=9.0), ""), (dict(trend="falling", eta_to_bank_h=None), ""),
                    (dict(status="normal", pct_of_bank=40.0), ""), (dict(), "notify_level='alert'")]:
        c, out, go = _run(tmp_path, monkeypatch, _station(**kw), sql)
        assert go() == (0, 0), kw


def test_fast_rise_warns_even_when_far_from_the_bank_but_not_when_low_or_a_glitch(tmp_path, monkeypatch):
    quick = dict(trend="steady", eta_to_bank_h=None, status="normal", pct_of_bank=60.0, rise_3h_m=0.62)
    c, out, go = _run(tmp_path, monkeypatch, _station(**quick))
    assert go() == (1, 0) and "เตือนน้ำขึ้นเร็ว" in out[0] and "+0.62 ม. ใน 3 ชม." in out[0] and "60%" in out[0]
    assert go() == (0, 0)                                           # shares the six-hour cooldown
    for kw in (dict(pct_of_bank=30.0), dict(rise_3h_m=0.4), dict(rise_3h_m=None), dict(pct_of_bank=None)):
        c, out, go = _run(tmp_path, monkeypatch, _station(**{**quick, **kw}))
        assert go() == (0, 0), kw
    c, out, go = _run(tmp_path, monkeypatch, _station(**quick), "notify_level='alert'")
    assert go() == (0, 0)                                           # this user only wants alert-level news


def test_early_warning_mentions_the_rise_when_both_apply(tmp_path, monkeypatch):
    c, out, go = _run(tmp_path, monkeypatch, _station(rise_3h_m=0.55))
    assert go() == (1, 0) and "เตือนล่วงหน้า" in out[0] and "+0.55 ม. ใน 3 ชม." in out[0]


def test_quiet_hours_hold_early_warning_unless_alert(tmp_path, monkeypatch):
    night = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)   # 01:00 Thai
    c, out, go = _run(tmp_path, monkeypatch, _station(), "quiet=1", at=night)
    assert go() == (0, 0)
    c, out, go = _run(tmp_path, monkeypatch, _station(status="alert", pct_of_bank=93.0), "quiet=1", at=night)
    assert go() == (1, 0)


def test_confirmed_report_near_home_is_sent_once_and_unconfirmed_or_far_ones_are_not(tmp_path, monkeypatch):
    st = _station(trend="steady", eta_to_bank_h=None, status="normal", pct_of_bank=30.0)
    c, out, go = _run(tmp_path, monkeypatch, st, reports=[("a", 14.205, 99.002)])
    assert go() == (0, 0)                                           # one person only: not confirmed
    fr.add(c, 14.206, 99.003, 3, None, "web", "b", AT)              # a second, different person nearby
    assert go() == (1, 0) and "ยืนยันโดย 2 คน" in out[0] and "ยังไม่ผ่านการตรวจสอบ" in out[0]
    assert go() == (0, 0)                                           # cooldown
    c, out, go = _run(tmp_path, monkeypatch, st, reports=[("a", 15.5, 100.5), ("b", 15.501, 100.501)])
    assert go() == (0, 0)                                           # confirmed, but far from home


def test_station_card_shows_the_three_hour_rise():
    import cards, json
    st = _station(distance_km=1.2, rise_3h_m=0.62)
    assert "ระดับน้ำขึ้น +0.62 ม. ใน 3 ชม." in json.dumps(cards.station_card(None, st), ensure_ascii=False)
    assert "ระดับน้ำขึ้น" not in json.dumps(cards.station_card(None, {**st, "rise_3h_m": 0.1}), ensure_ascii=False)


def test_snooze_holds_early_warnings_and_status_changes_but_not_alert_and_ends_on_time(tmp_path, monkeypatch):
    until = "2026-10-01T22:30:00Z"                                   # six hours after AT
    c, out, go = _run(tmp_path, monkeypatch, _station(), f"snooze_until='{until}'")
    assert go() == (0, 0)                                            # early warning held while paused
    c, out, go = _run(tmp_path, monkeypatch, _station(status="alert", pct_of_bank=93.0), f"snooze_until='{until}'")
    assert go() == (1, 0)                                            # alert level is never held
    c, out, go = _run(tmp_path, monkeypatch, _station(status="watch", pct_of_bank=75.0, trend="steady", eta_to_bank_h=None),
                      f"snooze_until='{until}', last_status='normal'")
    c.execute("UPDATE subscriptions SET last_status='normal'")
    assert go() == (0, 0) and c.execute("SELECT last_status FROM subscriptions").fetchone()[0] == "normal"   # change is kept for later
    c.execute("UPDATE subscriptions SET snooze_until='2026-10-01T16:00:00Z'")                                  # pause has run out
    assert go() == (1, 0) and "เฝ้าระวัง" in out[0]                  # the change is sent now

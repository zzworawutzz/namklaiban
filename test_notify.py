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


def _run(tmp_path, monkeypatch, station, sub_sql="", at=AT, reports=(), world=()):
    d = tmp_path / str(next(_n)); d.mkdir()          # a fresh database per scenario
    c = setup(d)
    if sub_sql:
        c.execute("UPDATE subscriptions SET " + sub_sql)
    c.execute("UPDATE subscriptions SET last_status=?", (station["status"],))   # status already known: no change message
    c.commit()
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [station] + list(world))
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


def two_places(tmp_path):
    c = setup(tmp_path)
    c.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('stdout','U1',14.21,99.01,'จุดที่ 2')")
    c.commit()
    return c


def test_two_places_of_one_person_are_merged_into_one_message_with_one_carousel(tmp_path):
    c, out = two_places(tmp_path), []
    senders = {"stdout": lambda t, m, flex=None: out.append((t, m, flex))}
    assert notify.run(c, AT, senders) == (1, 0)                               # sent counts messages, not alerts
    assert len(out) == 1
    _, text, flex = out[0]
    assert "2 จุด" in text and "(บ้าน)" in text and "(จุดที่ 2)" in text
    assert text.count(notify.FOOTER) == 1                                     # the disclaimer once, not once per place
    assert flex["type"] == "carousel" and len(flex["contents"]) == 2
    assert all(c_["type"] == "bubble" for c_ in flex["contents"])
    assert [r[0] for r in c.execute("SELECT last_status FROM subscriptions ORDER BY id")] == ["alert", "alert"]
    assert notify.run(c, AT, senders) == (0, 0)                               # both are marked, nothing repeats


def test_a_failed_merged_message_leaves_every_place_to_be_retried(tmp_path):
    c = two_places(tmp_path)
    assert notify.run(c, AT, {"stdout": lambda t, m, flex=None: 1 / 0}) == (0, 1)
    assert [r[0] for r in c.execute("SELECT last_status FROM subscriptions")] == [None, None]
    assert notify.run(c, AT, {"stdout": lambda t, m, flex=None: None}) == (1, 0)


def test_places_of_different_people_are_not_merged_and_a_single_alert_is_unchanged(tmp_path):
    c, out = two_places(tmp_path), []
    c.execute("UPDATE subscriptions SET target='U2' WHERE label='จุดที่ 2'"); c.commit()
    senders = {"stdout": lambda t, m, flex=None: out.append((t, m, flex))}
    assert notify.run(c, AT, senders) == (2, 0)
    assert sorted(o[0] for o in out) == ["U1", "U2"]
    assert all("2 จุด" not in o[1] and o[2]["type"] == "bubble" for o in out)  # one alert: plain text + single card as before


def test_merged_message_counts_once_in_the_send_log(tmp_path):
    c = two_places(tmp_path)
    notify.run(c, AT, {"stdout": lambda t, m, flex=None: None})
    assert c.execute("SELECT COUNT(*) FROM send_log").fetchone()[0] == 1


def test_a_report_alert_without_a_card_gets_a_small_card_inside_the_carousel():
    evs = [{"kind": "status", "label": "บ้าน", "text": "ข้อความหนึ่ง\n" + notify.FOOTER, "flex": {"type": "bubble"}},
           {"kind": "report", "label": "จุดที่ 2", "text": "มีผู้ใช้รายงานน้ำท่วม\n" + notify.FOOTER, "flex": None}]
    text, flex = notify.merge_events(evs)
    assert len(flex["contents"]) == 2 and flex["contents"][1]["type"] == "bubble"
    assert "มีผู้ใช้รายงานน้ำท่วม" in str(flex["contents"][1]) and "มีผู้ใช้รายงานน้ำท่วม" in text


def test_alert_message_and_card_say_how_far_above_the_bank_when_it_is_known():
    import cards
    st = {"name": "ท่าช้าง", "province": "อยุธยา", "distance_km": 1.0, "status": "alert", "pct_of_bank": 107.0, "trend": None,
          "trend_pct_per_hr": None, "eta_to_bank_h": None, "advice": "x", "over_bank_cm": 80, "rise_3h_m": None}
    text = notify.message({"label": None}, st)
    assert "สูงกว่าตลิ่งราว 80 ซม." in text and "ไม่ใช่ความลึกน้ำที่บ้าน" in text
    assert "สูงกว่าตลิ่งราว 80 ซม." in str(cards.station_card(None, st))
    st["over_bank_cm"] = None
    assert "ตลิ่งราว" not in notify.message({"label": None}, st) and "ตลิ่งราว" not in str(cards.station_card(None, st))


# ---- the station nearest to someone has gone quiet while it was high ----

def _background(status="alert"):
    """Four reporting stations far away with the same status as the silent one (so the subscriber's substitute station brings no
    status change of its own): the rest of the country is as it was, so one silent station is just that station."""
    pct = {"alert": 105.0, "watch": 80.0, "normal": 30.0}[status]
    return [_station(id=f"bg{i}", name=f"พื้นหลัง{i}", lat=17.0 + i, lng=100.0, status=status, pct_of_bank=pct, trend=None, eta_to_bank_h=None) for i in range(4)]


def _silent(**kw):
    base = dict(stale=True, age_min=8 * 60, status="alert", pct_of_bank=105.0, trend=None, trend_pct_per_hr=None, eta_to_bank_h=None)
    base.update(kw)
    return _station(**base)


def test_silent_high_station_is_reported_once_then_forgotten_when_it_reports_again(tmp_path, monkeypatch):
    c, out, run = _run(tmp_path, monkeypatch, _silent(), world=_background())
    assert run() == (1, 0)
    assert "ไม่ส่งข้อมูลใหม่มาแล้วราว 8 ชม." in out[0] and "105%" in out[0] and "ไม่ใช่ระดับน้ำตอนนี้" in out[0]
    assert "แทน" in out[0] and "พื้นหลัง" in out[0] and "1784" in out[0]                # the nearest station that does report is named
    assert c.execute("SELECT stale_notified FROM subscriptions").fetchone()[0] is not None
    assert c.execute("SELECT kind FROM send_log").fetchone()[0] == "stale"
    assert run() == (0, 0)                                                      # not again while it stays silent
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [_station(status="alert", pct_of_bank=105.0, trend=None, eta_to_bank_h=None)] + _background())
    notify.run(c, AT, {"stdout": lambda t, m: out.append(m)})
    assert c.execute("SELECT stale_notified FROM subscriptions").fetchone()[0] is None   # reporting again: ready for the next outage


def test_silent_station_says_which_reporting_station_is_used_instead(tmp_path, monkeypatch):
    c, out, run = _run(tmp_path, monkeypatch, _silent(), world=_background())
    far = _station(id="s2", name="สถานีสำรอง", lat=14.4, lng=99.0, status="alert", pct_of_bank=95.0, trend=None, eta_to_bank_h=None)
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [_silent(), far] + _background())
    c.execute("UPDATE subscriptions SET last_status='alert'"); c.commit()
    assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (1, 0)
    assert "สถานีสำรอง" in out[0] and "แทน" in out[0]


def test_no_silent_notice_when_it_does_not_matter(tmp_path, monkeypatch):
    for name, station, sql in (("normal when last seen", _silent(status="normal", pct_of_bank=40.0), ""),
                               ("quiet only 3 h", _silent(age_min=3 * 60), ""),
                               ("retired: silent over a week", _silent(age_min=8 * 24 * 60), ""),
                               ("only alert-level wanted, last reading was watch", _silent(status="watch", pct_of_bank=80.0), "notify_level='alert'"),
                               ("never reported", _silent(age_min=None), "")):
        c, out, run = _run(tmp_path, monkeypatch, station, sql, world=_background(station["status"]))
        assert run() == (0, 0), name
        assert c.execute("SELECT stale_notified FROM subscriptions").fetchone()[0] is None, name


def test_silent_notice_respects_snooze_but_alert_level_gets_through_and_is_sent_after(tmp_path, monkeypatch):
    later = (AT + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c, out, run = _run(tmp_path, monkeypatch, _silent(status="watch", pct_of_bank=80.0), f"snooze_until='{later}'", world=_background("watch"))
    assert run() == (0, 0) and c.execute("SELECT stale_notified FROM subscriptions").fetchone()[0] is None   # held, not lost
    assert notify.run(c, AT + timedelta(hours=4), {"stdout": lambda t, m: out.append(m)}) == (1, 0)           # snooze over: now
    c2, out2, run2 = _run(tmp_path, monkeypatch, _silent(), f"snooze_until='{later}'", world=_background())                        # alert level: not held back
    assert run2() == (1, 0)


def test_silent_notice_and_a_status_change_for_the_same_person_become_one_message(tmp_path, monkeypatch):
    c, out, run = _run(tmp_path, monkeypatch, _silent(), world=_background())
    fresh = _station(id="s2", name="สถานีสำรอง", lat=14.3, lng=99.0, status="watch", pct_of_bank=80.0, trend=None, eta_to_bank_h=None)
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [_silent(), fresh] + _background())
    c.execute("UPDATE subscriptions SET last_status='normal'"); c.commit()                # the substitute's status is news too
    assert notify.run(c, AT, {"stdout": lambda t, m, flex=None: out.append((m, flex))}) == (1, 0)
    assert len(out) == 1 and "ไม่ส่งข้อมูลใหม่" in out[0][0] and "สถานีสำรอง" in out[0][0] and out[0][1]["type"] == "carousel"


def test_no_silent_notice_when_most_stations_are_silent_at_once(tmp_path, monkeypatch):
    """A fault in our ingest (or the source) makes every station old together; that is not one station going quiet, and
    must not send everyone near a high station a message. The owner is told by the watchdog."""
    c, out, run = _run(tmp_path, monkeypatch, _silent())
    others = [_silent(id=f"o{i}", name=f"อื่น{i}", lat=15.0 + i, lng=100.0) for i in range(3)]
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [_silent()] + others)
    assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (0, 0) and out == []
    assert c.execute("SELECT stale_notified FROM subscriptions").fetchone()[0] is None
    fine = [_station(id=f"f{i}", name=f"ปกติ{i}", lat=15.0 + i, lng=100.0, status="normal", pct_of_bank=30.0, trend=None, eta_to_bank_h=None) for i in range(5)]
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [_silent()] + fine)          # one of six silent: that one really is quiet
    assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (1, 0)


def test_silent_message_when_no_other_station_reports():
    text = notify.stale_message({"label": "บ้าน"}, _silent(distance_km=1.2), None)
    assert "(บ้าน)" in text and "ยังไม่มีสถานีอื่นใกล้เคียงที่ส่งข้อมูลมาแทน" in text and "ราว 8 ชม." in text
    assert "ราว 3 วัน" in notify.stale_message({"label": None}, _silent(age_min=3 * 24 * 60, distance_km=1.2), None)    # days once it is long


def test_when_one_agency_goes_quiet_but_others_report_the_people_near_its_stations_are_told(tmp_path, monkeypatch):
    """6 Oct 2026: HII and FOP stopped publishing, RID and EGAT carried on. Our ingest was fine, so this is "those stations went quiet"
    and must be said, unlike a failure of our own ingest where nothing reports."""
    c, out, run = _run(tmp_path, monkeypatch, _silent(source="HII"))
    hii = [_silent(source="HII", id=f"h{i}", name=f"เอชไอไอ{i}", lat=15.0 + i, lng=100.0) for i in range(5)]
    rid = [_station(source="RID", id=f"r{i}", name=f"อาร์ไอดี{i}", lat=17.0 + i, lng=100.0, status="alert", pct_of_bank=105.0, trend=None, eta_to_bank_h=None) for i in range(6)]
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [_silent(source="HII")] + hii + rid)
    assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (1, 0)
    assert "ไม่ส่งข้อมูลใหม่" in out[0] and "อาร์ไอดี" in out[0]                                  # told, and which station is used instead
    assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (0, 0)                      # once


def test_when_every_agency_is_quiet_nobody_is_told(tmp_path, monkeypatch):
    c, out, run = _run(tmp_path, monkeypatch, _silent(source="HII"))
    everyone = [_silent(source=s, id=f"{s}{i}", name=f"{s}{i}", lat=15.0 + i, lng=100.0) for s in ("HII", "RID") for i in range(6)]
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [_silent(source="HII")] + everyone)
    assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (0, 0) and out == []        # our ingest, or the whole source: the watchdog's job


def test_drop_back_below_the_bank_is_told_once_even_when_the_status_is_unchanged(tmp_path, monkeypatch):
    over = _station(status="alert", pct_of_bank=108.0, trend=None, eta_to_bank_h=None, over_bank_cm=25)
    c, out, run = _run(tmp_path, monkeypatch, over)
    run()
    assert c.execute("SELECT was_over_bank FROM subscriptions").fetchone()[0] == 1       # remembered
    n = len(out)
    below = _station(status="alert", pct_of_bank=98.0, trend=None, eta_to_bank_h=None, over_bank_cm=-5)
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [below])
    assert run() == (0, 0)                                                              # 98 % is not yet "back below": no flapping
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [dict(below, pct_of_bank=95.0, over_bank_cm=-30)])
    assert run() == (1, 0)
    assert "ลดลงต่ำกว่าตลิ่งแล้ว" in out[-1] and "95%" in out[-1] and "ไม่ได้แปลว่าปลอดภัย" in out[-1]
    assert run() == (0, 0) and c.execute("SELECT was_over_bank FROM subscriptions").fetchone()[0] == 0   # once


def test_drop_below_the_bank_is_held_in_quiet_hours_and_alert_only_users_still_get_it(tmp_path, monkeypatch):
    over = _station(status="alert", pct_of_bank=108.0, trend=None, eta_to_bank_h=None, over_bank_cm=25)
    low = dict(over, pct_of_bank=95.0, over_bank_cm=-30)
    c, out, run = _run(tmp_path, monkeypatch, over, sub_sql="quiet=1")
    run()
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [low])
    night = datetime(2026, 10, 1, 17, 30, tzinfo=timezone.utc)                          # 00:30 Thai
    assert notify.run(c, night, {"stdout": lambda t, m: out.append(m)}) == (0, 0)
    assert c.execute("SELECT was_over_bank FROM subscriptions").fetchone()[0] == 1       # kept for after the quiet hours
    assert notify.run(c, night + timedelta(hours=7), {"stdout": lambda t, m: out.append(m)}) == (1, 0)
    c, out, run = _run(tmp_path, monkeypatch, over, sub_sql="notify_level='alert'")
    run()
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [low])                  # still alert level: alert-only users hear it too
    assert run() == (1, 0) and "ลดลงต่ำกว่าตลิ่งแล้ว" in out[-1]


def test_drop_below_the_bank_with_a_status_change_sends_only_the_status_message(tmp_path, monkeypatch):
    over = _station(status="alert", pct_of_bank=108.0, trend=None, eta_to_bank_h=None, over_bank_cm=25)
    c, out, run = _run(tmp_path, monkeypatch, over)
    run()
    monkeypatch.setattr(notify.core, "latest", lambda conn, a: [dict(over, status="watch", pct_of_bank=80.0, over_bank_cm=-60)])
    assert run() == (1, 0) and "ลดลงต่ำกว่าตลิ่งแล้ว" not in out[-1]
    assert c.execute("SELECT was_over_bank FROM subscriptions").fetchone()[0] == 0


# ---- heads-up: heavy rain in the next hours (province-level, never the person's own place) ----

import rainalert


def _soon(monkeypatch, mm=(5.0, 22.0, 10.0), seen=None):
    def fake(lat, lng):
        if seen is not None:
            seen.append((round(lat, 3), round(lng, 3)))
        return {"hourly": {"time": ["2026-10-01T23:00", "2026-10-02T00:00", "2026-10-02T01:00"], "precipitation": list(mm)}}
    monkeypatch.setattr(rainalert, "fetch_soon", fake)


def test_heavy_rain_soon_sends_one_heads_up_then_waits_twelve_hours(tmp_path, monkeypatch):
    seen = []
    _soon(monkeypatch, seen=seen)                                   # 37 mm in 3 h, peak 22 mm at 00:00
    c, out, run = _run(tmp_path, monkeypatch, _station(status="normal", pct_of_bank=30.0, trend=None, eta_to_bank_h=None))
    assert run() == (1, 0)
    assert "พยากรณ์ฝน 3 ชม." in out[0] and "~37 มม." in out[0] and "00:00 น." in out[0] and "ไม่ใช่ตำแหน่งบ้านคุณ" in out[0] and "อยุธยา" in out[0]
    assert c.execute("SELECT kind FROM send_log").fetchone()[0] == "rain"
    assert run() == (0, 0)                                          # cooldown
    c.execute("UPDATE subscriptions SET last_rain_alert='2026-10-01T03:00:00Z'")   # 13.5 h ago
    assert run() == (1, 0)


def test_the_forecast_is_asked_for_at_the_province_centre_never_at_the_persons_saved_place(tmp_path, monkeypatch):
    seen = []
    _soon(monkeypatch, seen=seen)
    st = _station(status="normal", pct_of_bank=30.0, trend=None, eta_to_bank_h=None, lat=14.9, lng=100.9)   # the province's only station is far from the person
    c, out, run = _run(tmp_path, monkeypatch, st)
    c.execute("UPDATE subscriptions SET lat=14.2, lng=99.0")                                                 # the person's own place
    run()
    assert seen == [(14.9, 100.9)], seen                                                                     # the middle of the province's stations, not (14.2, 99.0)


def test_no_heads_up_when_the_rain_is_light_or_the_person_wants_quiet(tmp_path, monkeypatch):
    base = dict(status="normal", pct_of_bank=30.0, trend=None, eta_to_bank_h=None)
    _soon(monkeypatch, mm=(3.0, 5.0, 4.0))                          # 12 mm in 3 h: not heavy
    c, out, run = _run(tmp_path, monkeypatch, _station(**base))
    assert run() == (0, 0)
    _soon(monkeypatch)
    c, out, run = _run(tmp_path, monkeypatch, _station(**base), sub_sql="notify_level='alert'")              # alert-level news only
    assert run() == (0, 0)
    c, out, run = _run(tmp_path, monkeypatch, _station(**base), sub_sql="quiet=1", at=datetime(2026, 10, 1, 17, 30, tzinfo=timezone.utc))   # 00:30 Thai, quiet nights
    assert run() == (0, 0)
    c, out, run = _run(tmp_path, monkeypatch, _station(**base), sub_sql="snooze_until='2026-10-02T12:00:00Z'")
    assert run() == (0, 0)


def test_one_hour_of_very_heavy_rain_is_enough_and_two_places_in_one_province_get_one_message(tmp_path, monkeypatch):
    seen = []
    _soon(monkeypatch, mm=(0.0, 21.0, 0.0), seen=seen)               # only 21 mm in total, but 21 in a single hour
    c, out, run = _run(tmp_path, monkeypatch, _station(status="normal", pct_of_bank=30.0, trend=None, eta_to_bank_h=None))
    c.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,last_status) VALUES('stdout','U1',14.21,99.01,'ที่ทำงาน','normal')")
    assert run() == (1, 0) and len(out) == 1 and len(seen) == 1       # one person, one province: one message, one forecast

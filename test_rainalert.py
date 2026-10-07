from datetime import timedelta

import pytest

import cards, notify, rainalert, report
from test_report import AT, PROV, conn  # noqa: F401  (fixture)


def fresh(rows, province, fetcher):
    """for_province without a cached answer from an earlier call in the same test."""
    rainalert._cache.clear()
    return rainalert.for_province(rows, province, fetcher)


def wet(mm_per_hour):
    return lambda lat, lng: {"hourly": {"precipitation": [mm_per_hour] * 24}}


ROWS = [{"province": "ก", "lat": 14.0, "lng": 100.0}, {"province": "ก", "lat": 15.0, "lng": 101.0},
        {"province": "ข", "lat": 18.0, "lng": 99.0}, {"province": "ก", "lat": None, "lng": None}]


def test_province_point_is_the_middle_of_its_stations():
    assert rainalert.province_point(ROWS, "ก") == (14.5, 100.5) and rainalert.province_point(ROWS, "ไม่มี") is None


def test_only_heavy_forecasts_count_and_classes_match_the_map():
    assert fresh(ROWS, "ก", wet(1.0)) is None                                        # 24 mm: not heavy
    assert fresh(ROWS, "ก", wet(1.5)) == {"mm": 36.0, "label": "ฝนหนัก"}
    assert fresh(ROWS, "ก", wet(4.0))["label"] == "ฝนหนักมาก"                        # 96 mm
    assert fresh(ROWS, "ไม่มี", wet(9.0)) is None


def test_label_stays_honest_when_the_threshold_is_lowered(monkeypatch):
    monkeypatch.setenv("RAIN_ALERT_MM", "10")
    assert fresh(ROWS, "ก", wet(0.5))["label"] == "ฝนค่อนข้างมาก"                    # 12 mm is not "heavy"


def test_threshold_is_configurable_and_bad_values_fall_back(monkeypatch):
    monkeypatch.setenv("RAIN_ALERT_MM", "50")
    assert fresh(ROWS, "ก", wet(1.5)) is None and fresh(ROWS, "ก", wet(2.5)) is not None
    monkeypatch.setenv("RAIN_ALERT_MM", "x")
    assert rainalert.heavy_mm() == 35.0


def test_failure_gives_no_warning_and_is_not_cached():
    def boom(lat, lng):
        raise OSError("down")
    assert rainalert.for_province(ROWS, "ก", boom) is None
    assert rainalert.for_province(ROWS, "ก", wet(2.0)) is not None                  # next try works


def test_forecast_is_cached_for_half_an_hour():
    calls = []
    def f(lat, lng):
        calls.append(1); return wet(2.0)(lat, lng)
    t = [1000.0]
    for _ in range(3):
        rainalert.for_province(ROWS, "ก", f, now=lambda: t[0])
    assert len(calls) == 1
    t[0] += rainalert.CACHE_S + 1
    rainalert.for_province(ROWS, "ก", f, now=lambda: t[0])
    assert len(calls) == 2


def test_summary_gets_the_rain_line_in_text_and_card_only_when_heavy(conn, monkeypatch):
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('stdout','U1',14.36,100.55,'บ้าน')")
    conn.commit()
    out = []
    send = {"stdout": lambda t, m, flex=None: out.append((m, flex))}
    monkeypatch.setattr(rainalert, "fetch", wet(0.0))
    assert notify.run_digest(conn, AT, send) == (1, 0)
    assert "พยากรณ์ฝน" not in out[0][0] and "พยากรณ์ฝน" not in str(out[0][1])
    conn.execute("UPDATE subscriptions SET last_digest=NULL"); conn.commit()
    rainalert._cache.clear()
    monkeypatch.setattr(rainalert, "fetch", wet(2.5))                                # 60 mm
    assert notify.run_digest(conn, AT, send) == (1, 0)
    text, flex = out[1]
    assert "🌧 พยากรณ์ฝน 24 ชม. ข้างหน้า ~60 มม. (ฝนหนัก)" in text and "Open-Meteo" in text
    assert "พยากรณ์ฝน 24 ชม." in str(flex)


def test_summary_still_goes_out_when_the_forecast_service_is_down(conn, monkeypatch):
    conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('stdout','U1',14.36,100.55,'บ้าน')")
    conn.commit()
    out = []
    def boom(lat, lng):
        raise OSError("down")
    monkeypatch.setattr(rainalert, "fetch", boom)
    assert notify.run_digest(conn, AT, {"stdout": lambda t, m, flex=None: out.append(m)}) == (1, 0)
    assert "สรุปสถานการณ์น้ำ" in out[0] and "พยากรณ์ฝน" not in out[0]


def test_one_forecast_per_province_not_per_person(conn, monkeypatch):
    for i in range(4):
        conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES('stdout',?,14.36,100.55,'บ้าน')", (f"U{i}",))
    conn.commit()
    calls = []
    def f(lat, lng):
        calls.append((round(lat, 2), round(lng, 2))); return wet(2.5)(lat, lng)
    monkeypatch.setattr(rainalert, "fetch", f)
    assert notify.run_digest(conn, AT, {"stdout": lambda t, m, flex=None: None}) == (4, 0)
    assert len(calls) == 1 and all(14.3 < c[0] < 14.7 for c in calls)               # the middle of the province, not a home


def test_next_three_hours_gives_the_total_the_peak_hour_and_a_label_only_when_heavy():
    rows = [{"province": "ป", "lat": 14.0, "lng": 100.0}]
    mk = lambda mm: (lambda lat, lng: {"hourly": {"time": ["2026-10-01T10:00", "2026-10-01T11:00", "2026-10-01T12:00"], "precipitation": mm}})
    rainalert._soon_cache.clear()
    r = rainalert.soon_for_province(rows, "ป", fetcher=mk([4.0, 30.0, 6.0]))
    assert r["mm"] == 40.0 and r["peak_mm"] == 30.0 and r["peak_at"] == "11:00" and r["label"] == "ฝนหนัก" and r["hours"] == 3
    rainalert._soon_cache.clear()
    assert rainalert.soon_for_province(rows, "ป", fetcher=mk([2.0, 3.0, 4.0])) is None                       # 9 mm: not heavy
    rainalert._soon_cache.clear()
    assert rainalert.soon_for_province(rows, "ป", fetcher=mk([0.0, 55.0, 0.0]))["label"] == "ฝนหนักมาก"     # one violent hour
    rainalert._soon_cache.clear()
    assert rainalert.soon_for_province(rows, "ป", fetcher=lambda a, b: 1 / 0) is None                          # no forecast: no message
    assert rainalert.soon_for_province(rows, "ไม่มี", fetcher=mk([50.0, 0, 0])) is None                       # a province with no station

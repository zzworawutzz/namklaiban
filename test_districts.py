# -*- coding: utf-8 -*-
import districts


SQUARE = [[[[100.0, 14.0], [101.0, 14.0], [101.0, 15.0], [100.0, 15.0], [100.0, 14.0]]]]
DONUT = [[[[100.0, 14.0], [101.0, 14.0], [101.0, 15.0], [100.0, 15.0], [100.0, 14.0]],
          [[100.4, 14.4], [100.6, 14.4], [100.6, 14.6], [100.4, 14.6], [100.4, 14.4]]]]


def row(name, lat, lng, status, pct, stale=False, province="จ"):
    return {"name": name, "lat": lat, "lng": lng, "status": status, "pct_of_bank": pct, "stale": stale, "province": province}


def test_point_in_polygon_with_a_hole():
    assert districts.contains(SQUARE, 100.5, 14.5)
    assert not districts.contains(SQUARE, 102.0, 14.5)
    assert districts.contains(DONUT, 100.1, 14.1)
    assert not districts.contains(DONUT, 100.5, 14.5)           # inside the hole


def fake(monkeypatch, shapes):
    monkeypatch.setattr(districts.boundaries, "_all", lambda: {"p": {}, "d": {"จ": shapes}, "t": {}})


def test_district_takes_the_worst_fresh_station_and_ignores_stale_or_unknown(monkeypatch):
    fake(monkeypatch, {"เขต1": SQUARE, "เขต2": [[[[102.0, 14.0], [103.0, 14.0], [103.0, 15.0], [102.0, 15.0], [102.0, 14.0]]]]})
    rows = [row("ก", 14.5, 100.5, "normal", 40), row("ข", 14.6, 100.6, "watch", 80), row("ค", 14.7, 100.7, "alert", 120, stale=True),
            row("ง", 14.2, 100.2, "unknown", None), row("นอก", 14.5, 105.0, "alert", 150), row("อื่น", 14.5, 100.5, "alert", 99, province="x")]
    fc = districts.summary("จ", rows)
    one, two = [f["properties"] for f in fc["features"]]
    assert one["district"] == "เขต1" and one["status"] == "watch"           # the stale alert does not count
    assert one["stations"] == 4 and one["fresh"] == 2 and one["counts"] == {"alert": 0, "watch": 1, "normal": 1}
    assert one["top"] == {"name": "ข", "pct": 80}
    assert two["status"] == "none" and two["stations"] == 0 and two["top"] is None   # no gauge: never reported as normal


def test_a_district_with_only_stale_stations_is_none_not_normal(monkeypatch):
    fake(monkeypatch, {"เขต1": SQUARE})
    p = districts.summary("จ", [row("ก", 14.5, 100.5, "normal", 10, stale=True)])["features"][0]["properties"]
    assert p["status"] == "none" and p["stations"] == 1 and p["fresh"] == 0


def test_unknown_province_has_no_summary():
    assert districts.summary("ไม่มีจังหวัดนี้", []) is None

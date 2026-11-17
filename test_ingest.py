import sqlite3
import pytest
import ingest as ig
from conftest import real_rows


def row(**over):
    base = {
        "station": {"id": "S1", "tele_station_lat": "14.35", "tele_station_long": "100.56",
                    "tele_station_name": {"th": "อยุธยา"}, "min_bank": 4.0, "ground_level": -1.0,
                    "tele_station_oldcode": "C.35"},
        "geocode": {"province_name": {"th": "พระนครศรีอยุธยา"}},
        "agency": {"agency_shortname": {"en": "RID"}},
        "waterlevel_msl": 3.5, "waterlevel_datetime": "2026-10-01 10:00:00",
        "storage_percent": 90,
    }
    base.update(over)
    return base


@pytest.mark.parametrize("pct,exp", [
    (None, "unknown"), (0, "normal"), (69.9, "normal"), (70.0, "watch"),
    (89.9, "watch"), (90.0, "alert"), (130, "alert")])
def test_status_boundaries(pct, exp):
    assert ig.status_of(pct) == exp


def test_pct_uses_bed_to_bank_depth():
    assert ig.pct_of_bank(3.5, -1.0, 4.0) == 90.0


def test_pct_falls_back_to_api_value():
    assert ig.pct_of_bank(3.5, None, 4.0, "55") == 55.0
    assert ig.pct_of_bank(3.5, 4.0, 4.0, None) is None  # bank == ground is invalid


def test_timestamp_converted_from_bangkok_to_utc():
    assert ig.parse_ts("2026-10-01 10:00:00") == "2026-10-01T03:00:00Z"
    assert ig.parse_ts("garbage") is None and ig.parse_ts(None) is None


def test_normalise_ok():
    st, rd = ig.normalise(row())
    assert st["name"] == "อยุธยา" and st["province"] == "พระนครศรีอยุธยา" and st["source"] == "RID"
    assert rd["status"] == "alert" and rd["ts"] == "2026-10-01T03:00:00Z"


@pytest.mark.parametrize("lat,lng", [("0", "0"), ("abc", "100"), (None, None), ("40", "100.5")])
def test_bad_coordinates_dropped(lat, lng):
    r = row()
    r["station"]["tele_station_lat"], r["station"]["tele_station_long"] = lat, lng
    assert ig.normalise(r) is None


def test_missing_level_keeps_station_skips_reading():
    st, rd = ig.normalise(row(waterlevel_msl=None))
    assert st["id"] == "S1" and rd is None


def test_extract_rows_shapes():
    r = [row()]
    assert ig.extract_rows(r) == r
    assert ig.extract_rows({"data": r}) == r
    assert ig.extract_rows({"data": {"result": r}}) == r
    assert ig.extract_rows({"x": 1}) == []


def test_rerun_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.executescript(ig.SCHEMA)
    assert ig.save(conn, [row()]) == (1, 1, 0)
    assert ig.save(conn, [row()]) == (1, 0, 0)  # same ts -> no duplicate reading
    assert conn.execute("select count(*) from readings").fetchone()[0] == 1
    assert ig.save(conn, [row(), row(station={})]) [2] == 1  # bad row counted as skipped


def test_real_rows_match_api_storage_percent():
    from conftest import real_rows
    for raw in real_rows():
        st, rd = ig.normalise(raw)
        assert abs(rd["pct_of_bank"] - float(raw["storage_percent"])) < 0.1
        assert rd["status"] == "alert" and st["source"] == "RID"
    assert ig.normalise(real_rows()[0])[0]["id"] == "505018"


def test_first_seen_is_set_once_and_missing_for_stations_that_were_there_before(tmp_path):
    c = sqlite3.connect(tmp_path / "f.db")
    c.row_factory = sqlite3.Row
    ig.init_db(c)
    ig.save(c, real_rows())
    first = {r["id"]: r["first_seen"] for r in c.execute("SELECT id, first_seen FROM stations")}
    assert first and all(first.values())
    some = next(iter(first))
    c.execute("UPDATE stations SET first_seen=NULL WHERE id=?", (some,))   # a station saved before the column existed
    c.execute("UPDATE stations SET updated_at='2000-01-01T00:00:00Z'")
    c.commit()
    ig.save(c, real_rows())
    now = {r["id"]: r["first_seen"] for r in c.execute("SELECT id, first_seen FROM stations")}
    assert now[some] is None                                                # unknown stays unknown
    assert all(now[k] == first[k] for k in first if k != some)             # a later run never moves it


def test_init_db_adds_first_seen_to_an_old_database(tmp_path):
    c = sqlite3.connect(tmp_path / "old.db")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE stations(id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT, province TEXT, lat REAL NOT NULL, "
              "lng REAL NOT NULL, bank_level REAL, ground_level REAL, updated_at TEXT, river TEXT, basin TEXT, watch_pct REAL, alert_pct REAL)")
    c.execute("INSERT INTO stations(id,name,lat,lng) VALUES('1','a',14,100)")
    ig.init_db(c)
    assert c.execute("SELECT first_seen FROM stations").fetchone()["first_seen"] is None

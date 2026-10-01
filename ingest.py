#!/usr/bin/env python3
"""Ingest job for "น้ำใกล้บ้านฉัน".

Fetches the latest water level readings from ThaiWater, normalises them into
our central schema (stations / readings) and upserts into SQLite (a file path)
or Postgres (a postgres:// URL in DATABASE_URL / --db). See db.py.

Run:  python ingest.py                 # fetch live data
      python ingest.py --file dump.json  # replay a saved payload (testing)
      python ingest.py --thresholds thresholds.json  # per-station watch/alert %
Cron: */20 * * * * /usr/bin/python3 /path/to/ingest.py

NOTE: field names below follow the public ThaiWater endpoint as used by
community clients. Verify them against a real response before relying on this
(run with --save-raw to keep a copy of what the API returned).
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import db

API_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
TZ_TH = timezone(timedelta(hours=7))
WATCH_PCT = 70.0   # >= watch
ALERT_PCT = 90.0   # >= alert  (ThaiWater itself marks "overbank" at 100%)
STALE_MIN = 120
KEEP_DAYS = 31     # pruned on each run; the API serves at most 30 days of history

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT, province TEXT,
  lat REAL NOT NULL, lng REAL NOT NULL, bank_level REAL, ground_level REAL,
  updated_at TEXT, river TEXT, basin TEXT, watch_pct REAL, alert_pct REAL);
CREATE TABLE IF NOT EXISTS readings(
  station_id TEXT NOT NULL REFERENCES stations(id), ts TEXT NOT NULL,
  water_level REAL, pct_of_bank REAL, status TEXT NOT NULL,
  PRIMARY KEY(station_id, ts));
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
CREATE TABLE IF NOT EXISTS ingest_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, ok INTEGER NOT NULL,
  stations INTEGER, readings INTEGER, skipped INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS subscriptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, target TEXT NOT NULL,
  lat REAL NOT NULL, lng REAL NOT NULL, label TEXT, last_status TEXT, last_notified TEXT);
CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, until TEXT NOT NULL);
"""
# Same tables for Postgres: REAL there is 4-byte float, which would blur coordinates.
SCHEMA_PG = (SCHEMA.replace("REAL", "DOUBLE PRECISION")
             .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"))
NEW_STATION_COLS = {"river": "TEXT", "basin": "TEXT", "watch_pct": "REAL", "alert_pct": "REAL"}


def init_db(conn):
    """Create tables and add columns introduced after a db file was first made."""
    if getattr(conn, "pg", False):
        conn.executescript(SCHEMA_PG)
        conn.commit()
        return
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(stations)")}
    for col, typ in NEW_STATION_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE stations ADD COLUMN {col} {typ}")
    conn.commit()


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_thresholds(path):
    """{station_id: {"watch": 60, "alert": 85}}; missing keys fall back to defaults."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        return {str(k): v for k, v in json.load(f).items()}


def pick(d, *keys):
    """First non-None value among keys; unwraps {'th': ..} style names."""
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v is not None:
            return v.get("th") or v.get("en") if isinstance(v, dict) else v
    return None


def to_float(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def parse_ts(s):
    """ThaiWater times are local (UTC+7). Return UTC ISO string or None."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return (datetime.strptime(str(s)[:19], fmt).replace(tzinfo=TZ_TH)
                    .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            continue
    return None


def status_of(pct, watch=WATCH_PCT, alert=ALERT_PCT):
    if pct is None:
        return "unknown"
    return "alert" if pct >= alert else "watch" if pct >= watch else "normal"


def pct_of_bank(level, ground, bank, api_pct=None):
    """Depth above river bed relative to bank height (0-100+%).
    Prefer our own calc when ground/bank exist; fall back to the API figure."""
    if None not in (level, ground, bank) and bank > ground:
        return round((level - ground) / (bank - ground) * 100, 1)
    return to_float(api_pct)


def normalise(raw, thresholds=None):
    """One API row -> (station_dict, reading_dict) or None if unusable.
    thresholds: optional {station_id: {"watch": %, "alert": %}} overrides."""
    st = raw.get("station") or raw
    geo = raw.get("geocode") or st.get("geocode") or {}
    lat = to_float(st.get("tele_station_lat", raw.get("tele_station_lat")))
    lng = to_float(st.get("tele_station_long", raw.get("tele_station_long")))
    if lat is None or lng is None or not (5 <= lat <= 21 and 97 <= lng <= 106):
        return None  # outside Thailand => bad coordinates
    ag = (raw.get("agency") or {}).get("agency_shortname")
    agency = (ag.get("en") or ag.get("th") or "") if isinstance(ag, dict) else (ag or "")
    code = pick(st, "tele_station_oldcode") or ""
    sid = str(st.get("id") or st.get("tele_station_id") or f"{agency}:{code}".strip(":"))
    if not sid:
        return None
    level = to_float(raw.get("waterlevel_msl", st.get("waterlevel_msl")))
    ground, bank = to_float(st.get("ground_level")), to_float(st.get("min_bank"))
    ts = parse_ts(raw.get("waterlevel_datetime"))
    pct = pct_of_bank(level, ground, bank, raw.get("storage_percent"))
    th = (thresholds or {}).get(sid, {})
    watch, alert = to_float(th.get("watch")), to_float(th.get("alert"))
    basin = raw.get("basin") or {}
    station = dict(id=sid, name=pick(st, "tele_station_name") or "ไม่ระบุชื่อ",
                   source=str(agency), province=pick(geo, "province_name"),
                   lat=lat, lng=lng, bank_level=bank, ground_level=ground,
                   river=(raw.get("river_name") or "").strip() or None,
                   basin=pick(basin, "basin_name"), watch_pct=watch, alert_pct=alert)
    status = status_of(pct, watch if watch is not None else WATCH_PCT,
                       alert if alert is not None else ALERT_PCT)
    reading = dict(station_id=sid, ts=ts, water_level=level, pct_of_bank=pct,
                   status=status) if ts and level is not None else None
    return station, reading


def extract_rows(payload):
    if isinstance(payload, list):
        return payload
    for path in (("data",), ("result",), ("data", "result"), ("waterlevel_data", "data")):
        cur = payload
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if isinstance(cur, list):
            return cur
    return []


def save(conn, rows, thresholds=None):
    """Upsert stations and insert new readings in two batched statements
    (one round trip each on Postgres, instead of one per row)."""
    stations, readings, skipped = [], [], 0
    now = utc_now()
    for raw in rows:
        out = normalise(raw, thresholds)
        if not out:
            skipped += 1
            continue
        st, rd = out
        stations.append({**st, "u": now})
        if rd:
            readings.append(rd)
    if stations:
        conn.executemany(
            """INSERT INTO stations(id,name,source,province,lat,lng,bank_level,ground_level,updated_at,
                                    river,basin,watch_pct,alert_pct)
               VALUES(:id,:name,:source,:province,:lat,:lng,:bank_level,:ground_level,:u,
                      :river,:basin,:watch_pct,:alert_pct)
               ON CONFLICT(id) DO UPDATE SET name=:name, source=:source, province=:province,
               lat=:lat, lng=:lng, bank_level=:bank_level, ground_level=:ground_level, updated_at=:u,
               river=:river, basin=:basin, watch_pct=:watch_pct, alert_pct=:alert_pct""", stations)
    n_rd = 0
    if readings:  # same (station, ts) is ignored -> safe to re-run
        cur = conn.executemany(
            """INSERT INTO readings(station_id,ts,water_level,pct_of_bank,status)
               VALUES(:station_id,:ts,:water_level,:pct_of_bank,:status)
               ON CONFLICT(station_id, ts) DO NOTHING""", readings)
        n_rd = cur.rowcount
    conn.commit()
    return len(stations), n_rd, skipped


def prune(conn, keep_days=KEEP_DAYS):
    """Drop readings older than keep_days. Returns the number removed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def log_run(conn, ok, n_st=None, n_rd=None, skipped=None, error=None):
    conn.execute("INSERT INTO ingest_runs(ts,ok,stations,readings,skipped,error) VALUES(?,?,?,?,?,?)",
                 (utc_now(), int(ok), n_st, n_rd, skipped, error))
    conn.commit()


def fetch():
    url = f"{API_URL}?timestamp={int(datetime.now().timestamp() * 1000)}"
    req = urllib.request.Request(url, headers={"User-Agent": "nam-klai-baan-chan/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def try_lock(conn, name, ttl_s=600):
    """Take a named lock for ttl_s seconds; False if someone else holds it. Cron can
    deliver the same run twice or overlap with a slow one, so jobs guard themselves."""
    now = datetime.now(timezone.utc)
    cur = conn.execute(
        """INSERT INTO locks(name, until) VALUES(:n, :until)
           ON CONFLICT(name) DO UPDATE SET until=:until WHERE locks.until < :now""",
        {"n": name, "until": (now + timedelta(seconds=ttl_s)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "now": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    conn.commit()
    return cur.rowcount == 1


def release_lock(conn, name):
    conn.execute("UPDATE locks SET until='' WHERE name=?", (name,))
    conn.commit()


def run_ingest(conn, loader=None, thresholds=None, keep_days=KEEP_DAYS, save_raw=None):
    """One ingest cycle. Failures are logged to ingest_runs (so /health can show them)
    and re-raised. Returns (stations, new_readings, skipped, pruned)."""
    try:
        payload = (loader or fetch)()
        if save_raw:
            json.dump(payload, open(save_raw, "w", encoding="utf-8"), ensure_ascii=False)
        rows = extract_rows(payload)
        if not rows:
            raise ValueError("no rows in payload - API format may have changed")
        n_st, n_rd, skipped = save(conn, rows, thresholds)
    except Exception as e:
        conn.rollback()
        log_run(conn, False, error=f"{type(e).__name__}: {e}"[:300])
        raise
    log_run(conn, True, n_st, n_rd, skipped)
    conn.execute("DELETE FROM ingest_runs WHERE id <= (SELECT MAX(id) FROM ingest_runs) - 500")  # keep it small
    return n_st, n_rd, skipped, prune(conn, keep_days)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="SQLite path or postgres:// URL (default: $DATABASE_URL, $WATER_DB, water.db)")
    ap.add_argument("--file", help="read payload from a JSON file instead of the API")
    ap.add_argument("--save-raw", help="write the raw API response to this path")
    ap.add_argument("--thresholds", help="JSON file of per-station watch/alert percentages")
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS, help="prune readings older than this")
    a = ap.parse_args(argv)
    with db.connect(a.db) as conn:
        init_db(conn)
        loader = (lambda: json.load(open(a.file, encoding="utf-8"))) if a.file else None
        try:
            n_st, n_rd, skipped, pruned = run_ingest(conn, loader, load_thresholds(a.thresholds),
                                                     a.keep_days, a.save_raw)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    print(f"stations upserted={n_st} new readings={n_rd} skipped={skipped} pruned={pruned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

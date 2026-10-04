"""Replay stored readings through the "fast rise" LINE warning (notify.fast_rise) and count how often it would fire.
Read-only: sends nothing and changes nothing. Use it to tune FAST_RISE_M / FAST_MIN_PCT in notify.py and to
estimate how many messages the warning adds.

    python backtest_fast.py --db water.db                       # a local SQLite file
    python backtest_fast.py --api https://your-site --days 30   # reads our own /stations API into memory (slow: one call per station)

Every station is treated as if one person lived next to it. The six-hour cooldown per person is applied, and events
the older ETA-based early warning would already have sent are counted separately, because those add no message."""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import timedelta

import core
import ingest
import notify

STEP_H = 1        # evaluate each station once per hour of its own history


def replay(conn, rise_m=None, min_pct=None):
    """Returns {'events': [...], 'span_days': float, 'stations': int, 'glitches': int}. An event is
    {'station', 'name', 'province', 'ts', 'rise', 'pct', 'eta_too'}."""
    rise_m = notify.FAST_RISE_M if rise_m is None else rise_m
    min_pct = notify.FAST_MIN_PCT if min_pct is None else min_pct
    meta = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, province FROM stations")}
    series = defaultdict(list)
    for r in conn.execute("SELECT station_id, ts, water_level, pct_of_bank FROM readings "
                          "WHERE pct_of_bank IS NOT NULL ORDER BY station_id, ts"):
        series[r["station_id"]].append((core.parse(r["ts"]), r["water_level"], r["pct_of_bank"]))
    events, glitches, first, last = [], 0, None, None
    for sid, pts in series.items():
        if not pts:
            continue
        first = pts[0][0] if first is None else min(first, pts[0][0])
        last = pts[-1][0] if last is None else max(last, pts[-1][0])
        cooldown_until, next_eval = None, None
        for i, (t, level, pct) in enumerate(pts):
            if next_eval is not None and t < next_eval:
                continue
            next_eval = t + timedelta(hours=STEP_H)
            window = [(a, b) for a, b, _ in pts[: i + 1] if b is not None and t - a <= timedelta(hours=core.FAST_HOURS)]
            rise, suspect = core.rise_of(window)
            glitches += 1 if suspect else 0
            if rise is None or rise < rise_m or pct < min_pct:
                continue
            if cooldown_until and t < cooldown_until:
                continue
            cooldown_until = t + timedelta(hours=notify.EARLY_COOLDOWN_H)
            six = [(a, c) for a, _, c in pts[: i + 1] if t - a <= timedelta(hours=core.TREND_HOURS)]
            label, rate = core.trend_of(six)
            eta = core.eta_to_bank_h(pct, rate) if label == "rising" else None
            status = ingest.status_of(pct)
            eta_too = bool(eta and eta <= notify.EARLY_WITHIN_H and status in ("watch", "alert"))
            m = meta.get(sid, {})
            events.append({"station": sid, "name": m.get("name", sid), "province": m.get("province") or "", "ts": core.iso(t),
                           "rise": rise, "pct": round(pct, 1), "eta_too": eta_too})
    span = ((last - first).total_seconds() / 86400) if first and last else 0.0
    return {"events": events, "span_days": round(span, 1), "stations": len(series), "glitches": glitches}


def report(res, rise_m, min_pct):
    ev, span = res["events"], max(res["span_days"], 0.01)
    new = [e for e in ev if not e["eta_too"]]
    lines = [f"Replay of {res['stations']} stations over {res['span_days']} days (thresholds: rise >= {rise_m} m in 3 h, pct >= {min_pct}%)",
             f"fast-rise events: {len(ev)}  (already covered by the ETA early warning: {len(ev) - len(new)}, NEW messages: {len(new)})",
             f"sensor glitches filtered out: {res['glitches']}",
             f"extra messages per day if one person lived by every station: {len(new) / span:.1f}"]
    per_day = Counter(e["ts"][:10] for e in new)
    if per_day:
        lines.append("new messages per day: " + ", ".join(f"{d} {n}" for d, n in sorted(per_day.items())))
    for title, key in (("noisiest stations", "name"), ("provinces", "province")):
        top = Counter(e[key] for e in new).most_common(8)
        if top:
            lines.append(f"{title}: " + "; ".join(f"{k} x{n}" for k, n in top))
    if res["span_days"] < 14:
        lines.append(f"NOTE: only {res['span_days']} days of history, treat these numbers as a rough sample")
    return "\n".join(lines)


def load_api(base, days, pause=0.15):
    """Copy /stations and each station's readings from our own API into an in-memory SQLite database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ingest.init_db(conn)
    get = lambda path: json.loads(urllib.request.urlopen(urllib.request.Request(
        base.rstrip("/") + path, headers={"User-Agent": "nkb-backtest/1"}), timeout=30).read())
    stations = [s for s in get("/stations") if s.get("pct_of_bank") is not None]
    for n, s in enumerate(stations, 1):
        conn.execute("INSERT INTO stations(id,name,province,lat,lng) VALUES(?,?,?,?,?)", (s["id"], s["name"], s["province"], s["lat"], s["lng"]))
        for r in get(f"/stations/{s['id']}/readings?days={days}"):
            conn.execute("INSERT OR IGNORE INTO readings VALUES(?,?,?,?,?)", (s["id"], r["ts"], r["water_level"], r["pct_of_bank"], r["status"]))
        if n % 100 == 0:
            print(f"  loaded {n}/{len(stations)} stations", file=sys.stderr)
        time.sleep(pause)
    return conn


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", help="SQLite file with stations and readings")
    ap.add_argument("--api", help="base URL of this site; reads /stations and /stations/<id>/readings")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--rise", type=float, default=notify.FAST_RISE_M, help="metres in 3 h")
    ap.add_argument("--min-pct", type=float, default=notify.FAST_MIN_PCT)
    a = ap.parse_args(argv)
    if bool(a.db) == bool(a.api):
        ap.error("give exactly one of --db or --api")
    conn = load_api(a.api, a.days) if a.api else sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    print(report(replay(conn, a.rise, a.min_pct), a.rise, a.min_pct))
    return 0


if __name__ == "__main__":
    sys.exit(main())

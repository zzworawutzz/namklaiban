"""Shared read-side logic for the API and the notifier: latest readings per
station, staleness, trend / time-to-bank estimate, plain-language advice and
upstream/downstream lookup. No web framework imports, so notify.py stays light."""
import math
from datetime import datetime, timedelta, timezone

from ingest import STALE_MIN, WATCH_PCT, ALERT_PCT

TREND_HOURS = 6        # window used to estimate the rate of change
TREND_MIN_SPAN_H = 0.75
TREND_FLAT = 0.5       # |%/hour| below this counts as steady
ETA_MAX_H = 48
RELATED_KM = 150
COLOCATED_KM = 0.5     # same site reported by another agency (e.g. RID vs EGAT)
FAST_HOURS = 3         # "rising fast" looks at the last few hours, in metres
FAST_MIN_RISE_M = 0.30
FAST_MIN_POINTS, FAST_MIN_SPAN_H = 3, 1.5
SPIKE_STEP_M = 2.0     # a jump this big between two readings is a sensor glitch, not a flood wave
SPIKE_TOTAL_M = 3.0
TIDE_HOURS = 72        # a tide-affected gauge swings up and down again and again; look this far back to see it
TIDE_SWING_M = 0.4     # a swing smaller than this is noise
TIDE_HALF_CYCLE_H = (4, 14)   # time between one turning point and the next on a tidal river (a rain pulse is slower or irregular)
TIDE_MIN_HALF_CYCLES = 3
RANK = {"unknown": 0, "normal": 1, "watch": 2, "alert": 3}

LATEST = """
SELECT s.id, s.name, s.source, s.province, s.lat, s.lng, s.bank_level, s.ground_level,
       s.river, s.basin, s.watch_pct, s.alert_pct,
       r.ts, r.water_level, r.pct_of_bank, r.status
FROM stations s
LEFT JOIN readings r ON r.station_id = s.id
 AND r.ts = (SELECT MAX(ts) FROM readings WHERE station_id = s.id)
"""


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def km(lat1, lng1, lat2, lng2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def trend_of(points):
    """points: [(datetime, pct)] oldest first -> (label, pct_per_hour) or (None, None)."""
    if len(points) < 2:
        return None, None
    (t0, p0), (t1, p1) = points[0], points[-1]
    span_h = (t1 - t0).total_seconds() / 3600
    if span_h < TREND_MIN_SPAN_H:
        return None, None
    rate = (p1 - p0) / span_h
    label = "rising" if rate >= TREND_FLAT else "falling" if rate <= -TREND_FLAT else "steady"
    return label, round(rate, 2)


def eta_to_bank_h(pct, rate):
    """Hours until 100% of bank at the current rate, only when rising and still below."""
    if pct is None or rate is None or rate < TREND_FLAT or pct >= 100:
        return None
    h = (100 - pct) / rate
    return round(h, 1) if h <= ETA_MAX_H else None


def advice(d):
    """Short Thai guidance. Informational only; the official source is ปภ./local authority."""
    pct, trend = d.get("pct_of_bank"), d.get("trend")
    if d["status"] == "unknown":
        return "สถานีนี้ไม่มีค่าตลิ่ง/ท้องน้ำให้เทียบ จึงประเมินสถานะไม่ได้"
    if d["stale"]:
        return "ข้อมูลไม่อัปเดตเกิน 2 ชั่วโมง ตรวจสถานการณ์จริงจากหน่วยงานในพื้นที่ (สายด่วน ปภ. 1784)"
    rising = trend == "rising"
    if pct is not None and pct >= 100:
        return "น้ำล้นตลิ่งแล้ว ติดตามประกาศของ ปภ. และเตรียมย้ายของขึ้นที่สูง (สายด่วน 1784)"
    if d["status"] == "alert":
        return ("ใกล้ล้นตลิ่งและกำลังสูงขึ้น ควรยกของขึ้นที่สูงและติดตามประกาศใกล้ชิด" if rising
                else "ระดับน้ำสูงใกล้ตลิ่ง ติดตามต่อเนื่อง")
    if d["status"] == "watch":
        return ("ระดับน้ำกำลังสูงขึ้น เฝ้าระวังและตรวจสอบทุกชั่วโมง" if rising
                else "ระดับน้ำค่อนข้างสูง ยังไม่ถึงขั้นเตือนภัย")
    return "ระดับน้ำปกติ" if not rising else "ระดับน้ำปกติแต่กำลังสูงขึ้น ติดตามต่อเนื่อง"


def rise_of(levels):
    """levels: [(datetime, metres)] oldest first -> (rise_m, suspect). rise_m is None when there is too
    little data; suspect is True when the series jumps like a faulty sensor (rise_m is then None too)."""
    if len(levels) < FAST_MIN_POINTS or (levels[-1][0] - levels[0][0]).total_seconds() / 3600 < FAST_MIN_SPAN_H:
        return None, False
    steps = [b[1] - a[1] for a, b in zip(levels, levels[1:])]
    total = levels[-1][1] - levels[0][1]
    if any(abs(x) > SPIKE_STEP_M for x in steps) or abs(total) > SPIKE_TOTAL_M:
        return None, True
    return round(total, 2), False


def _turning_points(levels, amp):
    """[(datetime, metres)] -> the highs and lows of the zigzag whose swings are at least `amp`."""
    out, ext, direction = [], levels[0], 0
    for t, v in levels[1:]:
        if direction >= 0 and v <= ext[1] - amp:
            out.append(ext); direction, ext = -1, (t, v)
        elif direction <= 0 and v >= ext[1] + amp:
            out.append(ext); direction, ext = 1, (t, v)
        elif (direction == 1 and v > ext[1]) or (direction == -1 and v < ext[1]):
            ext = (t, v)
    return out


def is_tidal(levels):
    """True when the gauge keeps rising and falling about every 6 hours, which is the sea moving the water
    and not a flood wave arriving. levels: [(datetime, metres)] oldest first, about TIDE_HOURS long.
    Tested on 10 days of the coastal gauges (Samut Prakan / Songkhram / Sakhon): they swing 1.6-2.4 m a day."""
    if len(levels) < 12:
        return False
    tp = _turning_points(levels, TIDE_SWING_M)[1:]   # the first one is only where the window starts
    lo, hi = TIDE_HALF_CYCLE_H
    return sum(lo <= (b[0] - a[0]).total_seconds() / 3600 <= hi for a, b in zip(tp, tp[1:])) >= TIDE_MIN_HALF_CYCLES


OVER_BANK_MAX_CM = 300    # more than 3 m above the bank is far more likely a datum error in the source than a real flood: say nothing
UNDER_BANK_MAX_CM = 100   # "below the bank" only matters when the water is within a metre of it


def no_bank_figures(bank, ground):
    """ThaiWater gives some gauges (reservoirs, forest-unit stations) bank 0 and bed 0: that is "no thresholds recorded", not a bank at sea level."""
    return bank == 0 and not ground


def over_bank_cm(d):
    """Water level minus bank level in cm (positive = above the bank), rounded to 5 cm, or None when it should not be shown:
    stale or missing figures, a number outside a believable range, or one that disagrees with the % of bank
    (the % is measured from the river bed, so the two only agree when the bed, the bank and the level are consistent).
    The bank is ThaiWater's lowest bank height at the station: a rough guide, not the depth of water at someone's house."""
    lv, bk, gd, pct = d.get("water_level"), d.get("bank_level"), d.get("ground_level"), d.get("pct_of_bank")
    if d.get("stale") or lv is None or bk is None:
        return None
    if no_bank_figures(bk, gd) or (gd is not None and bk <= gd):    # no thresholds recorded (0 / 0), or a bank not above the river bed
        return None
    cm = (lv - bk) * 100
    if cm > OVER_BANK_MAX_CM or cm < -UNDER_BANK_MAX_CM:
        return None
    if pct is not None and abs(cm) > 2 and (pct >= 100) != (cm >= 0):
        return None
    return int(round(cm / 5.0)) * 5


def over_bank_text(cm):
    """'สูงกว่าตลิ่งราว 80 ซม.' for a value from over_bank_cm; empty for None."""
    if cm is None:
        return ""
    if cm == 0:
        return "น้ำเสมอระดับตลิ่ง"
    return ("สูงกว่าตลิ่งราว " if cm > 0 else "ต่ำกว่าตลิ่งราว ") + f"{abs(cm)} ซม."


def agency_shares(rows, max_age_min):
    """{agency: {"stations": n, "recent": how many have a reading no older than max_age_min}}. ThaiWater's four agencies
    (HII, RID, FOP, EGAT) publish independently, so one can go quiet while the rest, and our own ingest, are fine."""
    out = {}
    for r in rows:
        a = out.setdefault(r.get("source") or "?", {"stations": 0, "recent": 0})
        a["stations"] += 1
        if r.get("age_min") is not None and r["age_min"] <= max_age_min:
            a["recent"] += 1
    return out


def bank_data_quality(rows, limit=10):
    """How trustworthy the bank figures are, for the owner (GET /api/cron/stats). Looks only at fresh readings and sorts each
    station into one of: fine, missing a level or the bank, bank not above the river bed (so the % is meaningless), far above
    the bank (> OVER_BANK_MAX_CM: probably a datum error in the source), % and cm disagreeing, or simply far below the bank
    (normal, not a problem). over_bank_cm hides the last three problems from users; this says how many there are and which."""
    out = {"fresh": 0, "usable": 0, "far_below": 0, "missing": 0,
           "bank_not_above_bed": [], "too_high": [], "disagree": []}
    for r in rows:
        if r.get("stale") or r.get("water_level") is None and r.get("bank_level") is None:
            continue
        out["fresh"] += 1
        lv, bk, gd, pct = r.get("water_level"), r.get("bank_level"), r.get("ground_level"), r.get("pct_of_bank")
        item = {"id": r["id"], "name": r["name"], "province": r.get("province")}
        if lv is None or bk is None or no_bank_figures(bk, gd):
            out["missing"] += 1
            continue
        if gd is not None and bk <= gd:
            out["bank_not_above_bed"].append(dict(item, bank=bk, bed=gd))
            continue
        cm = (lv - bk) * 100
        if cm > OVER_BANK_MAX_CM:
            out["too_high"].append(dict(item, cm=round(cm), pct=None if pct is None else round(pct)))
        elif cm < -UNDER_BANK_MAX_CM:
            out["far_below"] += 1
        elif pct is not None and abs(cm) > 2 and (pct >= 100) != (cm >= 0):
            out["disagree"].append(dict(item, cm=round(cm), pct=round(pct)))
        else:
            out["usable"] += 1
    for k in ("bank_not_above_bed", "too_high", "disagree"):
        out[k + "_count"] = len(out[k])
        out[k] = sorted(out[k], key=lambda i: -abs(i.get("cm", 0)))[:limit]
    return out


def shape(r, at, points=None, levels=None):
    d = dict(r)
    d["watch_pct"] = d["watch_pct"] if d.get("watch_pct") is not None else WATCH_PCT
    d["alert_pct"] = d["alert_pct"] if d.get("alert_pct") is not None else ALERT_PCT
    if d["ts"]:
        age = int((at - parse(d["ts"])).total_seconds() // 60)
        d["age_min"], d["stale"] = max(age, 0), age > STALE_MIN
    else:
        d["status"], d["age_min"], d["stale"] = "unknown", None, True
    d["status"] = d["status"] or "unknown"
    d["trend"], d["trend_pct_per_hr"] = (None, None) if d["stale"] else trend_of(points or [])
    d["rise_3h_m"], d["rise_suspect"] = (None, False) if d["stale"] else rise_of(levels or [])
    d["over_bank_cm"] = over_bank_cm(d)
    d["eta_to_bank_h"] = eta_to_bank_h(d["pct_of_bank"], d["trend_pct_per_hr"])
    d["advice"] = advice(d)
    return d


def recent_points(conn, at, hours=TREND_HOURS):
    since = iso(at - timedelta(hours=hours))
    out = {}
    for r in conn.execute(
            "SELECT station_id, ts, pct_of_bank FROM readings "
            "WHERE ts >= ? AND pct_of_bank IS NOT NULL ORDER BY station_id, ts", (since,)):
        out.setdefault(r["station_id"], []).append((parse(r["ts"]), r["pct_of_bank"]))
    return out


NEAR_STATIONS = 8      # latest(near=...): per saved place, this many nearest stations (and this many nearest fresh ones) get trends
MAX_SERIES_IDS = 300   # more than this and the whole scan is simpler (and the parameter list stays small)


def ids_near(raw, at, places):
    """Ids of the stations worth computing trends for when only a few places matter: for each place the NEAR_STATIONS nearest
    stations, and the NEAR_STATIONS nearest ones that are not stale (the nearest fresh one can be far down the plain list)."""
    cut = iso(at - timedelta(minutes=STALE_MIN))
    pts = [(r["id"], r["lat"], r["lng"], bool(r["ts"] and r["ts"] >= cut)) for r in raw if r["lat"] is not None and r["lng"] is not None]
    ids = set()
    for lat, lng in places:
        by = sorted(pts, key=lambda p: km(lat, lng, p[1], p[2]))
        ids.update(p[0] for p in by[:NEAR_STATIONS])
        ids.update([p[0] for p in by if p[3]][:NEAR_STATIONS])
    return ids


def recent_series(conn, at, ids=None):
    """(points, levels) in ONE pass over the last TREND_HOURS of readings, instead of two separate scans (recent_points + recent_levels):
    about a third fewer rows leave the database. Every row is ~60 bytes on the wire and ~18,000 rows were being read for each call
    (6 Oct 2026: 812 stations, HII/FOP about every 15 min, RID/EGAT hourly), which is what ate Neon's 5 GB monthly transfer."""
    since, since_levels = iso(at - timedelta(hours=TREND_HOURS)), iso(at - timedelta(hours=FAST_HOURS))
    pts, lv = {}, {}
    only, args = "", [since]
    if ids is not None and len(ids) <= MAX_SERIES_IDS:
        if not ids:
            return pts, lv
        only, args = f" AND station_id IN ({','.join('?' * len(ids))})", [since, *sorted(ids)]
    for r in conn.execute(
            "SELECT station_id, ts, pct_of_bank, water_level FROM readings "
            "WHERE ts >= ? AND (pct_of_bank IS NOT NULL OR water_level IS NOT NULL)" + only + " ORDER BY station_id, ts", args):
        t = None
        if r["pct_of_bank"] is not None:
            t = parse(r["ts"])
            pts.setdefault(r["station_id"], []).append((t, r["pct_of_bank"]))
        if r["water_level"] is not None and r["ts"] >= since_levels:
            lv.setdefault(r["station_id"], []).append((t or parse(r["ts"]), r["water_level"]))
    return pts, lv


FRESH = """
SELECT s.id, s.source, r.ts
FROM stations s
LEFT JOIN readings r ON r.station_id = s.id
 AND r.ts = (SELECT MAX(ts) FROM readings WHERE station_id = s.id)
"""


def freshness(conn, at):
    """[{id, source, ts, age_min, stale}] for every station: only what the health checks need (how old is each station's newest
    reading, and which agency is it), without the trend and tide scans of latest(). About 40 KB instead of over a megabyte."""
    out = []
    for r in conn.execute(FRESH):
        d = {"id": r["id"], "source": r["source"], "ts": r["ts"]}
        if r["ts"]:
            age = int((at - parse(r["ts"])).total_seconds() // 60)
            d["age_min"], d["stale"] = max(age, 0), age > STALE_MIN
        else:
            d["age_min"], d["stale"] = None, True
        out.append(d)
    return out


def recent_levels(conn, at, hours=FAST_HOURS):
    since = iso(at - timedelta(hours=hours))
    out = {}
    for r in conn.execute(
            "SELECT station_id, ts, water_level FROM readings "
            "WHERE ts >= ? AND water_level IS NOT NULL ORDER BY station_id, ts", (since,)):
        out.setdefault(r["station_id"], []).append((parse(r["ts"]), r["water_level"]))
    return out


def fast_risers(rows, limit=5):
    """Stations whose water rose the most over the last FAST_HOURS, even if still far below the bank.
    One entry per site (twins reported by two agencies count once)."""
    best = {}
    for r in rows:
        if r.get("rise_3h_m") is None or r["rise_3h_m"] < FAST_MIN_RISE_M:
            continue
        key = (round(r["lat"], 2), round(r["lng"], 2))
        if key not in best or r["rise_3h_m"] > best[key]["rise_3h_m"]:
            best[key] = r
    return sorted(best.values(), key=lambda r: -r["rise_3h_m"])[:limit]


def latest(conn, at, province=None, status=None, near=None):
    """Every station with its newest reading. `near=[(lat, lng), ...]`: compute trends only for the stations around those places
    (the notifier needs nothing else; the other rows come back with trend/rise left empty). This is the difference between reading
    a few hundred readings and about twelve thousand from the database."""
    sql, args, where = LATEST, [], []
    if province:
        where.append("s.province LIKE ?")
        args.append(f"%{province}%")
    if status:
        where.append("COALESCE(r.status, 'unknown') = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    raw = conn.execute(sql, args).fetchall()
    pts, lv = recent_series(conn, at, None if near is None else ids_near(raw, at, near))
    rows = [shape(r, at, pts.get(r["id"]), lv.get(r["id"])) for r in raw]
    mark_tidal(conn, rows, at)
    return attach_twins(rows)


def mark_tidal(conn, rows, at):
    """A rise that is only the tide is not a flood wave: drop rise_3h_m and set rise_tidal. Only the few stations
    that look like they are rising fast are checked, so this costs one small query."""
    cand = [r for r in rows if r.get("rise_3h_m") is not None and r["rise_3h_m"] >= FAST_MIN_RISE_M]
    for r in rows:
        r["rise_tidal"] = False
    if not cand:
        return
    ids = [r["id"] for r in cand]
    since = iso(at - timedelta(hours=TIDE_HOURS))
    series = {}
    for x in conn.execute(
            f"SELECT station_id, ts, water_level FROM readings WHERE station_id IN ({','.join('?' * len(ids))}) "
            "AND ts >= ? AND water_level IS NOT NULL ORDER BY station_id, ts", (*ids, since)):
        series.setdefault(x["station_id"], []).append((parse(x["ts"]), x["water_level"]))
    for r in cand:
        if is_tidal(series.get(r["id"], [])):
            r["rise_3h_m"], r["rise_tidal"] = None, True


def attach_twins(rows):
    """Flag sites reported by more than one agency. Each station keeps its own status;
    `twin_conflict` is True when a fresh twin disagrees, so the UI/notifier can say so."""
    buckets = {}
    for r in rows:
        buckets.setdefault((round(r["lat"], 2), round(r["lng"], 2)), []).append(r)
    for r in rows:
        r["twins"], r["twin_conflict"] = [], False
    for group in buckets.values():
        for a in group:
            for b in group:
                if a is b or km(a["lat"], a["lng"], b["lat"], b["lng"]) > COLOCATED_KM:
                    continue
                a["twins"].append({k: b[k] for k in ("id", "source", "status", "pct_of_bank", "stale")})
                if (not a["stale"] and not b["stale"] and "unknown" not in (a["status"], b["status"])
                        and a["status"] != b["status"]):
                    a["twin_conflict"] = True
    return rows


def nearest(rows, lat, lng, limit=3, fresh_only=False):
    if fresh_only:
        rows = [r for r in rows if not r["stale"]]
    for r in rows:
        r["distance_km"] = round(km(lat, lng, r["lat"], r["lng"]), 1)
    return sorted(rows, key=lambda r: r["distance_km"])[:limit]


def related(rows, station_id, limit=5):
    """Stations on the same river within RELATED_KM. Upstream = higher river-bed
    elevation. This is a heuristic (no river network data), so callers should label it so."""
    me = next((r for r in rows if r["id"] == station_id), None)
    if not me or not me["river"] or me["ground_level"] is None:
        return {"river": me["river"] if me else None, "upstream": [], "downstream": [], "colocated": []}
    up, down, same = [], [], []
    for r in rows:
        if r["id"] == station_id or r["river"] != me["river"] or r["ground_level"] is None:
            continue
        dist = km(me["lat"], me["lng"], r["lat"], r["lng"])
        if dist > RELATED_KM:
            continue
        item = {k: r[k] for k in ("id", "name", "province", "status", "pct_of_bank", "trend",
                                  "trend_pct_per_hr", "stale", "lat", "lng", "source")}
        item["distance_km"] = round(dist, 1)
        if dist <= COLOCATED_KM:
            same.append(item)
        elif r["ground_level"] != me["ground_level"]:
            (up if r["ground_level"] > me["ground_level"] else down).append(item)
    key = lambda x: x["distance_km"]
    return {"river": me["river"], "upstream": sorted(up, key=key)[:limit],
            "downstream": sorted(down, key=key)[:limit], "colocated": same}

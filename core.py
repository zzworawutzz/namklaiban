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


def shape(r, at, points=None):
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


def latest(conn, at, province=None, status=None):
    sql, args, where = LATEST, [], []
    if province:
        where.append("s.province LIKE ?")
        args.append(f"%{province}%")
    if status:
        where.append("COALESCE(r.status, 'unknown') = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    pts = recent_points(conn, at)
    return attach_twins([shape(r, at, pts.get(r["id"])) for r in conn.execute(sql, args)])


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

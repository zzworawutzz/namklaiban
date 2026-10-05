"""Large dams from ThaiWater (the "analyst/dam" feed behind thaiwater.net/water/dam): how full each reservoir is and how
much it lets out. Water leaving a dam travels down the same river system, so a station's page can say which big dams
sit in its basin or above it. This is context, never a forecast: a dam above 100% of its "normal" storage is not by
itself a flood, and one far below can still release water.

The feed is large (about 1 MB, 170 kB compressed) and changes once a day, so it is fetched at most once per CACHE_S
per server instance. Only dam_daily is used (about 50 large dams); the hourly list in the feed is years old and the
medium/small reservoirs are too many and too local to say anything about a river."""
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/analyst/dam"
CACHE_S = 1800
MAX_AGE_DAYS = 3                 # a dam that has not reported for this long is left out
DEFAULT_ALERT_PCT = 90.0         # shown in the LINE summary from this share of normal storage
TZ_TH = timezone(timedelta(hours=7))
_cache = {"at": 0.0, "rows": None}

# Basins whose water reaches another basin we have stations in. The Chao Phraya is formed by the Ping, Wang, Yom and Nan
# (joined by Sakae Krang and Pasak on the way down); the Chi runs into the Mun.
UPSTREAM = {
    "ลุ่มน้ำเจ้าพระยา": ("ลุ่มน้ำปิง", "ลุ่มน้ำวัง", "ลุ่มน้ำยม", "ลุ่มน้ำน่าน", "ลุ่มน้ำสะแกกรัง", "ลุ่มน้ำป่าสัก"),
    "ลุ่มน้ำมูล": ("ลุ่มน้ำชี",),
}


def alert_pct():
    try:
        return float(os.environ.get("DAM_ALERT_PCT", DEFAULT_ALERT_PCT))
    except ValueError:
        return DEFAULT_ALERT_PCT


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "nam-klai-baan-chan/0.1", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw)


def parse(payload, now=None):
    """Compact list of the large dams that reported recently:
    {name, province, basin, pct, storage_mcm, normal_mcm, inflow, released, date, lat, lng, agency}.
    pct is the share of the dam's normal storage (ThaiWater's own figure); it can pass 100."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TZ_TH).date()
    out = []
    for r in ((payload.get("data") or {}).get("dam_daily") or []):
        try:
            dam = r["dam"]
            pct = r.get("dam_storage_percent")
            day = datetime.strptime(r["dam_date"][:10], "%Y-%m-%d").date()
            if pct is None or float(pct) < 0 or (today - day).days > MAX_AGE_DAYS or (today - day).days < -1:
                continue
            if float(pct) == 0 and (r.get("dam_storage") or 0) > 0:   # a reservoir holding water cannot be at 0 %: a bad figure
                continue
            out.append({"name": (dam.get("dam_name") or {}).get("th") or str(dam.get("id")),
                        "province": ((r.get("geocode") or {}).get("province_name") or {}).get("th") or "",
                        "basin": ((r.get("basin") or {}).get("basin_name") or {}).get("th") or "",
                        "pct": round(float(pct), 1),
                        "storage_mcm": None if r.get("dam_storage") is None else round(float(r["dam_storage"]), 1),
                        "normal_mcm": None if dam.get("normal_storage") is None else round(float(dam["normal_storage"]), 1),
                        "inflow": None if r.get("dam_inflow") is None else round(float(r["dam_inflow"]), 1),
                        "released": None if r.get("dam_released") is None else round(float(r["dam_released"]), 1),
                        "date": day.isoformat(), "lat": float(dam["dam_lat"]), "lng": float(dam["dam_long"]),
                        "agency": (((r.get("agency") or {}).get("agency_shortname") or {}).get("th") or "").strip()})
        except (KeyError, TypeError, ValueError):
            continue
    # The same dam is often listed twice (RID and EGAT), the second copy a day older and sometimes with a wrong percentage:
    # keep the newest report of each dam.
    newest = {}
    for r in out:
        if r["name"] not in newest or r["date"] > newest[r["name"]]["date"]:
            newest[r["name"]] = r
    return [r for r in out if newest[r["name"]] is r]


def get(fetcher=None, clock=time.time, now=None):
    """The recent dam readings, or None when the feed cannot be reached and no recent copy exists."""
    if _cache["rows"] is not None and clock() - _cache["at"] < CACHE_S:
        return _cache["rows"]
    try:
        rows = parse((fetcher or fetch)(), now)
    except Exception as e:
        print(f"dams failed: {type(e).__name__}: {e}", file=sys.stderr)
        return _cache["rows"] if _cache["rows"] is not None and clock() - _cache["at"] < 6 * 3600 else None
    _cache["at"], _cache["rows"] = clock(), rows
    return rows


def basins_of(conn, province, min_stations=2):
    """The basins our stations in this province sit in (a basin needs a couple of stations to count, so a stray one on the
    border does not pull in another river system)."""
    rows = conn.execute("SELECT basin, COUNT(*) AS n FROM stations WHERE province = ? AND basin IS NOT NULL AND basin <> '' "
                        "GROUP BY basin ORDER BY n DESC, basin", (province,)).fetchall()
    return [r["basin"] for r in rows if r["n"] >= min_stations]


def related(rows, basins):
    """Dams in these basins or above them, fullest first."""
    wanted = set(basins)
    for b in basins:
        wanted.update(UPSTREAM.get(b, ()))
    return sorted((r for r in rows if r["basin"] in wanted), key=lambda r: -r["pct"])


def for_province(conn, province, rows=None):
    """{'as_of', 'dams': [...]} for a province, or None when the feed is down or no dam is related."""
    rows = rows if rows is not None else get()
    if not rows:
        return None
    dams = related(rows, basins_of(conn, province))
    return {"as_of": max(d["date"] for d in dams), "dams": dams} if dams else None


def high_in(conn, province):
    """The related dams at or above DAM_ALERT_PCT (at most three, fullest first), else None. Never raises: the LINE summary
    goes out without this line if the feed is down."""
    try:
        g = for_province(conn, province)
        top = sorted((d for d in (g["dams"] if g else []) if d["pct"] >= alert_pct()), key=lambda d: -(d["normal_mcm"] or 0))[:3]   # the big reservoirs matter most downstream
        return top or None
    except Exception as e:
        print(f"dam line failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def line(dams):
    """The sentence for the LINE summary."""
    parts = []
    for d in dams:
        out = f" ระบาย {d['released']:.0f} ล้าน ลบ.ม./วัน" if (d.get("released") or 0) >= 0.5 else ""
        parts.append(f"{d['name']} {round(d['pct'])}%{out}")
    return ("🏞 เขื่อนใหญ่ในลุ่มน้ำนี้และต้นน้ำที่เก็บน้ำสูง: " + " · ".join(parts)
            + " (% ของความจุปกติ ข้อมูลรายวัน เป็นบริบทประกอบ ไม่ได้บอกว่าจะท่วม)")

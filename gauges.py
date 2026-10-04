"""Rain actually measured by gauges, from ThaiWater's rain_24h feed (about 4,600 stations; mm over the last 24 h and
the last hour). The forecast on the rain card comes from a model; this is what the gauges caught, so the two are shown side by side.

The feed is large (about 4.6 MB, 0.6 MB compressed), so it is fetched at most once every CACHE_S seconds per server
instance and every answer is built from that copy. Readings older than MAX_AGE_MIN are ignored: a gauge that stopped
reporting must not show yesterday's rain as today's."""
import gzip
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from core import km

URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h"
CACHE_S = 600
MAX_AGE_MIN = 180
TZ_TH = timezone(timedelta(hours=7))
_cache = {"at": 0.0, "rows": None}


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "nam-klai-baan-chan/0.1", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw)


def parse(payload, now=None):
    """Compact list of fresh readings: {name, province, lat, lng, mm24, mm1, age_min, at}."""
    now = now or datetime.now(timezone.utc)
    out = []
    for r in payload.get("data") or []:
        try:
            st, geo = r["station"], r.get("geocode") or {}
            at = datetime.strptime(r["rainfall_datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ_TH)
            age = int((now - at).total_seconds() // 60)
            mm24, mm1 = r.get("rain_24h"), r.get("rain_1h")
            if mm24 is None or float(mm24) < 0 or age > MAX_AGE_MIN or age < -30:
                continue
            out.append({"name": (st.get("tele_station_name") or {}).get("th") or str(st.get("id")),
                        "province": (geo.get("province_name") or {}).get("th") or "",
                        "lat": float(st["tele_station_lat"]), "lng": float(st["tele_station_long"]),
                        "mm24": round(float(mm24), 1), "mm1": None if mm1 is None else round(float(mm1), 1),
                        "age_min": max(age, 0), "at": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get(fetcher=None, clock=time.time, now=None):
    """The fresh readings, or None when the feed cannot be reached and no recent copy exists."""
    if _cache["rows"] is not None and clock() - _cache["at"] < CACHE_S:
        return _cache["rows"]
    try:
        rows = parse((fetcher or fetch)(), now)
    except Exception as e:
        print(f"rain gauges failed: {type(e).__name__}: {e}", file=sys.stderr)
        return _cache["rows"] if _cache["rows"] is not None and clock() - _cache["at"] < 3 * 3600 else None
    _cache["at"], _cache["rows"] = clock(), rows
    return rows


def for_province(rows, province, top=5):
    """{'stations', 'mean_mm', 'max': {...}, 'over_35', 'top': [...], 'as_of'} for a province, or None when no gauge reports."""
    mine = [r for r in rows if r["province"] == province]
    if not mine:
        return None
    ranked = sorted(mine, key=lambda r: -r["mm24"])
    return {"stations": len(mine), "mean_mm": round(sum(r["mm24"] for r in mine) / len(mine), 1),
            "max": ranked[0], "over_35": sum(1 for r in mine if r["mm24"] >= 35),
            "top": ranked[:top], "as_of": max(r["at"] for r in mine)}


def nearest(rows, lat, lng, limit=3, max_km=25):
    near = []
    for r in rows:
        if abs(r["lat"] - lat) > max_km / 100 or abs(r["lng"] - lng) > max_km / 100:
            continue
        d = km(lat, lng, r["lat"], r["lng"])
        if d <= max_km:
            near.append((d, r))
    near.sort(key=lambda t: t[0])
    return [{**r, "distance_km": round(d, 1)} for d, r in near[:limit]]


def heavy_in(province, threshold=None):
    """for_province() of the gauges when the heaviest 24 h total reaches the warning threshold (RAIN_ALERT_MM), else None.
    Never raises: the summary goes out without this line if the feed is down."""
    import rainalert
    try:
        rows = get()
        g = for_province(rows, province) if rows else None
        return g if g and g["max"]["mm24"] >= (threshold or rainalert.heavy_mm()) else None
    except Exception as e:
        print(f"gauge line failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def line(g):
    """The sentence for the LINE summary when gauges in the province measured heavy rain."""
    m = g["max"]
    return (f"🌧 เครื่องวัดฝนจริง 24 ชม. สูงสุด {round(m['mm24'])} มม. ที่ {m['name']} "
            f"(เกิน 35 มม. {g['over_35']} จาก {g['stations']} สถานี) ค่านี้วัดจริง ไม่ใช่พยากรณ์")

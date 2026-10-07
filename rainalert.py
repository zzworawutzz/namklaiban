"""Heavy-rain line for the LINE situation summary.

Once per province (not per person) the server asks Open-Meteo for the next 24 hours of rain at the middle of the
province's stations and, when the total is heavy, the summary gets one extra sentence. The same goes for the next
3 hours (soon_for_province), which can send a person one heads-up message. No subscriber location is sent anywhere:
both look at the middle of the province's stations, never at a person's saved place. Open-Meteo is a model forecast, not an announcement of the Thai Meteorological Department, and the
sentence says so. If the forecast cannot be fetched the summary goes out without it.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

URL = "https://api.open-meteo.com/v1/forecast"
CACHE_S = 1800
VERY_HEAVY_MM = 90.0                      # same classes as the map's rain card: heavy 35-90 mm, very heavy over 90 mm
_cache = {}


def heavy_mm():
    """Rain total (mm in the next 24 h) from which a province gets the warning line (env RAIN_ALERT_MM, default 35)."""
    try:
        return max(1.0, float(os.environ.get("RAIN_ALERT_MM", "35")))
    except ValueError:
        return 35.0


def province_point(rows, province):
    pts = [(r["lat"], r["lng"]) for r in rows if r["province"] == province and r["lat"] is not None and r["lng"] is not None]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def fetch(lat, lng):
    q = urllib.parse.urlencode({"latitude": f"{lat:.3f}", "longitude": f"{lng:.3f}", "hourly": "precipitation",
                                "forecast_hours": 24, "timezone": "Asia/Bangkok"})
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": "nam-klai-baan-chan/0.1"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def next_24h_mm(lat, lng, fetcher=None, now=time.time):
    """Forecast rain total for the next 24 h in mm, or None when it cannot be fetched. Cached for 30 minutes."""
    key = (round(lat, 1), round(lng, 1))
    hit = _cache.get(key)
    if hit and now() - hit[0] < CACHE_S:
        return hit[1]
    try:
        values = (fetcher or fetch)(lat, lng)["hourly"]["precipitation"]
        mm = round(sum(v or 0 for v in values[:24]), 1) if values else None
    except Exception as e:
        print(f"rain forecast failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    _cache[key] = (now(), mm)
    return mm


def for_province(rows, province, fetcher=None, now=time.time):
    """{'mm': 62.0, 'label': 'ฝนหนัก'} when heavy rain is forecast for the province, else None."""
    pt = province_point(rows, province)
    if not pt:
        return None
    mm = next_24h_mm(pt[0], pt[1], fetcher, now)
    if mm is None or mm < heavy_mm():
        return None
    return {"mm": mm, "label": "ฝนหนักมาก" if mm >= VERY_HEAVY_MM else "ฝนหนัก" if mm >= 35 else "ฝนค่อนข้างมาก"}


def line(rain):
    """The sentence added to the summary."""
    return (f"🌧 พยากรณ์ฝน 24 ชม. ข้างหน้า ~{round(rain['mm'])} มม. ({rain['label']}) ระวังน้ำเพิ่ม "
            "(ข้อมูลจากแบบจำลอง Open-Meteo ไม่ใช่ประกาศของกรมอุตุนิยมวิทยา)")


# ---- the next few hours ---------------------------------------------------------------------------------------------
SOON_HOURS = 3
_soon_cache = {}


def soon_total_mm():
    """Rain total over the next SOON_HOURS hours (mm) from which people get the heads-up (env RAIN_SOON_MM, default 30)."""
    try:
        return max(1.0, float(os.environ.get("RAIN_SOON_MM", "30")))
    except ValueError:
        return 30.0


def soon_hourly_mm():
    """...or any single hour with at least this much (env RAIN_SOON_HOURLY_MM, default 20)."""
    try:
        return max(1.0, float(os.environ.get("RAIN_SOON_HOURLY_MM", "20")))
    except ValueError:
        return 20.0


def fetch_soon(lat, lng):
    q = urllib.parse.urlencode({"latitude": f"{lat:.3f}", "longitude": f"{lng:.3f}", "hourly": "precipitation",
                                "forecast_hours": SOON_HOURS, "timezone": "Asia/Bangkok"})
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": "nam-klai-baan-chan/0.1"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def soon_for_province(rows, province, fetcher=None, now=time.time):
    """{'mm': 34.0, 'peak_mm': 22.0, 'peak_at': '14:00', 'hours': 3, 'label': 'ฝนหนัก'} when heavy rain is forecast for the province
    in the next SOON_HOURS hours, else None (also None when the forecast cannot be fetched). Cached for 30 minutes."""
    pt = province_point(rows, province)
    if not pt:
        return None
    key = (round(pt[0], 1), round(pt[1], 1))
    hit = _soon_cache.get(key)
    if hit and now() - hit[0] < CACHE_S:
        data = hit[1]
    else:
        try:
            h = (fetcher or fetch_soon)(pt[0], pt[1])["hourly"]
            vals = [v or 0 for v in h["precipitation"][:SOON_HOURS]]
            times = h.get("time", [])[:SOON_HOURS]
            data = {"vals": vals, "times": times}
        except Exception as e:
            print(f"rain forecast (next hours) failed: {type(e).__name__}: {e}", file=sys.stderr)
            return None
        _soon_cache[key] = (now(), data)
    vals = data["vals"]
    if not vals:
        return None
    total, peak = round(sum(vals), 1), max(vals)
    if total < soon_total_mm() and peak < soon_hourly_mm():
        return None
    at = data["times"][vals.index(peak)][11:16] if data["times"] and len(data["times"]) == len(vals) else None
    return {"mm": total, "peak_mm": round(peak, 1), "peak_at": at, "hours": len(vals),
            "label": "ฝนหนักมาก" if total >= VERY_HEAVY_MM or peak >= 50 else "ฝนหนัก"}

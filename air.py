"""PM2.5 measured by the Pollution Control Department's air-quality stations (Air4Thai, about 180 stations; hourly).
This is what the instruments read, not a model. The feed is 140 kB and changes once an hour, so it is fetched at most once
per CACHE_S per server instance and every answer is built from that copy. A reading older than MAX_AGE_MIN is ignored:
a station that stopped reporting must not show last week's air as today's.

Levels are the Thai bands for PM2.5 (PCD, 2023): up to 15 very good, up to 25 good, up to 37.5 moderate, up to 75 starts to
affect health, above that affects health. The feed's value is the latest hourly one while the bands are written for a
24-hour average, so the page says so; it is information, never medical advice."""
import gzip
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import km

URL = "https://air4thai.pcd.go.th/services/getNewAQI_JSON.php"
CACHE_S = 900
MAX_AGE_MIN = 240
NEAR_KM = 50                       # PM2.5 is regional and the stations are sparse, so a wider radius than for rain
TZ_TH = timezone(timedelta(hours=7))
_cache = {"at": 0.0, "rows": None}

# (upper bound in µg/m³, level 1..5, short label)
BANDS = ((15.0, 1, "ดีมาก"), (25.0, 2, "ดี"), (37.5, 3, "ปานกลาง"), (75.0, 4, "เริ่มมีผลกระทบต่อสุขภาพ"), (float("inf"), 5, "มีผลกระทบต่อสุขภาพ"))
BKK = ("กรุงเทพฯ", "กรุงเทพ", "กรุงเทพมหานคร")


def level(pm):
    for top, n, label in BANDS:
        if pm <= top:
            return n, label


CHAIN = Path(__file__).parent / "air4thai_chain.pem"


def tls_context():
    """The normal trust store plus the two Let's Encrypt certificates that air4thai.pcd.go.th fails to send. Its server
    sends a Sectigo chain that does not belong to its Let's Encrypt certificate: browsers and curl go and fetch the missing
    link, Python on Vercel does not and stops with 'unable to get local issuer certificate'. The certificate is still fully
    checked (chain and host name); the bundle only supplies the intermediates. If the file is unreadable the plain store is
    used and the feed fails closed, as before."""
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cafile=str(CHAIN))
    except (OSError, ssl.SSLError) as e:
        print(f"air4thai chain not loaded: {type(e).__name__}: {e}", file=sys.stderr)
    return ctx


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "nam-klai-baan-chan/0.1", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=25, context=tls_context()) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw)


SPELLING = {"กาฬสินธ์ุ": "กาฬสินธุ์", "ประจวบคิรีขันธ์": "ประจวบคีรีขันธ์"}   # the feed's own typos


def province_of(area):
    """'ต.ปากน้ำ อ.เมือง, กระบี่' -> 'กระบี่'. The last part is typed by hand in the feed: some carry a 'จ.' prefix, two
    provinces are misspelt, and Bangkok stations are named after a park or a district ('... เขตจตุจักร') instead of the city."""
    tail = (area or "").split(",")[-1].strip()
    if tail.startswith("จ."):
        tail = tail[2:].strip()
    if tail in BKK or "เขต" in tail or "แขวง" in tail:
        return "กรุงเทพมหานคร"
    return SPELLING.get(tail, tail)


def parse(payload, now=None):
    """Compact list of fresh readings: {id, name, province, lat, lng, pm25, level, label, age_min, at}."""
    now = now or datetime.now(timezone.utc)
    out = []
    for s in payload.get("stations") or []:
        try:
            last = s["AQILast"]
            pm = float(last["PM25"]["value"])
            at = datetime.strptime(last["date"] + " " + last["time"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ_TH)
            age = int((now - at).total_seconds() // 60)
            if pm < 0 or age > MAX_AGE_MIN or age < -30:
                continue
            n, label = level(pm)
            out.append({"id": s.get("stationID"), "name": s.get("nameTH") or str(s.get("stationID")), "province": province_of(s.get("areaTH")),
                        "lat": float(s["lat"]), "lng": float(s["long"]), "pm25": round(pm, 1), "level": n, "label": label,
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
        print(f"air quality failed: {type(e).__name__}: {e}", file=sys.stderr)
        return _cache["rows"] if _cache["rows"] is not None and clock() - _cache["at"] < 3 * 3600 else None
    _cache["at"], _cache["rows"] = clock(), rows
    return rows


def nearest(rows, lat, lng, limit=3, max_km=NEAR_KM):
    near = []
    for r in rows:
        if abs(r["lat"] - lat) > max_km / 100 or abs(r["lng"] - lng) > max_km / 100:
            continue
        d = km(lat, lng, r["lat"], r["lng"])
        if d <= max_km:
            near.append((d, r))
    near.sort(key=lambda t: t[0])
    return [{**r, "distance_km": round(d, 1)} for d, r in near[:limit]]


def for_province(rows, province, centre=None, top=3):
    """{'stations', 'mean', 'max': {...}, 'top': [...], 'as_of', 'nearby': bool} for a province, or None.
    Stations whose feed address names the province are used; when none does, the stations nearest to `centre`
    (lat, lng of the province's water stations) are used instead and `nearby` is true so the page can say so."""
    mine = [r for r in rows if r["province"] == province]
    nearby = False
    if not mine and centre:
        mine = nearest(rows, centre[0], centre[1], limit=top, max_km=NEAR_KM)
        nearby = bool(mine)
    if not mine:
        return None
    ranked = sorted(mine, key=lambda r: -r["pm25"])
    mean = sum(r["pm25"] for r in mine) / len(mine)
    n, label = level(mean)
    return {"stations": len(mine), "mean": round(mean, 1), "level": n, "label": label, "max": ranked[0], "top": ranked[:top],
            "as_of": max(r["at"] for r in mine), "nearby": nearby}

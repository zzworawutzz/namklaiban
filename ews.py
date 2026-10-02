"""DWR (กรมทรัพยากรน้ำ) early-warning stations: flash-flood / landslide warnings.

The agency's station list is ~3 MB (2,000+ rain and water-level stations), so the server fetches it,
keeps only the stations the agency itself has flagged (status 1-3) and caches the small result.
Status meaning follows the agency's own scale: 1 watch, 2 prepare, 3 critical. We do not compute
anything on top of it, and every station carries its own update time because a flag can outlive the event.
"""
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

URL = "https://ews.dwr.go.th/ews/web-service/stn"
CACHE_S = 600        # re-fetch at most every 10 minutes
STALE_AFTER_H = 24   # a flag older than this is shown as possibly outdated
LABEL = {1: "เฝ้าระวัง", 2: "เตรียมพร้อม", 3: "วิกฤต"}
BKK = timezone(timedelta(hours=7))

_cache = {"at": 0.0, "data": None}


def fetch():
    """POST the form the agency's own site uses. Returns the raw list of stations."""
    boundary = uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\nLoadStation\r\n--{boundary}--\r\n').encode()
    req = urllib.request.Request(URL, data=body, headers={
        "User-Agent": "Mozilla/5.0", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read(20_000_000))


def parse_date(text):
    """'02/10/69 13:15 น.' (Thai time, Buddhist year) -> aware UTC datetime, or None."""
    try:
        d, t = str(text).replace("น.", "").split()
        dd, mm, yy = (int(x) for x in d.split("/"))
        hh, mi = (int(x) for x in t.split(":"))
        year = yy + 2500 - 543 if yy < 100 else yy - 543
        return datetime(year, mm, dd, hh, mi, tzinfo=BKK).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def warnings(rows, at=None):
    """Stations flagged 1-3, most severe first, in a compact shape for the map."""
    at = at or datetime.now(timezone.utc)
    out = []
    for r in rows or []:
        try:
            status = int(r.get("status"))
        except (TypeError, ValueError):
            continue
        lat, lng = _num(r.get("latitude")), _num(r.get("longitude"))
        if status not in LABEL or lat is None or lng is None:
            continue
        when = parse_date(r.get("date"))
        kind = (r.get("stn_type") or "").strip().lower()
        out.append({
            "id": r.get("stn"), "name": (r.get("name") or "").strip(), "lat": lat, "lng": lng,
            "province": r.get("province"), "district": r.get("amphoe"), "subdistrict": r.get("tambon"),
            "kind": "wl" if kind == "wl" else "rain", "status": status, "label": LABEL[status],
            "rain": _num(r.get("rain")), "rain12h": _num(r.get("rain12h")),
            "wl": _num(r.get("wl")) if r.get("wl") not in ("N/A", None) else None,
            "updated": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
            "age_h": round((at - when).total_seconds() / 3600, 1) if when else None,
            "stale": when is None or at - when > timedelta(hours=STALE_AFTER_H)})
    return sorted(out, key=lambda s: (-s["status"], s["age_h"] if s["age_h"] is not None else 1e9))


def current(loader=None, now=time.time):
    """Cached warnings. If the agency is unreachable the last good copy is served (flagged), else the error is raised."""
    t = now()
    if _cache["data"] is not None and t - _cache["at"] < CACHE_S:
        return _cache["data"]
    try:
        rows = (loader or fetch)()
        data = {"total": len(rows), "stations": warnings(rows), "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "cached": False}
    except Exception as e:
        print(f"EWS fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        if _cache["data"] is not None:
            return {**_cache["data"], "cached": True}
        raise
    _cache.update(at=t, data=data)
    return data

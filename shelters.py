"""Temporary flood shelters from the DDPM (ปภ.) open dataset, loaded once from shelters_data.json
(built by build_shelters.py). Rows: [lat, lng, name, province, district, subdistrict, capacity, phone]."""
import json
from functools import lru_cache
from pathlib import Path

import boundaries
from core import km

DATA = Path(__file__).parent / "shelters_data.json"
FIELDS = ("lat", "lng", "name", "province", "district", "subdistrict", "capacity", "phone")


@lru_cache(maxsize=1)
def _all():
    try:
        rows = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(dict(zip(FIELDS, r)) for r in rows)


def by_province(province):
    return [dict(s) for s in _all() if s["province"] == province]


def nearest(lat, lng, limit=5, max_km=50):
    near = []
    for s in _all():
        if abs(s["lat"] - lat) > max_km / 100 or abs(s["lng"] - lng) > max_km / 100:  # cheap box filter first
            continue
        d = km(lat, lng, s["lat"], s["lng"])
        if d <= max_km:
            near.append((d, s))
    near.sort(key=lambda t: t[0])
    return [{**s, "distance_km": round(d, 1)} for d, s in near[:limit]]


def directions_url(s):
    return f"https://www.google.com/maps/dir/?api=1&destination={s['lat']},{s['lng']}"


def line_text(items):
    lines = ["ศูนย์พักพิงชั่วคราวใกล้คุณ (ข้อมูล ปภ. อาจไม่เป็นปัจจุบัน โทรสอบถามก่อนเดินทาง ฉุกเฉินโทร 1784)"]
    for i, s in enumerate(items, 1):
        row = f"{i}. {s['name']} · {s['distance_km']} กม. · ต.{s['subdistrict']} อ.{s['district']}"
        if s["capacity"]:
            row += f" · รองรับ ~{s['capacity']} คน"
        if s["phone"]:
            row += f" · โทร {s['phone']}"
        lines.append(row + "\n   นำทาง: " + directions_url(s))
    return "\n".join(lines)


def _same(a, b):
    """Place names in the DDPM sheet are sometimes abbreviated or prefixed; accept equal names, or one starting with the other."""
    a, b = (a or "").strip().removeprefix("เมือง"), (b or "").strip().removeprefix("เมือง")
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 6 and (a.startswith(b) or b.startswith(a))))


def _inside(lat, lng, multipolygon):
    """Ray casting over a GeoJSON MultiPolygon's coordinates ([[ring, hole...], ...] with [lng, lat] points)."""
    for poly in multipolygon:
        if not _in_ring(lng, lat, poly[0]):
            continue
        if not any(_in_ring(lng, lat, hole) for hole in poly[1:]):
            return True
    return False


def _in_ring(x, y, ring):
    c = False
    for i in range(len(ring) - 1):
        (x1, y1), (x2, y2) = ring[i], ring[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def by_area(province, district=None, tambon=None):
    """Shelters of a province, narrowed to a district or subdistrict. A shelter counts when its recorded
    names match OR its point lies inside the area's outline (the names in the source sheet are untidy)."""
    rows = by_province(province)
    if not district:
        return rows
    outline = boundaries.outline(province, district, tambon if tambon else None)
    coords = outline["geometry"]["coordinates"] if outline else None
    out = []
    for s in rows:
        by_name = _same(s["district"], district) and (not tambon or _same(s["subdistrict"], tambon))
        if by_name or (coords and _inside(s["lat"], s["lng"], coords)):
            out.append(s)
    return out

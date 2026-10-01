"""Temporary flood shelters from the DDPM (ปภ.) open dataset, loaded once from shelters_data.json
(built by build_shelters.py). Rows: [lat, lng, name, province, district, subdistrict, capacity, phone]."""
import json
from functools import lru_cache
from pathlib import Path

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

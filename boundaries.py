"""Outlines of provinces / districts / subdistricts, loaded lazily from boundaries_data.json
(built by build_boundaries.py; UN OCHA COD-AB boundaries, CC BY-IGO)."""
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "boundaries_data.json"
ATTRIBUTION = "ขอบเขตพื้นที่: UN OCHA / กรมแผนที่ทหาร (CC BY-IGO) แบบย่อรูป ไม่ใช่แนวเขตทางกฎหมาย"


@lru_cache(maxsize=1)
def _all():
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"p": {}, "d": {}, "t": {}}


def outline(province, district=None, tambon=None):
    """GeoJSON Feature of the deepest requested level, or None if we have no outline for it."""
    d = _all()
    if province and district and tambon:
        coords = d["t"].get(province, {}).get(district, {}).get(tambon)
    elif province and district:
        coords = d["d"].get(province, {}).get(district)
    elif province:
        coords = d["p"].get(province)
    else:
        coords = None
    if not coords:
        return None
    return {"type": "Feature", "properties": {"province": province, "district": district, "tambon": tambon},
            "geometry": {"type": "MultiPolygon", "coordinates": coords}}

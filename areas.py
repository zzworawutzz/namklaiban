"""Subdistrict (ตำบล) gazetteer for the "check my area" picker: province -> district -> [(name, lat, lng)],
loaded from areas_data.json (built by build_areas.py)."""
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "areas_data.json"


@lru_cache(maxsize=1)
def _all():
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def province(name):
    """[{"name": district, "tambons": [{"name", "lat", "lng"}]}] sorted by district name, or None."""
    p = _all().get(name)
    if not p:
        return None
    return [{"name": d, "tambons": [{"name": t[0], "lat": t[1], "lng": t[2]} for t in p[d]]} for d in sorted(p)]

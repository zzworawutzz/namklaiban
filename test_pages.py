import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

import api

ROOT = Path(__file__).parent
PLACES = json.loads((ROOT / "public" / "places.json").read_text(encoding="utf-8"))


def test_every_slug_is_lowercase_letters_and_has_a_province():
    assert all(re.fullmatch(r"[a-z]+", k) and v["province"] for k, v in PLACES.items())
    assert "rangsit" in PLACES and "ayutthaya" in PLACES


def test_vercel_rewrite_lists_exactly_the_slugs():
    rw = json.loads((ROOT / "vercel.json").read_text())["rewrites"]
    assert len(rw) == 1 and rw[0]["destination"] == "/index.html"
    listed = re.fullmatch(r"/:slug\((.+)\)", rw[0]["source"]).group(1).split("|")
    assert sorted(listed) == sorted(PLACES)


def test_slugs_do_not_shadow_real_routes():
    taken = {"health", "stations", "api", "docs", "openapi", "redoc"}
    assert not taken & set(PLACES)


def test_place_provinces_exist_in_the_area_gazetteer():
    import areas
    assert all(areas.province(v["province"]) for v in PLACES.values())


def test_local_server_serves_the_app_on_pretty_paths():
    c = TestClient(api.app)
    r = c.get("/rangsit")
    assert r.status_code == 200 and "น้ำใกล้บ้านฉัน" in r.text
    assert c.get("/not-a-place").status_code == 404
    assert c.get("/health").status_code == 200   # real routes still win


def test_static_files_still_reachable_next_to_the_pretty_paths():
    c = TestClient(api.app)
    assert c.get("/places.json").status_code == 200
    assert c.get("/help.html").status_code == 200

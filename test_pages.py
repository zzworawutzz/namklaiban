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


def test_stations_are_cacheable_with_stale_while_revalidate(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_DB", str(tmp_path / "p.db"))
    api._ready.clear()
    r = TestClient(api.app).get("/stations")
    cc = r.headers["cache-control"]
    assert "max-age=60" in cc and "stale-while-revalidate=300" in cc


def test_page_starts_the_stations_request_in_the_head_and_getjson_reuses_it():
    html = (Path(__file__).parent / "public" / "index.html").read_text(encoding="utf-8")
    head = html[:html.index("</head>")]
    assert 'window.__stations = fetch("/stations")' in head                      # early, before the map libraries
    assert 'rel="preconnect" href="https://tile.openstreetmap.org"' in head
    assert html.count('fetch("/stations")') == 1                                  # nothing else asks for it a second time
    assert 'path==="/stations" && window.__stations' in html                      # getJSON picks the early answer up


def test_emergency_numbers_are_hidden_while_booting_but_always_come_back():
    html = (Path(__file__).parent / "public" / "index.html").read_text(encoding="utf-8")
    head = html[:html.index("</head>")]
    assert 'classList.add("booting")' in head and "setTimeout(window.__unboot, 3000)" in head   # fallback does not depend on the map libraries
    assert "html.booting #emergency, html.booting #foot{display:none}" in html

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


def _public(name):
    return (Path(__file__).parent / "public" / name).read_text(encoding="utf-8")


def test_page_starts_the_stations_request_in_the_head_and_getjson_reuses_it():
    html, js = _public("index.html"), _public("app.js")
    head = html[:html.index("</head>")]
    assert 'window.__stations = fetch("/stations")' in head                      # early, before the map libraries
    assert 'rel="preconnect" href="https://tile.openstreetmap.org"' in head
    assert (html + js).count('fetch("/stations")') == 1                           # nothing else asks for it a second time
    assert 'path==="/stations" && window.__stations' in js                        # getJSON picks the early answer up


def test_emergency_numbers_are_hidden_while_booting_but_always_come_back():
    html = _public("index.html")
    head = html[:html.index("</head>")]
    assert 'classList.add("booting")' in head and "setTimeout(window.__unboot, 3000)" in head   # fallback does not depend on the map libraries
    assert "html.booting #emergency, html.booting #foot{display:none}" in _public("app.css")


def test_app_files_are_versioned_and_match_the_service_worker():
    html, sw = _public("index.html"), _public("sw.js")
    m = re.search(r'href="app\.css\?v=(\d+)"', html), re.search(r'src="app\.js\?v=(\d+)"', html)
    assert m[0] and m[1] and m[0].group(1) == m[1].group(1)                       # both files carry the same version
    version = m[0].group(1)
    assert f'const SHELL = "nkb-shell-v{version}"' in sw                          # bump it in all three places together
    for f in (f"app.css?v={version}", f"app.js?v={version}"):
        assert f in sw, f                                                         # precached, so the page opens offline
    assert "<style>" not in html and html.count("<script>") == 1                  # only the small head script is inline


def test_map_libraries_from_the_cdn_are_pinned_with_integrity_hashes():
    html = _public("index.html")
    tags = re.findall(r'<(?:script|link)[^>]*cdnjs\.cloudflare\.com[^>]*>', html)
    tags = [t for t in tags if "preconnect" not in t]
    assert len(tags) == 4, tags                                                    # leaflet js+css, markercluster js+css
    for t in tags:
        assert re.search(r'integrity="sha384-[A-Za-z0-9+/]{64}"', t) and 'crossorigin="anonymous"' in t, t

import json
from pathlib import Path

from fastapi.testclient import TestClient

import api
import security


def test_api_sends_security_headers():
    r = TestClient(api.app).get("/health")
    for k, v in security.HEADERS.items():
        assert r.headers[k] == v


def test_vercel_json_matches_security_py():
    cfg = json.loads((Path(__file__).parent / "vercel.json").read_text())
    rule = next(h for h in cfg["headers"] if h["source"] == "/(.*)")
    assert {h["key"]: h["value"] for h in rule["headers"]} == security.HEADERS


def test_csp_allows_every_host_the_page_calls():
    page = "".join((Path(__file__).parent / "public" / f).read_text(encoding="utf-8") for f in ("index.html", "app.js"))
    for host in ("https://api.open-meteo.com", "https://api.rainviewer.com",
                 "https://nominatim.openstreetmap.org", "https://router.project-osrm.org"):
        assert host in page and host in security.CSP

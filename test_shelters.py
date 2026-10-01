import json
import pytest
from fastapi.testclient import TestClient

import api, db, ingest as ig, line_webhook as lw, shelters
from test_floodreports import AT, ev


def test_dataset_is_loaded_and_ayutthaya_has_shelters():
    assert len(shelters._all()) > 5000
    ay = shelters.by_province("พระนครศรีอยุธยา")
    assert len(ay) > 20 and all(5 <= s["lat"] <= 21 for s in ay)
    assert all(set(s) == set(shelters.FIELDS) for s in ay)
    assert not any("ผู้ประสาน" in k for k in shelters._all()[0])   # no personal name column


def test_nearest_sorted_and_limited():
    n = shelters.nearest(14.35, 100.57, 3, 50)
    assert 0 < len(n) <= 3 and [s["distance_km"] for s in n] == sorted(s["distance_km"] for s in n)
    assert shelters.nearest(0.0, 0.0) == []


def test_api_by_province_and_nearby_and_validation():
    cl = TestClient(api.app)
    r = cl.get("/api/shelters", params={"province": "พระนครศรีอยุธยา"})
    assert r.status_code == 200 and len(r.json()) > 20 and "max-age" in r.headers["cache-control"]
    n = cl.get("/api/shelters", params={"lat": 14.35, "lng": 100.57, "limit": 2}).json()
    assert len(n) == 2 and "distance_km" in n[0]
    assert cl.get("/api/shelters").status_code == 422


def test_line_shelter_command_needs_a_saved_location_then_lists_three(tmp_path):
    c = db.connect(str(tmp_path / "s.db")); ig.init_db(c)
    out = []
    send = lambda tok, t, flex=None, quick=None: out.append(t)
    lw.handle_event(c, ev({"type": "text", "text": "ศูนย์พักพิง"}), AT, send)
    assert "ส่งตำแหน่ง" in out[-1]
    lw.handle_event(c, ev({"type": "location", "latitude": 14.35, "longitude": 100.57}), AT, send)
    lw.handle_event(c, ev({"type": "text", "text": "ศูนย์พักพิง"}), AT, send)
    assert out[-1].count("นำทาง: https://www.google.com/maps/dir/") == 3 and "1784" in out[-1]

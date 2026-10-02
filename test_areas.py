from fastapi.testclient import TestClient

import api, areas


def test_ayutthaya_has_districts_and_most_subdistricts_with_valid_points():
    d = areas.province("พระนครศรีอยุธยา")
    names = {x["name"] for x in d}
    assert {"พระนครศรีอยุธยา", "บางปะอิน"} <= names
    tambons = [t for x in d for t in x["tambons"]]
    assert len(tambons) > 150 and all(5 <= t["lat"] <= 21 and 97 <= t["lng"] <= 106 for t in tambons)
    assert d == sorted(d, key=lambda x: x["name"])
    assert areas.province("ไม่มีจังหวัดนี้") is None


def test_api_areas_and_unknown_province():
    cl = TestClient(api.app)
    r = cl.get("/api/areas", params={"province": "ฉะเชิงเทรา"})
    assert r.status_code == 200 and r.json()["province"] == "ฉะเชิงเทรา" and len(r.json()["districts"]) >= 8
    assert "max-age" in r.headers["cache-control"]
    assert cl.get("/api/areas", params={"province": "ไม่มี"}).status_code == 404
    assert cl.get("/api/areas").status_code == 422


def test_bangkok_comes_from_the_supplementary_source():
    d = areas.province("กรุงเทพมหานคร")
    assert d and len(d) >= 45 and sum(len(x["tambons"]) for x in d) >= 150
    assert all(5 <= t["lat"] <= 21 for x in d for t in x["tambons"])

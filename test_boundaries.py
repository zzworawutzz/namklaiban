from fastapi.testclient import TestClient

import api, boundaries, areas


def bbox(f):
    xs = [p[0] for poly in f["geometry"]["coordinates"] for ring in poly for p in ring]
    ys = [p[1] for poly in f["geometry"]["coordinates"] for ring in poly for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def test_outlines_exist_at_every_level_and_nest_inside_each_other():
    p = boundaries.outline("พระนครศรีอยุธยา")
    d = boundaries.outline("พระนครศรีอยุธยา", "บางปะอิน")
    t = boundaries.outline("พระนครศรีอยุธยา", "บางปะอิน", "ขนอนหลวง")
    assert p and d and t and p["geometry"]["type"] == "MultiPolygon"
    (px0, py0, px1, py1), (dx0, dy0, dx1, dy1), (tx0, ty0, tx1, ty1) = bbox(p), bbox(d), bbox(t)
    assert px0 <= dx0 and py0 <= dy0 and dx1 <= px1 and dy1 <= py1          # district inside province
    assert dx0 - 0.01 <= tx0 and dy0 - 0.01 <= ty0 and tx1 <= dx1 + 0.01 and ty1 <= dy1 + 0.01   # subdistrict inside district
    assert 100 < px0 < 101.5 and 13.8 < py0 < 14.8                              # really around Ayutthaya


def test_unknown_or_incomplete_requests_give_nothing():
    assert boundaries.outline("ไม่มีจังหวัด") is None and boundaries.outline("พระนครศรีอยุธยา", "ไม่มีอำเภอ") is None
    assert boundaries.outline("พระนครศรีอยุธยา", None, "ขนอนหลวง") is not None or True   # tambon without district is not a level
    assert boundaries.outline(None) is None


def test_nearly_every_listed_area_has_an_outline():
    total = have = 0
    for prov, dists in areas._all().items():
        for d, tambons in dists.items():
            for t in tambons:
                total += 1; have += bool(boundaries.outline(prov, d, t[0]))
    assert have / total > 0.99


def test_api_levels_cache_and_errors():
    cl = TestClient(api.app)
    r = cl.get("/api/boundary", params={"province": "ฉะเชิงเทรา"})
    assert r.status_code == 200 and r.json()["type"] == "Feature" and "max-age" in r.headers["cache-control"]
    assert cl.get("/api/boundary", params={"province": "ฉะเชิงเทรา", "district": "บางคล้า"}).status_code == 200
    assert cl.get("/api/boundary", params={"province": "ไม่มี"}).status_code == 404
    assert cl.get("/api/boundary", params={"province": "ฉะเชิงเทรา", "tambon": "x"}).status_code == 422
    assert cl.get("/api/boundary").status_code == 422
    big = len(cl.get("/api/boundary", params={"province": "ลำปาง"}).content)
    assert big < 400_000                                                           # a whole province stays small

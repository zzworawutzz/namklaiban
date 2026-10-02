import sqlite3

from fastapi.testclient import TestClient

import api, ingest as ig, suggest
from conftest import real_rows


def test_short_or_prefix_only_queries_return_nothing():
    assert suggest.search("ก") == [] and suggest.search("  ") == [] and suggest.search("ต.") == []


def test_subdistrict_district_and_province_suggestions_with_coordinates():
    out = suggest.search("บางปะอิน")
    d = next(o for o in out if o["type"] == "อำเภอ")
    t = suggest.search("คลองสะแก")[0]
    assert t["type"] == "ตำบล" and t["label"] == "ต.คลองสะแก อ.นครหลวง จ.พระนครศรีอยุธยา" and t["name"] == "ต.คลองสะแก อ.นครหลวง"
    assert d["label"] == "อ.บางปะอิน จ.พระนครศรีอยุธยา" and 14 < d["lat"] < 15 and 100 < d["lng"] < 101
    assert all(len(o["label"]) and "lat" in o and "lng" in o for o in out)
    prov = suggest.search("นครนายก")
    assert prov[0]["type"] == "จังหวัด" and prov[0]["label"] == "จ.นครนายก"       # exact province name ranks first


def test_prefix_words_are_ignored_and_bangkok_uses_khet_khwaeng():
    assert suggest.search("อ.บางปะอิน")[0]["name"] == suggest.search("บางปะอิน")[0]["name"]
    d = suggest.search("บางรัก")[0]
    assert d["type"] == "อำเภอ" and d["label"] == "เขตบางรัก จ.กรุงเทพมหานคร"
    w = suggest.search("สีลม")[0]
    assert w["type"] == "ตำบล" and w["name"].startswith("แขวงสีลม เขต") and "จ.กรุงเทพมหานคร" in w["label"]


def test_results_are_capped_unique_and_each_kind_limited():
    out = suggest.search("บาง")
    assert 0 < len(out) <= suggest.LIMIT and len({o["label"] for o in out}) == len(out)
    for kind, cap in suggest.PER_TYPE.items():
        assert sum(o["type"] == kind for o in out) <= cap


def test_stations_are_included(tmp_path):
    out = suggest.search("บ้านปากแซง", [{"name": "บ้านปากแซง", "province": "กาญจนบุรี", "lat": 14.2, "lng": 99.0}])
    assert out[0]["type"] == "สถานี" and out[0]["label"] == "สถานีวัดน้ำ บ้านปากแซง (จ.กาญจนบุรี)"


def test_endpoint(tmp_path, monkeypatch):
    p = tmp_path / "s.db"
    c = sqlite3.connect(p); c.row_factory = sqlite3.Row; ig.init_db(c); ig.save(c, real_rows()); c.commit(); c.close()
    monkeypatch.setenv("WATER_DB", str(p))
    cl = TestClient(api.app)
    assert cl.get("/api/suggest", params={"q": "ก"}).json() == []
    r = cl.get("/api/suggest", params={"q": "บ้านแก้ง"})
    assert r.status_code == 200 and any(o["type"] == "สถานี" for o in r.json()) and "max-age" in r.headers["cache-control"]
    assert cl.get("/api/suggest", params={"q": "x" * 200}).status_code == 422

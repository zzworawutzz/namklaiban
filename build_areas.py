#!/usr/bin/env python3
"""Build areas_data.json (province -> district -> subdistricts with centre coordinates) from
https://github.com/kongvut/thai-province-data (MIT), files api/latest/{province,district,sub_district}.json.

  python build_areas.py province.json district.json sub_district.json [spicydog_output.csv]

The optional CSV is from https://github.com/spicydog/thailand-province-district-subdistrict-zipcode-latitude-longitude
(MIT). It is used only for provinces the first source has no coordinates for (Bangkok's แขวง).
"""
import csv
import json
import sys


def build(province_path, district_path, sub_path):
    load = lambda p: json.load(open(p, encoding="utf-8"))
    prov = {p["id"]: p["name"]["th"] for p in load(province_path) if not p.get("deleted_at")}
    dist = {d["id"]: (d["name"]["th"], prov.get(d["province_id"])) for d in load(district_path) if not d.get("deleted_at")}
    out = {}
    for s in load(sub_path):
        if s.get("deleted_at") or not s.get("lat") or not s.get("long") or s["district_id"] not in dist:
            continue
        lat, lng = float(s["lat"]), float(s["long"])
        dname, pname = dist[s["district_id"]]
        if not pname or not (5 <= lat <= 21 and 97 <= lng <= 106):
            continue
        out.setdefault(pname, {}).setdefault(dname, []).append([s["name"]["th"], round(lat, 5), round(lng, 5)])
    for p in out.values():
        for tambons in p.values():
            tambons.sort(key=lambda t: t[0])
    return out


def supplement(data, csv_path):
    """Add provinces that have no coordinates in the main dataset from the CSV (province,district,subdistrict,zipcode,lat,lng)."""
    added = {}
    for r in csv.DictReader(open(csv_path, encoding="utf-8-sig")):
        try:
            lat, lng = float(r["latitude"]), float(r["longitude"])
        except ValueError:
            continue
        if r["province"] in data or not (5 <= lat <= 21 and 97 <= lng <= 106):
            continue
        added.setdefault(r["province"], {}).setdefault(r["district"], []).append([r["subdistrict"], round(lat, 5), round(lng, 5)])
    for p in added.values():
        for tambons in p.values():
            tambons.sort(key=lambda t: t[0])
    data.update(added)
    return data


if __name__ == "__main__":
    data = build(*sys.argv[1:4])
    if len(sys.argv) > 4:
        supplement(data, sys.argv[4])
    with open("areas_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    n = sum(len(t) for p in data.values() for t in p.values())
    print(f"{len(data)} provinces, {n} subdistricts written to areas_data.json")

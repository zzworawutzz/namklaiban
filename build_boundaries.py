#!/usr/bin/env python3
"""Build boundaries_data.json (outlines of provinces, districts and subdistricts) for the area picker.

Source: https://github.com/siwakorne/thailocate  data/province.geojson, amphoe.geojson, tambon.geojson
(UN OCHA COD-AB boundaries of Thailand, from the Royal Thai Survey Department; licence CC BY-IGO, attribution required).

  python build_boundaries.py province.geojson amphoe.geojson tambon.geojson

Outlines are stored under OUR names (the ones in areas_data.json), matched exactly or, when the source spells a name
a little differently, by a unique prefix match inside the same province/district. Anything unmatched is left out
(the map then simply draws no outline for it). Coordinates are rounded to 4 decimals (~11 m).
"""
import json
import sys

from areas import _all as our_areas

PREC = 4


def ring(r):
    out = []
    for x, y in r:
        p = [round(x, PREC), round(y, PREC)]
        if not out or out[-1] != p:
            out.append(p)
    return out if len(out) >= 4 else None


def geom(g):
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    res = []
    for poly in polys:
        rings = [r for r in (ring(r) for r in poly) if r]
        if rings:
            res.append(rings)
    return res


def pick(name, options):
    """Exact name, else a unique prefix / 'เมือง' variant within the same parent. None if unsure."""
    if name in options:
        return name
    key = name.removeprefix("เมือง")
    hits = [o for o in options if len(o) >= 6 and len(name) >= 6 and (o.startswith(name) or name.startswith(o))]
    hits += [o for o in options if o.removeprefix("เมือง") == key and o not in hits]
    return hits[0] if len(hits) == 1 else None


def build(province_path, amphoe_path, tambon_path):
    load = lambda p: json.load(open(p, encoding="utf-8"))["features"]
    prov = {f["properties"]["ADM1_TH"]: f for f in load(province_path)}
    amp, tam = {}, {}
    for f in load(amphoe_path):
        amp.setdefault(f["properties"]["ADM1_TH"], {})[f["properties"]["ADM2_TH"]] = f
    for f in load(tambon_path):
        pr = f["properties"]
        tam.setdefault(pr["ADM1_TH"], {}).setdefault(pr["ADM2_TH"], {})[pr["ADM3_TH"]] = f
    out = {"p": {}, "d": {}, "t": {}}
    stats = {"p": [0, 0], "d": [0, 0], "t": [0, 0]}
    for p, dists in our_areas().items():
        stats["p"][1] += 1
        if p in prov:
            out["p"][p] = geom(prov[p]["geometry"]); stats["p"][0] += 1
        for d, tambons in dists.items():
            stats["d"][1] += 1
            dkey = pick(d, amp.get(p, {}))
            if dkey:
                out["d"].setdefault(p, {})[d] = geom(amp[p][dkey]["geometry"]); stats["d"][0] += 1
            tsrc = tam.get(p, {}).get(dkey) if dkey else None
            for t in tambons:
                stats["t"][1] += 1
                tkey = pick(t[0], tsrc or {})
                if tkey:
                    out["t"].setdefault(p, {}).setdefault(d, {})[t[0]] = geom(tsrc[tkey]["geometry"]); stats["t"][0] += 1
    return out, stats


if __name__ == "__main__":
    data, stats = build(*sys.argv[1:4])
    with open("boundaries_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    for k, name in (("p", "provinces"), ("d", "districts"), ("t", "subdistricts")):
        print(f"{name}: {stats[k][0]}/{stats[k][1]} matched")

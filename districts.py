"""Per-district summary of the water-level stations: which district is worst right now.
A station belongs to the district whose outline (boundaries_data.json, UN OCHA) contains its coordinates.
The colour of a district is the status of its worst FRESH station with known thresholds - a statement about the gauges
inside it, not about where the water actually is, so a district without a gauge is "none", never "normal"."""
import boundaries

RANK = {"normal": 1, "watch": 2, "alert": 3}


def _in_ring(lng, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def contains(multipolygon, lng, lat):
    """GeoJSON MultiPolygon coordinates: [polygon][ring][[lng, lat]]; ring 0 is the outline, the others are holes."""
    for poly in multipolygon:
        if poly and _in_ring(lng, lat, poly[0]) and not any(_in_ring(lng, lat, hole) for hole in poly[1:]):
            return True
    return False


def summary(province, rows):
    """GeoJSON FeatureCollection of the province's districts with the station summary in each one's properties,
    or None when we have no district outlines for the province."""
    shapes = boundaries._all()["d"].get(province)
    if not shapes:
        return None
    mine = [r for r in rows if r["province"] == province and r["lat"] is not None and r["lng"] is not None]
    feats = []
    for name, coords in sorted(shapes.items()):
        inside = [r for r in mine if contains(coords, r["lng"], r["lat"])]
        fresh = [r for r in inside if not r["stale"] and r["status"] in RANK]
        counts = {k: sum(1 for r in fresh if r["status"] == k) for k in ("alert", "watch", "normal")}
        worst = max(fresh, key=lambda r: (RANK[r["status"]], r["pct_of_bank"] or 0), default=None)
        top = max((r for r in fresh if r["pct_of_bank"] is not None), key=lambda r: r["pct_of_bank"], default=None)
        feats.append({"type": "Feature",
                      "properties": {"district": name, "stations": len(inside), "fresh": len(fresh),
                                     "status": worst["status"] if worst else "none", "counts": counts,
                                     "top": {"name": top["name"], "pct": round(top["pct_of_bank"])} if top else None},
                      "geometry": {"type": "MultiPolygon", "coordinates": coords}})
    return {"type": "FeatureCollection", "features": feats, "attribution": boundaries.ATTRIBUTION}

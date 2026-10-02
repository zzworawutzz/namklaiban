"""Type-ahead suggestions for the map's search box, built only from data we already hold:
subdistricts / districts / provinces (areas_data.json), water stations (the database) and DDPM shelters.
Nothing leaves our server, and landmarks (markets, malls) are deliberately not covered - those still go
through the normal OpenStreetMap search when the user presses Enter."""
import re
from functools import lru_cache

import areas
import shelters

MIN_Q, MAX_Q, LIMIT = 2, 40, 8
PER_TYPE = {"ตำบล": 4, "อำเภอ": 3, "จังหวัด": 2, "สถานี": 4, "ศูนย์พักพิง": 3}
ORDER = ["จังหวัด", "อำเภอ", "ตำบล", "สถานี", "ศูนย์พักพิง"]
_PREFIX = re.compile(r"^(ตำบล|แขวง|อำเภอ|เขต|จังหวัด|ต\.|อ\.|จ\.)\s*")


def norm(q):
    return _PREFIX.sub("", (q or "").strip().lower())


@lru_cache(maxsize=1)
def _static():
    """[(kind, name_lowercase, name, label, short, lat, lng, extra)] for places that do not change;
    `extra` says where the place sits so the map can open the right province / district / subdistrict."""
    out = []
    for prov, dists in areas._all().items():
        lats, lngs = [], []
        for dname, tambons in dists.items():
            dl, dn = [], []
            for name, lat, lng in tambons:
                dl.append(lat); dn.append(lng)
                tp = "แขวง" if prov == "กรุงเทพมหานคร" else "ต."
                dp = "เขต" if prov == "กรุงเทพมหานคร" else "อ."
                short = f"{tp}{name} {dp}{dname}"
                out.append(("ตำบล", name.lower(), name, f"{short} จ.{prov}", short, lat, lng,
                            {"province": prov, "district": dname, "tambon": name}))
            if dl:
                lats += dl; lngs += dn
                dp = "เขต" if prov == "กรุงเทพมหานคร" else "อ."
                out.append(("อำเภอ", dname.lower(), dname, f"{dp}{dname} จ.{prov}", f"{dp}{dname}",
                            sum(dl) / len(dl), sum(dn) / len(dn), {"province": prov, "district": dname}))
        if lats:
            out.append(("จังหวัด", prov.lower(), prov, f"จ.{prov}", f"จ.{prov}", sum(lats) / len(lats), sum(lngs) / len(lngs),
                    {"province": prov}))
    for s in shelters._all():
        out.append(("ศูนย์พักพิง", s["name"].lower(), s["name"],
                    f"{s['name']} (ต.{s['subdistrict']} อ.{s['district']} จ.{s['province']})", s["name"], s["lat"], s["lng"],
                    {"province": s["province"]}))
    return tuple(out)


def _rank(name_lc, q):
    if name_lc == q:
        return 0
    if name_lc.startswith(q):
        return 1
    return 2 if q in name_lc else None


def search(q, stations=(), limit=LIMIT):
    """Best matches for the typed text. `stations` = rows with name/province/lat/lng."""
    q = norm(q)[:MAX_Q]
    if len(q) < MIN_Q:
        return []
    cands = []
    for kind, name_lc, name, label, short, lat, lng, extra in _static():
        r = _rank(name_lc, q)
        if r is not None:
            cands.append((r, ORDER.index(kind), len(name), kind, label, short, lat, lng, extra))
    for s in stations:
        r = _rank((s["name"] or "").lower(), q)
        if r is not None:
            cands.append((r, ORDER.index("สถานี"), len(s["name"]), "สถานี",
                          f"สถานีวัดน้ำ {s['name']} (จ.{s['province']})", s["name"], s["lat"], s["lng"],
                          {"province": s["province"], "id": s.get("id")}))
    cands.sort(key=lambda c: c[:3])
    out, used, seen = [], {}, set()
    for r, _, _, kind, label, short, lat, lng, extra in cands:
        if used.get(kind, 0) >= PER_TYPE[kind] or label in seen:
            continue
        used[kind] = used.get(kind, 0) + 1; seen.add(label)
        out.append({"type": kind, "label": label, "name": short, "lat": round(lat, 5), "lng": round(lng, 5), **extra})
        if len(out) >= limit:
            break
    return out

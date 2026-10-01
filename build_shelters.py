#!/usr/bin/env python3
"""Build shelters_data.json from the DDPM (ปภ.) open dataset "ข้อมูลศูนย์พักพิง" (Open Data Common).

  curl -L -o shelters.csv "https://catalog.disaster.go.th/dataset/4fa4748c-8cdc-4a81-975f-947bffbd89e0/resource/a727b6b7-e8e0-448b-9462-eb89f43cd0a5/download/dpm-gd002_final2.csv"
  python build_shelters.py shelters.csv

Keeps only what helps someone find a shelter: place, area, capacity and the published phone number.
The coordinator's personal name column is deliberately dropped.
"""
import csv
import json
import re
import sys

LOCAL_GOV = ("ทต.", "อบต.", "ทม.", "ทน.", "อบจ.", "เทศบาล", "องค์การบริหาร")


def num(x):
    try:
        return float(str(x).strip())
    except ValueError:
        return None


def phone(x):
    x = re.sub(r"[^0-9,;/\- ]", "", str(x or "")).strip(" ,;/-")
    return x[:30] if sum(c.isdigit() for c in x) >= 8 else ""


def build(path):
    out, seen = [], set()
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        lat, lng = num(r.get("ละติจูด")), num(r.get("ลองจิจูด"))
        if lat is None or lng is None:
            continue
        if 97 <= lat <= 106 and 5 <= lng <= 21:   # swapped columns
            lat, lng = lng, lat
        if not (5 <= lat <= 21 and 97 <= lng <= 106):
            continue
        name = (r.get("สถานที่") or "").strip()
        owner = (r.get("สถานที่รับผิดชอบอปท.") or "").strip()
        if not name:
            name = owner
        elif owner and owner != name and not owner.startswith(LOCAL_GOV):
            name = f"{name} ({owner})"         # generic type plus the actual place
        if not name or name in ("-",):
            continue
        key = (name, round(lat, 5), round(lng, 5))
        if key in seen:
            continue
        seen.add(key)
        cap = num(r.get("รองรับ"))
        out.append([round(lat, 5), round(lng, 5), name[:80], (r.get("จังหวัด") or "").strip(),
                    (r.get("อำเภอ") or "").strip(), (r.get("ตำบล") or "").strip(),
                    int(cap) if cap else None, phone(r.get("หมายเลขโทรศัพท์"))])
    return out


if __name__ == "__main__":
    rows = build(sys.argv[1] if len(sys.argv) > 1 else "shelters.csv")
    with open("shelters_data.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    print(len(rows), "shelters written to shelters_data.json")

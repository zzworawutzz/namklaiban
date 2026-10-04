"""Province situation report: headline numbers, change vs 24 h ago, top stations,
fastest-rising stations and a daily series, plus the Thai text used for the LINE digest.
Built only from data we already store (latest readings + up to 31 days of history)."""
import os
from datetime import timedelta

import core
from ingest import TZ_TH, status_of

DIGEST_FIRST_H, DIGEST_END_H = 7, 22   # Thai local hours: summaries go out from 07:00 and never at or after 22:00


def digest_every_h():
    """Hours between situation summaries (env DIGEST_EVERY_H, default 24 = once a day at 07:00; 3 = 07:00, 10:00, 13:00, 16:00, 19:00)."""
    try:
        return max(1, min(24, int(os.environ.get("DIGEST_EVERY_H", "24"))))
    except ValueError:
        return 24


def digest_slots():
    """Thai local hours at which a summary is due, e.g. [7, 10, 13, 16, 19]."""
    return list(range(DIGEST_FIRST_H, DIGEST_END_H, digest_every_h()))


def digest_label():
    """How the schedule reads in chat, e.g. 'ทุก 3 ชั่วโมง (07:00–19:00)'."""
    slots = digest_slots()
    return "ทุกเช้า 07:00" if len(slots) == 1 else f"ทุก {digest_every_h()} ชั่วโมง ({slots[0]:02d}:00–{slots[-1]:02d}:00)"


TOP_N = 10
TEXT_TOP = 5
THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
ARROW = {"rising": "▲", "falling": "▼", "steady": ""}   # steady is shown by the status square alone
SQUARE = {"alert": "🟥", "watch": "🟨", "normal": "🟩", "unknown": "⬜"}   # coloured by status so the share text reads at a glance


def thai_date(dt):
    th = dt.astimezone(TZ_TH)
    return f"{th.day} {THAI_MONTHS[th.month - 1]} {th.year + 543} {th:%H:%M} น."


def _status(row, pct):
    return status_of(pct, row["watch_pct"], row["alert_pct"])


def _history(conn, province, at, days):
    since = core.iso(at - timedelta(days=days + 1))
    out = {}
    for r in conn.execute(
            "SELECT r.station_id, r.ts, r.pct_of_bank FROM readings r JOIN stations s ON s.id = r.station_id "
            "WHERE s.province = ? AND r.ts >= ? AND r.pct_of_bank IS NOT NULL ORDER BY r.station_id, r.ts",
            (province, since)):
        out.setdefault(r["station_id"], []).append((core.parse(r["ts"]), r["pct_of_bank"]))
    return out


def _at_or_before(points, when):
    last = None
    for t, pct in points:
        if t > when:
            break
        last = pct
    return last


def _brief(r):
    return {k: r[k] for k in ("id", "name", "status", "pct_of_bank", "trend", "trend_pct_per_hr",
                              "eta_to_bank_h", "stale", "twin_conflict")}


def build(conn, province, at, days=14, rows=None):
    """Report dict for `province` (exact name) or None if no station has that province."""
    rows = rows if rows is not None else core.latest(conn, at)
    prov = [r for r in rows if r["province"] == province]
    if not prov:
        return None
    counts = {k: 0 for k in ("alert", "watch", "normal", "unknown")}
    for r in prov:
        counts[r["status"]] += 1
    hist = _history(conn, province, at, days)

    # change vs 24 h ago, only over stations that already reported back then
    ago, now_c, comparable, newly_over = {"alert": 0, "watch": 0}, {"alert": 0, "watch": 0}, 0, []
    for r in prov:
        before = _at_or_before(hist.get(r["id"], []), at - timedelta(hours=24))
        if before is None or r["pct_of_bank"] is None or r["stale"]:
            continue
        comparable += 1
        for k, pct in (("ago", before), ("now", r["pct_of_bank"])):
            st = _status(r, pct)
            if st in ago:
                (ago if k == "ago" else now_c)[st] += 1
        if r["pct_of_bank"] >= 100 > before:
            newly_over.append(_brief(r))
    delta = ({k: now_c[k] - ago[k] for k in ago} if comparable >= max(1, len(prov) // 2) else None)

    # daily series by Thai calendar day: a station counts by its peak that day
    today = at.astimezone(TZ_TH).date()
    series = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        c = {"alert": 0, "watch": 0, "normal": 0}
        for r in prov:
            peaks = [p for t, p in hist.get(r["id"], []) if t.astimezone(TZ_TH).date() == day]
            if peaks:
                c[_status(r, max(peaks))] += 1
        series.append({"day": day.isoformat(), **c, "reporting": sum(c.values())})

    by_pct = sorted([r for r in prov if r["pct_of_bank"] is not None], key=lambda r: -r["pct_of_bank"])
    rising = sorted([r for r in prov if r["trend"] == "rising"], key=lambda r: -(r["trend_pct_per_hr"] or 0))
    rep = {"province": province, "generated_at": core.iso(at), "stations": len(prov), "counts": counts,
           "stale": sum(1 for r in prov if r["stale"]),
           "over_bank": sum(1 for r in prov if (r["pct_of_bank"] or 0) >= 100),
           "newly_over_bank": newly_over, "delta_24h": delta,
           "top": [_brief(r) for r in by_pct[:TOP_N]], "rising": [_brief(r) for r in rising[:5]],
           "fast": [{**_brief(r), "rise_3h_m": r["rise_3h_m"]} for r in core.fast_risers(prov, 5)],
           "series": series}
    rep["text"] = digest_text(rep, at)
    return rep


def digest_text(rep, at, url=None):
    c = rep["counts"]
    lines = [f"น้ำใกล้บ้านฉัน · สรุปสถานการณ์น้ำ จ.{rep['province']}", thai_date(at),
             f"{rep['stations']} สถานี: เตือนภัย {c['alert']} · เฝ้าระวัง {c['watch']} · ปกติ {c['normal']}"
             + (f" · ข้อมูลค้าง {rep['stale']}" if rep["stale"] else "")]
    d = rep["delta_24h"]
    if d:
        sign = lambda n: f"+{n}" if n > 0 else str(n)
        lines.append(f"เทียบ 24 ชม. ก่อน: เตือนภัย {sign(d['alert'])} · เฝ้าระวัง {sign(d['watch'])}")
    if rep["over_bank"]:
        new = f" (ล้นตลิ่งใหม่ใน 24 ชม. {len(rep['newly_over_bank'])})" if rep["newly_over_bank"] else ""
        lines.append(f"ล้นตลิ่งแล้ว {rep['over_bank']} สถานี{new}")
    if rep.get("rain"):
        import rainalert
        lines.append(rainalert.line(rep["rain"]))
    if rep.get("gauge"):
        import gauges
        lines.append(gauges.line(rep["gauge"]))
    if rep["top"]:
        lines.append("น้ำสูงสุด:")
        for i, r in enumerate(rep["top"][:TEXT_TOP], 1):
            lines.append(f"{i}. {r['name']} {round(r['pct_of_bank'])}% {SQUARE.get(r['status'], '')}{ARROW.get(r['trend'], '')}".rstrip())
    if rep.get("fast"):
        lines.append("น้ำขึ้นเร็วใน 3 ชม.: " + ", ".join(f"{r['name']} +{r['rise_3h_m']:.2f} ม." for r in rep["fast"][:3]))
    if url:
        lines.append(url)
    lines.append("ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น ให้ยึดประกาศ ปภ. (1784) เป็นหลัก")
    return "\n".join(lines)

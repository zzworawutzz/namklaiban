#!/usr/bin/env python3
"""Notify subscribers when the station nearest their home changes status.

Run after each ingest:   python notify.py run
Manage subscribers:      python notify.py add --channel line --target <LINE userId> --lat 14.2 --lng 99.0 --label บ้าน
                         python notify.py list | python notify.py remove <id>

Channels:
  line    LINE Messaging API push. Needs env LINE_CHANNEL_TOKEN (channel access token
          of your LINE Official Account); target is the user's LINE userId.
  stdout  Prints the message. For testing without any account.

Rules: the first run only notifies if the station is already watch/alert; after that
a message goes out on every status change. A failed send is retried next run.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

import core
import db
from ingest import init_db, utc_now

LINE_PUSH = "https://api.line.me/v2/bot/message/push"
LABEL = {"normal": "ปกติ", "watch": "เฝ้าระวัง", "alert": "เตือนภัย", "unknown": "ไม่ทราบ"}
FOOTER = "ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น ให้ยึดประกาศ ปภ. ในพื้นที่เป็นหลัก"


def send_line(target, text):
    token = os.environ.get("LINE_CHANNEL_TOKEN")
    if not token:
        raise RuntimeError("LINE_CHANNEL_TOKEN is not set")
    if not token.isascii() or " " in token:
        raise RuntimeError("LINE_CHANNEL_TOKEN does not look like a real token "
                           "(contains non-ASCII characters or spaces) - did you leave the placeholder text in?")
    body = json.dumps({"to": target, "messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request(LINE_PUSH, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15):
        pass


def send_stdout(target, text):
    print(f"--> {target}\n{text}\n")


SENDERS = {"line": send_line, "stdout": send_stdout}


def message(sub, st):
    pct = "-" if st["pct_of_bank"] is None else f"{round(st['pct_of_bank'])}%"
    where = f" ({sub['label']})" if sub["label"] else ""
    lines = [f"น้ำใกล้บ้านฉัน{where}",
             f"สถานี {st['name']} {st['province'] or ''} อยู่ห่าง {st['distance_km']} กม.",
             f"สถานะ: {LABEL[st['status']]} · {pct} ของตลิ่ง"]
    if st["trend"] == "rising":
        eta = f" คาดถึงตลิ่งใน ~{st['eta_to_bank_h']} ชม." if st["eta_to_bank_h"] else ""
        lines.append(f"แนวโน้ม: กำลังสูงขึ้น {st['trend_pct_per_hr']:+.1f}%/ชม.{eta}")
    elif st["trend"] == "falling":
        lines.append(f"แนวโน้ม: กำลังลดลง {st['trend_pct_per_hr']:+.1f}%/ชม.")
    if st.get("twin_conflict"):
        lines.append("หมายเหตุ: จุดนี้มีอีกหน่วยงานรายงานด้วยและสถานะต่างกัน (" + ", ".join(
            f"{t['source']} {LABEL[t['status']]}" for t in st["twins"]) + ") ควรตรวจกับหน่วยงานในพื้นที่")
    lines += [st["advice"], FOOTER]
    return "\n".join(lines)


def run(conn, at=None, senders=SENDERS):
    """Returns (sent, failed). Only fresh stations are considered for a subscriber."""
    at = at or datetime.now(timezone.utc)
    rows = core.latest(conn, at)
    sent = failed = 0
    for sub in conn.execute("SELECT * FROM subscriptions").fetchall():
        near = core.nearest([dict(r) for r in rows], sub["lat"], sub["lng"], 1, fresh_only=True)
        if not near:
            continue
        st = near[0]
        prev = sub["last_status"]
        changed = st["status"] != prev
        if not changed or (prev is None and st["status"] not in ("watch", "alert")):
            if prev is None:  # silent baseline
                conn.execute("UPDATE subscriptions SET last_status=? WHERE id=?", (st["status"], sub["id"]))
            continue
        try:
            senders[sub["channel"]](sub["target"], message(sub, st))
        except Exception as e:
            print(f"send failed for subscription {sub['id']}: {e}", file=sys.stderr)
            failed += 1
            continue
        conn.execute("UPDATE subscriptions SET last_status=?, last_notified=? WHERE id=?",
                     (st["status"], utc_now(), sub["id"]))
        sent += 1
    conn.commit()
    return sent, failed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="SQLite path or postgres:// URL (default: $DATABASE_URL, $WATER_DB, water.db)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    add = sp.add_parser("add")
    add.add_argument("--channel", choices=sorted(SENDERS), required=True)
    add.add_argument("--target", required=True)
    add.add_argument("--lat", type=float, required=True)
    add.add_argument("--lng", type=float, required=True)
    add.add_argument("--label", default="")
    sp.add_parser("list")
    sp.add_parser("run")
    rm = sp.add_parser("remove")
    rm.add_argument("id", type=int)
    a = ap.parse_args(argv)

    conn = db.connect(a.db)
    init_db(conn)
    if a.cmd == "add":
        if not (-90 <= a.lat <= 90 and -180 <= a.lng <= 180):
            print("ERROR: lat/lng out of range", file=sys.stderr)
            return 1
        new_id = conn.execute("INSERT INTO subscriptions(channel,target,lat,lng,label) VALUES(?,?,?,?,?) RETURNING id",
                              (a.channel, a.target, a.lat, a.lng, a.label)).fetchone()["id"]
        conn.commit()
        print(f"added subscription {new_id}")
    elif a.cmd == "list":
        for r in conn.execute("SELECT * FROM subscriptions"):
            print(dict(r))
    elif a.cmd == "remove":
        conn.execute("DELETE FROM subscriptions WHERE id=?", (a.id,))
        conn.commit()
    else:
        sent, failed = run(conn)
        print(f"sent={sent} failed={failed}")
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import inspect
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import cards
import core
import db
import report
from ingest import TZ_TH, init_db, utc_now

LINE_PUSH = "https://api.line.me/v2/bot/message/push"
LABEL = {"normal": "ปกติ", "watch": "เฝ้าระวัง", "alert": "เตือนภัย", "unknown": "ไม่ทราบ"}
FOOTER = "ข้อมูลจาก ThaiWater ใช้ประกอบการตัดสินใจเท่านั้น ให้ยึดประกาศ ปภ. ในพื้นที่เป็นหลัก"


def _invoke(fn, *args, **kw):
    """Call fn with only the keyword arguments it declares (simple senders just ignore cards/buttons)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return fn(*args, **{k: v for k, v in kw.items() if v is not None})
    return fn(*args, **{k: v for k, v in kw.items() if k in params and v is not None})


def _line_token():
    token = os.environ.get("LINE_CHANNEL_TOKEN")
    if not token:
        raise RuntimeError("LINE_CHANNEL_TOKEN is not set")
    if not token.isascii() or " " in token:
        raise RuntimeError("LINE_CHANNEL_TOKEN does not look like a real token "
                           "(contains non-ASCII characters or spaces) - did you leave the placeholder text in?")
    return token


def _line_post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {_line_token()}"})
    with urllib.request.urlopen(req, timeout=timeout):
        pass


def _text_msg(text, quick=None):
    m = {"type": "text", "text": text[:4900]}
    if quick:
        m["quickReply"] = {"items": quick}
    return m


def _flex_msg(flex, alt, quick=None):
    m = {"type": "flex", "altText": alt[:400], "contents": flex}
    if quick:
        m["quickReply"] = {"items": quick}
    return m


def _post_with_fallback(url, base, text, flex, quick, timeout):
    """Send the card if there is one; if LINE rejects it (HTTP 400) send the plain text instead."""
    try:
        _line_post(url, {**base, "messages": [_flex_msg(flex, text, quick) if flex else _text_msg(text, quick)]}, timeout)
    except urllib.error.HTTPError as e:
        if flex is None or e.code != 400:
            raise
        print(f"LINE rejected the card ({e.read()[:200]!r}); sending plain text", file=sys.stderr)
        _line_post(url, {**base, "messages": [_text_msg(text, quick)]}, timeout)


def send_line(target, text, flex=None):
    _post_with_fallback(LINE_PUSH, {"to": target}, text, flex, None, 15)


def reply_line(reply_token, text, flex=None, quick=None):
    """Answer a user's message through the Reply API (free, valid ~1 minute)."""
    _post_with_fallback("https://api.line.me/v2/bot/message/reply", {"replyToken": reply_token}, text, flex, quick, 10)


_basic_id_cache = {}


def bot_basic_id():
    """The official account's public ID (e.g. "@123abcde") from LINE's bot-info API, cached in memory.
    Used for the website's "add friend" button. None if it cannot be fetched."""
    if "id" in _basic_id_cache:
        return _basic_id_cache["id"]
    try:
        req = urllib.request.Request("https://api.line.me/v2/bot/info", headers={"Authorization": f"Bearer {_line_token()}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            basic = json.load(r).get("basicId")
    except Exception as e:
        print(f"bot info failed: {e}", file=sys.stderr)
        return None   # not cached, so a later request can retry
    _basic_id_cache["id"] = basic
    return basic


def send_stdout(target, text, flex=None):
    print(f"--> {target}{' [card]' if flex else ''}\n{text}\n")


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


QUIET_FROM_H, QUIET_TO_H = 22, 6   # Thai local time: no pushes for people who asked for quiet nights


def is_group(sub):
    """LINE groupIds start with C, roomIds with R (userIds with U). Groups only get the morning report."""
    return sub["channel"] == "line" and sub["target"][:1] in ("C", "R")


def in_quiet_hours(at):
    h = at.astimezone(TZ_TH).hour
    return h >= QUIET_FROM_H or h < QUIET_TO_H


def run(conn, at=None, senders=SENDERS):
    """Returns (sent, failed). Only fresh stations are considered for a subscriber.
    Settings: notify_level 'alert' = only when entering/leaving the alert level;
    quiet = no pushes 22:00-06:00 Thai time unless the status is alert (held back, sent after 06:00)."""
    at = at or datetime.now(timezone.utc)
    rows = core.latest(conn, at)
    sent = failed = 0
    for sub in conn.execute("SELECT * FROM subscriptions").fetchall():
        if is_group(sub):
            continue
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
        if sub["notify_level"] == "alert" and "alert" not in (st["status"], prev):
            conn.execute("UPDATE subscriptions SET last_status=? WHERE id=?", (st["status"], sub["id"]))
            continue  # this user only wants alert-level news; remember the state without a message
        if sub["quiet"] and in_quiet_hours(at) and st["status"] != "alert":
            continue  # keep last_status unchanged so it is sent after the quiet hours if still different
        try:
            _invoke(senders[sub["channel"]], sub["target"], message(sub, st),
                    flex=cards.station_card(sub["label"], st, report_link(st["province"])))
        except Exception as e:
            print(f"send failed for subscription {sub['id']}: {e}", file=sys.stderr)
            failed += 1
            continue
        conn.execute("UPDATE subscriptions SET last_status=?, last_notified=? WHERE id=?",
                     (st["status"], utc_now(), sub["id"]))
        sent += 1
    conn.commit()
    return sent, failed


DIGEST_FROM_H, DIGEST_TO_H = 7, 11   # Thai local hours [7, 11): sent once a day, retried until 11:00


def public_url():
    """Base URL of this deployment (Vercel provides the production domain), or None."""
    host = os.environ.get("PUBLIC_URL") or os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    if not host:
        return None
    return host if host.startswith("http") else f"https://{host}"


def province_of(rows, lat, lng):
    """Province of the station nearest to the point (fresh or not)."""
    best = min((r for r in rows if r["province"]), key=lambda r: core.km(lat, lng, r["lat"], r["lng"]), default=None)
    return best["province"] if best else None


def report_link(province):
    base = public_url()
    return f"{base}/report.html?province={urllib.parse.quote(province)}" if base else None


def run_digest(conn, at=None, senders=SENDERS):
    """Send the morning situation report to subscribers who have not had today's yet.
    Returns (sent, failed). Outside 07:00-10:59 Thai time it does nothing."""
    at = at or datetime.now(timezone.utc)
    th = at.astimezone(TZ_TH)
    if not (DIGEST_FROM_H <= th.hour < DIGEST_TO_H):
        return 0, 0
    today = th.strftime("%Y-%m-%d")
    subs = conn.execute("SELECT * FROM subscriptions WHERE digest = 1 AND (last_digest IS NULL OR last_digest <> ?)",
                        (today,)).fetchall()
    if not subs:
        return 0, 0
    rows = core.latest(conn, at)
    cache, sent, failed = {}, 0, 0
    for sub in subs:
        prov = (sub["label"] or "")[2:] if is_group(sub) else province_of(rows, sub["lat"], sub["lng"])
        if not prov:
            continue
        if prov not in cache:
            cache[prov] = report.build(conn, prov, at, rows=rows)
        rep = cache[prov]
        try:
            if rep is None:
                continue
            _invoke(senders[sub["channel"]], sub["target"], report.digest_text(rep, at, report_link(prov)),
                    flex=cards.digest_card(rep, report.thai_date(at), report_link(prov)))
        except Exception as e:
            print(f"digest failed for subscription {sub['id']}: {e}", file=sys.stderr)
            failed += 1
            continue
        conn.execute("UPDATE subscriptions SET last_digest=? WHERE id=?", (today, sub["id"]))
        conn.commit()
        sent += 1
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
    sp.add_parser("digest")
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
    elif a.cmd == "digest":
        sent, failed = run_digest(conn)
        print(f"digests sent={sent} failed={failed} (only sends 07:00-10:59 Thai time)")
        return 1 if failed else 0
    else:
        sent, failed = run(conn)
        print(f"sent={sent} failed={failed}")
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

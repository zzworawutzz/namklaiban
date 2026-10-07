"""A small log of the messages we push, so "why did people get so many messages yesterday?" has an answer.
One row per message: when, what kind, which province, whether LINE accepted it. No LINE ids, no places smaller
than a province, nothing about the person. Rows older than KEEP_DAYS are dropped on every ingest run."""
import sys
from datetime import timedelta

import core
from ingest import TZ_TH

KEEP_DAYS = 60
KINDS = ("status", "early", "fast", "report", "digest", "stale", "rain", "admin")   # admin = the owner's own alerts (watchdog)


def record(conn, at, kind, province, ok=True):
    """Never raises: logging must not break the message it describes."""
    try:
        conn.execute("INSERT INTO send_log(ts, kind, province, ok) VALUES(?,?,?,?)",
                     (core.iso(at), kind, province or "", 1 if ok else 0))
    except Exception as e:
        print(f"send_log failed: {type(e).__name__}: {e}", file=sys.stderr)


def prune(conn, at, keep_days=KEEP_DAYS):
    conn.execute("DELETE FROM send_log WHERE ts < ?", (core.iso(at - timedelta(days=keep_days)),))


def summary(conn, at, days=7):
    """{'days': [{'day', 'total', 'failed', 'by_kind': {...}}], 'top_provinces': [...]} for the last `days` Thai days."""
    since = core.iso(at - timedelta(days=days + 1))
    first = (at.astimezone(TZ_TH) - timedelta(days=days - 1)).date().isoformat()
    per, prov = {}, {}
    for r in conn.execute("SELECT ts, kind, province, ok FROM send_log WHERE ts >= ?", (since,)):
        day = core.parse(r["ts"]).astimezone(TZ_TH).date().isoformat()
        if day < first:
            continue
        d = per.setdefault(day, {"day": day, "total": 0, "failed": 0, "by_kind": {}})
        d["total"] += 1
        d["failed"] += 0 if r["ok"] else 1
        d["by_kind"][r["kind"]] = d["by_kind"].get(r["kind"], 0) + 1
        if r["province"]:
            prov[r["province"]] = prov.get(r["province"], 0) + 1
    top = sorted(prov.items(), key=lambda t: -t[1])[:10]
    return {"days": [per[k] for k in sorted(per)], "top_provinces": [{"province": p, "messages": n} for p, n in top]}

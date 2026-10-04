"""Save a copy of the data that cannot be rebuilt: who follows the bot (subscriptions) and the flood reports people
sent. Run it on YOUR computer, against YOUR database; nothing is uploaded anywhere.

    DATABASE_URL="postgres://..." python backup_export.py                 # writes backup-YYYY-MM-DD.json here
    DATABASE_URL="postgres://..." python backup_export.py --restore backup-2026-10-04.json   # put the followers back

The Neon connection string is in the Vercel project's environment variables (DATABASE_URL or POSTGRES_URL).
The file contains LINE user ids: keep it private (it is created readable by you only) and never put it in the repo.
Readings are not backed up: they refill from ThaiWater within a day, and the history older than that is not kept."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import db
import ingest

TABLES = ("subscriptions", "flood_reports")


def export(conn, now=None):
    now = now or datetime.now(timezone.utc)
    out = {"format": 1, "exported_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    for t in TABLES:
        out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()]
    return out


def restore(conn, data):
    """Add the followers from a backup that are not already there. Returns how many were added.
    Flood reports are not restored: they expire after hours and would only confuse people."""
    if data.get("format") != 1:
        raise ValueError("this is not a backup file made by backup_export.py")
    have = {(r["channel"], r["target"], round(r["lat"], 5), round(r["lng"], 5)) for r in
            conn.execute("SELECT channel, target, lat, lng FROM subscriptions").fetchall()}
    cols = [r for r in data["subscriptions"][:1] for r in r.keys() if r != "id"]
    added = 0
    for row in data["subscriptions"]:
        key = (row["channel"], row["target"], round(row["lat"], 5), round(row["lng"], 5))
        if key in have:
            continue
        conn.execute(f"INSERT INTO subscriptions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                     tuple(row[c] for c in cols))
        have.add(key)
        added += 1
    conn.commit()
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", help="SQLite path or postgres:// URL (default: $DATABASE_URL, $POSTGRES_URL, $WATER_DB, water.db)")
    ap.add_argument("--out", help="file to write (default backup-<date>.json)")
    ap.add_argument("--restore", metavar="FILE", help="add the followers in FILE back into the database")
    a = ap.parse_args(argv)
    conn = db.connect(a.db)
    ingest.init_db(conn)
    if a.restore:
        with open(a.restore, encoding="utf-8") as f:
            added = restore(conn, json.load(f))
        print(f"restored {added} follower(s) that were missing")
        return 0
    data = export(conn)
    path = a.out or f"backup-{data['exported_at'][:10]}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"saved {len(data['subscriptions'])} follower(s) and {len(data['flood_reports'])} flood report(s) to {path}")
    print("this file holds LINE user ids: keep it private")
    return 0


if __name__ == "__main__":
    sys.exit(main())

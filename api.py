"""Read-only API for "น้ำใกล้บ้านฉัน". Reads the SQLite file written by ingest.py.
Database: SQLite file locally (WATER_DB), Postgres when DATABASE_URL is set (Vercel + Neon).
The web UI lives in ./public: Vercel serves it from the CDN; locally we mount it at "/".

Run:  pip install -r requirements.txt
      uvicorn api:app --reload        (docs at http://127.0.0.1:8000/docs)
"""
import hmac
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import core
import db
import ingest
import notify

HEALTH_MAX_INGEST_AGE_MIN = 60  # ingest runs every ~20 min; this long means it is stuck

app = FastAPI(title="น้ำใกล้บ้านฉัน API", version="0.2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])


@app.middleware("http")
async def short_cache(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/stations"):
        resp.headers["Cache-Control"] = "public, max-age=60"  # data refreshes every ~20 min
    return resp


def now():
    return datetime.now(timezone.utc)


_ready = set()  # targets whose tables we already created in this process


def conn():
    target = db.default_target()
    c = db.connect(target)
    if target not in _ready:  # avoid re-running DDL on every request (a network trip on Postgres)
        ingest.init_db(c)
        _ready.add(target)
    return c


@app.get("/health")
def health():
    at = now()
    with conn() as c:
        rows = core.latest(c, at)
        run = c.execute("SELECT ts, ok, error FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
        ok_run = c.execute("SELECT MAX(ts) AS m FROM ingest_runs WHERE ok=1").fetchone()["m"]
    last = max((r["ts"] for r in rows if r["ts"]), default=None)
    ingest_age = int((at - core.parse(ok_run)).total_seconds() // 60) if ok_run else None
    healthy = ingest_age is not None and ingest_age <= HEALTH_MAX_INGEST_AGE_MIN
    return {"stations": len(rows), "stale": sum(r["stale"] for r in rows), "latest_reading": last,
            "last_ingest_age_min": ingest_age,
            "last_ingest_error": run["error"] if run and not run["ok"] else None,
            "ingest_ok": healthy}


@app.get("/stations")
def stations(province: Optional[str] = None,
             status: Optional[str] = Query(None, pattern="^(normal|watch|alert|unknown)$")):
    with conn() as c:
        return core.latest(c, now(), province, status)


@app.get("/stations/nearby")
def nearby(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
           limit: int = Query(3, ge=1, le=20), fresh_only: bool = False):
    with conn() as c:
        rows = core.latest(c, now())
    return core.nearest(rows, lat, lng, limit, fresh_only)


@app.get("/stations/{station_id}/readings")
def readings(station_id: str, days: int = Query(7, ge=1, le=30)):
    since = core.iso(now() - timedelta(days=days))
    with conn() as c:
        if not c.execute("SELECT 1 FROM stations WHERE id=?", (station_id,)).fetchone():
            raise HTTPException(404, "ไม่พบสถานีนี้")
        rs = c.execute("SELECT ts, water_level, pct_of_bank, status FROM readings "
                       "WHERE station_id=? AND ts>=? ORDER BY ts", (station_id, since))
        return [dict(r) for r in rs]


@app.get("/stations/{station_id}/related")
def related(station_id: str):
    """Other stations on the same river. Up/downstream is inferred from bed elevation."""
    with conn() as c:
        rows = core.latest(c, now())
    if not any(r["id"] == station_id for r in rows):
        raise HTTPException(404, "ไม่พบสถานีนี้")
    return core.related(rows, station_id)


@app.get("/api/cron/ingest", include_in_schema=False)
def cron_ingest(authorization: Optional[str] = Header(None)):
    """Fetch new readings, then send notifications. Called by Vercel Cron or any scheduler
    with `Authorization: Bearer $CRON_SECRET`. Refuses to run when CRON_SECRET is unset."""
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or not hmac.compare_digest((authorization or "").encode(), f"Bearer {secret}".encode()):
        raise HTTPException(401, "unauthorized")
    with conn() as c:
        if not ingest.try_lock(c, "cron"):
            return {"skipped": "another run is in progress"}
        try:
            th = ingest.load_thresholds(os.environ.get("THRESHOLDS_FILE"))
            n_st, n_rd, skipped, pruned = ingest.run_ingest(c, thresholds=th)
            sent, failed = notify.run(c, now())
        except Exception as e:
            c.rollback()
            raise HTTPException(502, f"{type(e).__name__}: {e}"[:300])
        finally:
            ingest.release_lock(c, "cron")
    return {"stations": n_st, "new_readings": n_rd, "skipped": skipped, "pruned": pruned,
            "notifications_sent": sent, "notifications_failed": failed}


_web = Path(__file__).parent / "public"
if _web.is_dir() and not os.environ.get("VERCEL"):  # on Vercel, public/ is served by the CDN
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")

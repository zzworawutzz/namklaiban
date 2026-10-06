"""Read-only API for "น้ำใกล้บ้านฉัน". Reads the SQLite file written by ingest.py.
Database: SQLite file locally (WATER_DB), Postgres when DATABASE_URL is set (Vercel + Neon).
The web UI lives in ./public: Vercel serves it from the CDN; locally we mount it at "/".

Run:  pip install -r requirements.txt
      uvicorn api:app --reload        (docs at http://127.0.0.1:8000/docs)
"""
import hmac
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import areas
import boundaries
import budget
import core
import dams
import ews
import db
import floodreports
import gauges
import ingest
import line_webhook
import notify
import ratelimit
import report
import security
import sendlog
import shelters
import suggest
import watchdog

HEALTH_MAX_INGEST_AGE_MIN = 60  # ingest runs every ~20 min; this long means it is stuck

# The interactive docs list every route, including the owner-only ones: off on the live site, on with ENABLE_DOCS=1 for local work.
_DOCS = os.environ.get("ENABLE_DOCS") == "1"
app = FastAPI(title="น้ำใกล้บ้านฉัน API", version="0.2",
              docs_url="/docs" if _DOCS else None, redoc_url="/redoc" if _DOCS else None, openapi_url="/openapi.json" if _DOCS else None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])   # the data is public to read; browsers refuse cross-site POSTs (no preflight allowance)


@app.middleware("http")
async def own_site_writes_only(request: Request, call_next):
    """A page on another site must not make a visitor's browser file or flag flood reports (it would be counted against the
    visitor's address). A write that carries an Origin header must come from our own host; scripts and tools send none."""
    if request.method == "POST" and request.url.path.startswith("/api/flood-reports"):
        origin = request.headers.get("origin")
        if origin and urllib.parse.urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "cross-site requests are not allowed"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def short_cache(request: Request, call_next):
    if ratelimit.applies(request.url.path):
        ok, retry = ratelimit.limiter.check(request_ip(request), ratelimit.per_minute())
        if not ok:
            resp = JSONResponse({"detail": "เรียกถี่เกินไป โปรดรอสักครู่แล้วลองใหม่"}, status_code=429,
                                headers={"Retry-After": str(retry)})
            for k, v in security.HEADERS.items():
                resp.headers.setdefault(k, v)
            return resp
    resp = await call_next(request)
    for k, v in security.HEADERS.items():
        resp.headers.setdefault(k, v)
    if request.url.path.startswith(("/stations", "/reports")):
        # data refreshes every ~20 min; stale-while-revalidate lets the CDN and browsers answer at once and refresh in the background
        resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return resp


def trust_proxy():
    """x-forwarded-for is a header the client can write. Vercel replaces it with the real address, so it is believed there
    (VERCEL is set by the platform) or when TRUST_PROXY=1 says a reverse proxy we run does the same. Anywhere else
    (e.g. plain Docker) a visitor could invent an address per request and dodge the rate limit and the report spam guard."""
    return os.environ.get("TRUST_PROXY") == "1" or bool(os.environ.get("VERCEL"))


def request_ip(request):
    """The visitor's address: the first x-forwarded-for entry behind a trusted proxy, otherwise the connection's own address."""
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip() if trust_proxy() else ""
    return fwd or (request.client.host if request.client else "?")


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


def health_data():
    at = now()
    with conn() as c:
        rows = core.latest(c, at)
        run = c.execute("SELECT ts, ok, error FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
        ok_run = c.execute("SELECT MAX(ts) AS m FROM ingest_runs WHERE ok=1").fetchone()["m"]
    last = max((r["ts"] for r in rows if r["ts"]), default=None)
    ingest_age = int((at - core.parse(ok_run)).total_seconds() // 60) if ok_run else None
    reading_age = int((at - core.parse(last)).total_seconds() // 60) if last else None
    healthy = ingest_age is not None and ingest_age <= HEALTH_MAX_INGEST_AGE_MIN
    quiet = sorted(n for n, a in core.agency_shares(rows, MONITOR_MAX_READING_AGE_MIN).items()
                   if a["stations"] >= MONITOR_AGENCY_MIN_STATIONS and a["recent"] / a["stations"] < MONITOR_AGENCY_MIN_RECENT)
    return {"stations": len(rows), "stale": sum(r["stale"] for r in rows), "latest_reading": last,
            "latest_reading_age_min": reading_age, "quiet_agencies": quiet,   # same test as /health/monitor; the page turns it into a notice
            "last_ingest_age_min": ingest_age,
            "last_ingest_error": run["error"] if run and not run["ok"] else None,
            "ingest_ok": healthy}


@app.get("/health")
def health():
    return health_data()


MONITOR_MAX_READING_AGE_MIN = 180   # no new reading at all for this long: the source (or our reading of it) has stopped
MONITOR_AGENCY_MIN_STATIONS = 20    # an agency this big...
MONITOR_AGENCY_MIN_RECENT = 0.5     # ...with fewer than this share of its stations reporting within MONITOR_MAX_READING_AGE_MIN has gone quiet


@app.get("/health/monitor")
def health_monitor():
    """For an outside uptime monitor (cron-job.org, UptimeRobot...) that can only look at the HTTP status: 200 when data is
    flowing, 503 with the reason when ingest has failed for over an hour, no station exists, or no reading at all is newer than
    3 hours. /health itself always answers 200 (the web page reads it for its "data may be late" bar), so it cannot be used for this.
    Also 503 when one whole agency (HII, RID, FOP, EGAT...) has gone quiet while the others still report: the newest-reading
    test alone would pass that (6 Oct 2026: HII and FOP, 421 stations, stopped at the source while RID and EGAT kept going).
    A few stations being old is NOT a failure: every agency has some dead ones."""
    h = health_data()
    with conn() as c:
        agencies = core.agency_shares(core.latest(c, now()), MONITOR_MAX_READING_AGE_MIN)
    problems = []
    if not h["stations"]:
        problems.append("no stations")
    if not h["ingest_ok"]:
        problems.append("ingest has not succeeded recently" + (f" ({h['last_ingest_error']})" if h["last_ingest_error"] else ""))
    if h["latest_reading_age_min"] is not None and h["latest_reading_age_min"] > MONITOR_MAX_READING_AGE_MIN:
        problems.append(f"newest reading is {h['latest_reading_age_min']} min old")
    for name, a in sorted(agencies.items()):
        if a["stations"] >= MONITOR_AGENCY_MIN_STATIONS and a["recent"] / a["stations"] < MONITOR_AGENCY_MIN_RECENT:
            problems.append(f"agency {name} has gone quiet: {a['recent']} of {a['stations']} stations reported in the last "
                            f"{MONITOR_MAX_READING_AGE_MIN // 60} h")
    body = {"ok": not problems, "problems": problems, "agencies": agencies, **h}
    return JSONResponse(body, status_code=503 if problems else 200, headers={"Cache-Control": "no-store"})


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


@app.get("/reports/{province}")
def province_report(province: str, days: int = Query(14, ge=2, le=30)):
    """Situation report for one province (exact name as in /stations), with a daily series."""
    with conn() as c:
        rep = report.build(c, province, now(), days)
    if not rep:
        raise HTTPException(404, "ไม่พบจังหวัดนี้")
    return rep


class FloodReport(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    level: int = Field(..., ge=1, le=4)
    note: Optional[str] = Field(None, max_length=300)


def client_id(request: Request):
    return floodreports.who(request_ip(request))


@app.get("/api/flood-reports")
def flood_reports_list(response: Response):
    """Spots people reported as flooded in the last 12 hours (unverified)."""
    response.headers["Cache-Control"] = "public, max-age=30"
    with conn() as c:
        return floodreports.active(c, now())


@app.post("/api/flood-reports", status_code=201)
def flood_reports_add(body: FloodReport, request: Request):
    try:
        with conn() as c:
            floodreports.add(c, body.lat, body.lng, body.level, body.note, "web", client_id(request), now())
    except floodreports.Rejected as e:
        raise HTTPException(e.code, e.message)
    return {"ok": True}


@app.post("/api/flood-reports/{report_id}/flag")
def flood_reports_flag(report_id: int, request: Request):
    """"This is not true / already dry": three different people hide a report."""
    with conn() as c:
        if not floodreports.flag(c, report_id, client_id(request)):
            raise HTTPException(404, "ไม่พบรายงานนี้")
    return {"ok": True}


@app.get("/api/rain")
def rain_gauges(response: Response, province: Optional[str] = None,
                lat: Optional[float] = Query(None, ge=-90, le=90), lng: Optional[float] = Query(None, ge=-180, le=180),
                limit: int = Query(3, ge=1, le=10)):
    """Rain measured by gauges over the last 24 h (ThaiWater): a province summary, or the gauges nearest to lat/lng."""
    if province is None and (lat is None or lng is None):
        raise HTTPException(422, "give province, or lat and lng")
    rows = gauges.get()
    if rows is None:
        raise HTTPException(503, "สถานีวัดฝนไม่พร้อมให้บริการตอนนี้")
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600, stale-while-revalidate=600"
    rows3 = gauges.get3()                      # the completed-days totals; the page works without them
    if province is not None:
        out = gauges.for_province(rows, province) or {"stations": 0}
        g3 = gauges.province_3d(rows3, province) if rows3 else None
        if g3:
            out["three_day"] = g3
        return out
    near = gauges.nearest(rows, lat, lng, limit)
    period = gauges.with_3d(near, rows3)
    return {"nearby": near, **({"three_day_period": period} if period else {})}


@app.get("/api/dams")
def dams_for_province(response: Response, province: str = Query(..., min_length=1, max_length=60), limit: int = Query(6, ge=1, le=20)):
    """Large dams (ThaiWater) in the basins of a province's stations and above them: how full and how much they release."""
    rows = dams.get()
    if rows is None:
        raise HTTPException(503, "ข้อมูลเขื่อนไม่พร้อมให้บริการตอนนี้")
    with conn() as c:
        g = dams.for_province(c, province, rows)
    response.headers["Cache-Control"] = "public, max-age=900, s-maxage=1800, stale-while-revalidate=1800"
    if not g:
        return {"dams": [], "total": 0}
    return {"as_of": g["as_of"], "dams": dams.add_trends(g["dams"][:limit]), "total": len(g["dams"])}


@app.get("/api/shelters")
def shelters_list(response: Response, province: Optional[str] = None, district: Optional[str] = None,
                  tambon: Optional[str] = None,
                  lat: Optional[float] = Query(None, ge=-90, le=90), lng: Optional[float] = Query(None, ge=-180, le=180),
                  limit: int = Query(5, ge=1, le=20)):
    """Temporary flood shelters (DDPM open data): all in a province, or the nearest to lat/lng."""
    response.headers["Cache-Control"] = "public, max-age=86400"   # static yearly dataset
    if lat is not None and lng is not None:
        return shelters.nearest(lat, lng, limit)
    if province:
        if tambon and not district:
            raise HTTPException(422, "tambon needs district")
        return shelters.by_area(province, district, tambon)
    raise HTTPException(422, "give province, or lat and lng")


@app.get("/api/areas")
def areas_list(response: Response, province: str):
    """Districts and subdistricts of a province with their centre points, for the area picker."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    data = areas.province(province)
    if data is None:
        raise HTTPException(404, "ไม่มีรายชื่อตำบลของจังหวัดนี้")
    return {"province": province, "districts": data}


@app.get("/api/boundary")
def boundary(response: Response, province: str, district: Optional[str] = None, tambon: Optional[str] = None):
    """Outline (GeoJSON Feature) of a province, a district of it, or a subdistrict of that district."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    if tambon and not district:
        raise HTTPException(422, "tambon needs district")
    f = boundaries.outline(province, district, tambon)
    if f is None:
        raise HTTPException(404, "ไม่มีขอบเขตของพื้นที่นี้")
    return f


@app.get("/api/line/add-friend", include_in_schema=False)
def line_add_friend(response: Response):
    """Link for the website's "add the LINE bot" button; 404 when LINE is not configured."""
    basic = notify.bot_basic_id()
    if not basic:
        raise HTTPException(404, "LINE bot is not configured")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return {"url": "https://line.me/R/ti/p/" + urllib.parse.quote(basic, safe="")}


GISTDA_TILES = "https://api-gateway.gistda.or.th/api/2.0/resources/maps/flood/{period}/tms/{z}/{x}/{y}"
GISTDA_PERIODS = ("1day", "3days", "7days", "30days")
GISTDA_ZOOMS = (5, 15)                              # the map layer never asks for more (maxNativeZoom 15) or less than this over Thailand
GISTDA_BOX = (5.4, 97.2, 20.7, 105.8)               # south, west, north, east: Thailand with a margin


def tile_in_thailand(z, x, y):
    """True when XYZ tile z/x/y overlaps GISTDA_BOX. The proxy spends our GISTDA quota, so it only answers for our own country."""
    if not GISTDA_ZOOMS[0] <= z <= GISTDA_ZOOMS[1]:
        return False
    n = 2 ** z
    s, w, nn, e = GISTDA_BOX
    def col(lng): return int((lng + 180.0) / 360.0 * n)
    def row(lat):
        r = math.radians(lat)
        return int((1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n)
    return col(w) <= x <= col(e) and row(nn) <= y <= row(s)


def gistda_key():
    return os.environ.get("GISTDA_API_KEY", "").strip()


@app.get("/api/gistda/status", include_in_schema=False)
def gistda_status(response: Response):
    """Lets the map know whether to offer the satellite flood layer (needs GISTDA_API_KEY)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return {"enabled": bool(gistda_key()), "periods": list(GISTDA_PERIODS)}


@app.get("/api/gistda/flood/{period}/{z}/{x}/{y}", include_in_schema=False)
def gistda_flood_tile(period: str, z: int, x: int, y: int):
    """Proxy for GISTDA satellite flood-extent tiles, so the API key never reaches the browser."""
    key = gistda_key()
    if not key or period not in GISTDA_PERIODS or not tile_in_thailand(z, x, y):
        raise HTTPException(404, "not available")
    url = GISTDA_TILES.format(period=period, z=z, x=x, y=y) + "?" + urllib.parse.urlencode({"api_key": key})
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            body, ctype = r.read(), r.headers.get("Content-Type", "image/png")
    except urllib.error.HTTPError as e:
        # no flood in this tile is normal; anything else (bad key, quota) must not be cached for long
        raise HTTPException(404 if e.code == 404 else 502, "tile unavailable")
    except Exception:
        raise HTTPException(502, "tile unavailable")
    if not ctype.startswith("image/"):
        raise HTTPException(502, "tile unavailable")
    return Response(body, media_type=ctype, headers={"Cache-Control": "public, max-age=1800, s-maxage=1800"})


@app.get("/api/ews/warnings", include_in_schema=False)
def ews_warnings(response: Response):
    """Stations the DWR early-warning system currently flags (status 1-3), for the map layer."""
    # Off unless EWS_ENABLED=1: the web page does not use it, DWR does not answer from outside Thailand (a call waits ~15 s
    # and then fails), and anyone could keep serverless functions busy by calling it in a loop.
    if os.environ.get("EWS_ENABLED") != "1":
        raise HTTPException(404, "not available")
    try:
        data = ews.current()
    except Exception as e:
        print(f"DWR early-warning fetch failed: {type(e).__name__}: {e}"[:300], file=sys.stderr)   # for the owner's logs, not for visitors
        raise HTTPException(502, "DWR early-warning data unavailable")
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600, stale-while-revalidate=3600"
    return data


@app.get("/api/suggest", include_in_schema=False)
def suggest_places(response: Response, q: str = Query("", max_length=80)):
    """Type-ahead for the search box: subdistricts, districts, provinces, water stations and shelters."""
    if len(suggest.norm(q)) < suggest.MIN_Q:
        return []
    with conn() as c:
        rows = c.execute("SELECT id, name, province, lat, lng FROM stations").fetchall()
    response.headers["Cache-Control"] = "public, max-age=3600"
    return suggest.search(q, [dict(r) for r in rows])


def _cron_authorized(authorization):
    secrets = [os.environ.get(k, "") for k in ("CRON_SECRET", "CRON_SECRET_EXTERNAL")]
    given = (authorization or "").encode()
    return any(s and hmac.compare_digest(given, f"Bearer {s}".encode()) for s in secrets)


@app.get("/api/cron/ingest", include_in_schema=False)
def cron_ingest(authorization: Optional[str] = Header(None)):
    """Fetch new readings, then send notifications. Called by Vercel Cron or any scheduler
    with `Authorization: Bearer $CRON_SECRET`. Refuses to run when CRON_SECRET is unset."""
    # CRON_SECRET is for Vercel Cron / GitHub Actions; CRON_SECRET_EXTERNAL lets a third-party
    # scheduler call this endpoint with its own key that can be revoked on its own.
    if not _cron_authorized(authorization):
        raise HTTPException(401, "unauthorized")
    with conn() as c:
        if not ingest.try_lock(c, "cron"):
            return {"skipped": "another run is in progress"}
        try:
            th = ingest.load_thresholds(os.environ.get("THRESHOLDS_FILE"))
            n_st, n_rd, skipped, pruned = ingest.run_ingest(c, thresholds=th)
            sent, failed = notify.run(c, now())
            digests, digest_failed = notify.run_digest(c, now())
        except Exception as e:
            c.rollback()
            watchdog.check(c, now())
            raise HTTPException(502, f"{type(e).__name__}: {e}"[:300])
        finally:
            ingest.release_lock(c, "cron")
        watchdog.check(c, now())
        watchdog.check_storage(c, now())
        watchdog.check_line_quota(c, now())
        watchdog.check_silent(c, now())
        watchdog.check_backup_reminder(c, now())
        watchdog.check_shelter_reminder(c, now())
    return {"stations": n_st, "new_readings": n_rd, "skipped": skipped, "pruned": pruned,
            "notifications_sent": sent, "notifications_failed": failed,
            "digests_sent": digests, "digests_failed": digest_failed}


@app.get("/api/cron/stats", include_in_schema=False)
def cron_stats(days: int = Query(7, ge=1, le=60), authorization: Optional[str] = Header(None)):
    """Owner's view: messages pushed per day / kind / province (send_log), subscribers, database size and LINE quota.
    Same auth as /api/cron/ingest; read-only."""
    if not _cron_authorized(authorization):
        raise HTTPException(401, "unauthorized")
    at = now()
    with conn() as c:
        out = sendlog.summary(c, at, days)
        out["subscriptions"] = c.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"]
        out["db_mb"] = round(watchdog.db_size_mb(c), 1)
        out["db_limit_mb"] = watchdog.db_limit_mb()
        out["silent_groups"] = watchdog.silent_groups(c, at)
        out["bank_data"] = core.bank_data_quality(core.latest(c, at))
        out["watch_provinces"] = watchdog.watched_provinces()
        row = c.execute("SELECT ts FROM alert_state WHERE name=?", (watchdog.BACKUP_KEY,)).fetchone()
        if row and os.environ.get("BACKUP_REMIND", "1") != "0":
            due = core.parse(row["ts"]) + timedelta(days=watchdog.BACKUP_EVERY_DAYS)
            out["backup"] = {"since": row["ts"], "days_left": max(math.ceil((due - at).total_seconds() / 86400), 0)}
        else:
            out["backup"] = None
    try:
        limit, used = notify.line_quota()
        out["line_quota"] = {"limit": limit, "used": used}
        out["budget"] = None if limit is None else {"reserve": budget.reserve(limit), "holding": limit - used <= budget.reserve(limit)}
    except Exception as e:
        out["line_quota"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        out["budget"] = None
    return out


@app.get("/api/cron/test-alert", include_in_schema=False)
def cron_test_alert(authorization: Optional[str] = Header(None)):
    """Send one test message to ADMIN_LINE_ID so the owner can confirm alerts arrive.
    Same auth as /api/cron/ingest; changes no state."""
    if not _cron_authorized(authorization):
        raise HTTPException(401, "unauthorized")
    target = os.environ.get("ADMIN_LINE_ID", "").strip()
    if not target:
        raise HTTPException(400, "ADMIN_LINE_ID is not set (redeploy after adding it)")
    try:
        notify.send_line(target, "🔔 น้ำใกล้บ้านฉัน: ข้อความทดสอบ ถ้าเห็นข้อความนี้ แปลว่าระบบแจ้งเตือนผู้ดูแลทำงานปกติ")
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}"[:300])
    return {"sent": True}


@app.post("/api/line/webhook", include_in_schema=False)
async def line_webhook_endpoint(request: Request, x_line_signature: Optional[str] = Header(None)):
    """LINE Messaging API webhook. Set LINE_CHANNEL_SECRET (verifies the signature) and
    LINE_CHANNEL_TOKEN (sends replies)."""
    secret = os.environ.get("LINE_CHANNEL_SECRET")
    if not secret:
        raise HTTPException(503, "LINE webhook is not configured")
    body = await request.body()
    if not line_webhook.valid_signature(secret, body, x_line_signature):
        raise HTTPException(401, "bad signature")
    try:
        events = json.loads(body or b"{}").get("events", [])
    except ValueError:
        raise HTTPException(400, "invalid json")

    def reply(token, text, flex=None, quick=None):
        try:
            notify.reply_line(token, text, flex=flex, quick=quick)
        except Exception as e:  # a failed reply must not make LINE retry the whole webhook
            print(f"LINE reply failed: {e}")

    with conn() as c:
        for ev in events:
            try:
                line_webhook.handle_event(c, ev, now(), reply)
            except Exception as e:
                c.rollback()
                print(f"LINE event failed: {type(e).__name__}: {e}")
    return {"ok": True}


_web = Path(__file__).parent / "public"
if _web.is_dir() and not os.environ.get("VERCEL"):  # on Vercel, public/ is served by the CDN
    def _place_page():
        return FileResponse(_web / "index.html")

    # same pretty paths as the rewrite in vercel.json (/rangsit, /ayutthaya ...); one route per slug so static files and API paths are untouched
    for _slug in json.loads((_web / "places.json").read_text(encoding="utf-8")):
        app.add_api_route("/" + _slug, _place_page, methods=["GET"], include_in_schema=False)
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")

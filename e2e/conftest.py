"""Browser tests for the web page. They start the real API on a throw-away SQLite database, open the real page in
Chromium and click through it. Not part of the normal `pytest` run (it needs Playwright and a browser); run them with

    pip install playwright pytest && playwright install chromium
    E2E=1 python -m pytest e2e -q                  # add E2E_CHROME=1 to use the Chrome already installed on this machine

CI does exactly this in .github/workflows/e2e.yml. The map libraries come from cdnjs, so the machine needs internet;
map tiles, rain radar and the rain forecast are replaced by local stand-ins."""
import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
import db  # noqa: E402
import ingest as ig  # noqa: E402

# id, name, province, lat, lng, bank (m), ground (m), lag (h)
STATIONS = [("A", "ท่าช้าง", "ปทุมธานี", 14.03, 100.73, 3.0, 0.5, 0),
            ("B", "บางบาล", "ปทุมธานี", 14.10, 100.60, 3.0, 1.0, 8),
            ("C", "คลองหลวง", "ปทุมธานี", 14.20, 100.50, 3.0, 2.0, 16),
            ("D", "เมืองนนท์", "นนทบุรี", 13.86, 100.51, 3.0, 0.2, 0)]


def build_database(path):
    """Four stations on one river, a week of hourly readings. A is at alert level and has just risen 45 cm."""
    c = db.connect(str(path))
    ig.init_db(c)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for sid, name, prov, lat, lng, bank, ground, lag in STATIONS:
        c.execute("INSERT INTO stations(id,name,source,province,lat,lng,bank_level,ground_level,river) VALUES(?,?,?,?,?,?,?,?,?)",
                  (sid, name, "RID", prov, lat, lng, bank, ground, "แม่น้ำทดสอบ"))
    for h in range(7 * 24, -1, -1):
        ts = core.iso(now - timedelta(hours=h, minutes=10))
        for sid, name, prov, lat, lng, bank, ground, lag in STATIONS:
            level = 1.2 + 1.0 * math.sin((7 * 24 - h - lag) / 30.0)
            if sid == "A":
                level = 2.4 + (3 - h) * 0.2 if h <= 3 else 1.2 + 1.0 * math.sin((7 * 24 - h) / 30.0) * 0.5
            pct = level / bank * 100
            c.execute("INSERT INTO readings VALUES(?,?,?,?,?)", (sid, ts, level, pct, ig.status_of(pct)))
    c.execute("INSERT INTO ingest_runs(ts,ok,stations,readings,skipped) VALUES(?,1,4,4,0)", (core.iso(now),))
    c.commit()
    c.close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    dbfile = tmp_path_factory.mktemp("e2e") / "water.db"
    build_database(dbfile)
    port = _free_port()
    env = {**os.environ, "WATER_DB": str(dbfile), "RATE_LIMIT_PER_MIN": "0", "PYTHONPATH": str(ROOT)}
    for k in ("DATABASE_URL", "POSTGRES_URL", "LINE_CHANNEL_TOKEN", "LINE_CHANNEL_SECRET"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app", "--port", str(port), "--log-level", "warning"],
                            cwd=str(ROOT), env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("the test server did not start")
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(channel="chrome") if os.environ.get("E2E_CHROME") else p.chromium.launch()
        yield b
        b.close()


def fake_forecast():
    """What Open-Meteo would answer: 24 h of past rain, 3 days ahead, a little rain this evening."""
    th = datetime.now(timezone(timedelta(hours=7))).replace(minute=0, second=0, microsecond=0)
    times = [(th + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(-24, 72)]
    mm = [1.0 if i < 0 and i % 6 == 0 else (2.0 if 3 <= i <= 8 else 0.0) for i in range(-24, 72)]
    days = [(th + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
    return {"hourly": {"time": times, "precipitation": mm, "precipitation_probability": [40] * len(times)},
            "daily": {"time": days, "precipitation_sum": [6.0, 4.0, 2.0], "precipitation_probability_max": [60, 50, 40]}}


def stub_outside_world(context):
    """Tiles, radar and the rain forecast are not what we test; answer them locally."""
    context.route("**/tile.openstreetmap.org/**", lambda r: r.abort())
    context.route("**/api.rainviewer.com/**", lambda r: r.fulfill(status=200, content_type="application/json", body='{"host":"https://tilecache.invalid","radar":{"past":[{"path":"/v2/radar/1","time":1}]}}'))
    context.route("**/tilecache.invalid/**", lambda r: r.abort())
    context.route("**/api.open-meteo.com/**", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(fake_forecast())))
    context.route("**/api/rain?*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"nearby": [{"name": "ปตร.ทดสอบ", "distance_km": 2.4, "mm24": 14.0, "mm1": 1.0}, {"name": "สะพานทดสอบ", "distance_km": 6.1, "mm24": 9.0, "mm1": 0.0}]}
        if "lat=" in r.request.url else
        {"stations": 11, "mean_mm": 9.1, "over_35": 1, "max": {"name": "รร.วัดทดสอบ", "mm24": 62.0, "mm1": 12.0}, "top": [], "as_of": "2026-10-04T14:00:00Z"})))
    context.route("**/api.open-meteo.com/v1/elevation*", lambda r: r.fulfill(status=200, content_type="application/json", body='{"elevation":[2.6]}'))   # registered after the forecast stub, so it wins
    context.route("**/api/line/add-friend", lambda r: r.fulfill(status=200, content_type="application/json", body='{"url":"https://line.me/R/ti/p/%40test"}'))   # production has the LINE button; the test server has no bot
    context.route("**/nominatim.openstreetmap.org/**", lambda r: r.fulfill(status=200, content_type="application/json", body="[]"))


class Errors(list):
    """Uncaught script errors and Content-Security-Policy violations seen by a page."""


@pytest.fixture()
def context(browser, site):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="th-TH", service_workers="block")
    stub_outside_world(ctx)
    yield ctx
    ctx.close()


def watch(page):
    errors = Errors()
    page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" and "Content Security Policy" in m.text else None)
    return errors


@pytest.fixture()
def page(context):
    p = context.new_page()
    p.errors = watch(p)
    yield p
    assert not p.errors, p.errors            # every test also proves the page threw nothing


def until(page, expression, timeout=15.0):
    """Wait until a JavaScript expression is truthy. (page.wait_for_function evaluates a string with eval(), which
    the page's own Content-Security-Policy forbids, so poll with page.evaluate instead.)"""
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for: {expression}")


def open_page(page, site, path="/pathumthani"):
    page.goto(site + path, wait_until="domcontentloaded")
    page.wait_for_selector("#sumBody .verdict", timeout=30000)
    page.wait_for_selector(".leaflet-marker-icon", timeout=30000)
    return page

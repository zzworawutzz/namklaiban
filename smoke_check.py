"""Open the live site in a real browser and check that it works. Run by .github/workflows/smoke.yml after
every upload and a few times a day; exits 1 (so GitHub e-mails the owner) when something is broken.

    python smoke_check.py [BASE_URL]            # default: production
    SMOKE_CHROME=1 python smoke_check.py http://localhost:8000   # use the Chrome installed on this machine

Needs `pip install playwright` (and `playwright install chromium` on CI). Not part of the app or the tests."""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://namklaiban-murex.vercel.app").rstrip("/")
MAX_READING_AGE_MIN = 240   # the source updates about every 20 min; 4 h without a new reading is a real problem
SECURITY_HEADERS = ("content-security-policy", "x-content-type-options", "x-frame-options")


def get_json(path):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "nkb-smoke/1"}), timeout=30) as r:
        return json.loads(r.read()), r.headers


def api_checks():
    problems = []
    h, headers = get_json("/health")
    if not h.get("ingest_ok"):
        problems.append(f"/health: ingest not ok ({h.get('last_ingest_error')})")
    if not h.get("stations"):
        problems.append("/health: no stations")
    age = h.get("latest_reading_age_min")
    if age is None or age > MAX_READING_AGE_MIN:
        problems.append(f"/health: newest reading is {age} min old")
    for name in SECURITY_HEADERS:
        if name not in headers:
            problems.append(f"missing response header {name}")
    stations, _ = get_json("/stations")
    if len(stations) < 100:
        problems.append(f"/stations returned only {len(stations)} stations")
    sug, _ = get_json("/api/suggest?v=2&q=" + urllib.parse.quote("รังสิต"))
    if not sug:
        problems.append("/api/suggest returned nothing for 'รังสิต'")
    return problems


def page_checks():
    from playwright.sync_api import sync_playwright
    problems = []
    with sync_playwright() as p:
        launch = {"channel": "chrome"} if os.environ.get("SMOKE_CHROME") else {}
        browser = p.chromium.launch(**launch)
        for path, title_part in (("/", "น้ำใกล้บ้านฉัน"), ("/rangsit", "รังสิต")):
            ctx = browser.new_context(viewport={"width": 390, "height": 800}, locale="th-TH")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
            page.on("console", lambda m: errors.append(f"console: {m.text}")
                    if m.type == "error" and "Content Security Policy" in m.text else None)
            page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}")
                    if r.status >= 400 and r.url.startswith(BASE) else None)
            page.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector(".leaflet-tile-loaded", timeout=30000)
                page.wait_for_selector("#sumBody .verdict", timeout=30000)
                page.wait_for_selector(".leaflet-marker-icon", timeout=30000)
            except Exception as e:
                problems.append(f"{path}: map/summary did not appear ({type(e).__name__})")
            try:   # the area pages set their title once places.json has loaded
                page.wait_for_function("t => document.title.includes(t)", arg=title_part, timeout=15000)
            except Exception:
                pass
            if title_part not in page.title():
                problems.append(f"{path}: unexpected title {page.title()!r}")
            problems += [f"{path}: {e}" for e in errors]
            ctx.close()
        browser.close()
    return problems


if __name__ == "__main__":
    found = []
    for check in (api_checks, page_checks):
        try:
            found += check()
        except Exception as e:   # site down, DNS, timeout: that is exactly what this check is for
            found.append(f"{check.__name__} crashed: {type(e).__name__}: {e}"[:300])
    for line in found:
        print("FAIL", line)
    print(f"smoke check of {BASE}: " + ("OK" if not found else f"{len(found)} problem(s)"))
    sys.exit(1 if found else 0)

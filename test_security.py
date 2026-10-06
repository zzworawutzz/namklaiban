import json
from pathlib import Path

from fastapi.testclient import TestClient

import api
import security


def test_api_sends_security_headers():
    r = TestClient(api.app).get("/health")
    for k, v in security.HEADERS.items():
        assert r.headers[k] == v


def test_vercel_json_matches_security_py():
    cfg = json.loads((Path(__file__).parent / "vercel.json").read_text())
    rule = next(h for h in cfg["headers"] if h["source"] == "/(.*)")
    assert {h["key"]: h["value"] for h in rule["headers"]} == security.HEADERS


def test_csp_allows_every_host_the_page_calls():
    page = "".join((Path(__file__).parent / "public" / f).read_text(encoding="utf-8") for f in ("index.html", "app.js"))
    for host in ("https://api.open-meteo.com", "https://api.rainviewer.com",
                 "https://nominatim.openstreetmap.org", "https://router.project-osrm.org"):
        assert host in page and host in security.CSP


def _inline_script_hashes():
    import base64, glob, hashlib, re
    from pathlib import Path
    out = set()
    for f in sorted(glob.glob(str(Path(__file__).parent / "public" / "*.html"))):
        for m in re.finditer(r"<script>(.*?)</script>", Path(f).read_text(encoding="utf-8"), re.S):
            out.add("'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode() + "'")
    return sorted(out)


def test_script_src_has_no_unsafe_inline_and_allows_exactly_the_inline_scripts_of_the_pages():
    script_src = [p for p in security.CSP.split("; ") if p.startswith("script-src")][0]
    assert "unsafe-inline" not in script_src and "unsafe-eval" not in script_src
    assert sorted(security.INLINE_SCRIPT_HASHES) == _inline_script_hashes(), (
        "an inline <script> changed: INLINE_SCRIPT_HASHES in security.py and script-src in vercel.json must be\n" + "\n".join(_inline_script_hashes()))


def test_pages_have_no_inline_event_handlers_or_javascript_urls():
    import glob, re
    from pathlib import Path
    for f in glob.glob(str(Path(__file__).parent / "public" / "*.html")):
        text = Path(f).read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text), f + " has an inline event handler (blocked by the CSP)"
        assert "javascript:" not in text, f

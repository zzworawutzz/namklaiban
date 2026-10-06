"""Security response headers, sent by the API (self-hosting) and mirrored in vercel.json (static
files on Vercel never pass through Python). test_security.py keeps the two copies identical.

Scripts: only our own files, cdnjs, and the few inline <script> blocks of the pages, each allowed by its SHA-256
hash (INLINE_SCRIPT_HASHES), so an injected <script> or onclick= attribute does not run. Styles still allow
'unsafe-inline' (the pages use style="" attributes). test_security.py recomputes the hashes from public/*.html:
change an inline script and that test prints the new line to paste here and into vercel.json."""

INLINE_SCRIPT_HASHES = (
    "'sha256-7wchnvNoATHBzoDEhK+1py6z2eoWOfr5hpTtWeVyz9o='",
    "'sha256-9Rovun6UTaZcNv14HvfHv15E7cVotAmR5cjGaDLEmvA='",
    "'sha256-dphJhtjz0/ESdYbu32Ey95qRoNT19W1N9u0okF8Us7M='",
    "'sha256-oU3o0yiN53B17VqWDNnt2mA9d7m3h6+uG4nIov5DJqY='",
)

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' " + " ".join(INLINE_SCRIPT_HASHES) + " https://cdnjs.cloudflare.com",
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:",
    "img-src 'self' data: blob: https:",
    "connect-src 'self' https://api.open-meteo.com https://api.rainviewer.com "
    "https://nominatim.openstreetmap.org https://router.project-osrm.org",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=(), payment=()",
}

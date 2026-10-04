"""Security response headers, sent by the API (self-hosting) and mirrored in vercel.json (static
files on Vercel never pass through Python). test_security.py keeps the two copies identical.

The page uses inline <script>/<style> and onclick= handlers, so the CSP has to allow
'unsafe-inline'; it still blocks other origins from running script, framing us, or receiving
data from fetch()."""

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
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

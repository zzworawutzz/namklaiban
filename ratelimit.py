"""A per-visitor request limit for the API, so one script (or one runaway browser tab) cannot burn the hosting quota.

Fixed one-minute windows per (visitor, route group), kept in memory. On Vercel every serverless instance has its own
memory, so this is best-effort: it stops a single client hammering us, but it is not a hard global cap. The CDN cache
on /stations, /api/suggest and the other cacheable routes absorbs most of the rest. Set RATE_LIMIT_PER_MIN=0 to turn it off."""
import os
import time

WINDOW_S = 60
DEFAULT_PER_MIN = 240       # a page load makes about 10 calls, so this also tolerates a few people behind one Wi-Fi
MAX_KEYS = 20000            # forget old visitors beyond this many, so memory cannot grow without bound
LIMITED_PREFIXES = ("/api/", "/stations", "/reports")
EXEMPT_PREFIXES = ("/api/cron/", "/api/line/webhook")   # our own schedulers and LINE's servers, both authenticated


def per_minute():
    try:
        return max(0, int(os.environ.get("RATE_LIMIT_PER_MIN", DEFAULT_PER_MIN)))
    except ValueError:
        return DEFAULT_PER_MIN


def applies(path):
    return path.startswith(LIMITED_PREFIXES) and not path.startswith(EXEMPT_PREFIXES)


class Limiter:
    def __init__(self, clock=time.monotonic):
        self.clock, self.hits = clock, {}

    def reset(self):
        self.hits.clear()

    def check(self, key, limit):
        """(allowed, retry_after_seconds). Counts the call."""
        if limit <= 0:
            return True, 0
        now = self.clock()
        start, n = self.hits.get(key, (now, 0))
        if now - start >= WINDOW_S:
            start, n = now, 0
        n += 1
        self.hits[key] = (start, n)
        if len(self.hits) > MAX_KEYS:
            self.hits = {k: v for k, v in self.hits.items() if now - v[0] < WINDOW_S}
        return (n <= limit), max(1, int(WINDOW_S - (now - start)))


limiter = Limiter()

"""Keep some of this month's LINE message quota for the messages that matter.

When the quota is nearly used up, only alert-level news (and nothing else) is pushed to people; summaries, early
warnings, "water rose fast" and neighbour reports wait. Held status changes are sent later (the station's last
status is left unchanged, like quiet hours) and a held summary is retried in the next slot. Replies to people who
write to the bot never count against the quota and are not affected.

LINE is asked for the quota at most every CHECK_MIN minutes; the answer is kept in the alert_state table. If LINE
cannot be asked, or the plan has no limit, nothing is held: not knowing is never a reason to stay silent."""
import math
import os
import sys
from datetime import timedelta

import core

KEY = "line_budget:"        # alert_state row: name = "line_budget:<used>:<limit>", ts = when LINE was asked
CHECK_MIN = 30
DEFAULT_RESERVE_PCT = 5.0   # keep this share of the monthly limit (at least MIN_RESERVE messages) for alert-level news
MIN_RESERVE = 10


def reserve(limit):
    pct = float(os.environ.get("MSG_RESERVE_PCT", DEFAULT_RESERVE_PCT))
    return max(MIN_RESERVE, math.ceil(limit * pct / 100))


class Budget:
    def __init__(self, conn, at, fetch=None):
        self.conn, self.at, self.fetch = conn, at, fetch
        self.limit = self.used = None
        self.loaded = False

    def _load(self):
        self.loaded = True
        try:
            row = self.conn.execute("SELECT name, ts FROM alert_state WHERE name LIKE ?", (KEY + "%",)).fetchone()
            if row and self.at - core.parse(row["ts"]) < timedelta(minutes=CHECK_MIN):
                used, limit = row["name"][len(KEY):].split(":")
                self.used, self.limit = int(used), (int(limit) if limit != "None" else None)
                return
            if self.fetch is None:
                import notify
                self.fetch = notify.line_quota
            self.limit, self.used = self.fetch()
            self.conn.execute("DELETE FROM alert_state WHERE name LIKE ?", (KEY + "%",))
            self.conn.execute("INSERT INTO alert_state(name, ts) VALUES(?,?)",
                              (f"{KEY}{self.used}:{self.limit}", core.iso(self.at)))
            self.conn.commit()
        except Exception as e:   # cannot ask LINE: do not hold anything
            print(f"message budget check failed: {type(e).__name__}: {e}", file=sys.stderr)
            self.limit = self.used = None
            try:
                self.conn.rollback()
            except Exception:
                pass

    def allow(self, urgent):
        """May this message go out now? Urgent = alert-level news. Everything else waits once only the reserve is left."""
        if urgent:
            return True
        if not self.loaded:
            self._load()
        if self.limit is None or self.used is None:
            return True
        return self.limit - self.used > reserve(self.limit)

    def spent(self):
        """One more message went out in this run."""
        if self.used is not None:
            self.used += 1

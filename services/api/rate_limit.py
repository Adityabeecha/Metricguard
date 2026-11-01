"""Per-address request limits for the public, unauthenticated entry points.

In memory per API process (the hosted service runs one), so a restart forgets the counts; that
is fine for slowing scripted sign-ups and link scraping, which is all these limits are for.
Uvicorn runs with proxy headers on, so request.client.host is the visitor's address, not Vercel's.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

# (path prefix, requests allowed, per seconds); the first matching prefix applies.
RULES = (
    ('/api/v1/auth/oauth/', 20, 3600),   # starting or finishing a Google/GitHub sign-in
    ('/api/v1/shared-cases/', 240, 60),  # public read-only case links
    ('/api/v1/feedback', 30, 3600),
)
MAX_KEYS = 20_000


class Limiter:
    def __init__(self, rules=RULES):
        self.rules = rules
        self.hits: dict[tuple[str, str], deque] = {}
        self.lock = threading.Lock()

    def check(self, path: str, address: str, now: float | None = None) -> int | None:
        """None when allowed; otherwise the seconds to wait before trying again."""
        if os.environ.get('METRICGUARD_RATE_LIMITS', 'on').strip().lower() in ('off', 'false', '0', 'no'):
            return None
        rule = next((r for r in self.rules if path.startswith(r[0])), None)
        if rule is None:
            return None
        prefix, limit, window = rule
        now = time.monotonic() if now is None else now
        with self.lock:
            if len(self.hits) > MAX_KEYS:
                self.hits.clear()  # bounded memory; a burst of addresses only resets the counts
            queue = self.hits.setdefault((prefix, address), deque())
            while queue and queue[0] <= now - window:
                queue.popleft()
            if len(queue) >= limit:
                return max(1, int(queue[0] + window - now))
            queue.append(now)
        return None

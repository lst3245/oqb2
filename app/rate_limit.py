"""Per-key sliding-window rate limiter (in-process), built on the login
throttle's window. Used by the SMS token POST and the directory feed.
Process-local by design: one Flask process serves OQB."""
from __future__ import annotations

import time

from app.login_guard import _Window


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int, clock=time.monotonic):
        self._window = _Window(limit, window_seconds, clock)

    def hit(self, key: str) -> int:
        """Count one request for ``key``. Returns 0 when allowed, else the
        seconds to wait (the refused request is not counted)."""
        wait = self._window.retry_after(key)
        if wait:
            return wait
        self._window.record(key)
        return 0

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._window._hits.clear()
        else:
            self._window.clear(key)

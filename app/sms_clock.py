"""The clock SMS sign-in reads (token times, trust windows, jti purge).

Tests freeze it with ``frozen(ts)`` instead of sleeping; the official fixtures
are signed at a fixed reference time. Process-wide on purpose, so threads
started by a test see the same time.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone

_frozen: int | None = None


def now() -> int:
    """Unix seconds."""
    return _frozen if _frozen is not None else int(time.time())


def utcnow() -> datetime:
    """Naive UTC datetime for DB columns (the app stores naive UTC)."""
    return datetime.fromtimestamp(now(), timezone.utc).replace(tzinfo=None)


def to_datetime(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), timezone.utc).replace(tzinfo=None)


def to_ts(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.replace(tzinfo=timezone.utc).timestamp())


@contextmanager
def frozen(ts: int):
    global _frozen
    previous = _frozen
    _frozen = int(ts)
    try:
        yield
    finally:
        _frozen = previous

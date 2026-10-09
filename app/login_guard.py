"""Login throttling, client IP behind the reverse proxy, and safe post-login redirects.

Pure helpers: no database and no Flask app object. The process is single-worker,
so the counters live in memory and reset when the process restarts.
"""
from __future__ import annotations

import ipaddress
import threading
import time
from urllib.parse import urlparse


# Failed password attempts. The IP cap slows a scanner; the username cap still
# applies when many people share one address (a school NAT, or the proxy when
# it does not forward a client IP).
IP_FAILURE_LIMIT = 60
USERNAME_FAILURE_LIMIT = 8
WINDOW_SECONDS = 15 * 60


def _parse_ip(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _trusted_set(trusted_proxies) -> set[str] | None:
    """``None`` means every private, loopback, and link-local peer is a proxy."""
    if trusted_proxies is None:
        return None
    found = set()
    for value in trusted_proxies:
        parsed = _parse_ip(value)
        if parsed:
            found.add(parsed)
    return found


def peer_is_trusted(remote_addr, trusted_proxies) -> bool:
    remote = _parse_ip(remote_addr)
    if remote is None:
        return False
    allowed = _trusted_set(trusted_proxies)
    if allowed is None:
        addr = ipaddress.ip_address(remote)
        return bool(addr.is_private or addr.is_loopback or addr.is_link_local)
    return remote in allowed


def client_ip(remote_addr, x_real_ip, x_forwarded_for, trusted_proxies) -> str:
    """Address to throttle.

    Forwarded headers are read only when the TCP peer is a trusted proxy.
    ``X-Real-IP`` wins (the proxy overwrites it). Otherwise the rightmost
    ``X-Forwarded-For`` entry is the hop the nearest proxy added; a client
    who prepends a fake address does not get to choose the bucket.
    """
    remote = _parse_ip(remote_addr)
    if not peer_is_trusted(remote_addr, trusted_proxies):
        return remote or 'unknown'
    real = _parse_ip(x_real_ip)
    if real:
        return real
    if x_forwarded_for:
        for part in reversed(str(x_forwarded_for).split(',')):
            parsed = _parse_ip(part)
            if parsed:
                return parsed
    return remote or 'unknown'


def safe_next_url(target) -> str | None:
    """Same-site path only. Anything with a host, scheme, or backslash is dropped."""
    if not isinstance(target, str):
        return None
    target = target.strip()
    if not target.startswith('/') or target.startswith('//'):
        return None
    if '\\' in target or any(ord(ch) < 32 or ch.isspace() for ch in target):
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target


def retry_phrase(seconds: int) -> str:
    seconds = max(1, int(seconds))
    if seconds < 60:
        unit = 'second' if seconds == 1 else 'seconds'
        return f'{seconds} {unit}'
    minutes = (seconds + 59) // 60
    unit = 'minute' if minutes == 1 else 'minutes'
    return f'{minutes} {unit}'


class _Window:
    def __init__(self, limit: int, window_seconds: int, clock):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) < self.limit:
                return 0
            return max(1, int(self.window - (now - hits[0]) + 0.999))

    def record(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            hits = self._prune(key, now)
            hits.append(now)
            self._hits[key] = hits

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _prune(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._hits.get(key, ()) if now - t < self.window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits


class LoginThrottle:
    def __init__(self, ip_limit=IP_FAILURE_LIMIT, user_limit=USERNAME_FAILURE_LIMIT,
                 window_seconds=WINDOW_SECONDS, clock=time.monotonic):
        self.ips = _Window(ip_limit, window_seconds, clock)
        self.users = _Window(user_limit, window_seconds, clock)

    def retry_after(self, ip: str, username: str) -> int:
        wait = self.ips.retry_after(ip or 'unknown')
        key = (username or '').strip().casefold()
        if key:
            wait = max(wait, self.users.retry_after(key))
        return wait

    def record_failure(self, ip: str, username: str) -> None:
        self.ips.record(ip or 'unknown')
        key = (username or '').strip().casefold()
        if key:
            self.users.record(key)

    def record_success(self, username: str) -> None:
        key = (username or '').strip().casefold()
        if key:
            self.users.clear(key)

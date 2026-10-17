"""Proxy awareness and origin checks (contract v1.2 sections 7, 13;
docs/modules/sms-sso.md). Stage 6 adds cookie flags, session limits and the
global security headers here.

* ``PROXY_FIX_HOPS > 0`` wraps the WSGI app in ProxyFix, so ``remote_addr``,
  scheme and host come from the reverse proxy's X-Forwarded-* headers;
* ``public_origin()`` is the configured ``PUBLIC_ORIGIN``; ``origin_ok``
  compares the request's ``Origin`` header with it exactly (never with
  Flask's own URL, which is ``http://`` behind the proxy).
"""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import current_app


def normalise_origin(value) -> str:
    """``scheme://host[:port]`` lower-cased, default ports dropped; '' if unusable."""
    text = (value or '').strip()
    if not text or text == 'null':
        return ''
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ''
    scheme, host = (parts.scheme or '').lower(), (parts.hostname or '').lower()
    if scheme not in ('http', 'https') or not host or parts.path not in ('', '/') \
            or parts.query or parts.fragment or parts.username or parts.password:
        return ''
    if port is None or (scheme, port) in (('http', 80), ('https', 443)):
        return f'{scheme}://{host}'
    return f'{scheme}://{host}:{port}'


def public_origin() -> str:
    return normalise_origin(current_app.config.get('PUBLIC_ORIGIN'))


def origin_ok(req) -> bool:
    """The request's ``Origin`` equals ``PUBLIC_ORIGIN``. A missing or
    ``null`` Origin, or no configured origin, is refused."""
    expected = public_origin()
    got = normalise_origin(req.headers.get('Origin'))
    return bool(expected) and got == expected


def client_ip(req) -> str:
    """The client address. Behind ProxyFix ``remote_addr`` already is the
    client; otherwise the login guard's TRUSTED_PROXIES rules apply."""
    if int(current_app.config.get('PROXY_FIX_HOPS') or 0) > 0:
        return req.remote_addr or ''
    from app.login_guard import client_ip as guarded
    return guarded(req.remote_addr, req.headers.get('X-Real-IP'),
                   req.headers.get('X-Forwarded-For'), current_app.config.get('TRUSTED_PROXIES'))


def init_app(app) -> None:
    hops = int(app.config.get('PROXY_FIX_HOPS') or 0)
    if hops > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops)

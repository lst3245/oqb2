"""Proxy awareness, origin checks, cookies, session limits and security
headers (contract v1.2 sections 7, 11, 13, 14; docs/modules/sms-sso.md,
ADR-018).

* ``PROXY_FIX_HOPS > 0`` wraps the WSGI app in ProxyFix, so ``remote_addr``,
  scheme and host come from the reverse proxy's X-Forwarded-* headers;
* ``public_origin()`` is the configured ``PUBLIC_ORIGIN``; ``origin_ok``
  compares the request's ``Origin`` header with it exactly (never with
  Flask's own URL, which is ``http://`` behind the proxy);
* every sign-in stores ``session['oqb_auth'] = {'m', 'at', 'seen'[, 'iss',
  'r']}``; ``check_session`` is the pure limit policy and ``enforce_session``
  (``before_request``) ends a session that breaks it;
* cookies are ``oqb_session`` / ``oqb_remember``, HttpOnly, SameSite=Lax,
  Secure per ``SESSION_COOKIE_SECURE``; with ``SMS_EMBED_ENABLED`` they become
  ``SameSite=None; Secure; Partitioned``;
* ``security_headers`` (``after_request``) adds frame-ancestors /
  X-Frame-Options / nosniff / Referrer-Policy to every response.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

from flask import current_app, flash, request, session
from flask.sessions import SecureCookieSessionInterface
from flask_login import current_user, login_user, logout_user, user_loaded_from_cookie

from app import sms_clock
from app.i18n import t as _t

log = logging.getLogger(__name__)

SESSION_COOKIE = 'oqb_session'
REMEMBER_COOKIE = 'oqb_remember'
AUTH_KEY = 'oqb_auth'
SMS, PASSWORD = 'sms', 'password'

# Contract section 11 MUSTs for SMS sessions; password sessions use the same
# limits while PASSWORD_SESSION_LIMITS is on.
ABSOLUTE_SECONDS = 12 * 3600
IDLE_SECONDS = 2 * 3600
MAX_REMEMBER_DAYS = 30
SEEN_STEP_SECONDS = 60  # 'seen' is rewritten at most once a minute
SESSION_EXEMPT_ENDPOINTS = frozenset({'static', 'sso.directory_feed'})

ENDED_MESSAGE = 'Your session has ended. Please sign in again.'


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


# ---------------------------------------------------------------- sessions

def now() -> int:
    """Clock for the session limits; tests freeze ``sms_clock``."""
    return sms_clock.now()


def remember_days() -> int:
    """``PASSWORD_REMEMBER_DAYS`` clamped to 0-30 (0 = remember-me off)."""
    try:
        days = int(current_app.config.get('PASSWORD_REMEMBER_DAYS') or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_REMEMBER_DAYS, days))


def check_session(auth, now: int, *, linked: bool, limits_on: bool, remember_days: int):
    """Why this session must end, or ``None`` to keep it.

    ``auth`` is ``session['oqb_auth']``. SMS sessions: 12 h absolute, 2 h idle,
    never remembered. Password sessions: the same limits when ``limits_on``;
    a remembered one has only the ``remember_days`` absolute limit and is
    refused for a linked account or once remember-me is turned off.
    """
    if not isinstance(auth, dict) or auth.get('m') not in (SMS, PASSWORD):
        return 'no_marker'
    at, seen = auth.get('at'), auth.get('seen')
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (at, seen)):
        return 'no_marker'
    if auth['m'] == SMS:
        if auth.get('r'):
            return 'remember_sms'
        limits_on = True
    elif auth.get('r'):
        if linked:
            return 'remember_linked'
        if remember_days <= 0:
            return 'remember_off'
        return 'absolute' if now - at >= remember_days * 86400 else None
    if not limits_on:
        return None
    if now - at >= ABSOLUTE_SECONDS:
        return 'absolute'
    if now - seen >= IDLE_SECONDS:
        return 'idle'
    return None


def start_auth(method: str, *, issuer: str | None = None, remember: bool = False) -> None:
    """Stamp the session marker right after ``login_user``."""
    t = now()
    auth = {'m': method, 'at': t, 'seen': t}
    if issuer:
        auth['iss'] = issuer
    if remember:
        auth['r'] = True
    session[AUTH_KEY] = auth


def auth_method():
    auth = session.get(AUTH_KEY)
    return auth.get('m') if isinstance(auth, dict) else None


def is_linked(user_id: int) -> bool:
    """The account has an SMS identity. Unknown (query failed) counts as linked."""
    from app import db
    from app.models import ExternalIdentity
    try:
        return db.session.query(ExternalIdentity.id).filter_by(user_id=user_id).first() is not None
    except Exception as exc:
        db.session.rollback()
        log.error('session: identity lookup failed (%s)', exc.__class__.__name__)
        return True


def remember_allowed(user) -> bool:
    return remember_days() > 0 and not is_linked(user.id)


def password_login(user, remember: bool) -> bool:
    """``login_user`` for a password sign-in; returns whether remember-me was
    granted (opt-in, unlinked accounts only, at most PASSWORD_REMEMBER_DAYS)."""
    remember = bool(remember) and remember_allowed(user)
    if remember:
        login_user(user, remember=True, duration=timedelta(days=remember_days()))
    else:
        login_user(user, remember=False)
        session['_remember'] = 'clear'
    start_auth(PASSWORD, remember=remember)
    return remember


def relogin(user) -> None:
    """Re-issue the login for the current session after its session version
    changed (own password change); keeps the marker and remember choice."""
    auth = session.get(AUTH_KEY)
    remember = isinstance(auth, dict) and bool(auth.get('r')) and remember_allowed(user)
    if remember:
        login_user(user, remember=True, duration=timedelta(days=remember_days()))
    else:
        login_user(user, remember=False)


def end_session(reason: str) -> None:
    """Sign the current user out (any remember cookie is deleted)."""
    log.info('session ended: %s', reason)
    logout_user()
    session.pop(AUTH_KEY, None)
    from app import csrf
    csrf.rotate()
    flash(_t(ENDED_MESSAGE), 'info')


def enforce_session():
    """``before_request``: end a signed-in session that breaks the limits."""
    if request.endpoint in SESSION_EXEMPT_ENDPOINTS:
        return None
    # Loading the user marks the session not-fresh (Flask-Login session
    # protection) and so re-sends the cookie; skip it for anonymous requests.
    if '_user_id' not in session and current_app.config.get('REMEMBER_COOKIE_NAME') not in request.cookies:
        return None
    if not current_user.is_authenticated:
        return None
    cfg = current_app.config
    auth = session.get(AUTH_KEY)
    t = now()
    remembered = isinstance(auth, dict) and bool(auth.get('r'))
    reason = check_session(auth, t, linked=remembered and is_linked(current_user.id),
                           limits_on=bool(cfg.get('PASSWORD_SESSION_LIMITS', True)),
                           remember_days=remember_days())
    if reason:
        end_session(reason)
    elif t - auth['seen'] >= SEEN_STEP_SECONDS:
        session[AUTH_KEY] = dict(auth, seen=t)
    return None


def _loaded_from_cookie(sender, user=None, **_extra) -> None:
    """A remember-me cookie restored ``user``: mark the session as remembered.
    ``enforce_session`` then refuses it for a linked account."""
    start_auth(PASSWORD, remember=True)


# ---------------------------------------------------------------- cookies

def cookie_secure(mode, origin) -> bool:
    """``SESSION_COOKIE_SECURE``: true / false / auto (= PUBLIC_ORIGIN is https)."""
    value = str(mode if mode is not None else 'auto').strip().lower()
    if value in ('1', 'true', 'yes', 'on'):
        return True
    if value in ('0', 'false', 'no', 'off'):
        return False
    return normalise_origin(origin).startswith('https://')


def embed_enabled(app=None) -> bool:
    return bool((app or current_app).config.get('SMS_EMBED_ENABLED'))


_DROP_ATTRS = ('samesite', 'secure', 'partitioned')


def embed_cookie(header: str) -> str:
    """A ``Set-Cookie`` value rewritten for a third-party iframe:
    ``Secure; SameSite=None; Partitioned`` (Flask 3.0 cannot set Partitioned)."""
    name_value, *attrs = [p.strip() for p in header.split(';')]
    kept = [a for a in attrs if a and a.split('=', 1)[0].strip().lower() not in _DROP_ATTRS]
    return '; '.join([name_value, *kept, 'Secure', 'SameSite=None', 'Partitioned'])


def embed_cookies(response, names) -> None:
    headers = response.headers.getlist('Set-Cookie')
    if not headers:
        return
    response.headers.remove('Set-Cookie')
    for h in headers:
        if h.split('=', 1)[0].strip() in names:
            h = embed_cookie(h)
        response.headers.add('Set-Cookie', h)


class SessionInterface(SecureCookieSessionInterface):
    """Flask's cookie session; with embedding on, the session and remember
    cookies get ``SameSite=None; Secure; Partitioned``. This runs after every
    ``after_request`` (Flask-Login sets the remember cookie there)."""

    def save_session(self, app, session_obj, response):
        super().save_session(app, session_obj, response)
        if embed_enabled(app):
            embed_cookies(response, {app.config.get('SESSION_COOKIE_NAME'),
                                     app.config.get('REMEMBER_COOKIE_NAME')})


# ---------------------------------------------------------------- headers

def frame_ancestors(trust, now_ts: int) -> list:
    """Who may frame OQB while embedding is on: the live SMS, plus the
    workshop SMS only while its trust entry is active."""
    from app.sms_trust import LIVE_ISSUER, WORKSHOP_ISSUER
    found = [LIVE_ISSUER]
    entry = trust.get(WORKSHOP_ISSUER) if trust is not None else None
    if entry is not None and entry.active(now_ts):
        found.append(WORKSHOP_ISSUER)
    return found


def _embed_ancestors() -> str:
    trust = None
    if current_app.config.get('SMS_SCHEMA_READY'):
        try:
            from app import sms_trust
            trust = sms_trust.load_trust_list()
        except Exception as exc:
            log.error('headers: trust list unavailable (%s)', exc.__class__.__name__)
    return ' '.join(frame_ancestors(trust, now()))


_FRAME_ANCESTORS = re.compile(r"frame-ancestors[^;]*")


def security_headers(response):
    """``after_request``: framing, sniffing and referrer headers on every
    response. A route's own CSP (the /sso/* pages) is kept; only its
    frame-ancestors source list follows the embed switch."""
    embed = embed_enabled()
    ancestors = _embed_ancestors() if embed else "'none'"
    directive = f'frame-ancestors {ancestors}'
    csp = response.headers.get('Content-Security-Policy')
    if not csp:
        response.headers['Content-Security-Policy'] = directive
    elif embed:
        if _FRAME_ANCESTORS.search(csp):
            csp = _FRAME_ANCESTORS.sub(directive, csp, count=1)
        else:
            csp = f'{csp}; {directive}'
        response.headers['Content-Security-Policy'] = csp
    if embed:
        response.headers.pop('X-Frame-Options', None)
    else:
        response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response


def init_proxy(app) -> None:
    hops = int(app.config.get('PROXY_FIX_HOPS') or 0)
    if hops > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops)


def init_app(app) -> None:
    init_proxy(app)
    secure = cookie_secure(app.config.get('SESSION_COOKIE_SECURE_MODE'), app.config.get('PUBLIC_ORIGIN'))
    app.config.update(SESSION_COOKIE_NAME=SESSION_COOKIE, REMEMBER_COOKIE_NAME=REMEMBER_COOKIE,
                      SESSION_COOKIE_SECURE=secure, REMEMBER_COOKIE_SECURE=secure,
                      SESSION_COOKIE_HTTPONLY=True, REMEMBER_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', REMEMBER_COOKIE_SAMESITE='Lax')
    app.session_interface = SessionInterface()
    app.before_request(enforce_session)
    app.after_request(security_headers)
    user_loaded_from_cookie.connect(_loaded_from_cookie, app)

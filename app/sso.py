"""SMS sign-in routes (contract v1.2 sections 4, 6, 7, 9, 11, 12;
docs/modules/sms-sso.md).

``GET /sso/sms`` is a standalone landing page: a nonce'd script, first in
``<head>``, strips ``#handoff=<JWT>`` from the address bar and history, then
POSTs the token to ``POST /sso/sms`` with ``fetch`` (so ``Origin`` is sent).
The POST checks Origin == PUBLIC_ORIGIN, rate-limits per IP, verifies the
token and runs the handoff transaction, then answers ``{"next": <fixed path>}``
which the page allowlists. The token is never logged or stored.

Every ``/sso/*`` route answers 404 unless ``SMS_SSO_ENABLED`` and
``PUBLIC_ORIGIN`` are set, and 503 when the SSO schema patches failed (fail
closed).

``POST /api/sms/directory`` (section 10) is gated by ``SMS_FEED_ENABLED``
alone, so the roster can arrive before sign-in is switched on. It is
authenticated by the snapshot's signature (no cookies, no session), and the
body is never logged.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from urllib.parse import quote

from flask import (Blueprint, abort, current_app, flash, jsonify, make_response, redirect,
                   render_template, request, session, url_for)
from flask_login import current_user, login_user, logout_user

from app import (auth_audit, db, sms_clock, sms_feed_service, sms_linking,
                 sms_sso_service as service, sms_trust, sms_verify, web_security)
from app.auth import SSO_LINK_KEY
from app.i18n import t as _t
from app.models import User
from app.rate_limit import RateLimiter

log = logging.getLogger(__name__)

sso_bp = Blueprint('sso', __name__)

NEXT_HOME = '/'
NEXT_LINK = '/sso/sms/link'
NEXT_FAILED = '/sso/sms/failed'
NEXT_PATHS = (NEXT_HOME, NEXT_LINK, NEXT_FAILED)

RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 300
MAX_BODY_BYTES = 16 * 1024
FORM_KEY = 'sso_link_form'  # interim per-link form token (global CSRF arrives in stage 6)
AUTH_METHOD_KEY = 'auth_method'

_limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW_SECONDS)

FEED_RATE_LIMIT = 30
FEED_RATE_WINDOW_SECONDS = 600
FEED_CONTENT_TYPE = 'application/jwt'
_feed_limiter = RateLimiter(FEED_RATE_LIMIT, FEED_RATE_WINDOW_SECONDS)

FAILED_MESSAGE = ("Sign-in from SMS didn't work. Please open the Question Bank from SMS again, "
                  "or sign in with your OQB password.")
NOT_LINKED_MESSAGE = ("Your SMS account isn't linked to an OQB account yet. Sign in with your OQB "
                      "password to link it, or ask an OQB admin.")


# ---------------------------------------------------------------- helpers

def _enabled() -> bool:
    cfg = current_app.config
    return bool(cfg.get('SMS_SSO_ENABLED')) and bool(web_security.public_origin())


def _gate() -> None:
    if not _enabled():
        abort(404)
    if not current_app.config.get('SMS_SCHEMA_READY'):
        abort(503)


def signin_url():
    """``{primary issuer}/apps/oqb?origin={PUBLIC_ORIGIN}`` for the login page,
    or ``None`` while SMS sign-in cannot work (off, no schema, no usable key)."""
    try:
        if not _enabled() or not current_app.config.get('SMS_SCHEMA_READY'):
            return None
        entry = sms_trust.load_trust_list().primary(sms_clock.now())
        if entry is None or not (entry.keys or entry.jwks_auto):
            return None
        return f'{entry.issuer}/apps/oqb?origin={quote(web_security.public_origin(), safe=":/")}'
    except Exception as exc:
        log.error('sso: sign-in link unavailable (%s)', exc.__class__.__name__)
        return None


def _private(resp, csp="default-src 'none'; form-action 'self'; frame-ancestors 'none'"):
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Content-Security-Policy'] = csp
    return resp


def _wants_json() -> bool:
    return request.is_json or 'application/json' in (request.headers.get('Accept') or '')


def _answer(next_path: str, status: int):
    if next_path not in NEXT_PATHS:
        next_path = NEXT_FAILED
    if _wants_json():
        return _private(make_response(jsonify({'next': next_path}), status))
    if next_path == NEXT_FAILED:
        return _failed_page(status)
    return _private(redirect(next_path, code=303))


def _failed_page(status: int = 200):
    html = render_template('sso/failed.html', message=_t(FAILED_MESSAGE))
    return _private(make_response(html, status))


def _refuse(reason: str, ip: str, status: int = 401):
    auth_audit.record_now(auth_audit.SSO_LOGIN, 'fail', reason, ip=ip)
    return _answer(NEXT_FAILED, status)


def _token_from_request():
    length = request.content_length
    if length is None or length > MAX_BODY_BYTES:
        return None
    if request.is_json:
        body = request.get_json(silent=True)
        token = body.get('token') if isinstance(body, dict) else None
    else:
        token = request.form.get('token')
    return token if isinstance(token, str) else None


def _start_session(user: User) -> None:
    """A different signed-in user is replaced (the session is cleared); the
    same user keeps the session and gets fresh levels. SMS sessions never get
    a remember-me cookie, and any existing one is deleted."""
    if current_user.is_authenticated and current_user.id != user.id:
        logout_user()
    if not current_user.is_authenticated or current_user.id != user.id:
        session.clear()
    login_user(user, remember=False, fresh=True)
    session['_remember'] = 'clear'
    session[AUTH_METHOD_KEY] = 'sms'


def _form_token() -> str:
    token = session.get(FORM_KEY)
    if not token:
        token = session[FORM_KEY] = secrets.token_urlsafe(24)
    return token


def _form_token_ok() -> bool:
    expected = session.get(FORM_KEY) or ''
    return bool(expected) and hmac.compare_digest(expected, request.form.get('form_token') or '')


def _pending():
    return service.pending_link(session.get(SSO_LINK_KEY))


def _drop_pending() -> None:
    session.pop(SSO_LINK_KEY, None)
    session.pop(FORM_KEY, None)


# ---------------------------------------------------------------- routes

@sso_bp.route('/sso/sms', methods=['GET'])
def landing():
    _gate()
    nonce = secrets.token_urlsafe(18)
    html = render_template('sso/landing.html', nonce=nonce, next_paths=NEXT_PATHS,
                           failed_path=NEXT_FAILED, post_path=url_for('sso.token_post'))
    csp = (f"default-src 'none'; script-src 'self' 'nonce-{nonce}'; connect-src 'self'; "
           f"form-action 'self'; frame-ancestors 'none'")
    return _private(make_response(html), csp)


@sso_bp.route('/sso/sms', methods=['POST'])
def token_post():
    _gate()
    ip = web_security.client_ip(request)
    if _limiter.hit(ip or '-'):
        return _refuse('rate_limited', ip, 429)
    if not web_security.origin_ok(request):
        return _refuse('bad_origin', ip)
    token = _token_from_request()
    if token is None:
        return _refuse('malformed', ip)
    now = sms_clock.now()
    try:
        result = sms_verify.verify_handoff(token, sms_trust.load_trust_list(), now,
                                           key_resolver=sms_trust.key_resolver)
    except sms_verify.VerifyError as exc:
        return _refuse(exc.reason, ip)
    except Exception as exc:
        log.error('sso: verification error (%s)', exc.__class__.__name__)
        return _refuse('server_error', ip)
    del token

    outcome = service.complete_handoff(
        result, ip=ip, jit_enabled=bool(current_app.config.get('SMS_JIT_ENABLED')))
    service.maybe_purge()
    if outcome.kind == service.OK:
        user = db.session.get(User, outcome.user_id)
        if user is None:
            return _refuse('server_error', ip)
        _start_session(user)
        return _answer(NEXT_HOME, 200)
    if outcome.kind == service.LINK:
        # Never link to whoever happens to be signed in: a link always needs
        # a fresh password login (docs/modules/sms-sso.md).
        if current_user.is_authenticated:
            logout_user()
        session.clear()
        session['_remember'] = 'clear'
        session[SSO_LINK_KEY] = outcome.nonce
        return _answer(NEXT_LINK, 200)
    return _answer(NEXT_FAILED, 401)


@sso_bp.route('/sso/sms/failed')
def failed():
    _gate()
    return _failed_page()


@sso_bp.route('/sso/sms/link')
def link_prompt():
    _gate()
    found = _pending()
    if found is None:
        _drop_pending()
        return redirect(url_for('sso.failed'))
    if current_user.is_authenticated and session.get(AUTH_METHOD_KEY) == 'password':
        return redirect(url_for('sso.link_confirm'))
    _row, result = found
    resp = make_response(render_template(
        'sso/link_prompt.html', message=_t(NOT_LINKED_MESSAGE), sms_name=result.name,
        sms_staff_code=result.staff_code, form_token=_form_token()))
    return _private(resp, "frame-ancestors 'none'")


@sso_bp.route('/sso/sms/link/confirm', methods=['GET', 'POST'])
def link_confirm():
    _gate()
    found = _pending()
    if found is None:
        _drop_pending()
        flash(_t('The SMS link request has expired. Open the Question Bank from SMS again.'), 'warning')
        return redirect(url_for('auth.index'))
    if not current_user.is_authenticated or session.get(AUTH_METHOD_KEY) != 'password':
        return redirect(url_for('auth.login'))
    _row, result = found
    refusal = service.self_refusal(current_user, result)
    if request.method == 'POST':
        if not _form_token_ok():
            abort(400)
        ip = web_security.client_ip(request)
        outcome = service.self_link(session.get(SSO_LINK_KEY), current_user, ip=ip)
        _drop_pending()
        if outcome.kind == service.OK:
            flash(_t('Your OQB account is now linked to your SMS account.'), 'success')
        else:
            flash(_t(_refusal_message(outcome.reason)), 'danger')
        return redirect(url_for('auth.index'))
    resp = make_response(render_template(
        'sso/link_confirm.html', username=current_user.username, sms_name=result.name,
        sms_staff_code=result.staff_code, refusal=_t(_refusal_message(refusal)) if refusal else None,
        form_token=_form_token()))
    return _private(resp, "frame-ancestors 'none'")


@sso_bp.route('/sso/sms/link/cancel', methods=['POST'])
def link_cancel():
    _gate()
    if not _form_token_ok():
        abort(400)
    service.cancel_pending(session.get(SSO_LINK_KEY))
    _drop_pending()
    return redirect(url_for('auth.index'))


# ---------------------------------------------------------------- directory feed

def _feed_answer(status: int, body: dict):
    resp = make_response(jsonify(body), status)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _feed_reject(reason: str, ip, status: int | None = None):
    auth_audit.record_now(auth_audit.FEED_SNAPSHOT, 'fail', reason, ip=ip)
    return _feed_answer(status or sms_verify.feed_status(reason),
                        {'status': 'rejected', 'reason': reason})


@sso_bp.route('/api/sms/directory', methods=['POST'])
def directory_feed():
    cfg = current_app.config
    if not cfg.get('SMS_FEED_ENABLED'):
        abort(404)
    if not cfg.get('SMS_SCHEMA_READY'):
        return _feed_answer(503, {'status': 'unavailable'})
    ip = web_security.client_ip(request)
    if _feed_limiter.hit(ip or '-'):
        return _feed_reject('rate_limited', ip, 429)
    if request.mimetype != FEED_CONTENT_TYPE:
        return _feed_reject('malformed', ip)
    length = request.content_length
    if length is None or length > sms_verify.MAX_SNAPSHOT_BYTES:
        return _feed_reject('malformed', ip)
    body = request.get_data(cache=False)
    try:
        snap = sms_verify.verify_snapshot(body, sms_trust.load_trust_list(), sms_clock.now(),
                                          key_resolver=sms_trust.key_resolver)
    except sms_verify.VerifyError as exc:
        return _feed_reject(exc.reason, ip)
    except Exception as exc:
        log.error('sms feed: verification error (%s)', exc.__class__.__name__)
        return _feed_reject('server_error', ip, 500)
    del body
    result = sms_feed_service.apply_snapshot(
        snap, brake_min=int(cfg.get('SMS_FEED_BRAKE_MIN') or 5),
        brake_pct=int(cfg.get('SMS_FEED_BRAKE_PCT') or 10), ip=ip)
    service.maybe_purge()
    return _feed_answer(result.status, result.body)


def _refusal_message(reason) -> str:
    if reason == sms_linking.PRIVILEGED:
        return 'Super admin and local-only accounts cannot be linked to SMS.'
    if reason == sms_linking.ALREADY_LINKED:
        return 'This SMS account or this OQB account is already linked.'
    return 'The SMS account could not be linked. Ask an OQB admin.'

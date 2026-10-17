"""CSRF protection for every state-changing request (docs/core/04, ADR-018).

The token is a random value kept in the signed session (``session['_csrf']``),
created lazily and rotated on every login, logout and SSO sign-in. Pages get
it from ``<meta name="csrf-token">`` / ``{{ csrf_field() }}``; ``static/js/
csrf.js`` adds it to fetch, XHR, htmx and form posts.

``protect`` (``before_request``) checks POST / PUT / PATCH / DELETE: the
``X-CSRF-Token`` header, else the ``csrf_token`` form field, must equal the
session token. Exemptions are by endpoint name: the SSO token POST (checked by
Origin) and the directory feed (signed, no cookies). ``CSRF_MODE=report``
logs ``csrf_failed`` and lets the request through (rollout only).
"""
from __future__ import annotations

import hmac
import logging
import secrets

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape

from app.i18n import t as _t

log = logging.getLogger(__name__)

SESSION_KEY = '_csrf'
HEADER = 'X-CSRF-Token'
FIELD = 'csrf_token'
UNSAFE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
EXEMPT_ENDPOINTS = frozenset({'sso.token_post', 'sso.directory_feed'})
# A failed check answers 303 to the target instead of 400: the Web Share
# Target fallback cannot carry a token, and the view never runs.
REDIRECT_ON_FAILURE = {'toolbox.markup_share_target': 'toolbox.markup_tool'}
FORM_MIMETYPES = ('application/x-www-form-urlencoded', 'multipart/form-data')
ENFORCE, REPORT = 'enforce', 'report'
FAILED_MESSAGE = 'Your session has expired. Reload the page and try again.'


def get_token() -> str:
    token = session.get(SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = session[SESSION_KEY] = secrets.token_urlsafe(32)
    return token


def rotate() -> str:
    token = session[SESSION_KEY] = secrets.token_urlsafe(32)
    return token


def field() -> Markup:
    return Markup(f'<input type="hidden" name="{FIELD}" value="{escape(get_token())}">')


def token_matches(expected, sent) -> bool:
    if not isinstance(expected, str) or not isinstance(sent, str) or not expected or not sent:
        return False
    return hmac.compare_digest(expected.encode('utf-8'), sent.encode('utf-8'))


def needs_check(method, endpoint) -> bool:
    """Unknown endpoints (404 / 405) are left to routing."""
    return (str(method or '').upper() in UNSAFE_METHODS and endpoint is not None
            and endpoint not in EXEMPT_ENDPOINTS)


def mode() -> str:
    value = str(current_app.config.get('CSRF_MODE') or ENFORCE).strip().lower()
    return REPORT if value == REPORT else ENFORCE


def _sent_token():
    sent = request.headers.get(HEADER)
    if sent:
        return sent
    if request.mimetype in FORM_MIMETYPES:
        return request.form.get(FIELD)
    return None


def _failure():
    from app import authz
    target = REDIRECT_ON_FAILURE.get(request.endpoint)
    if target:
        return redirect(url_for(target), code=303)
    message = _t(FAILED_MESSAGE)
    if request.endpoint == 'auth.login':
        from app.auth import render_login
        flash(message, 'warning')
        return render_login(), 400
    if authz.wants_json():
        return jsonify({'success': False, 'error': 'csrf_failed', 'message': message}), 400
    return render_template('csrf_failed.html', message=message), 400


def protect():
    if not needs_check(request.method, request.endpoint):
        return None
    if token_matches(session.get(SESSION_KEY), _sent_token()):
        return None
    current = mode()
    log.warning('csrf_failed mode=%s endpoint=%s path=%s', current, request.endpoint, request.path)
    if current == REPORT:
        return None
    return _failure()


def init_app(app) -> None:
    app.jinja_env.globals.update(csrf_token=get_token, csrf_field=field)
    app.before_request(protect)

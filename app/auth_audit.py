"""Audit log for sign-in, linking, the directory feed and the trust list
(``auth_audit_log``; docs/modules/sms-sso.md).

``record`` adds a row to the caller's session, so it commits or rolls back
with the change it describes. ``record_now`` writes on its own connection for
events with no surrounding transaction (alerts, refused requests). Tokens are
never stored: ``detail`` is JSON with anything shaped like a JWS replaced.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

# Events
SSO_LOGIN = 'sso_login'
NOT_LINKED = 'not_linked'
NONPRIMARY_LOGIN = 'nonprimary_login'
LINK = 'link'
UNLINK = 'unlink'
REACTIVATE = 'reactivate'
DEPROVISION = 'deprovision'
PASSWORD_LOGIN = 'password_login'
PASSWORD_CHANGE = 'password_change'
FEED_SNAPSHOT = 'feed_snapshot'
FEED_HELD = 'feed_held'
FEED_DECISION = 'feed_decision'
FEED_MISSING = 'feed_missing'
MAPPING = 'sms_mapping'
TRUST_EDIT = 'trust_edit'
TRUST_PIN = 'trust_pin'
TRUST_UNPIN = 'trust_unpin'
TRUST_BOOTSTRAP_IGNORED = 'trust_bootstrap_ignored'
TEST_WINDOW = 'test_window'
JWKS_NEW_KID = 'jwks_new_kid'
ACCOUNT_STATE = 'account_state'
SESSIONS_REVOKED = 'sessions_revoked'

OUTCOMES = ('ok', 'fail', 'info')

_JWS = re.compile(r'[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}')
_DETAIL_MAX = 4000


def scrub(text: str) -> str:
    return _JWS.sub('[token]', text)


def _detail(detail) -> str | None:
    if detail is None:
        return None
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str, ensure_ascii=False,
                                                             sort_keys=True)
    return scrub(text)[:_DETAIL_MAX]


def _cut(value, n):
    if value is None:
        return None
    return scrub(str(value))[:n]


def _row(event, outcome, reason, issuer, sub, user_id, actor_id, ip, detail, alert):
    from app import sms_clock
    from app.models import AuthAuditLog
    if outcome not in OUTCOMES:
        outcome = 'info'
    return AuthAuditLog(
        created_at=sms_clock.utcnow(), event=str(event)[:40], outcome=outcome,
        reason=_cut(reason, 32), issuer=_cut(issuer, 191), sub=_cut(sub, 64),
        user_id=user_id, actor_user_id=actor_id, ip=_cut(ip, 45),
        detail=_detail(detail), is_alert=bool(alert),
    )


def record(event, outcome, reason=None, *, issuer=None, sub=None, user_id=None,
           actor_id=None, ip=None, detail=None, alert=False):
    """Add an audit row to ``db.session`` (the caller commits)."""
    from app import db
    row = _row(event, outcome, reason, issuer, sub, user_id, actor_id, ip, detail, alert)
    db.session.add(row)
    return row


def record_now(event, outcome, reason=None, *, issuer=None, sub=None, user_id=None,
               actor_id=None, ip=None, detail=None, alert=False) -> None:
    """Write an audit row in its own transaction (never raises)."""
    from sqlalchemy.orm import Session
    from app import db
    try:
        with Session(db.engine) as s:
            s.add(_row(event, outcome, reason, issuer, sub, user_id, actor_id, ip, detail, alert))
            s.commit()
    except Exception as exc:
        log.error('auth_audit: could not write %s (%s)', event, exc.__class__.__name__)

"""The handoff transaction and account linking (contract v1.2 sections 6
step 9, 8, 9; docs/modules/sms-sso.md).

``complete_handoff`` and ``self_link`` are transaction boundaries (they
commit or roll back). ``admin_link`` / ``unlink`` flush and leave the commit
to the calling route, like ``sms_trust``. Level names come from
``app/sms_levels.py``; stored levels are OQB names.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app import auth_audit, db, sms_clock, sms_levels, sms_linking, sms_trust
from app.models import (UNUSABLE_PASSWORD_PREFIX, ExternalIdentity, SmsRoster,
                        SmsSubjectCatalogue, SmsTrustIssuer, SsoPendingLink, SsoUsedJti,
                        User, UserSmsLevel)
from app.sms_verify import PURPOSE_HANDOFF, VerifiedHandoff, canonical_uuid

log = logging.getLogger(__name__)

PENDING_LINK_SECONDS = 600
JTI_KEEP_SECONDS = 86400
PURGE_EVERY_SECONDS = 600
JIT_PASSWORD_SENTINEL = UNUSABLE_PASSWORD_PREFIX + 'sms-no-password'
DEPROVISIONED = 'sms_deprovisioned'

# Outcomes
OK, LINK, FAIL = 'ok', 'link', 'fail'

METHOD_SELF, METHOD_ADMIN, METHOD_JIT = 'self', 'admin', 'jit'  # not a level


@dataclass
class Outcome:
    kind: str
    reason: str | None = None
    user_id: int | None = None
    nonce: str | None = None


class LinkError(ValueError):
    """``reason`` is a short code; ``str(exc)`` is English (a catalogue key)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------- levels

def replace_levels(user_id: int, issuer: str, default_level, subjects, source: str) -> int:
    """Replace, never merge: drop this user's rows for ``issuer`` and write
    the new set. ``default_level`` and each ``(uuid, code, level)`` are OQB
    level names; ``None`` default writes no default row."""
    UserSmsLevel.query.filter_by(user_id=user_id, issuer=issuer).delete(synchronize_session=False)
    now = sms_clock.utcnow()
    rows = []
    if default_level:
        rows.append(UserSmsLevel(user_id=user_id, issuer=issuer, sms_subject_uuid='',
                                 level=default_level, source=source, synced_at=now))
    for uid, code, level in subjects:
        rows.append(UserSmsLevel(user_id=user_id, issuer=issuer, sms_subject_uuid=uid,
                                 sms_subject_code=code, level=level, source=source, synced_at=now))
    db.session.add_all(rows)
    return len(rows)


def _handoff_levels(result: VerifiedHandoff):
    return result.default_level, [(s.uuid, s.code, s.level) for s in result.subjects]


def roster_levels(row: SmsRoster):
    """OQB levels from a roster row (SMS labels as received in the snapshot)."""
    default = sms_levels.to_oqb_level(row.default_level) if row.default_level else None
    try:
        subjects = json.loads(row.subjects_json or '[]')
    except ValueError:
        subjects = []
    out = []
    for s in subjects:
        level = sms_levels.to_oqb_level(s.get('level'))
        if level and s.get('uuid'):
            out.append((s['uuid'], s.get('code'), level))
    return default, out


def _cap_for(issuer: str, default, subjects):
    """Apply the non-primary cap to stored levels (handoffs arrive capped)."""
    row = SmsTrustIssuer.query.filter_by(issuer=issuer).first()
    if row is not None and row.is_primary and not row.level_cap:
        return default, subjects
    rank = {sms_levels.to_oqb_level(k): v for k, v in sms_levels.RANK.items() if sms_levels.to_oqb_level(k)}
    cap_d, cap_s = sms_levels.oqb_cap('default'), sms_levels.oqb_cap('subject')
    if default and rank.get(default, 0) > rank[cap_d]:
        default = cap_d
    capped = []
    for uid, code, level in subjects:
        level = cap_s if rank.get(level, 0) > rank[cap_s] else level
        if rank.get(level, 0) > rank.get(default, 0):
            capped.append((uid, code, level))
    return default, capped


def _refresh_profile(user: User, *, name, name_zh, email, staff_code) -> None:
    user.display_name = name or user.display_name
    user.name_zh = name_zh
    user.email = email
    user.staff_code = staff_code


def _upsert_catalogue(result: VerifiedHandoff) -> None:
    """Subjects seen in a primary-issuer token (the snapshot is authoritative)."""
    if not result.primary:
        return
    now = sms_clock.utcnow()
    for s in result.subjects:
        row = db.session.get(SmsSubjectCatalogue, s.uuid)
        if row is None:
            db.session.add(SmsSubjectCatalogue(
                sms_uuid=s.uuid, code=s.code, name_en=s.name_en or '', name_zh=s.name_zh,
                is_active=True, seen_in='token', issuer=result.issuer,
                first_seen_at=now, last_seen_at=now))
        else:
            row.code, row.last_seen_at = s.code, now
            if s.name_en:
                row.name_en = s.name_en
            if s.name_zh is not None:
                row.name_zh = s.name_zh


# ---------------------------------------------------------------- handoff

def _fail(reason, result, ip, user_id=None) -> Outcome:
    db.session.rollback()
    auth_audit.record_now(auth_audit.SSO_LOGIN, 'fail', reason, issuer=result.issuer,
                          sub=result.sub, user_id=user_id, ip=ip)
    return Outcome(FAIL, reason=reason, user_id=user_id)


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode('ascii')).hexdigest()


def _taken(name: str) -> bool:
    return db.session.query(User.id).filter(db.func.lower(User.username) == name.lower()).first() is not None


def _jit_user(result: VerifiedHandoff) -> User:
    user = User(username=sms_linking.derive_username(result.email, result.sub, _taken),
                password_hash=JIT_PASSWORD_SENTINEL, is_admin=False, is_super_admin=False)
    db.session.add(user)
    db.session.flush()
    db.session.add(ExternalIdentity(user_id=user.id, issuer=result.issuer, sub=result.sub,
                                    link_method=METHOD_JIT, linked_at=sms_clock.utcnow()))
    auth_audit.record(auth_audit.LINK, 'ok', METHOD_JIT, issuer=result.issuer, sub=result.sub,
                      user_id=user.id, detail={'username': user.username})
    return user


def complete_handoff(result: VerifiedHandoff, *, ip=None, jit_enabled=False) -> Outcome:
    """Contract section 6 step 9 in one transaction. Commits on success and on
    "not linked" (the jti row and the pending link only); rolls back and
    audits on failure."""
    try:
        db.session.add(SsoUsedJti(issuer=result.issuer, jti=result.jti, purpose=PURPOSE_HANDOFF,
                                  exp=result.exp, used_at=sms_clock.utcnow()))
        db.session.flush()
    except IntegrityError:
        return _fail('replay', result, ip)
    try:
        return _complete(result, ip=ip, jit_enabled=jit_enabled)
    except Exception as exc:
        log.error('sso: handoff transaction failed (%s)', exc.__class__.__name__)
        return _fail('server_error', result, ip)


def _complete(result, *, ip, jit_enabled) -> Outcome:
    identity = ExternalIdentity.query.filter_by(issuer=result.issuer, sub=result.sub).first()
    user = db.session.get(User, identity.user_id) if identity else None
    if identity is None:
        trust = sms_trust.load_trust_list()
        entry = trust.get(result.issuer)
        if result.primary and jit_enabled and entry is not None and entry.allow_jit:
            user = _jit_user(result)
            identity = ExternalIdentity.query.filter_by(issuer=result.issuer, sub=result.sub).one()
        elif result.primary:
            nonce = secrets.token_urlsafe(32)
            now = sms_clock.now()
            db.session.add(SsoPendingLink(
                nonce_hash=_nonce_hash(nonce), issuer=result.issuer, sub=result.sub,
                claims_json=result.to_json(), created_at=sms_clock.to_datetime(now),
                expires_at=sms_clock.to_datetime(now + PENDING_LINK_SECONDS)))
            auth_audit.record(auth_audit.NOT_LINKED, 'info', 'not_linked', issuer=result.issuer,
                              sub=result.sub, ip=ip)
            db.session.commit()
            return Outcome(LINK, reason='not_linked', nonce=nonce)
        else:
            return _fail('not_linked', result, ip)
    if user is None:
        return _fail('not_linked', result, ip)
    if user.is_privileged:
        return _fail(sms_linking.PRIVILEGED, result, ip, user.id)
    if not user.is_active:
        if user.disabled_reason != DEPROVISIONED:
            return _fail('disabled', result, ip, user.id)
        user.active, user.disabled_reason, user.disabled_at = True, None, None
        auth_audit.record(auth_audit.REACTIVATE, 'ok', 'handoff', issuer=result.issuer,
                          sub=result.sub, user_id=user.id, ip=ip)
    default, subjects = _handoff_levels(result)
    count = replace_levels(user.id, result.issuer, default, subjects, 'handoff')
    _upsert_catalogue(result)
    _refresh_profile(user, name=result.name, name_zh=result.name_zh, email=result.email,
                     staff_code=result.staff_code)
    now = sms_clock.utcnow()
    identity.last_login_at = now
    user.last_login_at, user.last_login_method = now, 'sms'
    detail = {'kid': result.kid, 'levels': count, 'academic_year': result.academic_year}
    auth_audit.record(auth_audit.SSO_LOGIN, 'ok', issuer=result.issuer, sub=result.sub,
                      user_id=user.id, ip=ip, detail=detail)
    if not result.primary:
        auth_audit.record(auth_audit.NONPRIMARY_LOGIN, 'info', issuer=result.issuer, sub=result.sub,
                          user_id=user.id, ip=ip, detail=detail, alert=True)
    db.session.commit()
    return Outcome(OK, user_id=user.id)


# ---------------------------------------------------------------- pending links

def pending_link(nonce):
    """``(row, VerifiedHandoff)`` for a live, unconsumed pending link, else ``None``."""
    if not isinstance(nonce, str) or not nonce:
        return None
    row = SsoPendingLink.query.filter_by(nonce_hash=_nonce_hash(nonce)).first()
    if row is None or row.consumed_at is not None or sms_clock.to_ts(row.expires_at) <= sms_clock.now():
        return None
    try:
        return row, VerifiedHandoff.from_json(row.claims_json)
    except Exception:
        return None


def cancel_pending(nonce) -> None:
    found = pending_link(nonce)
    if found is not None:
        found[0].consumed_at = sms_clock.utcnow()
        db.session.commit()


def linked_issuers(user_id: int) -> list:
    return [i for (i,) in db.session.query(ExternalIdentity.issuer).filter_by(user_id=user_id)]


def self_refusal(user: User, result: VerifiedHandoff) -> str | None:
    if ExternalIdentity.query.filter_by(issuer=result.issuer, sub=result.sub).first() is not None:
        return sms_linking.ALREADY_LINKED
    return sms_linking.link_refusal(user, result.issuer, linked_issuers(user.id))


def self_link(nonce: str, user: User, *, ip=None) -> Outcome:
    """Step 3 of self-linking: the signed-in ``user`` confirmed. Commits."""
    found = pending_link(nonce)
    if found is None:
        return Outcome(FAIL, reason='expired')
    row, result = found
    entry = sms_trust.load_trust_list().primary(sms_clock.now())
    if entry is None or entry.issuer != result.issuer:
        return Outcome(FAIL, reason='issuer_disabled')
    refusal = self_refusal(user, result)
    if refusal:
        auth_audit.record_now(auth_audit.LINK, 'fail', refusal, issuer=result.issuer, sub=result.sub,
                              user_id=user.id, actor_id=user.id, ip=ip, detail={'method': METHOD_SELF})
        return Outcome(FAIL, reason=refusal, user_id=user.id)
    try:
        row.consumed_at = sms_clock.utcnow()
        db.session.add(ExternalIdentity(user_id=user.id, issuer=result.issuer, sub=result.sub,
                                        link_method=METHOD_SELF, linked_at=sms_clock.utcnow(),
                                        linked_by_user_id=user.id, last_login_at=sms_clock.utcnow()))
        db.session.flush()
        default, subjects = _handoff_levels(result)
        replace_levels(user.id, result.issuer, default, subjects, 'handoff')
        _upsert_catalogue(result)
        _refresh_profile(user, name=result.name, name_zh=result.name_zh, email=result.email,
                         staff_code=result.staff_code)
        auth_audit.record(auth_audit.LINK, 'ok', METHOD_SELF, issuer=result.issuer, sub=result.sub,
                          user_id=user.id, actor_id=user.id, ip=ip)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return Outcome(FAIL, reason=sms_linking.ALREADY_LINKED, user_id=user.id)
    return Outcome(OK, user_id=user.id)


# ---------------------------------------------------------------- admin link / unlink

def admin_link(user: User, issuer: str, sub: str, *, actor_id=None, label=None) -> ExternalIdentity:
    """Link an existing OQB user to a typed or roster ``(issuer, sub)`` (caller
    commits). A non-primary issuer's ``sub`` must be on its allowlist. Levels
    come from the roster when the feed has listed the sub."""
    sub = canonical_uuid((sub or '').strip().lower())
    if sub is None:
        raise LinkError('bad_sub', 'The SMS sub must be a UUID.')
    row = SmsTrustIssuer.query.filter_by(issuer=issuer).first()
    if row is None:
        raise LinkError('unknown_issuer', 'That issuer is not on the trust list.')
    if not row.is_primary and sub not in {i['sub'] for i in sms_trust.allowed_sub_items(row)}:
        raise LinkError('sub_not_allowed', 'That sub is not on this issuer\'s test-account list.')
    if user is None:
        raise LinkError('no_user', 'Choose an OQB account.')
    if ExternalIdentity.query.filter_by(issuer=issuer, sub=sub).first() is not None:
        raise LinkError(sms_linking.ALREADY_LINKED, 'That SMS account is already linked to an OQB account.')
    refusal = sms_linking.link_refusal(user, issuer, linked_issuers(user.id))
    if refusal == sms_linking.PRIVILEGED:
        raise LinkError(refusal, 'Super admin and local-only accounts cannot be linked to SMS.')
    if refusal:
        raise LinkError(refusal, 'This OQB account is already linked for that issuer.')
    identity = ExternalIdentity(user_id=user.id, issuer=issuer, sub=sub, link_method=METHOD_ADMIN,
                                linked_at=sms_clock.utcnow(), linked_by_user_id=actor_id)
    db.session.add(identity)
    db.session.flush()
    roster = SmsRoster.query.filter_by(issuer=issuer, sub=sub).first()
    if roster is not None and roster.active:
        default, subjects = _cap_for(issuer, *roster_levels(roster))
        replace_levels(user.id, issuer, default, subjects, 'snapshot')
        _refresh_profile(user, name=roster.name, name_zh=roster.name_zh, email=roster.email,
                         staff_code=roster.staff_code)
    auth_audit.record(auth_audit.LINK, 'ok', METHOD_ADMIN, issuer=issuer, sub=sub, user_id=user.id,
                      actor_id=actor_id, detail={'label': label, 'from_roster': roster is not None})
    return identity


def unlink(user: User, issuer: str, *, actor_id=None) -> bool:
    """Remove the link and its SMS levels; end the user's sessions (caller commits)."""
    identity = ExternalIdentity.query.filter_by(user_id=user.id, issuer=issuer).first()
    if identity is None:
        return False
    sub = identity.sub
    db.session.delete(identity)
    UserSmsLevel.query.filter_by(user_id=user.id, issuer=issuer).delete(synchronize_session=False)
    user.bump_session_version()
    auth_audit.record(auth_audit.UNLINK, 'ok', issuer=issuer, sub=sub, user_id=user.id, actor_id=actor_id)
    return True


def linked_users(issuer: str) -> list:
    """``[(identity, user)]`` for an issuer, e.g. the test accounts at window close."""
    return (db.session.query(ExternalIdentity, User).join(User, User.id == ExternalIdentity.user_id)
            .filter(ExternalIdentity.issuer == issuer).order_by(User.username).all())


# ---------------------------------------------------------------- housekeeping

_last_purge = {'at': 0.0}


def purge(now: int | None = None) -> int:
    """Drop jti rows older than a day (and past ``exp + 30``) and dead pending
    links. Commits."""
    now = sms_clock.now() if now is None else now
    cutoff = sms_clock.to_datetime(now - JTI_KEEP_SECONDS)
    n = (SsoUsedJti.query.filter(SsoUsedJti.used_at < cutoff, SsoUsedJti.exp + 30 < now)
         .delete(synchronize_session=False))
    n += (SsoPendingLink.query.filter(db.or_(SsoPendingLink.expires_at < sms_clock.to_datetime(now),
                                             SsoPendingLink.consumed_at.isnot(None)))
          .delete(synchronize_session=False))
    db.session.commit()
    return n


def maybe_purge() -> None:
    """At most every ``PURGE_EVERY_SECONDS`` per process; never raises."""
    if time.monotonic() - _last_purge['at'] < PURGE_EVERY_SECONDS:
        return
    _last_purge['at'] = time.monotonic()
    try:
        purge()
    except Exception as exc:
        db.session.rollback()
        log.error('sso: purge failed (%s)', exc.__class__.__name__)

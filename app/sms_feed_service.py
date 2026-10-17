"""Apply SMS directory snapshots (contract v1.2 section 10;
docs/modules/sms-sso.md).

``apply_snapshot``, ``approve_held`` and ``discard_held`` are transaction
boundaries: they commit, or roll back and audit. The verified snapshot comes
from ``sms_verify.verify_snapshot`` (primary issuer only, signature, times,
claims). Pushes for one issuer are serialised by ``SELECT ... FOR UPDATE`` on
its ``sms_feed_state`` row; ``iat`` must grow and the jti is single-use.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError, OperationalError

from app import auth_audit, db, sms_clock
from app.models import (ExternalIdentity, SmsFeedState, SmsRoster, SmsSnapshot,
                        SmsSubjectCatalogue, SmsSubjectMap, SsoUsedJti, User, UserSmsLevel)
from app.sms_sso_service import DEPROVISIONED, _cap_for, _refresh_profile, replace_levels
from app.sms_verify import PURPOSE_SNAPSHOT, VerifiedSnapshot

log = logging.getLogger(__name__)

# sms_snapshots.status
APPLIED, HELD, APPROVED, DISCARDED, SUPERSEDED = 'applied', 'held', 'approved', 'discarded', 'superseded'
SNAPSHOT_KEEP_DAYS = 90


@dataclass
class FeedResult:
    status: int
    body: dict


class FeedError(ValueError):
    """A held-snapshot decision that cannot be made (``str(exc)`` is English)."""


def brake_threshold(linked: int, minimum: int, pct: int) -> float:
    """Deactivating more than this many linked users holds the snapshot."""
    return max(float(minimum), linked * pct / 100.0)


# ---------------------------------------------------------------- helpers

def _ensure_state(issuer: str) -> None:
    """Create the issuer's state row in its own transaction, so the
    ``FOR UPDATE`` below always locks an existing row (two first pushes
    locking a missing row take gap locks and deadlock)."""
    from sqlalchemy.orm import Session
    with Session(db.engine) as s:
        if s.get(SmsFeedState, issuer) is not None:
            return
        s.add(SmsFeedState(issuer=issuer, last_iat=0, updated_at=sms_clock.utcnow()))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()


def _lock_state(issuer: str) -> SmsFeedState:
    _ensure_state(issuer)
    return SmsFeedState.query.filter_by(issuer=issuer).with_for_update().populate_existing().one()


def _stale(snap: VerifiedSnapshot, ip) -> FeedResult:
    db.session.rollback()
    auth_audit.record_now(auth_audit.FEED_SNAPSHOT, 'fail', 'stale_snapshot', issuer=snap.issuer,
                          ip=ip, detail={'snapshot_id': snap.snapshot_id, 'iat': snap.iat})
    return FeedResult(409, {'status': 'stale'})


def _store_catalogue(snap: VerifiedSnapshot, now) -> None:
    for c in snap.catalogue:
        row = db.session.get(SmsSubjectCatalogue, c.uuid)
        if row is None:
            row = SmsSubjectCatalogue(sms_uuid=c.uuid, first_seen_at=now)
            db.session.add(row)
        row.code, row.name_en, row.name_zh, row.is_active = c.code, c.name_en, c.name_zh, c.is_active
        row.seen_in, row.issuer, row.last_seen_at = 'snapshot', snap.issuer, now


def _store_roster(snap: VerifiedSnapshot, now) -> None:
    """The roster is the latest snapshot only: replace the issuer's rows."""
    SmsRoster.query.filter_by(issuer=snap.issuer).delete(synchronize_session=False)
    db.session.add_all(SmsRoster(
        issuer=snap.issuer, sub=u.sub, active=u.active, email=u.email, name=u.name,
        name_zh=u.name_zh, staff_code=u.staff_code, default_level=u.sms_default_level,
        subjects_json=json.dumps(list(u.sms_subjects), ensure_ascii=False),
        snapshot_id=snap.snapshot_id, updated_at=now) for u in snap.users)


def _unmapped(snap: VerifiedSnapshot) -> list:
    """SMS subjects in use (granted to someone, or active in the catalogue)
    that map to no OQB subject."""
    mapped = {u for (u,) in db.session.query(SmsSubjectMap.sms_uuid).distinct()}
    used = {s.uuid for u in snap.users for s in u.subjects}
    used |= {c.uuid for c in snap.catalogue if c.is_active}
    return sorted(used - mapped)


def _deprovision(user: User, issuer: str, *, sub, ip, actor_id=None, why='snapshot') -> bool:
    """Drop every SMS-sourced level; disable an active account as
    ``sms_deprovisioned`` and end its sessions. True when it was active."""
    UserSmsLevel.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    if not user.is_active:
        return False
    user.active, user.disabled_reason, user.disabled_at = False, DEPROVISIONED, sms_clock.utcnow()
    user.bump_session_version()
    auth_audit.record(auth_audit.DEPROVISION, 'ok', why, issuer=issuer, sub=sub, user_id=user.id,
                      actor_id=actor_id, ip=ip)
    return True


def _links(issuer: str) -> list:
    return (db.session.query(ExternalIdentity, User).join(User, User.id == ExternalIdentity.user_id)
            .filter(ExternalIdentity.issuer == issuer).all())


# ---------------------------------------------------------------- apply

def apply_snapshot(snap: VerifiedSnapshot, *, brake_min: int = 5, brake_pct: int = 10,
                   ip=None) -> FeedResult:
    """Section 10 "Applying the snapshot" in one transaction."""
    try:
        state = _lock_state(snap.issuer)
        if snap.iat <= int(state.last_iat or 0):
            return _stale(snap, ip)
        db.session.add(SsoUsedJti(issuer=snap.issuer, jti=snap.jti, purpose=PURPOSE_SNAPSHOT,
                                  exp=snap.exp, used_at=sms_clock.utcnow()))
        db.session.flush()
    except IntegrityError:
        return _stale(snap, ip)
    except OperationalError as exc:  # lock wait / deadlock with a concurrent push
        db.session.rollback()
        log.error('sms feed: could not lock the feed state (%s)', exc.__class__.__name__)
        return FeedResult(503, {'status': 'busy'})
    try:
        return _apply(snap, state, brake_min=brake_min, brake_pct=brake_pct, ip=ip)
    except Exception as exc:
        db.session.rollback()
        log.error('sms feed: apply failed (%s)', exc.__class__.__name__)
        auth_audit.record_now(auth_audit.FEED_SNAPSHOT, 'fail', 'server_error', issuer=snap.issuer,
                              ip=ip, detail={'snapshot_id': snap.snapshot_id})
        return FeedResult(500, {'status': 'error'})


def _apply(snap, state, *, brake_min, brake_pct, ip) -> FeedResult:
    now = sms_clock.utcnow()
    _store_catalogue(snap, now)
    _store_roster(snap, now)
    present = {u.sub: u for u in snap.users}
    invalid = {s.lower() for _i, s, _why in snap.invalid_entries if s}
    links = _links(snap.issuer)
    leaving = [(i, u) for i, u in links
               if i.sub in present and not present[i.sub].active and u.is_active and not u.is_privileged]
    held = len(leaving) > brake_threshold(len(links), brake_min, brake_pct)
    counts = {'linked': len(links), 'updated': 0, 'deactivated': 0, 'reactivated': 0,
              'held': len(leaving) if held else 0, 'missing': 0, 'invalid_entries': len(snap.invalid_entries),
              'skipped_privileged': 0}

    for identity, user in links:
        entry = present.get(identity.sub)
        if entry is None:
            if identity.sub in invalid:
                continue  # unchanged; counted in invalid_entries
            counts['missing'] += 1
            if identity.missing_since is None:
                identity.missing_since = now
                auth_audit.record(auth_audit.FEED_MISSING, 'info', 'missing_from_snapshot',
                                  issuer=snap.issuer, sub=identity.sub, user_id=user.id, ip=ip,
                                  detail={'snapshot_id': snap.snapshot_id}, alert=True)
            continue
        identity.missing_since = None
        identity.last_seen_snapshot_at = now
        if user.is_privileged:
            counts['skipped_privileged'] += 1
            continue
        _refresh_profile(user, name=entry.name, name_zh=entry.name_zh, email=entry.email,
                         staff_code=entry.staff_code)
        if not entry.active:
            if held and user.is_active:
                continue
            if _deprovision(user, snap.issuer, sub=identity.sub, ip=ip):
                counts['deactivated'] += 1
            continue
        if not user.is_active and user.disabled_reason == DEPROVISIONED:
            user.active, user.disabled_reason, user.disabled_at = True, None, None
            auth_audit.record(auth_audit.REACTIVATE, 'ok', 'snapshot', issuer=snap.issuer,
                              sub=identity.sub, user_id=user.id, ip=ip)
            counts['reactivated'] += 1
        default, subjects = _cap_for(snap.issuer, entry.default_level,
                                     [(s.uuid, s.code, s.level) for s in entry.subjects])
        replace_levels(user.id, snap.issuer, default, subjects, 'snapshot')
        counts['updated'] += 1

    unmapped = _unmapped(snap)
    (SmsSnapshot.query.filter_by(issuer=snap.issuer, status=HELD)
     .update({'status': SUPERSEDED}, synchronize_session=False))
    db.session.add(SmsSnapshot(
        issuer=snap.issuer, snapshot_id=snap.snapshot_id, jti=snap.jti, iat=snap.iat,
        received_at=now, status=HELD if held else APPLIED, counts_json=json.dumps(counts),
        held_subs_json=json.dumps(sorted(i.sub for i, _u in leaving)) if held else None,
        unmapped_json=json.dumps(unmapped)))
    state.last_iat, state.last_snapshot_id, state.updated_at = snap.iat, snap.snapshot_id, now
    detail = dict(counts, snapshot_id=snap.snapshot_id, unmapped=len(unmapped))
    auth_audit.record(auth_audit.FEED_SNAPSHOT, 'ok', HELD if held else APPLIED, issuer=snap.issuer,
                      ip=ip, detail=detail)
    if held:
        auth_audit.record(auth_audit.FEED_HELD, 'info', 'safety_brake', issuer=snap.issuer, ip=ip,
                          detail=detail, alert=True)
    db.session.commit()

    body = {'status': HELD if held else APPLIED, 'snapshot_id': snap.snapshot_id,
            'linked': counts['linked'], 'deactivated': counts['deactivated'],
            'reactivated': counts['reactivated'], 'unmapped_subjects': unmapped,
            'missing': counts['missing'], 'invalid_entries': counts['invalid_entries']}
    if held:
        body['held'] = counts['held']
    return FeedResult(202 if held else 200, body)


# ---------------------------------------------------------------- held snapshots

def held_snapshots(issuer: str | None = None) -> list:
    q = SmsSnapshot.query.filter_by(status=HELD)
    if issuer:
        q = q.filter_by(issuer=issuer)
    return q.order_by(SmsSnapshot.iat.desc()).all()


def _held_row(snapshot_row_id: int) -> SmsSnapshot:
    row = db.session.get(SmsSnapshot, snapshot_row_id)
    if row is None or row.status != HELD:
        raise FeedError('That snapshot is no longer held.')
    state = _lock_state(row.issuer)
    if state.last_snapshot_id != row.snapshot_id:
        raise FeedError('A newer snapshot has arrived; review that one instead.')
    return row


def approve_held(snapshot_row_id: int, *, actor_id=None, ip=None) -> int:
    """Apply the deactivations the brake held, for subs the latest roster
    still lists as inactive. Commits; returns how many accounts were disabled."""
    try:
        row = _held_row(snapshot_row_id)
        subs = json.loads(row.held_subs_json or '[]')
        inactive = {r.sub for r in SmsRoster.query.filter_by(issuer=row.issuer, active=False)
                    .filter(SmsRoster.sub.in_(subs or ['']))}
        done = 0
        for identity, user in _links(row.issuer):
            if identity.sub in inactive and not user.is_privileged:
                if _deprovision(user, row.issuer, sub=identity.sub, ip=ip, actor_id=actor_id,
                                why='held_approved'):
                    done += 1
        row.status, row.decided_by_user_id, row.decided_at = APPROVED, actor_id, sms_clock.utcnow()
        auth_audit.record(auth_audit.FEED_DECISION, 'ok', 'approve', issuer=row.issuer,
                          actor_id=actor_id, ip=ip,
                          detail={'snapshot_id': row.snapshot_id, 'deactivated': done})
        db.session.commit()
        return done
    except Exception:
        db.session.rollback()
        raise


def discard_held(snapshot_row_id: int, *, actor_id=None, ip=None) -> None:
    """Keep the held accounts active. Commits. The next snapshot that still
    lists them as inactive is held again."""
    try:
        row = _held_row(snapshot_row_id)
        row.status, row.decided_by_user_id, row.decided_at = DISCARDED, actor_id, sms_clock.utcnow()
        auth_audit.record(auth_audit.FEED_DECISION, 'ok', 'discard', issuer=row.issuer,
                          actor_id=actor_id, ip=ip, detail={'snapshot_id': row.snapshot_id})
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def purge_snapshots(now: int | None = None) -> int:
    """Drop decided / applied snapshot records older than ``SNAPSHOT_KEEP_DAYS``
    (held ones stay until decided). The caller commits."""
    now = sms_clock.now() if now is None else now
    cutoff = sms_clock.to_datetime(now - SNAPSHOT_KEEP_DAYS * 86400)
    return (SmsSnapshot.query.filter(SmsSnapshot.received_at < cutoff, SmsSnapshot.status != HELD)
            .delete(synchronize_session=False))

"""Effective per-subject access levels: the one function every route, search
and helper uses (docs/core/02-auth-and-permissions.md, ADR-019).

Level on OQB subject S = the highest of
  * the local grant on S (``user_subject_permissions``),
  * the SMS ``default_level`` (every subject),
  * the level of every SMS subject mapped to S,
with the issuer's level cap applied. Super admin (``users.is_super_admin``,
local only) is ``admin`` everywhere. Levels are read from the DB once per
request and cached on ``flask.g``; never stored in the session.
"""
from __future__ import annotations

from typing import Iterable, Mapping

import logging

from flask import abort, current_app, g, has_app_context, has_request_context, jsonify, request
from flask_login import current_user

from app import sms_levels

log = logging.getLogger(__name__)

# OQB level names (``user_subject_permissions.role``, ``user_sms_levels.level``).
LEVEL_RANK = {'none': 0, 'viewer': 1, 'user': 2, 'admin': 3}
RANK_LEVEL = {1: 'viewer', 2: 'user', 3: 'admin'}

# Non-primary issuers (the cap itself is defined in app/sms_levels.py).
CAP_DEFAULT = LEVEL_RANK[sms_levels.oqb_cap('default')]
CAP_SUBJECT = LEVEL_RANK[sms_levels.oqb_cap('subject')]

_CACHE_ATTR = '_oqb_authz_levels'


def rank(level) -> int:
    return LEVEL_RANK.get(level or 'none', 0)


def compute_levels(*, local: Mapping[str, str],
                   sms_defaults: Iterable[tuple[str, str]] = (),
                   sms_subjects: Iterable[tuple[str, str, str]] = (),
                   mapping: Mapping[str, Iterable[str]] | None = None,
                   all_subjects: Iterable[str] = (),
                   issuer_caps: Mapping[str, bool] | None = None) -> dict[str, int]:
    """Pure level math. ``sms_defaults`` are ``(issuer, level)``,
    ``sms_subjects`` ``(issuer, sms_uuid, level)``, ``mapping`` maps an SMS
    uuid to OQB subject ids. ``issuer_caps`` lists the issuers whose rows
    count (``True`` = capped non-primary); rows from any other issuer are
    ignored. Returns ``{subject_id: rank}`` for ranks above ``none``."""
    caps = issuer_caps or {}
    mapping = mapping or {}
    out: dict[str, int] = {}

    def bump(sid, r):
        if r > out.get(sid, 0):
            out[sid] = r

    for sid, level in local.items():
        bump(sid, rank(level))
    subjects = list(all_subjects)
    for issuer, level in sms_defaults:
        if issuer not in caps:
            continue
        r = rank(level)
        if caps[issuer]:
            r = min(r, CAP_DEFAULT)
        if r:
            for sid in subjects:
                bump(sid, r)
    for issuer, uuid, level in sms_subjects:
        if issuer not in caps:
            continue
        r = rank(level)
        if caps[issuer]:
            r = min(r, CAP_SUBJECT)
        for sid in mapping.get(uuid, ()):
            bump(sid, r)
    return {sid: r for sid, r in out.items() if r > 0}


def sms_levels_count() -> bool:
    """SMS-sourced levels count only once the schema is in place and SMS
    sign-in is switched on (a pilot feed must not change anyone's access)."""
    if not has_app_context():
        return False
    cfg = current_app.config
    return bool(cfg.get('SMS_SCHEMA_READY') and cfg.get('SMS_SSO_ENABLED'))


def _sms_inputs(user) -> dict:
    """SMS-sourced inputs for ``compute_levels`` (``user_sms_levels`` rows,
    the trust list's issuer caps and the confirmed subject map). Any failure
    leaves the user with local grants only."""
    if not sms_levels_count():
        return {}
    try:
        from app import db, sms_clock, sms_trust
        from app.models import SmsSubjectMap, Subject, UserSmsLevel
        rows = (db.session.query(UserSmsLevel.issuer, UserSmsLevel.sms_subject_uuid,
                                 UserSmsLevel.level)
                .filter(UserSmsLevel.user_id == user.id).all())
        if not rows:
            return {}
        defaults = [(iss, level) for iss, uid, level in rows if not uid]
        subjects = [(iss, uid, level) for iss, uid, level in rows if uid]
        mapping: dict[str, list[str]] = {}
        if subjects:
            uuids = {uid for _, uid, _ in subjects}
            for uid, sid in (db.session.query(SmsSubjectMap.sms_uuid, SmsSubjectMap.oqb_subject_id)
                             .filter(SmsSubjectMap.sms_uuid.in_(uuids))):
                mapping.setdefault(uid, []).append(sid)
        all_subjects = [sid for (sid,) in db.session.query(Subject.id)] if defaults else []
        caps = sms_trust.load_trust_list().issuer_caps(sms_clock.now())
        return {'sms_defaults': defaults, 'sms_subjects': subjects, 'mapping': mapping,
                'all_subjects': all_subjects, 'issuer_caps': caps}
    except Exception as exc:
        log.error('authz: SMS levels unavailable (%s); local grants only', exc.__class__.__name__)
        return {}


def _load_levels(user) -> dict[str, str]:
    from app import db
    from app.models import Subject, UserSubjectPermission
    if not getattr(user, 'is_active', True):
        return {}
    if user.is_super_admin:
        return {sid: 'admin' for (sid,) in db.session.query(Subject.id)}
    local = dict(
        db.session.query(UserSubjectPermission.subject_id, UserSubjectPermission.role)
        .filter(UserSubjectPermission.user_id == user.id)
    )
    ranks = compute_levels(local=local, **_sms_inputs(user))
    return {sid: RANK_LEVEL[r] for sid, r in ranks.items()}


def _resolve(user):
    if user is None:
        user = current_user
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return user


def effective_levels(user=None) -> dict[str, str]:
    """``{subject_id: 'viewer'|'user'|'admin'}``. Do not mutate the result."""
    user = _resolve(user)
    if user is None:
        return {}
    cache = None
    if has_app_context():
        cache = g.get(_CACHE_ATTR)
        if cache is None:
            cache = {}
            setattr(g, _CACHE_ATTR, cache)
        if user.id in cache:
            return cache[user.id]
    levels = _load_levels(user)
    if cache is not None:
        cache[user.id] = levels
    return levels


def invalidate(user=None) -> None:
    if not has_app_context():
        return
    cache = g.get(_CACHE_ATTR)
    if not cache:
        return
    if user is None:
        cache.clear()
    else:
        cache.pop(getattr(user, 'id', None), None)


def is_super_admin(user=None) -> bool:
    user = _resolve(user)
    return bool(user is not None and user.is_super_admin and getattr(user, 'is_active', True))


def effective_level(user, subject_id) -> str | None:
    if not subject_id:
        return None
    return effective_levels(user).get(str(subject_id))


def has_level(user, subject_id, minimum: str) -> bool:
    """False for an empty subject id: callers that cannot name a subject are refused."""
    if not subject_id:
        return False
    if is_super_admin(user):
        return True
    return rank(effective_level(user, subject_id)) >= rank(minimum)


def subjects_with_level(user, minimum: str) -> list[str]:
    need = rank(minimum)
    return [sid for sid, level in effective_levels(user).items() if rank(level) >= need]


def any_level(user, minimum: str) -> bool:
    if is_super_admin(user):
        return True
    need = rank(minimum)
    return any(rank(level) >= need for level in effective_levels(user).values())


# ---------------------------------------------------------------- route edge

def wants_json() -> bool:
    """API-style request (fetch / XHR / JSON body) rather than a page load."""
    if not has_request_context():
        return False
    if request.is_json or '/api/' in request.path:
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('HX-Request'):
        return True
    mode = request.headers.get('Sec-Fetch-Mode')
    if mode and mode != 'navigate':
        return True
    best = request.accept_mimetypes.best
    return best == 'application/json'


def deny_payload(message: str) -> dict:
    """Both envelope families: modal routes read ``message``, data APIs ``error``."""
    return {'success': False, 'error': message, 'message': message}


def deny(message: str | None = None, status: int = 403):
    """Abort with ``status``; JSON envelope for APIs, the HTML error page otherwise."""
    from app.i18n import t as _t
    if wants_json():
        text = message or _t('You do not have permission to do that.')
        response = jsonify(deny_payload(text))
        response.status_code = status
        abort(response)
    abort(status)


def require_subject(subject_id, minimum: str) -> None:
    if not has_level(current_user, subject_id, minimum):
        deny()


def require_subjects(subject_ids: Iterable[str], minimum: str) -> None:
    ids = [s for s in subject_ids if s]
    if not ids:
        deny()
    for sid in ids:
        require_subject(sid, minimum)


def taxonomy_subjects(model, ids) -> set[str]:
    """Distinct subject ids owning the given Topic / Chapter ids, or Subtopic /
    Subchapter ids via their parent."""
    from app import db
    from app.models import Chapter, Subchapter, Subtopic, Topic
    ids = [int(i) for i in ids if str(i).strip().lstrip('-').isdigit()]
    if not ids:
        return set()
    if model is Subtopic:
        q = db.session.query(Topic.subject_id).join(Subtopic, Subtopic.topic_id == Topic.id)
        q = q.filter(Subtopic.id.in_(ids))
    elif model is Subchapter:
        q = db.session.query(Chapter.subject_id).join(Subchapter, Subchapter.chapter_id == Chapter.id)
        q = q.filter(Subchapter.id.in_(ids))
    else:
        q = db.session.query(model.subject_id).filter(model.id.in_(ids))
    return {row[0] for row in q.distinct()}


def require_taxonomy(model, ids, minimum: str = 'admin') -> set[str]:
    """Every subject owning ``ids`` must be at ``minimum``; ids that resolve to
    no subject at all are refused. Returns the subject set."""
    subjects = taxonomy_subjects(model, ids)
    require_subjects(subjects, minimum)
    return subjects


def path_in_subjects(rel: str, subject_ids) -> bool:
    """True when the first segment of ``rel`` names one of ``subject_ids``
    (case-insensitive, matching Windows folder names). Pure."""
    parts = [p for p in str(rel or '').replace('\\', '/').split('/') if p]
    if not parts or parts[0] in ('.', '..'):
        return False
    wanted = {str(s).casefold() for s in subject_ids or ()}
    return parts[0].casefold() in wanted


def pdf_source_allowed(rel: str, user=None, minimum: str = 'admin') -> bool:
    """Legacy ``PDF_SOURCE_PATH`` access: a super admin reads the whole
    folder; anyone else only ``<subject>/...`` for subjects at ``minimum``."""
    if is_super_admin(user):
        return True
    return path_in_subjects(rel, subjects_with_level(user, minimum))


def current_user_id() -> int | None:
    """The signed-in user's id inside a request; ``None`` in threads / CLI."""
    if not has_request_context():
        return None
    try:
        return current_user.id if current_user.is_authenticated else None
    except Exception:
        return None


def owns(owner_id) -> bool:
    """Owner-bound staging and jobs: the owner or a super admin. Rows that
    predate owner tracking (``owner_id`` None) are super admin only."""
    if is_super_admin():
        return True
    uid = current_user_id()
    return owner_id is not None and uid is not None and int(owner_id) == int(uid)


def scope_query(query, column, minimum: str = 'viewer'):
    """Clamp ``query`` to the current user's subjects at ``minimum``."""
    if is_super_admin():
        return query
    return query.filter(column.in_(subjects_with_level(current_user, minimum)))

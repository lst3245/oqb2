"""Admin → Users table: search, filters, paging, access summary.

The page lists one row per user. Per-subject permission controls are not
rendered here; they load on demand from the permissions panel.

Effective access uses ``authz.compute_levels`` (the same math as
``authz.effective_levels``): local grants, SMS default and mapped subject
levels, issuer caps. An inactive account has no effective access. An active
super admin is admin on every subject.

Filters (query args):

* ``q`` — case-insensitive substring of username, display_name, name_zh,
  email or staff_code (SQL ``LOWER`` + ``LIKE``).
* ``status`` — ``active`` (default; disabled accounts are hidden),
  ``disabled``, or ``all``. Anything else is ``active``.
* ``source`` — ``all`` (default), ``local`` (no ``external_identities`` row),
  ``sms`` (any identity), ``jit`` (an identity whose ``link_method`` is
  ``jit``). JIT is written only when SMS auto-creates the account
  (``sms_sso_service._jit_user``). It stays until that identity is removed;
  a later admin re-link is ``admin``, so the account is then SMS-linked but
  not JIT. There is no separate created-by column. When the SMS tables are
  not ready, ``local`` is everyone and ``sms`` / ``jit`` match nobody.
* ``super`` — ``1`` / ``true`` / ``on`` / ``yes`` keeps super admins only.
* ``subject`` — users whose *effective* level on that subject is ``user`` or
  ``admin`` (includes active super admins). Viewers do not match. Disabled
  accounts do not match, even if a local grant is still stored. Applied in
  Python over the SQL-filtered set after one bulk load (not per user).
* ``page`` — 25 rows. Out of range clamps to the last page; below 1 and
  non-integers become page 1.

Unknown query args are ignored.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from types import SimpleNamespace

from flask import current_app, url_for
from sqlalchemy import false, func, or_, select

from app import authz, db
from app.i18n import t as _t
from app.models import (
    UNUSABLE_PASSWORD_PREFIX, ExternalIdentity, SmsSubjectMap, Subject, User,
    UserSmsLevel, UserSubjectPermission,
)
from app.utils import utc_iso

log = logging.getLogger(__name__)

PAGE_SIZE = 25
_LIST_LIMIT = 8
_VIEWER_FOLD = 3


def parse_filters(args) -> dict:
    """Normalise the users-page query string. Invalid values fall back."""
    q = (args.get('q') or '').strip()
    status = (args.get('status') or '').strip().lower()
    if status not in ('active', 'disabled', 'all'):
        status = 'active'
    source = (args.get('source') or '').strip().lower()
    if source not in ('all', 'local', 'sms', 'jit'):
        source = 'all'
    try:
        page = int(args.get('page') or 1)
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    return {
        'q': q,
        'status': status,
        'source': source,
        'super_only': str(args.get('super') or '').lower() in ('1', 'true', 'on', 'yes'),
        'subject': (args.get('subject') or '').strip(),
        'page': page,
    }


def has_usable_password(user) -> bool:
    """A local password hash, not the SMS JIT ``!`` sentinel."""
    hashed = user.password_hash or ''
    return bool(hashed) and not hashed.startswith(UNUSABLE_PASSWORD_PREFIX)


def summarize(levels: dict, subject_ids, *, super_admin: bool, active: bool) -> str:
    """Compact effective-access line. ``levels`` is ``{subject_id: level}``."""
    if super_admin and active:
        return _t('Super admin: all')
    if not levels:
        return _t('No access')
    ids = list(subject_ids)
    groups = {'admin': [], 'user': [], 'viewer': []}
    seen = set(ids)
    for sid in ids:
        level = levels.get(sid)
        if level in groups:
            groups[level].append(sid)
    for sid in sorted(set(levels) - seen):
        level = levels.get(sid)
        if level in groups:
            groups[level].append(sid)
    covered = set(ids)
    if covered and set(groups['viewer']) == covered and not groups['admin'] and not groups['user']:
        return _t('Viewer: all subjects')
    if covered and set(groups['user']) == covered and not groups['admin'] and not groups['viewer']:
        return _t('User: all subjects')
    if covered and set(groups['admin']) == covered and not groups['user'] and not groups['viewer']:
        return _t('Admin: all subjects')
    parts = []
    higher = bool(groups['admin'] or groups['user'])
    for level, label in (('admin', 'Admin: {names}'), ('user', 'User: {names}'),
                         ('viewer', 'Viewer: {names}')):
        named = groups[level]
        if not named:
            continue
        if level == 'viewer' and higher and len(named) > _VIEWER_FOLD:
            parts.append(_t('Viewer: {n} others', n=len(named)))
            continue
        parts.append(_t(label, names=_name_list(named)))
    return ' | '.join(parts) if parts else _t('No access')


def _name_list(ids) -> str:
    if len(ids) <= _LIST_LIMIT:
        return ', '.join(ids)
    return _t('{names} + {n} more', names=', '.join(ids[:_LIST_LIMIT]), n=len(ids) - _LIST_LIMIT)


def listing(args) -> dict:
    """Template context for the users table (one page of rows)."""
    filters = parse_filters(args)
    query = _filtered_users(filters)
    if filters['subject']:
        users = query.all()
        bundle = _bundle([u.id for u in users])
        levels = {u.id: effective_for(u, bundle) for u in users}
        need = authz.rank('user')
        sid = filters['subject']
        users = [u for u in users if authz.rank(levels[u.id].get(sid)) >= need]
        total = len(users)
        page, pages = _clamp(filters['page'], total)
        chosen = users[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        level_of = levels
    else:
        total = query.order_by(None).count()
        page, pages = _clamp(filters['page'], total)
        chosen = (query.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all()
                  if total else [])
        bundle = _bundle([u.id for u in chosen])
        level_of = {u.id: effective_for(u, bundle) for u in chosen}
    filters['page'] = page
    start, end = _span(page, total)
    return {
        'rows': [pack(u, bundle, level_of[u.id]) for u in chosen],
        'subjects': bundle.subjects,
        'total': total,
        'page': page,
        'pages': pages,
        'start': start,
        'end': end,
        'filters': _view_filters(filters),
        'users_href': _href(filters),
        'page_numbers': _numbers(page, pages),
    }


def one_row(user) -> dict:
    bundle = _bundle([user.id])
    return pack(user, bundle, effective_for(user, bundle))


def permission_panel(user) -> dict:
    bundle = _bundle([user.id])
    return {
        'user': user,
        'subjects': bundle.subjects,
        'local': bundle.perms.get(user.id, {}),
        'effective': effective_for(user, bundle),
        'sms_only': sms_only_for(user, bundle),
        'sms_levels': bundle.sms_display.get(user.id, []),
    }


def effective_for(user, bundle) -> dict:
    """Same result as ``authz._load_levels``, from the bulk bundle."""
    if not getattr(user, 'is_active', True):
        return {}
    if user.is_super_admin:
        return {sid: 'admin' for sid in bundle.subject_ids}
    return _ranks_to_levels(_ranks(user, bundle, local=bundle.perms.get(user.id, {})))


def sms_only_for(user, bundle) -> dict:
    """SMS contribution alone, when SMS levels count. Empty otherwise."""
    if not authz.sms_levels_count():
        return {}
    return _ranks_to_levels(_ranks(user, bundle, local={}))


def pack(user, bundle, levels) -> dict:
    identities = bundle.identities.get(user.id, [])
    return {
        'user': user,
        'identities': identities,
        'summary': summarize(levels, bundle.subject_ids,
                             super_admin=bool(user.is_super_admin),
                             active=bool(user.is_active)),
        'meta': _meta(user),
        'login_iso': utc_iso(user.last_login_at) if user.last_login_at else None,
        'login_method': user.last_login_method or '',
        'can_reset_password': has_usable_password(user) and not identities,
        'missing_sms': any(getattr(i, 'missing_since', None) for i in identities),
    }


# ---------------------------------------------------------------- query

def _schema_ready() -> bool:
    return bool(current_app.config.get('SMS_SCHEMA_READY'))


def _like(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _filtered_users(filters):
    query = User.query
    if filters['status'] == 'active':
        query = query.filter(User.active.is_(True))
    elif filters['status'] == 'disabled':
        query = query.filter(User.active.is_(False))
    if filters['super_only']:
        query = query.filter(User.is_super_admin.is_(True))
    if filters['q']:
        needle = '%' + _like(filters['q'].lower()) + '%'
        cols = (User.username, User.display_name, User.name_zh, User.email, User.staff_code)
        query = query.filter(or_(*(func.lower(col).like(needle, escape='\\') for col in cols)))
    source = filters['source']
    if source != 'all':
        if not _schema_ready():
            if source != 'local':
                query = query.filter(false())
        else:
            linked = select(ExternalIdentity.id).where(ExternalIdentity.user_id == User.id)
            if source == 'local':
                query = query.filter(~linked.exists())
            elif source == 'sms':
                query = query.filter(linked.exists())
            else:
                from app.sms_sso_service import METHOD_JIT  # not a level
                jit = linked.where(ExternalIdentity.link_method == METHOD_JIT)
                query = query.filter(jit.exists())
    return query.order_by(User.username, User.id)


def _bundle(user_ids):
    subjects = Subject.query.order_by(Subject.id).all()
    subject_ids = [s.id for s in subjects]
    perms = defaultdict(dict)
    identities = defaultdict(list)
    sms_rows = defaultdict(list)
    sms_display = defaultdict(list)
    mapping = {}
    caps = {}
    if user_ids:
        for uid, sid, role in (db.session.query(
                UserSubjectPermission.user_id, UserSubjectPermission.subject_id,
                UserSubjectPermission.role)
                .filter(UserSubjectPermission.user_id.in_(user_ids))):
            perms[uid][sid] = role
        if _schema_ready():
            for ident in (ExternalIdentity.query
                          .filter(ExternalIdentity.user_id.in_(user_ids))
                          .order_by(ExternalIdentity.issuer)):
                identities[ident.user_id].append(ident)
            for row in (UserSmsLevel.query
                        .filter(UserSmsLevel.user_id.in_(user_ids))
                        .order_by(UserSmsLevel.issuer, UserSmsLevel.sms_subject_code)):
                sms_rows[row.user_id].append(row)
                sms_display[row.user_id].append(
                    (row.issuer, row.sms_subject_code or None, row.level))
            if authz.sms_levels_count() and sms_rows:
                try:
                    from app import sms_clock, sms_trust
                    caps = sms_trust.load_trust_list().issuer_caps(sms_clock.now())
                    uuids = {r.sms_subject_uuid for rows in sms_rows.values()
                             for r in rows if r.sms_subject_uuid}
                    if uuids:
                        for uid, sid in (db.session.query(
                                SmsSubjectMap.sms_uuid, SmsSubjectMap.oqb_subject_id)
                                .filter(SmsSubjectMap.sms_uuid.in_(uuids))):
                            mapping.setdefault(uid, []).append(sid)
                except Exception:
                    log.exception('users directory: SMS levels unavailable; local grants only')
                    caps, mapping = {}, {}
    return SimpleNamespace(
        subjects=subjects, subject_ids=subject_ids, perms=perms,
        identities=identities, sms_rows=sms_rows, sms_display=sms_display,
        mapping=mapping, caps=caps,
    )


def _ranks(user, bundle, *, local):
    extra = {}
    if authz.sms_levels_count():
        rows = bundle.sms_rows.get(user.id, [])
        if rows and bundle.caps:
            defaults = [(r.issuer, r.level) for r in rows if not r.sms_subject_uuid]
            subjects = [(r.issuer, r.sms_subject_uuid, r.level)
                        for r in rows if r.sms_subject_uuid]
            extra = {
                'sms_defaults': defaults,
                'sms_subjects': subjects,
                'mapping': bundle.mapping,
                'all_subjects': bundle.subject_ids if defaults else (),
                'issuer_caps': bundle.caps,
            }
    return authz.compute_levels(local=local, **extra)


def _ranks_to_levels(ranks) -> dict:
    return {sid: authz.RANK_LEVEL[rank] for sid, rank in ranks.items()}


def _meta(user) -> str:
    parts = []
    for value in (user.display_name, user.name_zh, user.email, user.staff_code):
        text = (value or '').strip()
        if text:
            parts.append(text)
    return ' · '.join(parts)


def _view_filters(filters) -> dict:
    return {
        'q': filters['q'],
        'status': filters['status'],
        'source': filters['source'],
        'super': filters['super_only'],
        'subject': filters['subject'],
    }


def _href(filters):
    def href(**over):
        cur = {
            'q': filters['q'],
            'status': filters['status'],
            'source': filters['source'],
            'super': filters['super_only'],
            'subject': filters['subject'],
            'page': filters['page'],
        }
        cur.update(over)
        params = {}
        if cur.get('q'):
            params['q'] = cur['q']
        if cur.get('status') not in (None, '', 'active'):
            params['status'] = cur['status']
        if cur.get('source') not in (None, '', 'all'):
            params['source'] = cur['source']
        if cur.get('super') in (True, 1, '1'):
            params['super'] = '1'
        if cur.get('subject'):
            params['subject'] = cur['subject']
        try:
            page = int(cur.get('page') or 1)
        except (TypeError, ValueError):
            page = 1
        if page > 1:
            params['page'] = page
        return url_for('admin.users', **params)
    return href


def _clamp(page, total):
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    if page > pages:
        page = pages
    if page < 1:
        page = 1
    return page, pages


def _span(page, total):
    if not total:
        return 0, 0
    start = (page - 1) * PAGE_SIZE + 1
    return start, min(page * PAGE_SIZE, total)


def _numbers(page, pages):
    if pages <= 9:
        return list(range(1, pages + 1))
    raw = [1]
    lo, hi = max(2, page - 2), min(pages - 1, page + 2)
    if lo > 2:
        raw.append(None)
    raw.extend(range(lo, hi + 1))
    if hi < pages - 1:
        raw.append(None)
    if pages > 1:
        raw.append(pages)
    return raw

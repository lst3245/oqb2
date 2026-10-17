"""Super-admin screens for SMS sign-in (docs/modules/sms-sso.md, stage 5):
overview, subject mapping, linking, snapshots, trust list, audit / alerts.

Every route is ``@login_required`` + ``@super_admin_required``. Pages are
server-rendered; every change is a POST form that redirects back with a
flash (POST / redirect / GET) and is audited. The work is done by the
services (``sms_trust``, ``sms_sso_service``, ``sms_feed_service``); this
module only parses forms and renders.
"""
from __future__ import annotations

import json
import logging

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from app import (auth_audit, db, sms_clock, sms_feed_service, sms_levels, sms_linking,
                 sms_sso_service, sms_trust, web_security)
from app.i18n import t as _t
from app.models import (AuthAuditLog, ExternalIdentity, SmsRoster, SmsSnapshot, SmsSubjectCatalogue,
                        SmsSubjectMap, SmsTrustIssuer, Subject, User, UserSmsLevel)
from app.sms_verify import canonical_uuid
from app.utils import super_admin_required

log = logging.getLogger(__name__)

sms_admin_bp = Blueprint('sms_admin', __name__, url_prefix='/admin/sms')

AUDIT_PAGE_SIZE = 100
SNAPSHOT_PAGE_SIZE = 50


# ---------------------------------------------------------------- helpers

def _ready() -> bool:
    return bool(current_app.config.get('SMS_SCHEMA_READY'))


def _require_ready() -> None:
    if not _ready():
        abort(503)


def _ip():
    return web_security.client_ip(request)


def _back(endpoint: str, **kw):
    return redirect(url_for(endpoint, **kw))


def _issuer_row(issuer_id: int) -> SmsTrustIssuer:
    row = db.session.get(SmsTrustIssuer, issuer_id)
    if row is None:
        abort(404)
    return row


def parse_allowed_subs(text: str) -> list:
    """One test account per line: ``<sub> [label]``. Blank lines and
    ``#`` comments are ignored; a sub that is not a UUID raises ValueError."""
    items = []
    for line in (text or '').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        sub, _, label = line.partition(' ')
        canon = canonical_uuid(sub.strip().lower())
        if canon is None:
            raise ValueError(sub)
        items.append({'sub': canon, 'label': label.strip() or None})
    return items


def _form_days(name: str):
    raw = (request.form.get(name) or '').strip()
    if not raw:
        return None
    try:
        days = float(raw)
    except ValueError:
        raise sms_trust.TrustEditError('the window length must be a number of days') from None
    if days <= 0:
        raise sms_trust.TrustEditError('the window length must be a number of days')
    return sms_clock.now() + int(days * 86400)


def _checked(name: str) -> bool:
    return request.form.get(name) in ('1', 'on', 'true', 'yes')


def open_alerts() -> int:
    """Unacknowledged alert rows (the nav badge); 0 when unavailable."""
    if not _ready():
        return 0
    try:
        return AuthAuditLog.query.filter(AuthAuditLog.is_alert.is_(True),
                                         AuthAuditLog.acknowledged_at.is_(None)).count()
    except Exception as exc:
        db.session.rollback()
        log.error('sms admin: alert count failed (%s)', exc.__class__.__name__)
        return 0


def break_glass_ok() -> bool:
    """An active super admin who can sign in with a password exists."""
    for u in User.query.filter(User.is_super_admin.is_(True)).all():
        if u.is_active and u.password_hash and not u.password_hash.startswith('!'):
            return True
    return False


def level_label(level) -> str:
    return _t(sms_levels.display(level))


def sms_levels_by_user(user_ids=None) -> dict:
    """``{user_id: [(issuer, code or None, level)]}`` for display only."""
    q = UserSmsLevel.query
    if user_ids is not None:
        q = q.filter(UserSmsLevel.user_id.in_(list(user_ids) or [0]))
    out = {}
    for r in q.order_by(UserSmsLevel.issuer, UserSmsLevel.sms_subject_code).all():
        out.setdefault(r.user_id, []).append((r.issuer, r.sms_subject_code or None, r.level))
    return out


def identities_by_user(user_ids=None) -> dict:
    q = ExternalIdentity.query
    if user_ids is not None:
        q = q.filter(ExternalIdentity.user_id.in_(list(user_ids) or [0]))
    out = {}
    for i in q.order_by(ExternalIdentity.issuer).all():
        out.setdefault(i.user_id, []).append(i)
    return out


@sms_admin_bp.app_context_processor
def _nav_globals():
    """``sms_alert_count()`` for the super-admin nav badge (lazy: one query
    only when a template calls it)."""
    def sms_alert_count():
        try:
            if current_user.is_authenticated and current_user.is_super_admin:
                return open_alerts()
        except Exception:
            return 0
        return 0
    return {'sms_alert_count': sms_alert_count, 'sms_level_label': level_label}


# ---------------------------------------------------------------- overview

@sms_admin_bp.route('/')
@login_required
@super_admin_required
def index():
    cfg = current_app.config
    ctx = {'schema_ready': _ready(), 'sso_enabled': bool(cfg.get('SMS_SSO_ENABLED')),
           'feed_enabled': bool(cfg.get('SMS_FEED_ENABLED')), 'jit_enabled': bool(cfg.get('SMS_JIT_ENABLED')),
           'public_origin': web_security.public_origin(), 'environment': sms_trust.environment()}
    if _ready():
        trust = sms_trust.load_trust_list()
        primary = trust.primary(sms_clock.now())
        mapped = {u for (u,) in db.session.query(SmsSubjectMap.sms_uuid).distinct()}
        ctx.update(
            bootstrap=sms_trust.bootstrap_in_use(), primary=primary,
            primary_keys=sorted(primary.keys) if primary else [],
            linked=ExternalIdentity.query.count(),
            unmapped=sum(1 for c in SmsSubjectCatalogue.query.filter_by(is_active=True).all()
                         if c.sms_uuid not in mapped),
            held=SmsSnapshot.query.filter_by(status=sms_feed_service.HELD).count(),
            alerts=open_alerts(), break_glass=break_glass_ok(),
            last_snapshot=SmsSnapshot.query.order_by(SmsSnapshot.id.desc()).first(),
            nonprimary_enabled=[e for e in trust.entries.values() if not e.primary and e.enabled],
        )
    return render_template('admin_sms/index.html', **ctx)


# ---------------------------------------------------------------- mapping

def _unmapped_uuids(mapped: set) -> list:
    """Active catalogue subjects plus any subject someone holds an SMS level
    for, with no mapping."""
    used = {c.sms_uuid for c in SmsSubjectCatalogue.query.filter_by(is_active=True)}
    used |= {u for (u,) in db.session.query(UserSmsLevel.sms_subject_uuid).distinct() if u}
    return sorted(used - mapped)


@sms_admin_bp.route('/mapping')
@login_required
@super_admin_required
def mapping():
    _require_ready()
    maps = SmsSubjectMap.query.order_by(SmsSubjectMap.sms_uuid, SmsSubjectMap.oqb_subject_id).all()
    by_uuid = {}
    for m in maps:
        by_uuid.setdefault(m.sms_uuid, []).append(m)
    catalogue = {c.sms_uuid: c for c in SmsSubjectCatalogue.query.all()}
    codes = {u: c for u, c in db.session.query(UserSmsLevel.sms_subject_uuid,
                                               UserSmsLevel.sms_subject_code).distinct() if u}
    uuids = sorted(set(catalogue) | set(by_uuid) | set(codes),
                   key=lambda u: ((catalogue[u].code if u in catalogue else codes.get(u)) or u).lower())
    rows = [{'uuid': u, 'cat': catalogue.get(u), 'code': (catalogue[u].code if u in catalogue else codes.get(u)),
             'maps': by_uuid.get(u, [])} for u in uuids]
    unmapped = set(_unmapped_uuids(set(by_uuid)))
    return render_template('admin_sms/mapping.html', rows=rows, unmapped=unmapped,
                           subjects=Subject.query.order_by(Subject.id).all())


@sms_admin_bp.route('/mapping/add', methods=['POST'])
@login_required
@super_admin_required
def mapping_add():
    _require_ready()
    uid = canonical_uuid((request.form.get('sms_uuid') or '').strip().lower())
    sid = (request.form.get('subject_id') or '').strip()
    if uid is None or db.session.get(Subject, sid) is None:
        flash(_t('Choose an SMS subject and an OQB subject.'), 'danger')
        return _back('sms_admin.mapping')
    if SmsSubjectMap.query.filter_by(sms_uuid=uid, oqb_subject_id=sid).first() is None:
        db.session.add(SmsSubjectMap(sms_uuid=uid, oqb_subject_id=sid, confirmed_by_user_id=current_user.id,
                                     confirmed_at=sms_clock.utcnow()))
        cat = db.session.get(SmsSubjectCatalogue, uid)
        auth_audit.record(auth_audit.MAPPING, 'ok', 'add', actor_id=current_user.id, ip=_ip(),
                          detail={'sms_uuid': uid, 'code': cat.code if cat else None, 'subject': sid})
        db.session.commit()
        flash(_t('Mapped to {subject}.', subject=sid), 'success')
    return _back('sms_admin.mapping')


@sms_admin_bp.route('/mapping/remove', methods=['POST'])
@login_required
@super_admin_required
def mapping_remove():
    _require_ready()
    uid = (request.form.get('sms_uuid') or '').strip().lower()
    sid = (request.form.get('subject_id') or '').strip()
    row = SmsSubjectMap.query.filter_by(sms_uuid=uid, oqb_subject_id=sid).first()
    if row is not None:
        db.session.delete(row)
        auth_audit.record(auth_audit.MAPPING, 'ok', 'remove', actor_id=current_user.id, ip=_ip(),
                          detail={'sms_uuid': uid, 'subject': sid})
        db.session.commit()
        flash(_t('Mapping to {subject} removed.', subject=sid), 'success')
    return _back('sms_admin.mapping')


# ---------------------------------------------------------------- linking

@sms_admin_bp.route('/linking')
@login_required
@super_admin_required
def linking():
    _require_ready()
    trust_rows = SmsTrustIssuer.query.order_by(SmsTrustIssuer.is_primary.desc(), SmsTrustIssuer.issuer).all()
    primary = next((r for r in trust_rows if r.is_primary), None)
    users = User.query.order_by(User.username).all()
    by_id = {u.id: u for u in users}
    links = ExternalIdentity.query.order_by(ExternalIdentity.issuer, ExternalIdentity.sub).all()
    linked_subs = {(i.issuer, i.sub): i for i in links}
    show = request.args.get('show', 'unlinked')
    roster = []
    if primary is not None:
        q = SmsRoster.query.filter_by(issuer=primary.issuer).order_by(SmsRoster.name)
        candidates = [u for u in users if u.is_active and not sms_linking.is_privileged(u)]
        linked_users = {i.user_id for i in links if i.issuer == primary.issuer}
        for r in q.all():
            identity = linked_subs.get((r.issuer, r.sub))
            if show == 'unlinked' and identity is not None:
                continue
            suggestions = []
            if identity is None and r.active:
                pool = [u for u in candidates if u.id not in linked_users]
                suggestions = sms_linking.suggest_users(
                    {'staff_code': r.staff_code, 'name': r.name, 'email': r.email}, pool)
            roster.append({'row': r, 'identity': identity,
                           'account': by_id.get(identity.user_id) if identity else None,
                           'suggestions': suggestions})
    allowed = {row.issuer: sms_trust.allowed_sub_items(row) for row in trust_rows if not row.is_primary}
    return render_template('admin_sms/linking.html', trust_rows=trust_rows, primary=primary,
                           roster=roster, links=links, users_by_id=by_id, show=show,
                           link_users=[u for u in users if not sms_linking.is_privileged(u)],
                           allowed=allowed)


@sms_admin_bp.route('/link', methods=['POST'])
@login_required
@super_admin_required
def link():
    _require_ready()
    issuer = (request.form.get('issuer') or '').strip()
    sub = (request.form.get('sub') or '').strip()
    label = (request.form.get('label') or '').strip() or None
    try:
        user_id = int(request.form.get('user_id') or 0)
    except ValueError:
        user_id = 0
    user = db.session.get(User, user_id) if user_id else None
    try:
        sms_sso_service.admin_link(user, issuer, sub, actor_id=current_user.id, label=label)
        db.session.commit()
    except sms_sso_service.LinkError as exc:
        db.session.rollback()
        auth_audit.record_now(auth_audit.LINK, 'fail', exc.reason, issuer=issuer[:191], sub=sub[:64],
                              user_id=user.id if user else None, actor_id=current_user.id, ip=_ip(),
                              detail={'method': sms_sso_service.METHOD_ADMIN})
        flash(_t(str(exc)), 'danger')
        return _back('sms_admin.linking', show=request.form.get('show', 'unlinked'))
    flash(_t('Linked {username} to the SMS account.', username=user.username), 'success')
    return _back('sms_admin.linking', show=request.form.get('show', 'unlinked'))


@sms_admin_bp.route('/unlink', methods=['POST'])
@login_required
@super_admin_required
def unlink():
    _require_ready()
    issuer = (request.form.get('issuer') or '').strip()
    try:
        user = db.session.get(User, int(request.form.get('user_id') or 0))
    except ValueError:
        user = None
    if user is not None and sms_sso_service.unlink(user, issuer, actor_id=current_user.id):
        db.session.commit()
        flash(_t('Unlinked {username}; their sessions have ended.', username=user.username), 'success')
    else:
        flash(_t('That link no longer exists.'), 'warning')
    nxt = request.form.get('next')
    if nxt == 'users':
        return _back('admin.users')
    return _back('sms_admin.linking', show=request.form.get('show', 'unlinked'))


# ---------------------------------------------------------------- snapshots

@sms_admin_bp.route('/snapshots')
@login_required
@super_admin_required
def snapshots():
    _require_ready()
    rows = SmsSnapshot.query.order_by(SmsSnapshot.id.desc()).limit(SNAPSHOT_PAGE_SIZE).all()
    held = []
    for r in rows:
        if r.status != sms_feed_service.HELD:
            continue
        subs = json.loads(r.held_subs_json or '[]')
        roster = {x.sub: x for x in SmsRoster.query.filter_by(issuer=r.issuer)
                  .filter(SmsRoster.sub.in_(subs or ['']))}
        ids = {i.sub: i for i in ExternalIdentity.query.filter_by(issuer=r.issuer)
               .filter(ExternalIdentity.sub.in_(subs or ['']))}
        users = {u.id: u for u in User.query.filter(User.id.in_([i.user_id for i in ids.values()] or [0]))}
        held.append({'row': r, 'people': [
            {'sub': s, 'roster': roster.get(s), 'account': users.get(ids[s].user_id) if s in ids else None}
            for s in subs]})
    counts = {r.id: json.loads(r.counts_json or '{}') for r in rows}
    unmapped = {r.id: len(json.loads(r.unmapped_json or '[]')) for r in rows}
    return render_template('admin_sms/snapshots.html', rows=rows, held=held, counts=counts,
                           unmapped=unmapped)


def _decide(snapshot_id: int, approve: bool):
    _require_ready()
    try:
        if approve:
            n = sms_feed_service.approve_held(snapshot_id, actor_id=current_user.id, ip=_ip())
            flash(_t('Approved: {n} account(s) disabled.', n=n), 'success')
        else:
            sms_feed_service.discard_held(snapshot_id, actor_id=current_user.id, ip=_ip())
            flash(_t('Discarded: the accounts stay active.'), 'success')
    except sms_feed_service.FeedError as exc:
        flash(_t(str(exc)), 'warning')
    return _back('sms_admin.snapshots')


@sms_admin_bp.route('/snapshots/<int:snapshot_id>/approve', methods=['POST'])
@login_required
@super_admin_required
def snapshot_approve(snapshot_id):
    return _decide(snapshot_id, True)


@sms_admin_bp.route('/snapshots/<int:snapshot_id>/discard', methods=['POST'])
@login_required
@super_admin_required
def snapshot_discard(snapshot_id):
    return _decide(snapshot_id, False)


# ---------------------------------------------------------------- trust list

@sms_admin_bp.route('/trust')
@login_required
@super_admin_required
def trust():
    _require_ready()
    rows = SmsTrustIssuer.query.order_by(SmsTrustIssuer.is_primary.desc(), SmsTrustIssuer.issuer).all()
    now = sms_clock.now()
    linked = {row.issuer: sms_sso_service.linked_users(row.issuer) for row in rows}
    loaded = sms_trust.load_trust_list()
    return render_template(
        'admin_sms/trust.html', rows=rows, linked=linked, now_dt=sms_clock.utcnow(),
        active={row.issuer: bool(loaded.get(row.issuer) and loaded.get(row.issuer).active(now)) for row in rows},
        allowed={row.issuer: sms_trust.allowed_sub_items(row) for row in rows},
        bootstrap=sms_trust.bootstrap_in_use(), max_days=sms_trust.NON_PRIMARY_MAX_DAYS,
        live_kid=sms_trust.LIVE_KID, live_fp=sms_trust.LIVE_FINGERPRINT,
        workshop_kid=sms_trust.WORKSHOP_KID, workshop_fp=sms_trust.WORKSHOP_FINGERPRINT)


def _trust_change(issuer_id: int, change):
    """Run ``change(row)`` (a service call), commit, flash; a refusal rolls back."""
    _require_ready()
    row = _issuer_row(issuer_id)
    try:
        message = change(row)
        db.session.commit()
    except (sms_trust.TrustEditError, ValueError) as exc:
        db.session.rollback()
        sms_trust.invalidate()
        flash(_t('Not saved: {error}', error=str(exc)), 'danger')
        return _back('sms_admin.trust')
    sms_trust.invalidate()
    flash(message, 'success')
    return _back('sms_admin.trust')


@sms_admin_bp.route('/trust/<int:issuer_id>/edit', methods=['POST'])
@login_required
@super_admin_required
def trust_edit(issuer_id):
    def change(row):
        fields = {'enabled': _checked('enabled'), 'jwks_url': (request.form.get('jwks_url') or '').strip()}
        until = _form_days('enabled_days')
        if until is not None:
            fields['enabled_until'] = until
        elif _checked('clear_until'):
            fields['enabled_until'] = None
        if row.is_primary:
            fields.update(allow_jit=_checked('allow_jit'), accepts_feed=_checked('accepts_feed'),
                          level_cap=_checked('level_cap'), jwks_auto=_checked('jwks_auto'))
        else:
            try:
                fields['allowed_subs'] = parse_allowed_subs(request.form.get('allowed_subs'))
            except ValueError as exc:
                raise sms_trust.TrustEditError(f'not a UUID: {exc}') from None
        sms_trust.upsert_issuer(row.issuer, actor_id=current_user.id, **fields)
        return _t('Trust entry saved.')
    return _trust_change(issuer_id, change)


@sms_admin_bp.route('/trust/<int:issuer_id>/pin', methods=['POST'])
@login_required
@super_admin_required
def trust_pin(issuer_id):
    def change(row):
        kid = (request.form.get('kid') or '').strip()
        sms_trust.pin_key(row.issuer, kid, request.form.get('pem') or '',
                          expected_fingerprint=request.form.get('fingerprint') or '',
                          actor_id=current_user.id)
        return _t('Key {kid} pinned.', kid=kid)
    return _trust_change(issuer_id, change)


@sms_admin_bp.route('/trust/<int:issuer_id>/unpin', methods=['POST'])
@login_required
@super_admin_required
def trust_unpin(issuer_id):
    def change(row):
        kid = (request.form.get('kid') or '').strip()
        if not sms_trust.unpin_key(row.issuer, kid, actor_id=current_user.id):
            raise sms_trust.TrustEditError('that kid is not pinned')
        return _t('Key {kid} unpinned.', kid=kid)
    return _trust_change(issuer_id, change)


@sms_admin_bp.route('/trust/<int:issuer_id>/window/open', methods=['POST'])
@login_required
@super_admin_required
def trust_window_open(issuer_id):
    def change(row):
        try:
            subs = parse_allowed_subs(request.form.get('allowed_subs'))
        except ValueError as exc:
            raise sms_trust.TrustEditError(f'not a UUID: {exc}') from None
        until = _form_days('enabled_days')
        sms_trust.open_test_window(row.issuer, kid=(request.form.get('kid') or '').strip(),
                                   pem=request.form.get('pem') or '',
                                   expected_fingerprint=request.form.get('fingerprint') or '',
                                   allowed_subs=subs, enabled_until=until, actor_id=current_user.id)
        return _t('Test window open until {until} UTC.',
                  until=sms_clock.to_datetime(until).strftime('%Y-%m-%d %H:%M'))
    return _trust_change(issuer_id, change)


def _disable_linked(row: SmsTrustIssuer) -> int:
    """Unlink and disable every account linked to a non-primary issuer
    (contract 2a closing step 1). Caller commits."""
    if row.is_primary:
        raise sms_trust.TrustEditError('accounts of the primary issuer are never disabled in bulk')
    n = 0
    for _identity, user in sms_sso_service.linked_users(row.issuer):
        sms_sso_service.unlink(user, row.issuer, actor_id=current_user.id)
        if user.is_active and not sms_linking.is_privileged(user):
            user.active, user.disabled_reason, user.disabled_at = False, 'local', sms_clock.utcnow()
            auth_audit.record(auth_audit.ACCOUNT_STATE, 'ok', 'disabled', user_id=user.id,
                              actor_id=current_user.id, ip=_ip(), detail={'test_window_close': row.issuer})
        n += 1
    return n


@sms_admin_bp.route('/trust/<int:issuer_id>/window/close', methods=['POST'])
@login_required
@super_admin_required
def trust_window_close(issuer_id):
    def change(row):
        kids = sms_trust.close_test_window(row.issuer, actor_id=current_user.id)
        n = _disable_linked(row) if _checked('disable_accounts') else 0
        return _t('Test window closed: {kids} key(s) unpinned, {n} account(s) unlinked and disabled.',
                  kids=len(kids), n=n)
    return _trust_change(issuer_id, change)


@sms_admin_bp.route('/trust/<int:issuer_id>/linked/disable', methods=['POST'])
@login_required
@super_admin_required
def trust_disable_linked(issuer_id):
    def change(row):
        return _t('{n} account(s) unlinked and disabled.', n=_disable_linked(row))
    return _trust_change(issuer_id, change)


# ---------------------------------------------------------------- audit

@sms_admin_bp.route('/audit')
@login_required
@super_admin_required
def audit():
    _require_ready()
    event = (request.args.get('event') or '').strip()
    alerts_only = request.args.get('alerts') == '1'
    try:
        page = max(1, int(request.args.get('page') or 1))
    except ValueError:
        page = 1
    q = AuthAuditLog.query
    if event:
        q = q.filter_by(event=event)
    if alerts_only:
        q = q.filter(AuthAuditLog.is_alert.is_(True))
    total = q.count()
    rows = q.order_by(AuthAuditLog.id.desc()).offset((page - 1) * AUDIT_PAGE_SIZE).limit(AUDIT_PAGE_SIZE).all()
    events = [e for (e,) in db.session.query(AuthAuditLog.event).distinct().order_by(AuthAuditLog.event)]
    open_rows = (AuthAuditLog.query.filter(AuthAuditLog.is_alert.is_(True), AuthAuditLog.acknowledged_at.is_(None))
                 .order_by(AuthAuditLog.id.desc()).limit(AUDIT_PAGE_SIZE).all())
    ids = {r.user_id for r in rows + open_rows if r.user_id} | {r.actor_user_id for r in rows + open_rows
                                                                if r.actor_user_id}
    names = {u.id: u.username for u in User.query.filter(User.id.in_(ids or [0]))}
    return render_template('admin_sms/audit.html', rows=rows, open_rows=open_rows, events=events,
                           event=event, alerts_only=alerts_only, page=page, total=total,
                           pages=max(1, -(-total // AUDIT_PAGE_SIZE)), names=names)


@sms_admin_bp.route('/audit/<int:row_id>/ack', methods=['POST'])
@login_required
@super_admin_required
def audit_ack(row_id):
    _require_ready()
    row = db.session.get(AuthAuditLog, row_id)
    if row is not None and row.is_alert and row.acknowledged_at is None:
        row.acknowledged_at, row.acknowledged_by = sms_clock.utcnow(), current_user.id
        db.session.commit()
    return _back('sms_admin.audit')


@sms_admin_bp.route('/audit/ack-all', methods=['POST'])
@login_required
@super_admin_required
def audit_ack_all():
    _require_ready()
    n = (AuthAuditLog.query.filter(AuthAuditLog.is_alert.is_(True), AuthAuditLog.acknowledged_at.is_(None))
         .update({'acknowledged_at': sms_clock.utcnow(), 'acknowledged_by': current_user.id},
                 synchronize_session=False))
    db.session.commit()
    flash(_t('{n} alert(s) acknowledged.', n=n), 'success')
    return _back('sms_admin.audit')

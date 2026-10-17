"""Super-admin account actions on the users page (docs/modules/sms-sso.md,
stage 5): end a user's sessions, disable / enable, local-only.

JSON in, JSON out (the users page calls these with ``fetch``). Every change
bumps ``session_version`` where it must end sessions and writes an audit row.
A super admin never acts on their own account here.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import auth_audit, db, sms_clock, web_security
from app.i18n import t as _t
from app.models import ExternalIdentity, User
from app.utils import super_admin_required

account_admin_bp = Blueprint('account_admin', __name__, url_prefix='/admin')

DISABLED_LOCAL = 'local'


def _target(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        return None, (jsonify({'error': _t('User not found')}), 404)
    if user.id == current_user.id:
        return None, (jsonify({'error': _t('You cannot do this to your own account.')}), 400)
    return user, None


def _flag(name: str):
    raw = request.form.get(name)
    if raw is None and request.is_json:
        raw = (request.get_json(silent=True) or {}).get(name)
    return str(raw).lower() in ('1', 'true', 'on', 'yes')


def _ip():
    return web_security.client_ip(request)


@account_admin_bp.route('/users/<int:user_id>/revoke-sessions', methods=['POST'])
@login_required
@super_admin_required
def revoke_sessions(user_id):
    user, err = _target(user_id)
    if err:
        return err
    user.bump_session_version()
    auth_audit.record(auth_audit.SESSIONS_REVOKED, 'ok', user_id=user.id, actor_id=current_user.id, ip=_ip())
    db.session.commit()
    return jsonify({'success': True, 'message': _t('All sessions of {username} have ended.',
                                                   username=user.username)})


@account_admin_bp.route('/users/<int:user_id>/active', methods=['POST'])
@login_required
@super_admin_required
def set_active(user_id):
    user, err = _target(user_id)
    if err:
        return err
    active = _flag('active')
    if active == user.is_active:
        return jsonify({'success': True, 'active': active})
    previous = user.disabled_reason
    if active:
        user.active, user.disabled_reason, user.disabled_at = True, None, None
    else:
        user.active, user.disabled_reason, user.disabled_at = False, DISABLED_LOCAL, sms_clock.utcnow()
        user.bump_session_version()
    auth_audit.record(auth_audit.ACCOUNT_STATE, 'ok', 'enabled' if active else 'disabled', user_id=user.id,
                      actor_id=current_user.id, ip=_ip(), detail={'previous_reason': previous})
    db.session.commit()
    return jsonify({'success': True, 'active': active})


@account_admin_bp.route('/users/<int:user_id>/local-only', methods=['POST'])
@login_required
@super_admin_required
def set_local_only(user_id):
    user, err = _target(user_id)
    if err:
        return err
    local_only = _flag('local_only')
    if local_only and ExternalIdentity.query.filter_by(user_id=user.id).first() is not None:
        return jsonify({'error': _t('Unlink this account from SMS before making it local-only.')}), 400
    if local_only != bool(user.local_only):
        user.local_only = local_only
        auth_audit.record(auth_audit.ACCOUNT_STATE, 'ok', 'local_only' if local_only else 'not_local_only',
                          user_id=user.id, actor_id=current_user.id, ip=_ip())
        db.session.commit()
    return jsonify({'success': True, 'local_only': local_only})

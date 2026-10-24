"""Super-admin user groups (docs/modules/user-groups.md).

Thin routes: parse the form or JSON, call ``app/user_groups.py``, commit.
Every route is ``@login_required`` + ``@super_admin_required``.
"""
from __future__ import annotations

import logging

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.i18n import t as _t
from app.models import UserGroup
from app.utils import super_admin_required
from app import user_groups

log = logging.getLogger(__name__)

groups_admin_bp = Blueprint('groups_admin', __name__, url_prefix='/admin/user-groups')


def _group_error(exc: user_groups.GroupError) -> str:
    code = exc.code
    if code == 'name_required':
        return _t('Enter a group name.')
    if code == 'name_too_long':
        return _t('Group name must be 80 characters or fewer.')
    if code == 'description_too_long':
        return _t('Description must be 200 characters or fewer.')
    if code == 'name_taken':
        return _t('A group with that name already exists.')
    if code == 'bad_role':
        return _t('Choose Viewer, User or Admin.')
    if code == 'unknown_subject':
        return _t('That subject does not exist.')
    if code == 'unknown_user':
        return _t('That user does not exist.')
    if code == 'unknown_group':
        return _t('That group does not exist.')
    if code == 'bad_user_ids':
        return _t('Choose users from the list.')
    if code == 'bad_group_ids':
        return _t('Choose groups from the list.')
    if code == 'bad_permissions':
        return _t('Could not read the permissions.')
    return _t('Could not save the group.')


def _load(group_id: int) -> UserGroup:
    group = db.session.get(UserGroup, group_id)
    if group is None:
        abort(404)
    return group


@groups_admin_bp.route('/', methods=['GET', 'POST'])
@login_required
@super_admin_required
def index():
    """List groups, or create one from the form at the top of the page."""
    if request.method == 'POST':
        try:
            user_groups.create_group(request.form.get('name'), request.form.get('description'))
            db.session.commit()
            flash(_t('Group created.'), 'success')
        except user_groups.GroupError as exc:
            db.session.rollback()
            flash(_group_error(exc), 'danger')
        except Exception:
            db.session.rollback()
            log.exception('create user group failed')
            flash(_t('Could not save the group.'), 'danger')
        return redirect(url_for('groups_admin.index'))
    return render_template('admin_user_groups.html', rows=user_groups.list_rows())


@groups_admin_bp.route('/<int:group_id>', methods=['POST'])
@login_required
@super_admin_required
def update_group(group_id):
    """Rename a group and replace its description."""
    group = _load(group_id)
    try:
        user_groups.rename_group(group, request.form.get('name'), request.form.get('description'))
        db.session.commit()
        flash(_t('Group saved.'), 'success')
    except user_groups.GroupError as exc:
        db.session.rollback()
        flash(_group_error(exc), 'danger')
    except Exception:
        db.session.rollback()
        log.exception('rename user group %s failed', group_id)
        flash(_t('Could not save the group.'), 'danger')
    return redirect(url_for('groups_admin.index'))


@groups_admin_bp.route('/<int:group_id>/delete', methods=['POST'])
@login_required
@super_admin_required
def delete_group(group_id):
    """Delete the group only. Personal grants and SMS levels stay."""
    group = _load(group_id)
    try:
        user_groups.delete_group(group)
        db.session.commit()
        flash(_t('Group deleted.'), 'success')
    except Exception:
        db.session.rollback()
        log.exception('delete user group %s failed', group_id)
        flash(_t('Could not save the group.'), 'danger')
    return redirect(url_for('groups_admin.index'))


@groups_admin_bp.route('/<int:group_id>/panel')
@login_required
@super_admin_required
def panel(group_id):
    """Offcanvas body: subject roles and the member checklist."""
    group = _load(group_id)
    return render_template('partials/admin_user_group_panel.html',
                           **user_groups.panel_context(group))


@groups_admin_bp.route('/<int:group_id>/permissions', methods=['POST'])
@login_required
@super_admin_required
def update_permissions(group_id):
    """JSON ``{permissions: {subject_id: role|''|null}}``. One subject per save."""
    group = _load(group_id)
    data = request.get_json(silent=True) or {}
    try:
        user_groups.apply_permissions(group, data.get('permissions'))
        db.session.commit()
    except user_groups.GroupError as exc:
        db.session.rollback()
        return jsonify({'error': _group_error(exc)}), 400
    except Exception:
        db.session.rollback()
        log.exception('user group %s permissions failed', group_id)
        return jsonify({'error': _t('Could not save group permissions.')}), 500
    return jsonify({'success': True, 'summary': user_groups.access_summary(group)})


@groups_admin_bp.route('/<int:group_id>/members', methods=['POST'])
@login_required
@super_admin_required
def update_members(group_id):
    """JSON ``{user_ids: [...]}`` replaces the member list."""
    group = _load(group_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'user_ids' not in data:
        return jsonify({'error': _t('Choose users from the list.')}), 400
    try:
        user_groups.set_members(group, data.get('user_ids'))
        db.session.commit()
    except user_groups.GroupError as exc:
        db.session.rollback()
        return jsonify({'error': _group_error(exc)}), 400
    except Exception:
        db.session.rollback()
        log.exception('user group %s members failed', group_id)
        return jsonify({'error': _t('Could not save members.')}), 500
    return jsonify({'success': True, 'member_count': user_groups.member_count(group)})

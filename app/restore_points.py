"""
Subject restore points — routes (``/admin/restore-points``,
``/admin/subjects/<sid>/restore-points/capture``,
``/admin/restore-points/<id>/preview|restore|pin``).

Subject-admin page to list a subject's restore points, take a manual one,
preview what a restore would change, and restore. Logic lives in
``app/subject_snapshot.py``. Spec: ``docs/modules/subject-snapshots.md``.

Authz: the capture route carries ``subject_id`` in the URL so
``@subject_admin_required`` fires; routes keyed by ``point_id`` check the
point's subject explicitly (the subject decorators pass through without a
``subject_id``).
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app
from flask_login import login_required, current_user

from app import db
from app import subject_snapshot as svc
from app.admin import _resolve_taxonomy_subject
from app.models import Subject, SubjectRestorePoint
from app.utils import admin_required, subject_admin_required

restore_points_bp = Blueprint('restore_points', __name__, url_prefix='/admin')


def _point_for_admin(point_id):
    """Return ``(point, None)`` or ``(None, (response, status))``."""
    point = db.session.get(SubjectRestorePoint, point_id)
    if point is None:
        return None, (jsonify({'success': False, 'error': 'Restore point not found.'}), 404)
    if not current_user.is_subject_admin(point.subject_id):
        return None, (jsonify({'success': False, 'error': 'Access denied for this subject.'}), 403)
    return point, None


@restore_points_bp.route('/restore-points')
@login_required
@admin_required
def page():
    """List one subject's restore points (shares the Topics / Chapters subject picker)."""
    subjects, subject = _resolve_taxonomy_subject()
    if subject and request.args.get('subject_id') != subject.id:
        return redirect(url_for('restore_points.page', subject_id=subject.id))
    points = svc.list_points(subject.id) if subject else []
    return render_template(
        'admin_restore_points.html',
        subjects=subjects,
        subject=subject,
        points=points,
        keep_per_subject=svc.KEEP_PER_SUBJECT,
        max_note_chars=svc.MAX_NOTE_CHARS,
    )


@restore_points_bp.route('/subjects/<subject_id>/restore-points/capture', methods=['POST'])
@login_required
@subject_admin_required
def capture(subject_id):
    """Manual snapshot. Body JSON ``{note?}``."""
    subject = db.session.get(Subject, subject_id)
    if subject is None:
        return jsonify({'success': False, 'error': 'Subject not found.'}), 404
    body = request.get_json(silent=True) or {}
    try:
        point = svc.capture(subject.id, 'manual', note=body.get('note'))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Manual restore point failed')
        return jsonify({'success': False, 'error': f'Could not save the restore point: {e}'}), 500
    return jsonify({'success': True, 'point': svc.point_meta(point, current_user.username)})


@restore_points_bp.route('/restore-points/<int:point_id>/pin', methods=['POST'])
@login_required
@admin_required
def pin(point_id):
    """Pin or unpin a point. JSON ``{"pinned": true|false}``.

    Unpinning prunes immediately: a point outside the automatic retention
    window is deleted in this request.
    """
    point, err = _point_for_admin(point_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get('pinned'), bool):
        return jsonify({
            'success': False,
            'error': 'Send {"pinned": true} or {"pinned": false}.',
        }), 400
    try:
        result = svc.set_pinned(point, body['pinned'])
        if result is None:
            db.session.rollback()
            return jsonify({'success': False, 'error': 'Restore point not found.'}), 404
        pinned, deleted = result
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Pin restore point %s failed', point_id)
        return jsonify({'success': False, 'error': f'Could not update the pin: {e}'}), 500
    if deleted:
        message = (
            'Unpinned and removed: it was outside the newest '
            f'{svc.KEEP_PER_SUBJECT} points and the daily anchors.'
        )
    elif pinned:
        message = 'Pinned. This point is kept until you unpin it.'
    else:
        message = 'Unpinned. It stays while it is inside the automatic retention.'
    return jsonify({
        'success': True,
        'pinned': pinned,
        'deleted': deleted,
        'message': message,
    })


@restore_points_bp.route('/restore-points/<int:point_id>/preview')
@login_required
@admin_required
def preview(point_id):
    """Read-only diff: what restoring this point would change now."""
    point, err = _point_for_admin(point_id)
    if err:
        return err
    try:
        summary = svc.preview(point)
    except svc.SnapshotError as e:
        return jsonify({'success': False, 'error': str(e)}), 409
    return jsonify({'success': True, 'point': svc.point_meta(point), 'preview': summary})


@restore_points_bp.route('/restore-points/<int:point_id>/restore', methods=['POST'])
@login_required
@admin_required
def restore(point_id):
    """Restore the point over its subject (one transaction; saves a
    ``before-restore`` point first so the restore can itself be undone)."""
    point, err = _point_for_admin(point_id)
    if err:
        return err
    try:
        result = svc.restore(point)
    except svc.SnapshotError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 409
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Restore point %s failed', point_id)
        return jsonify({'success': False, 'error': f'Restore failed, nothing was changed: {e}'}), 500
    s = result['summary']
    return jsonify({
        'success': True,
        'message': (f'Restored {s["subject_id"]} to point #{point_id}: '
                    f'{s["questions_changed"]} question(s) re-tagged. '
                    f'The previous state was saved as point #{result["before_point_id"]}.'),
        'before_point_id': result['before_point_id'],
        'summary': s,
    })

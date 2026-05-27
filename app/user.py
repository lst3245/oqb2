"""
User-facing routes: Saved Search Profiles and My Generated Files
"""
import io
import json
import os
import zipfile
from datetime import datetime

from flask import (
    Blueprint, render_template, request, jsonify, current_app,
    redirect, url_for, flash, abort, Response, stream_with_context, send_file,
)
from flask_login import login_required, current_user
from sqlalchemy import or_, and_

from app import db
from app.models import (
    SavedFilter, SavedGenerationProfile, SavedQuestionSet,
    GeneratedFile, FileSection, FileShare,
    Question, Subject, User,
)

user_bp = Blueprint('user', __name__, url_prefix='/user')


# ==================== My Files: helpers ====================

# Allowed values for the per-section sort dropdown. Keep in sync with the
# frontend dropdown options and the docs in user-files-profiles.mdc.
_VALID_SORT_FIELDS = {'name', 'created_at', 'completed_at', 'question_count', 'manual'}
_VALID_SORT_DIRS = {'asc', 'desc'}
_VALID_PAGE_SIZES = (5, 10, 25, 50, 100)
# Virtual section id used in URLs to represent the "Shared with me" view.
_SHARED_SECTION_ID = -1


def _get_or_create_default_section(user_id):
    """Return the user's default (Latest) section, creating it lazily.

    Every user's My Files page is expected to have at least this section.
    Called on /user/files page load and whenever a new file is created so
    new generations always have a home. Race-safe: a concurrent creator
    that loses the unique-constraint race will swallow IntegrityError and
    re-query to pick up the winner's row.
    """
    section = FileSection.query.filter_by(user_id=user_id, is_default=True).first()
    if section:
        return section

    # Auto-bump sort_order so the default sits at the top.
    section = FileSection(
        user_id=user_id,
        name='Latest',
        sort_order=0,
        sort_field='created_at',
        sort_direction='desc',
        page_size=10,
        is_default=True,
    )
    db.session.add(section)
    try:
        db.session.flush()  # need id
    except Exception:
        db.session.rollback()
        # Lost the race — re-query for the row a concurrent request just inserted.
        return FileSection.query.filter_by(user_id=user_id, is_default=True).first()

    # Ensure any existing files with section_id=NULL get attached to the
    # newly-created default. Safe to run repeatedly; new sections won't
    # have NULL-section files belonging to other users since this is
    # filtered by user_id.
    GeneratedFile.query.filter(
        GeneratedFile.user_id == user_id,
        GeneratedFile.section_id.is_(None),
    ).update({GeneratedFile.section_id: section.id}, synchronize_session=False)

    db.session.commit()
    return section


def _ids_of_files_shared_with(user_id):
    """Return a set of GeneratedFile.id visible to `user_id` through any
    FileShare row — either a direct file share or a share on the section
    the file currently lives in.
    """
    shared_ids = set()

    # Direct file shares
    rows = db.session.query(FileShare.file_id).filter(
        FileShare.shared_with_user_id == user_id,
        FileShare.file_id.isnot(None),
    ).all()
    shared_ids.update(r[0] for r in rows if r[0])

    # Section shares -> all files currently in those sections
    section_ids = [r[0] for r in db.session.query(FileShare.section_id).filter(
        FileShare.shared_with_user_id == user_id,
        FileShare.section_id.isnot(None),
    ).all() if r[0]]

    if section_ids:
        rows = db.session.query(GeneratedFile.id).filter(
            GeneratedFile.section_id.in_(section_ids)
        ).all()
        shared_ids.update(r[0] for r in rows)

    return shared_ids


def _user_can_view_file(gen_file, user):
    """Return True if `user` may see / download `gen_file`."""
    if gen_file.user_id == user.id or user.is_super_admin:
        return True
    # Shared (direct OR section-level)
    direct = FileShare.query.filter_by(
        file_id=gen_file.id, shared_with_user_id=user.id,
    ).first()
    if direct:
        return True
    if gen_file.section_id is not None:
        sect_share = FileShare.query.filter_by(
            section_id=gen_file.section_id, shared_with_user_id=user.id,
        ).first()
        if sect_share:
            return True
    return False


def _user_owns_file(gen_file, user):
    """Mutations (move, rename, delete) require ownership or super admin."""
    return gen_file.user_id == user.id or user.is_super_admin


def _serialise_section(section, file_count=0, owner_username=None, is_shared_in=False):
    return {
        'id': section.id,
        'name': section.name,
        'sort_order': section.sort_order,
        'sort_field': section.sort_field,
        'sort_direction': section.sort_direction,
        'page_size': section.page_size,
        'collapsed': bool(section.collapsed),
        'is_default': bool(section.is_default),
        'is_shared_in': is_shared_in,
        'owner_username': owner_username,
        'file_count': file_count,
        'created_at': section.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'updated_at': section.updated_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


def _serialise_file_row(gf, *, show_username=False, shared_by=None, is_read_only=False, output_path=None):
    """Build the JSON dict for one generated-file row.

    `shared_by` is set when this row is visible to the current user via a
    FileShare (string username); `is_read_only` is True for any row the
    current user cannot mutate.
    """
    _, _ext = os.path.splitext(gf.filename or '')
    file_ext = _ext.lstrip('.').lower() if _ext else ''

    format_priority_top = 'IMG'
    output_format = 'DOCX'
    if gf.generation_options:
        try:
            opts = json.loads(gf.generation_options)
            fp_raw = (opts.get('format_priority') or '').strip()
            if fp_raw:
                first = fp_raw.split(',')[0].strip().upper()
                if first in ('IMG', 'MD', 'DOC'):
                    format_priority_top = first
            of = (opts.get('output_format') or '').strip().upper()
            if of in ('DOCX', 'PDF'):
                output_format = of
        except (ValueError, TypeError):
            pass

    size_bytes = None
    if output_path and gf.filename and gf.status == 'completed':
        try:
            size_bytes = os.path.getsize(os.path.join(output_path, gf.filename))
        except OSError:
            size_bytes = None

    return {
        'id': gf.id,
        'display_name': gf.display_name,
        'filename': gf.filename,
        'file_ext': file_ext,
        'status': gf.status,
        'error_message': gf.error_message,
        'question_count': gf.question_count,
        'has_filter': bool(gf.filter_data),
        'has_generation_options': bool(gf.generation_options),
        'format_priority_top': format_priority_top,
        'output_format': output_format,
        'username': gf.user.username if (show_username or shared_by) else None,
        'shared_by': shared_by,
        'is_read_only': bool(is_read_only),
        'section_id': gf.section_id,
        'size_bytes': size_bytes,
        'created_at': gf.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'completed_at': gf.completed_at.strftime('%Y-%m-%dT%H:%M:%SZ') if gf.completed_at else None,
    }


def _apply_file_sort(query, sort_field, sort_direction):
    """Apply ORDER BY to a GeneratedFile query based on a section's sort
    config. Returns the modified query.
    """
    direction = sort_direction if sort_direction in _VALID_SORT_DIRS else 'desc'
    field = sort_field if sort_field in _VALID_SORT_FIELDS else 'created_at'

    col_map = {
        'name': GeneratedFile.display_name,
        'created_at': GeneratedFile.created_at,
        'completed_at': GeneratedFile.completed_at,
        'question_count': GeneratedFile.question_count,
        'manual': GeneratedFile.manual_position,
    }
    col = col_map[field]
    if direction == 'asc':
        return query.order_by(col.asc(), GeneratedFile.id.asc())
    return query.order_by(col.desc(), GeneratedFile.id.desc())


# ==================== Saved Filter Profiles ====================

@user_bp.route('/profiles')
@login_required
def profiles():
    """Saved search profiles page"""
    return render_template('saved_filters.html')


@user_bp.route('/profiles/list')
@login_required
def profiles_list():
    """API: list saved profiles (JSON).

    Returns:
      - user's own profiles, plus
      - any profile marked is_shared=True (owned by anyone)
      - super admin with ?show_all=1 sees every profile

    Each row has `is_own` and `is_shared` flags so the client can group them.
    """
    show_all = request.args.get('show_all', '0') == '1' and current_user.is_super_admin

    if show_all:
        filters = SavedFilter.query.order_by(
            SavedFilter.is_starred.desc(),
            SavedFilter.name.asc(),
        ).all()
    else:
        filters = SavedFilter.query.filter(
            (SavedFilter.user_id == current_user.id) | (SavedFilter.is_shared.is_(True))
        ).order_by(
            SavedFilter.is_starred.desc(),
            SavedFilter.name.asc(),
        ).all()

    result = []
    for f in filters:
        try:
            filter_data = json.loads(f.filter_data)
        except (json.JSONDecodeError, TypeError):
            filter_data = {}

        is_own = (f.user_id == current_user.id)
        result.append({
            'id': f.id,
            'name': f.name,
            'subject': filter_data.get('subject', ''),
            'source_type': filter_data.get('source_type', ''),
            'is_starred': bool(f.is_starred),
            'is_shared': bool(f.is_shared),
            'is_own': is_own,
            # Show owner username on rows the current user does not own (or always when show_all)
            'username': f.user.username if (show_all or not is_own) else None,
            'created_at': f.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        })

    return jsonify(result)


@user_bp.route('/profiles/save', methods=['POST'])
@login_required
def profiles_save():
    """API: save a new filter profile"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    name = data.get('name', '').strip()
    filter_data = data.get('filter_data')
    
    if not name:
        return jsonify({'error': 'Profile name is required'}), 400
    if not filter_data:
        return jsonify({'error': 'Filter data is required'}), 400
    
    profile = SavedFilter(
        user_id=current_user.id,
        name=name,
        filter_data=json.dumps(filter_data) if isinstance(filter_data, dict) else filter_data
    )
    db.session.add(profile)
    db.session.commit()
    
    return jsonify({'success': True, 'id': profile.id})


@user_bp.route('/profiles/<int:profile_id>/data')
@login_required
def profiles_data(profile_id):
    """API: get filter data for a profile (for restoring on dashboard).

    Accessible to: owner, super admin, or any logged-in user if the profile is shared.
    """
    profile = SavedFilter.query.get_or_404(profile_id)

    is_owner_or_admin = profile.user_id == current_user.id or current_user.is_super_admin
    if not is_owner_or_admin and not profile.is_shared:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        filter_data = json.loads(profile.filter_data)
    except (json.JSONDecodeError, TypeError):
        filter_data = {}
    
    return jsonify({
        'id': profile.id,
        'name': profile.name,
        'filter_data': filter_data
    })


@user_bp.route('/profiles/<int:profile_id>', methods=['DELETE'])
@login_required
def profiles_delete(profile_id):
    """API: delete a filter profile"""
    profile = SavedFilter.query.get_or_404(profile_id)
    
    # Only owner or super admin can delete
    if profile.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    db.session.delete(profile)
    db.session.commit()
    
    return jsonify({'success': True})


@user_bp.route('/profiles/bulk-delete', methods=['POST'])
@login_required
def profiles_bulk_delete():
    """API: delete multiple filter profiles"""
    data = request.get_json()
    ids = data.get('ids', []) if data else []
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    deleted = 0
    for pid in ids:
        profile = SavedFilter.query.get(pid)
        if not profile:
            continue
        if profile.user_id != current_user.id and not current_user.is_super_admin:
            continue
        db.session.delete(profile)
        deleted += 1

    db.session.commit()
    return jsonify({'success': True, 'deleted': deleted})


@user_bp.route('/profiles/<int:profile_id>/star', methods=['POST'])
@login_required
def profiles_star(profile_id):
    """API: toggle starred status of a filter profile (owner or super admin only)"""
    profile = SavedFilter.query.get_or_404(profile_id)

    if profile.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    if 'is_starred' in data:
        profile.is_starred = bool(data['is_starred'])
    else:
        profile.is_starred = not profile.is_starred

    db.session.commit()
    return jsonify({'success': True, 'is_starred': bool(profile.is_starred)})


@user_bp.route('/profiles/<int:profile_id>/share', methods=['POST'])
@login_required
def profiles_share(profile_id):
    """API: toggle shared status of a filter profile (super admin only).

    Shared profiles are visible to every logged-in user in their dropdown and list.
    """
    if not current_user.is_super_admin:
        return jsonify({'error': 'Only super admins can share profiles'}), 403

    profile = SavedFilter.query.get_or_404(profile_id)

    data = request.get_json() or {}
    if 'is_shared' in data:
        profile.is_shared = bool(data['is_shared'])
    else:
        profile.is_shared = not profile.is_shared

    db.session.commit()
    return jsonify({'success': True, 'is_shared': bool(profile.is_shared)})


# ==================== Saved Generation Profiles ====================

@user_bp.route('/gen-profiles')
@login_required
def gen_profiles():
    """Saved generation presets page"""
    return render_template('saved_gen_profiles.html')


@user_bp.route('/gen-profiles/list')
@login_required
def gen_profiles_list():
    """API: list saved generation presets (JSON), starred first, then by name.

    Returns the user's own presets plus any preset marked is_shared=True.
    Super admin with ?show_all=1 sees every preset.
    """
    show_all = request.args.get('show_all', '0') == '1' and current_user.is_super_admin

    if show_all:
        presets = SavedGenerationProfile.query.order_by(
            SavedGenerationProfile.is_starred.desc(),
            SavedGenerationProfile.name.asc(),
        ).all()
    else:
        presets = SavedGenerationProfile.query.filter(
            (SavedGenerationProfile.user_id == current_user.id) | (SavedGenerationProfile.is_shared.is_(True))
        ).order_by(
            SavedGenerationProfile.is_starred.desc(),
            SavedGenerationProfile.name.asc(),
        ).all()

    result = []
    for p in presets:
        is_own = (p.user_id == current_user.id)
        result.append({
            'id': p.id,
            'name': p.name,
            'is_starred': bool(p.is_starred),
            'is_shared': bool(p.is_shared),
            'is_own': is_own,
            'username': p.user.username if (show_all or not is_own) else None,
            'created_at': p.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'updated_at': p.updated_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        })

    return jsonify(result)


@user_bp.route('/gen-profiles/save', methods=['POST'])
@login_required
def gen_profiles_save():
    """API: save (upsert by name) a generation preset for the current user."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    name = (data.get('name') or '').strip()
    options_data = data.get('options_data')

    if not name:
        return jsonify({'error': 'Preset name is required'}), 400
    if not options_data:
        return jsonify({'error': 'Options data is required'}), 400

    # Never store question_ids in a reusable preset
    if isinstance(options_data, dict):
        options_data = {k: v for k, v in options_data.items() if k != 'question_ids'}
        options_json = json.dumps(options_data)
    else:
        # Already a JSON string; parse, drop question_ids, re-serialise
        try:
            parsed = json.loads(options_data)
            if isinstance(parsed, dict):
                parsed.pop('question_ids', None)
                options_json = json.dumps(parsed)
            else:
                options_json = options_data
        except (json.JSONDecodeError, TypeError):
            options_json = options_data

    existing = SavedGenerationProfile.query.filter_by(
        user_id=current_user.id, name=name
    ).first()

    if existing:
        existing.options_data = options_json
        db.session.commit()
        return jsonify({'success': True, 'id': existing.id, 'updated': True})

    preset = SavedGenerationProfile(
        user_id=current_user.id,
        name=name,
        options_data=options_json,
    )
    db.session.add(preset)
    db.session.commit()
    return jsonify({'success': True, 'id': preset.id, 'updated': False})


@user_bp.route('/gen-profiles/<int:preset_id>/data')
@login_required
def gen_profiles_data(preset_id):
    """API: get options data for a preset (for restoring on generate page).

    Accessible to owner, super admin, or any logged-in user if the preset is shared.
    """
    preset = SavedGenerationProfile.query.get_or_404(preset_id)

    is_owner_or_admin = preset.user_id == current_user.id or current_user.is_super_admin
    if not is_owner_or_admin and not preset.is_shared:
        return jsonify({'error': 'Access denied'}), 403

    try:
        options_data = json.loads(preset.options_data)
    except (json.JSONDecodeError, TypeError):
        options_data = {}

    return jsonify({
        'id': preset.id,
        'name': preset.name,
        'is_starred': bool(preset.is_starred),
        'options_data': options_data,
    })


@user_bp.route('/gen-profiles/<int:preset_id>', methods=['DELETE'])
@login_required
def gen_profiles_delete(preset_id):
    """API: delete a generation preset"""
    preset = SavedGenerationProfile.query.get_or_404(preset_id)

    if preset.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403

    db.session.delete(preset)
    db.session.commit()
    return jsonify({'success': True})


@user_bp.route('/gen-profiles/bulk-delete', methods=['POST'])
@login_required
def gen_profiles_bulk_delete():
    """API: delete multiple generation presets"""
    data = request.get_json()
    ids = data.get('ids', []) if data else []
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    deleted = 0
    for pid in ids:
        preset = SavedGenerationProfile.query.get(pid)
        if not preset:
            continue
        if preset.user_id != current_user.id and not current_user.is_super_admin:
            continue
        db.session.delete(preset)
        deleted += 1

    db.session.commit()
    return jsonify({'success': True, 'deleted': deleted})


@user_bp.route('/gen-profiles/<int:preset_id>/star', methods=['POST'])
@login_required
def gen_profiles_star(preset_id):
    """API: toggle starred status of a generation preset (owner or super admin only)"""
    preset = SavedGenerationProfile.query.get_or_404(preset_id)

    if preset.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    if 'is_starred' in data:
        preset.is_starred = bool(data['is_starred'])
    else:
        preset.is_starred = not preset.is_starred

    db.session.commit()
    return jsonify({'success': True, 'is_starred': bool(preset.is_starred)})


@user_bp.route('/gen-profiles/<int:preset_id>/share', methods=['POST'])
@login_required
def gen_profiles_share(preset_id):
    """API: toggle shared status of a generation preset (super admin only).

    Shared presets appear under the 'Shared' optgroup of every user's preset dropdown.
    """
    if not current_user.is_super_admin:
        return jsonify({'error': 'Only super admins can share presets'}), 403

    preset = SavedGenerationProfile.query.get_or_404(preset_id)

    data = request.get_json() or {}
    if 'is_shared' in data:
        preset.is_shared = bool(data['is_shared'])
    else:
        preset.is_shared = not preset.is_shared

    db.session.commit()
    return jsonify({'success': True, 'is_shared': bool(preset.is_shared)})


# ==================== Saved Question Sets ====================

def _serialize_question_set(qs, include_ids=False):
    """Build the JSON dict returned by list/data endpoints."""
    is_own = (qs.user_id == current_user.id)
    out = {
        'id': qs.id,
        'name': qs.name,
        'subject': qs.subject,
        'is_starred': bool(qs.is_starred),
        'is_shared': bool(qs.is_shared),
        'is_own': is_own,
        'username': qs.user.username if not is_own else None,
        'created_at': qs.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'updated_at': qs.updated_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    try:
        ids = json.loads(qs.question_ids)
        if not isinstance(ids, list):
            ids = []
    except (json.JSONDecodeError, TypeError):
        ids = []
    out['question_count'] = len(ids)
    if include_ids:
        out['question_ids'] = ids
    return out


def _can_view_set(qs):
    """Owner / super admin / shared-and-has-subject-access."""
    if qs.user_id == current_user.id or current_user.is_super_admin:
        return True
    if qs.is_shared and current_user.has_subject_access(qs.subject):
        return True
    return False


def _can_manage_set(qs):
    """Owner / super admin only — for delete, rename, star."""
    return qs.user_id == current_user.id or current_user.is_super_admin


@user_bp.route('/sets')
@login_required
def sets():
    """Saved question sets manage page."""
    if current_user.is_super_admin:
        subjects = Subject.query.order_by(Subject.id.asc()).all()
    else:
        accessible = {p.subject_id for p in current_user.subject_permissions}
        subjects = Subject.query.filter(Subject.id.in_(accessible)).order_by(Subject.id.asc()).all() if accessible else []
    subjects_data = [{'id': s.id, 'name': s.name} for s in subjects]
    return render_template('saved_question_sets.html', subjects=subjects_data)


@user_bp.route('/sets/list')
@login_required
def sets_list():
    """API: list saved question sets (JSON).

    Returns:
      - user's own sets, plus
      - shared sets the user has subject access to,
      - super admin with ?show_all=1 sees every set.

    Optional ?subject=<id> filters to a single subject.
    """
    show_all = request.args.get('show_all', '0') == '1' and current_user.is_super_admin
    subject_filter = (request.args.get('subject') or '').strip()

    query = SavedQuestionSet.query
    if subject_filter:
        query = query.filter(SavedQuestionSet.subject == subject_filter)

    if show_all:
        rows = query.order_by(
            SavedQuestionSet.is_starred.desc(),
            SavedQuestionSet.subject.asc(),
            SavedQuestionSet.name.asc(),
        ).all()
    else:
        rows = query.filter(
            (SavedQuestionSet.user_id == current_user.id) | (SavedQuestionSet.is_shared.is_(True))
        ).order_by(
            SavedQuestionSet.is_starred.desc(),
            SavedQuestionSet.subject.asc(),
            SavedQuestionSet.name.asc(),
        ).all()

    # When not show_all, hide shared sets the user lacks subject access to.
    result = []
    for qs in rows:
        if show_all:
            result.append(_serialize_question_set(qs))
            continue
        is_own = qs.user_id == current_user.id
        if is_own or current_user.has_subject_access(qs.subject):
            result.append(_serialize_question_set(qs))

    return jsonify(result)


@user_bp.route('/sets/save', methods=['POST'])
@login_required
def sets_save():
    """API: upsert a question set by (user_id, subject, name).

    Body: {name, subject, question_ids: [int...]}
    """
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    subject = (data.get('subject') or '').strip()
    question_ids = data.get('question_ids') or []

    if not name:
        return jsonify({'error': 'Set name is required'}), 400
    if not subject:
        return jsonify({'error': 'Subject is required'}), 400
    if not isinstance(question_ids, list):
        return jsonify({'error': 'question_ids must be a list'}), 400

    if not Subject.query.get(subject):
        return jsonify({'error': f'Unknown subject: {subject}'}), 400
    if not current_user.has_subject_access(subject):
        return jsonify({'error': 'No access to this subject'}), 403

    # Coerce to int and de-duplicate while preserving order
    seen = set()
    cleaned = []
    for qid in question_ids:
        try:
            qid_int = int(qid)
        except (TypeError, ValueError):
            continue
        if qid_int in seen:
            continue
        seen.add(qid_int)
        cleaned.append(qid_int)

    # Validate that the IDs exist and belong to this subject
    if cleaned:
        valid_rows = Question.query.with_entities(Question.id).filter(
            Question.id.in_(cleaned),
            Question.subject == subject,
        ).all()
        valid_ids = {row.id for row in valid_rows}
        cleaned = [qid for qid in cleaned if qid in valid_ids]

    payload = json.dumps(cleaned)

    existing = SavedQuestionSet.query.filter_by(
        user_id=current_user.id, subject=subject, name=name
    ).first()

    if existing:
        existing.question_ids = payload
        db.session.commit()
        return jsonify({
            'success': True, 'id': existing.id, 'updated': True,
            'question_count': len(cleaned),
        })

    qs = SavedQuestionSet(
        user_id=current_user.id,
        name=name,
        subject=subject,
        question_ids=payload,
    )
    db.session.add(qs)
    db.session.commit()
    return jsonify({
        'success': True, 'id': qs.id, 'updated': False,
        'question_count': len(cleaned),
    })


@user_bp.route('/sets/<int:set_id>/data')
@login_required
def sets_data(set_id):
    """API: return question IDs for a set (for loading into the set-ops modal)."""
    qs = SavedQuestionSet.query.get_or_404(set_id)

    if not _can_view_set(qs):
        return jsonify({'error': 'Access denied'}), 403

    payload = _serialize_question_set(qs, include_ids=True)
    # Strip IDs the user no longer has subject access to (paranoid: subject
    # access was checked above, but if a question was moved to a different
    # subject in the meantime we keep the payload consistent with permissions).
    if not current_user.has_subject_access(qs.subject):
        payload['question_ids'] = []
        payload['question_count'] = 0
    return jsonify(payload)


@user_bp.route('/sets/<int:set_id>', methods=['DELETE'])
@login_required
def sets_delete(set_id):
    qs = SavedQuestionSet.query.get_or_404(set_id)
    if not _can_manage_set(qs):
        return jsonify({'error': 'Access denied'}), 403
    db.session.delete(qs)
    db.session.commit()
    return jsonify({'success': True})


@user_bp.route('/sets/bulk-delete', methods=['POST'])
@login_required
def sets_bulk_delete():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    deleted = 0
    for sid in ids:
        qs = SavedQuestionSet.query.get(sid)
        if not qs:
            continue
        if not _can_manage_set(qs):
            continue
        db.session.delete(qs)
        deleted += 1
    db.session.commit()
    return jsonify({'success': True, 'deleted': deleted})


@user_bp.route('/sets/<int:set_id>/star', methods=['POST'])
@login_required
def sets_star(set_id):
    qs = SavedQuestionSet.query.get_or_404(set_id)
    if not _can_manage_set(qs):
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    if 'is_starred' in data:
        qs.is_starred = bool(data['is_starred'])
    else:
        qs.is_starred = not qs.is_starred
    db.session.commit()
    return jsonify({'success': True, 'is_starred': bool(qs.is_starred)})


@user_bp.route('/sets/<int:set_id>/share', methods=['POST'])
@login_required
def sets_share(set_id):
    """Toggle shared status (super admin only)."""
    if not current_user.is_super_admin:
        return jsonify({'error': 'Only super admins can share sets'}), 403

    qs = SavedQuestionSet.query.get_or_404(set_id)
    data = request.get_json() or {}
    if 'is_shared' in data:
        qs.is_shared = bool(data['is_shared'])
    else:
        qs.is_shared = not qs.is_shared
    db.session.commit()
    return jsonify({'success': True, 'is_shared': bool(qs.is_shared)})


@user_bp.route('/sets/<int:set_id>/rename', methods=['POST'])
@login_required
def sets_rename(set_id):
    qs = SavedQuestionSet.query.get_or_404(set_id)
    if not _can_manage_set(qs):
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    new_name = (data.get('name') or '').strip()
    if not new_name:
        return jsonify({'error': 'New name is required'}), 400

    # Avoid collision with another set the same user has under the same subject
    clash = SavedQuestionSet.query.filter(
        SavedQuestionSet.user_id == qs.user_id,
        SavedQuestionSet.subject == qs.subject,
        SavedQuestionSet.name == new_name,
        SavedQuestionSet.id != qs.id,
    ).first()
    if clash:
        return jsonify({'error': f'A set named "{new_name}" already exists for this subject'}), 409

    qs.name = new_name
    db.session.commit()
    return jsonify({'success': True, 'name': qs.name})


# ==================== File Sections (folders) ====================

@user_bp.route('/sections', methods=['GET'])
@login_required
def sections_list():
    """API: list sections visible to the current user.

    Default: their own sections + a virtual "Shared with me" entry (id=-1)
    if any FileShare row targets them.

    Super-admin with `show_all=1`: ALL users' sections, grouped by owner
    in the response. Used by the "Show all users" page toggle so admins
    can browse / mutate everyone's files in the same UI. Each row has
    `owner_username` set when it doesn't belong to the current user so
    the client can render it under that user's header.
    """
    show_all = request.args.get('show_all', '0') == '1' and current_user.is_super_admin

    # Ensure default exists before reading.
    _get_or_create_default_section(current_user.id)

    if show_all:
        # Show the admin's own sections first, then every other user's
        # sections in user_id order. Within a single user's group, keep
        # their default ("Latest") first.
        own_first = db.case(
            (FileSection.user_id == current_user.id, 0),
            else_=1,
        )
        rows = FileSection.query.order_by(
            own_first.asc(),
            FileSection.user_id.asc(),
            FileSection.is_default.desc(),
            FileSection.sort_order.asc(),
            FileSection.created_at.asc(),
        ).all()
    else:
        rows = FileSection.query.filter_by(user_id=current_user.id).order_by(
            FileSection.is_default.desc(),  # default always first
            FileSection.sort_order.asc(),
            FileSection.created_at.asc(),
        ).all()

    section_ids = [s.id for s in rows]
    counts = {sid: 0 for sid in section_ids}
    if section_ids:
        # Count files per section. For show_all we count every file in the
        # section regardless of owner (an admin could have moved one user's
        # file into another user's section, though our UI doesn't expose
        # that flow). For the normal path we scope to the section owner.
        count_q = db.session.query(
            GeneratedFile.section_id, db.func.count(GeneratedFile.id)
        ).filter(GeneratedFile.section_id.in_(section_ids))
        if not show_all:
            count_q = count_q.filter(GeneratedFile.user_id == current_user.id)
        for sid, c in count_q.group_by(GeneratedFile.section_id).all():
            counts[sid] = c

    result = []
    for s in rows:
        is_own = (s.user_id == current_user.id)
        result.append(_serialise_section(
            s,
            file_count=counts.get(s.id, 0),
            owner_username=None if is_own else (s.user.username if s.user else None),
        ))

    # Virtual "Shared with me" section (only when not in show_all mode —
    # in show_all the admin already sees the source files directly).
    if not show_all:
        shared_ids = _ids_of_files_shared_with(current_user.id)
        if shared_ids:
            result.append({
                'id': _SHARED_SECTION_ID,
                'name': 'Shared with me',
                'sort_order': 9999,
                'sort_field': 'created_at',
                'sort_direction': 'desc',
                'page_size': 10,
                'collapsed': False,
                'is_default': False,
                'is_shared_in': True,
                'owner_username': None,
                'file_count': len(shared_ids),
                'created_at': None,
                'updated_at': None,
            })

    return jsonify(result)


@user_bp.route('/sections', methods=['POST'])
@login_required
def sections_create():
    """Create a new section for the current user."""
    if not current_user.can_generate():
        return jsonify({'error': 'Only users with generate permission can create sections'}), 403

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    if len(name) > 120:
        return jsonify({'error': 'Name too long (max 120 chars)'}), 400

    clash = FileSection.query.filter_by(user_id=current_user.id, name=name).first()
    if clash:
        return jsonify({'error': f'A section named "{name}" already exists'}), 409

    # Append to the bottom by default. Default section keeps sort_order=0.
    max_order = db.session.query(db.func.max(FileSection.sort_order)).filter(
        FileSection.user_id == current_user.id
    ).scalar() or 0

    section = FileSection(
        user_id=current_user.id,
        name=name,
        sort_order=max_order + 1,
        sort_field='created_at',
        sort_direction='desc',
        page_size=10,
        is_default=False,
    )
    db.session.add(section)
    db.session.commit()
    return jsonify({'success': True, 'section': _serialise_section(section, file_count=0)})


@user_bp.route('/sections/<int:section_id>', methods=['PATCH'])
@login_required
def sections_update(section_id):
    """Partial update of a section (name / sort / page_size / collapsed).

    Default sections cannot be renamed but their sort + page_size + collapsed
    can be tweaked, mirroring how typical Inbox folders behave in mail apps.
    """
    section = FileSection.query.get_or_404(section_id)
    if section.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}

    if 'name' in data:
        new_name = (data.get('name') or '').strip()
        if not new_name:
            return jsonify({'error': 'Name cannot be empty'}), 400
        if section.is_default:
            return jsonify({'error': 'The default section cannot be renamed'}), 400
        if len(new_name) > 120:
            return jsonify({'error': 'Name too long (max 120 chars)'}), 400
        clash = FileSection.query.filter(
            FileSection.user_id == section.user_id,
            FileSection.name == new_name,
            FileSection.id != section.id,
        ).first()
        if clash:
            return jsonify({'error': f'A section named "{new_name}" already exists'}), 409
        section.name = new_name

    if 'sort_field' in data:
        sf = (data.get('sort_field') or '').strip()
        if sf not in _VALID_SORT_FIELDS:
            return jsonify({'error': f'Invalid sort_field: {sf}'}), 400
        section.sort_field = sf

    if 'sort_direction' in data:
        sd = (data.get('sort_direction') or '').strip()
        if sd not in _VALID_SORT_DIRS:
            return jsonify({'error': f'Invalid sort_direction: {sd}'}), 400
        section.sort_direction = sd

    if 'page_size' in data:
        try:
            ps = int(data.get('page_size'))
        except (TypeError, ValueError):
            return jsonify({'error': 'page_size must be an integer'}), 400
        if ps not in _VALID_PAGE_SIZES:
            return jsonify({'error': f'page_size must be one of {sorted(_VALID_PAGE_SIZES)}'}), 400
        section.page_size = ps

    if 'collapsed' in data:
        section.collapsed = bool(data.get('collapsed'))

    db.session.commit()
    return jsonify({'success': True, 'section': _serialise_section(section)})


@user_bp.route('/sections/<int:section_id>', methods=['DELETE'])
@login_required
def sections_delete(section_id):
    """Delete a section. Its files are moved to the user's default section."""
    section = FileSection.query.get_or_404(section_id)
    if section.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403
    if section.is_default:
        return jsonify({'error': 'The default section cannot be deleted'}), 400

    default = _get_or_create_default_section(section.user_id)

    GeneratedFile.query.filter_by(section_id=section.id).update(
        {GeneratedFile.section_id: default.id}, synchronize_session=False
    )

    db.session.delete(section)
    db.session.commit()
    return jsonify({'success': True, 'moved_to_section_id': default.id})


@user_bp.route('/sections/reorder', methods=['POST'])
@login_required
def sections_reorder():
    """Reorder sections vertically. Body: {ids: [section_id, ...]}.

    Only the user's own sections are touched; default section is always
    pinned to the top (sort_order=0) regardless of where it appears in
    the input. Unknown IDs are silently dropped.
    """
    data = request.get_json() or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list):
        return jsonify({'error': 'ids must be a list'}), 400

    own = {s.id: s for s in FileSection.query.filter_by(user_id=current_user.id).all()}
    order = 1  # default takes 0
    for sid in ids:
        sec = own.get(int(sid)) if isinstance(sid, (int, str)) and str(sid).lstrip('-').isdigit() else None
        if not sec or sec.is_default:
            continue
        sec.sort_order = order
        order += 1

    # Pin default
    for sec in own.values():
        if sec.is_default:
            sec.sort_order = 0

    db.session.commit()
    return jsonify({'success': True})


# ==================== Generated Files ====================

@user_bp.route('/files')
@login_required
def files():
    """My generated files page"""
    if not current_user.can_generate():
        abort(403)
    # Lazy-create the default section so the page never opens to an empty layout.
    _get_or_create_default_section(current_user.id)
    return render_template('my_files.html')


@user_bp.route('/files/list')
@login_required
def files_list():
    """API: list generated files for one section, paginated.

    Query params:
      - section_id (required): the FileSection.id OR -1 for "Shared with me"
      - page (int, default 1)
      - show_all (super-admin): include any other user's files in their
        own sections — used by the admin "Show all users" toggle and only
        meaningful for own (non-shared) sections.
    """
    section_id_raw = request.args.get('section_id', '')
    try:
        section_id = int(section_id_raw)
    except (TypeError, ValueError):
        return jsonify({'error': 'section_id is required'}), 400

    try:
        page = max(1, int(request.args.get('page', '1')))
    except (TypeError, ValueError):
        page = 1

    show_all = request.args.get('show_all', '0') == '1' and current_user.is_super_admin
    output_path = current_app.config['OUTPUT_PATH']

    # --- Virtual "Shared with me" section ---
    if section_id == _SHARED_SECTION_ID:
        shared_ids = _ids_of_files_shared_with(current_user.id)
        if not shared_ids:
            return jsonify({
                'section_id': _SHARED_SECTION_ID,
                'page': 1, 'page_size': 10, 'total': 0, 'pages': 0,
                'files': [],
            })

        page_size = 10  # virtual section has fixed page size; UI can override per state
        try:
            page_size = max(5, min(100, int(request.args.get('page_size', '10'))))
        except (TypeError, ValueError):
            page_size = 10

        q = GeneratedFile.query.filter(GeneratedFile.id.in_(shared_ids))
        q = _apply_file_sort(q, 'created_at', 'desc')
        total = q.count()
        files = q.limit(page_size).offset((page - 1) * page_size).all()

        # Build shared_by lookup for badges
        share_rows = FileShare.query.filter(
            FileShare.shared_with_user_id == current_user.id,
        ).all()
        # file_id -> shared_by_username
        direct_share = {}
        # section_id -> shared_by_username (for fall-back when file_id not in direct)
        section_share = {}
        for sh in share_rows:
            owner = sh.shared_by.username if sh.shared_by else None
            if sh.file_id:
                direct_share[sh.file_id] = owner
            elif sh.section_id:
                section_share[sh.section_id] = owner

        result = []
        for gf in files:
            shared_by = direct_share.get(gf.id)
            if shared_by is None and gf.section_id is not None:
                shared_by = section_share.get(gf.section_id)
            if shared_by is None:
                shared_by = gf.user.username if gf.user else None
            result.append(_serialise_file_row(
                gf,
                shared_by=shared_by,
                is_read_only=True,
                output_path=output_path,
            ))
        return jsonify({
            'section_id': _SHARED_SECTION_ID,
            'page': page, 'page_size': page_size,
            'total': total, 'pages': (total + page_size - 1) // page_size,
            'files': result,
        })

    # --- Real section ---
    section = FileSection.query.get_or_404(section_id)
    if section.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403

    # show_all only meaningful for super-admin viewing across all owners;
    # otherwise scoped to the section's owner.
    base = GeneratedFile.query.filter(GeneratedFile.section_id == section.id)
    if not show_all:
        base = base.filter(GeneratedFile.user_id == section.user_id)

    base = _apply_file_sort(base, section.sort_field, section.sort_direction)
    total = base.count()
    page_size = section.page_size or 10
    files = base.limit(page_size).offset((page - 1) * page_size).all()

    result = [
        _serialise_file_row(gf, show_username=show_all, output_path=output_path)
        for gf in files
    ]
    return jsonify({
        'section_id': section.id,
        'page': page, 'page_size': page_size,
        'total': total, 'pages': (total + page_size - 1) // page_size,
        'files': result,
    })


@user_bp.route('/files/<int:file_id>/filter')
@login_required
def files_filter_data(file_id):
    """API: get saved filter data from a generated file (for reuse)"""
    gen_file = GeneratedFile.query.get_or_404(file_id)

    if not _user_can_view_file(gen_file, current_user):
        return jsonify({'error': 'Access denied'}), 403

    if not gen_file.filter_data:
        return jsonify({'error': 'No filter data saved for this file'}), 404

    try:
        filter_data = json.loads(gen_file.filter_data)
    except (json.JSONDecodeError, TypeError):
        return jsonify({'error': 'Invalid filter data'}), 500

    return jsonify({'filter_data': filter_data})


@user_bp.route('/files/<int:file_id>/generation_options')
@login_required
def files_generation_options(file_id):
    """API: get saved generation options from a generated file (for regeneration)"""
    gen_file = GeneratedFile.query.get_or_404(file_id)

    if not _user_can_view_file(gen_file, current_user):
        return jsonify({'error': 'Access denied'}), 403

    try:
        generation_options = json.loads(gen_file.generation_options) if gen_file.generation_options else {}
    except (json.JSONDecodeError, TypeError):
        generation_options = {}

    return jsonify({
        'generation_options': generation_options,
        'display_name': gen_file.display_name,
        'filter_data': gen_file.filter_data or '',
    })


@user_bp.route('/files/<int:file_id>/move', methods=['POST'])
@login_required
def files_move(file_id):
    """Move one file into a different section (owner / super admin)."""
    gen_file = GeneratedFile.query.get_or_404(file_id)
    if not _user_owns_file(gen_file, current_user):
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    try:
        target_id = int(data.get('section_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'section_id is required'}), 400

    target = FileSection.query.get_or_404(target_id)
    if target.user_id != gen_file.user_id:
        return jsonify({'error': 'Section belongs to another user'}), 403

    gen_file.section_id = target.id
    db.session.commit()
    return jsonify({'success': True, 'section_id': target.id})


@user_bp.route('/files/bulk-move', methods=['POST'])
@login_required
def files_bulk_move():
    """Move multiple files into a section.

    The target section must be owned by the same user as every selected
    file (super-admin can move across, but each file still goes to a
    section owned by its own user — otherwise rejected).
    """
    data = request.get_json() or {}
    ids = data.get('ids') or []
    try:
        target_id = int(data.get('section_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'section_id is required'}), 400
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    target = FileSection.query.get_or_404(target_id)
    moved = 0
    skipped = 0
    for fid in ids:
        gf = GeneratedFile.query.get(fid)
        if not gf or not _user_owns_file(gf, current_user):
            skipped += 1
            continue
        if target.user_id != gf.user_id:
            skipped += 1
            continue
        gf.section_id = target.id
        moved += 1

    db.session.commit()
    return jsonify({'success': True, 'moved': moved, 'skipped': skipped})


@user_bp.route('/files/<int:file_id>/rename', methods=['POST'])
@login_required
def files_rename(file_id):
    """Rename a file's display_name (does NOT touch the file on disk)."""
    gen_file = GeneratedFile.query.get_or_404(file_id)
    if not _user_owns_file(gen_file, current_user):
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    new_name = (data.get('display_name') or '').strip()
    if not new_name:
        return jsonify({'error': 'display_name cannot be empty'}), 400
    if len(new_name) > 200:
        return jsonify({'error': 'display_name too long (max 200 chars)'}), 400

    gen_file.display_name = new_name
    db.session.commit()
    return jsonify({'success': True, 'display_name': gen_file.display_name})


@user_bp.route('/files/reorder', methods=['POST'])
@login_required
def files_reorder():
    """Manual-mode ordering of files within a section.

    Body: {section_id, ids: [file_id, file_id, ...]}.
    Sets `manual_position` based on the index in the supplied list. The
    section's `sort_field` is set to 'manual' as a side-effect so the new
    order actually shows up.
    """
    data = request.get_json() or {}
    try:
        section_id = int(data.get('section_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'section_id is required'}), 400
    ids = data.get('ids') or []
    if not isinstance(ids, list):
        return jsonify({'error': 'ids must be a list'}), 400

    section = FileSection.query.get_or_404(section_id)
    if section.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403

    for idx, fid in enumerate(ids):
        try:
            fid_int = int(fid)
        except (TypeError, ValueError):
            continue
        gf = GeneratedFile.query.get(fid_int)
        if gf and gf.section_id == section.id and _user_owns_file(gf, current_user):
            gf.manual_position = idx

    section.sort_field = 'manual'
    section.sort_direction = 'asc'
    db.session.commit()
    return jsonify({'success': True})


@user_bp.route('/files/<int:file_id>', methods=['DELETE'])
@login_required
def files_delete(file_id):
    """API: delete a generated file (DB record + file on disk)"""
    gen_file = GeneratedFile.query.get_or_404(file_id)

    if not _user_owns_file(gen_file, current_user):
        return jsonify({'error': 'Access denied'}), 403

    output_path = current_app.config['OUTPUT_PATH']
    file_path = os.path.join(output_path, gen_file.filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass  # File may already be gone

    db.session.delete(gen_file)
    db.session.commit()

    return jsonify({'success': True})


@user_bp.route('/files/bulk-delete', methods=['POST'])
@login_required
def files_bulk_delete():
    """API: delete multiple generated files (DB records + files on disk)"""
    data = request.get_json()
    ids = data.get('ids', []) if data else []
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    output_path = current_app.config['OUTPUT_PATH']
    deleted = 0
    for fid in ids:
        gen_file = GeneratedFile.query.get(fid)
        if not gen_file:
            continue
        if not _user_owns_file(gen_file, current_user):
            continue
        file_path = os.path.join(output_path, gen_file.filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        db.session.delete(gen_file)
        deleted += 1

    db.session.commit()
    return jsonify({'success': True, 'deleted': deleted})


@user_bp.route('/files/bulk-download', methods=['POST'])
@login_required
def files_bulk_download():
    """Stream a ZIP containing the selected (completed) generated files.

    Permission: each file individually must be visible to the current user
    (own / shared / super-admin). Non-completed and missing-on-disk files
    are silently skipped. Duplicate display_names get a `(2)`, `(3)`, ...
    suffix inside the zip so the user always gets every file.
    """
    data = request.get_json() or {}
    ids = data.get('ids') or []
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    output_path = current_app.config['OUTPUT_PATH']

    # Resolve and authorise upfront so we can bail with a clean 403 if needed
    files_to_zip = []
    used_names = {}
    for fid in ids:
        gf = GeneratedFile.query.get(fid)
        if not gf or gf.status != 'completed':
            continue
        if not _user_can_view_file(gf, current_user):
            continue
        disk_path = os.path.join(output_path, gf.filename)
        if not os.path.exists(disk_path):
            continue

        # Use display_name + actual extension; sanitise illegal chars and
        # de-duplicate
        _, ext = os.path.splitext(gf.filename or '')
        ext = ext or ''
        safe = ''.join(c if c not in '\\/:*?"<>|\r\n\t' else '_' for c in (gf.display_name or 'file'))
        candidate = f'{safe}{ext}'
        if candidate in used_names:
            used_names[candidate] += 1
            base = safe
            candidate = f'{base} ({used_names[candidate]}){ext}'
            while candidate in used_names:
                used_names[candidate] = used_names.get(candidate, 1) + 1
                candidate = f'{base} ({used_names[candidate]}){ext}'
        used_names[candidate] = 1
        files_to_zip.append((disk_path, candidate))

    if not files_to_zip:
        return jsonify({'error': 'No downloadable files in selection'}), 400

    # Build the ZIP in-memory. The output_path docs already produce files
    # on the order of a few MB each; if usage outgrows this we can switch
    # to a streaming `stream_zip` implementation. Tracked in CHANGELOG.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in files_to_zip:
            zf.write(src, arcname=arcname)
    buf.seek(0)

    ts = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'my-files-{ts}.zip',
    )


# ==================== File / Section Sharing (super admin) ====================

def _require_super_admin():
    if not current_user.is_super_admin:
        return jsonify({'error': 'Only super admins can manage file shares'}), 403
    return None


@user_bp.route('/shares', methods=['GET'])
@login_required
def shares_get():
    """List the current share targets for a file or section.

    Query params: file_id OR section_id (exactly one). Returns
    `{shares: [{id, user_id, username, created_at}], available_users: [...]}`.

    Available users excludes the file/section owner and any user already in
    the share list, so the frontend can pre-populate a picker without
    showing duplicates. Super admin only.
    """
    err = _require_super_admin()
    if err:
        return err

    file_id = request.args.get('file_id', type=int)
    section_id = request.args.get('section_id', type=int)
    if (file_id is None) == (section_id is None):
        return jsonify({'error': 'Exactly one of file_id / section_id required'}), 400

    if file_id is not None:
        gf = GeneratedFile.query.get_or_404(file_id)
        owner_id = gf.user_id
        rows = FileShare.query.filter_by(file_id=file_id).all()
    else:
        sect = FileSection.query.get_or_404(section_id)
        if sect.is_default:
            return jsonify({'error': 'The default section cannot be shared'}), 400
        owner_id = sect.user_id
        rows = FileShare.query.filter_by(section_id=section_id).all()

    shares = [{
        'id': sh.id,
        'user_id': sh.shared_with_user_id,
        'username': sh.shared_with.username if sh.shared_with else None,
        'created_at': sh.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
    } for sh in rows]

    taken = {owner_id, *(s['user_id'] for s in shares)}
    available = [
        {'id': u.id, 'username': u.username}
        for u in User.query.order_by(User.username).all()
        if u.id not in taken
    ]
    return jsonify({'shares': shares, 'available_users': available})


@user_bp.route('/shares', methods=['POST'])
@login_required
def shares_set():
    """Replace the share target list for a file or section.

    Body: {file_id?|section_id?, user_ids: [int, ...]}. Adds missing
    targets, removes ones not in the list, no-ops for already-present
    targets. Super admin only.
    """
    err = _require_super_admin()
    if err:
        return err

    data = request.get_json() or {}
    file_id = data.get('file_id')
    section_id = data.get('section_id')
    user_ids = data.get('user_ids') or []
    if (file_id is None) == (section_id is None):
        return jsonify({'error': 'Exactly one of file_id / section_id required'}), 400

    if file_id is not None:
        gf = GeneratedFile.query.get_or_404(int(file_id))
        owner_id = gf.user_id
        existing = {sh.shared_with_user_id: sh for sh in FileShare.query.filter_by(file_id=gf.id).all()}
        target_kwargs = {'file_id': gf.id}
    else:
        sect = FileSection.query.get_or_404(int(section_id))
        if sect.is_default:
            return jsonify({'error': 'The default section cannot be shared'}), 400
        owner_id = sect.user_id
        existing = {sh.shared_with_user_id: sh for sh in FileShare.query.filter_by(section_id=sect.id).all()}
        target_kwargs = {'section_id': sect.id}

    wanted_ids = set()
    for uid in user_ids:
        try:
            wanted_ids.add(int(uid))
        except (TypeError, ValueError):
            continue
    wanted_ids.discard(owner_id)  # never share to owner

    # Validate users exist
    if wanted_ids:
        valid_users = {u.id for u in User.query.filter(User.id.in_(wanted_ids)).all()}
        wanted_ids &= valid_users

    # Add missing
    added = 0
    for uid in wanted_ids - set(existing.keys()):
        db.session.add(FileShare(
            shared_by_user_id=current_user.id,
            shared_with_user_id=uid,
            **target_kwargs,
        ))
        added += 1

    # Remove dropped
    removed = 0
    for uid in set(existing.keys()) - wanted_ids:
        db.session.delete(existing[uid])
        removed += 1

    db.session.commit()
    return jsonify({'success': True, 'added': added, 'removed': removed, 'total': len(wanted_ids)})


@user_bp.route('/shares/<int:share_id>', methods=['DELETE'])
@login_required
def shares_delete(share_id):
    """Revoke a single share row. Super admin only."""
    err = _require_super_admin()
    if err:
        return err
    sh = FileShare.query.get_or_404(share_id)
    db.session.delete(sh)
    db.session.commit()
    return jsonify({'success': True})


@user_bp.route('/shares/users')
@login_required
def shares_users():
    """List all users (for the share-to picker). Super admin only."""
    err = _require_super_admin()
    if err:
        return err
    users = User.query.order_by(User.username).all()
    return jsonify([
        {'id': u.id, 'username': u.username, 'is_super_admin': bool(u.is_super_admin)}
        for u in users
    ])

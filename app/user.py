"""
User-facing routes: Saved Search Profiles and My Generated Files
"""
from flask import Blueprint, render_template, request, jsonify, current_app, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app import db
from app.models import SavedFilter, SavedGenerationProfile, GeneratedFile
import json
import os

user_bp = Blueprint('user', __name__, url_prefix='/user')


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


# ==================== Generated Files ====================

@user_bp.route('/files')
@login_required
def files():
    """My generated files page"""
    if not current_user.can_generate():
        abort(403)
    return render_template('my_files.html')


@user_bp.route('/files/list')
@login_required
def files_list():
    """API: list generated files (JSON)"""
    show_all = request.args.get('show_all', '0') == '1' and current_user.is_super_admin
    
    if show_all:
        gen_files = GeneratedFile.query.order_by(GeneratedFile.created_at.desc()).all()
    else:
        gen_files = GeneratedFile.query.filter_by(user_id=current_user.id).order_by(GeneratedFile.created_at.desc()).all()
    
    result = []
    for gf in gen_files:
        result.append({
            'id': gf.id,
            'display_name': gf.display_name,
            'filename': gf.filename,
            'status': gf.status,
            'error_message': gf.error_message,
            'question_count': gf.question_count,
            'has_filter': bool(gf.filter_data),
            'has_generation_options': bool(gf.generation_options),
            'username': gf.user.username if show_all else None,
            'created_at': gf.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'completed_at': gf.completed_at.strftime('%Y-%m-%dT%H:%M:%SZ') if gf.completed_at else None,
        })
    
    return jsonify(result)


@user_bp.route('/files/<int:file_id>/filter')
@login_required
def files_filter_data(file_id):
    """API: get saved filter data from a generated file (for reuse)"""
    gen_file = GeneratedFile.query.get_or_404(file_id)
    
    if gen_file.user_id != current_user.id and not current_user.is_super_admin:
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
    
    if gen_file.user_id != current_user.id and not current_user.is_super_admin:
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


@user_bp.route('/files/<int:file_id>', methods=['DELETE'])
@login_required
def files_delete(file_id):
    """API: delete a generated file (DB record + file on disk)"""
    gen_file = GeneratedFile.query.get_or_404(file_id)
    
    if gen_file.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    # Delete file from disk
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
        if gen_file.user_id != current_user.id and not current_user.is_super_admin:
            continue
        # Delete file from disk
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

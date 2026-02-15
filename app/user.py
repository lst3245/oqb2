"""
User-facing routes: Saved Search Profiles and My Generated Files
"""
from flask import Blueprint, render_template, request, jsonify, current_app, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models import SavedFilter, GeneratedFile
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
    """API: list saved profiles (JSON)"""
    show_all = request.args.get('show_all', '0') == '1' and current_user.is_super_admin
    
    if show_all:
        filters = SavedFilter.query.order_by(SavedFilter.created_at.desc()).all()
    else:
        filters = SavedFilter.query.filter_by(user_id=current_user.id).order_by(SavedFilter.created_at.desc()).all()
    
    result = []
    for f in filters:
        try:
            filter_data = json.loads(f.filter_data)
        except (json.JSONDecodeError, TypeError):
            filter_data = {}
        
        result.append({
            'id': f.id,
            'name': f.name,
            'subject': filter_data.get('subject', ''),
            'source_type': filter_data.get('source_type', ''),
            'username': f.user.username if show_all else None,
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
    """API: get filter data for a profile (for restoring on dashboard)"""
    profile = SavedFilter.query.get_or_404(profile_id)
    
    # Only owner or super admin can access
    if profile.user_id != current_user.id and not current_user.is_super_admin:
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


# ==================== Generated Files ====================

@user_bp.route('/files')
@login_required
def files():
    """My generated files page"""
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

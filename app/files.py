"""
Shared file-browser blueprint (``/files``).

Backs BOTH the super-admin browser page (``/admin/files``, rendered by
``app/admin.py``) and the per-user browser page (``/files/browser``) via one
root-aware, permission-checked JSON API (``/files/api/*``). Every request
resolves its ``root`` through :class:`app.files_service.RootRegistry`, so the
caller automatically gets only the roots they may see, with the correct
``can_write`` flag.

The personal ``generated/`` subfolder is read-only here (it's managed via the
My Files page) so browser edits never orphan ``GeneratedFile`` rows.
"""
from __future__ import annotations

import os

from flask import (Blueprint, render_template, request, jsonify, send_file,
                   abort)
from flask_login import login_required, current_user

from app import files_service
from app.files_service import RootRegistry, FileServiceError, ROOT_USER


files_bp = Blueprint('files', __name__, url_prefix='/files')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request_scope():
    """Resolve the requested root scope. ``admin`` is honoured only for
    super-admins (enforced again in :class:`RootRegistry`); everyone else —
    and super-admins by default — gets ``user`` scope."""
    scope = request.args.get('scope')
    if scope is None and request.form:
        scope = request.form.get('scope')
    if scope is None and request.is_json:
        scope = (request.get_json(silent=True) or {}).get('scope')
    return 'admin' if scope == 'admin' else 'user'


def _request_root():
    """Resolve the selected :class:`Root` from the request for this user."""
    root_id = request.args.get('root')
    if root_id is None and request.form:
        root_id = request.form.get('root')
    if root_id is None and request.is_json:
        body = request.get_json(silent=True) or {}
        root_id = body.get('root')
    return RootRegistry(current_user, scope=_request_scope()).resolve(root_id)


def _can_use_browser(user) -> bool:
    """Pure viewers get no file browser; everyone else (super-admin or any
    'user'/'admin' subject role) does."""
    if getattr(user, 'is_super_admin', False):
        return True
    try:
        return not user.is_all_view_only()
    except Exception:
        return False


def _is_generated_path(root, rel_path) -> bool:
    """True when a write target lands inside the personal ``generated/``
    subfolder (managed by My Files; read-only in the browser)."""
    if getattr(root, 'id', None) != ROOT_USER:
        return False
    rel = (rel_path or '').strip('/').strip('\\').replace('\\', '/')
    first = rel.split('/', 1)[0] if rel else ''
    return first.lower() == 'generated'


def _err(e: FileServiceError):
    return jsonify({'error': e.message}), e.status


def _generated_readonly_response():
    return jsonify({'error': 'The generated/ folder is managed in My Files and '
                             'is read-only here.'}), 403


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@files_bp.route('/browser')
@login_required
def browser():
    """Per-user file browser page (Shared + personal User folders).

    Always **user scope**, even for super-admins — the admin root set lives on
    the separate ``/admin/files`` page.
    """
    if not _can_use_browser(current_user):
        abort(403)
    from app.files_service import allowed_drive
    reg = RootRegistry(current_user, scope='user')
    files_service.ensure_root_dir(reg.list()[0]) if reg.list() else None
    return render_template(
        'files_browser.html',
        roots=reg.list_dicts(),
        allowed_drive=allowed_drive(),
        fb_can_manage_roots=False,
        fb_scope='user',
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@files_bp.route('/api/roots')
@login_required
def api_roots():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    from app.files_service import allowed_drive
    return jsonify({
        'roots': RootRegistry(current_user, scope=_request_scope()).list_dicts(),
        'allowed_drive': allowed_drive(),
    })


@files_bp.route('/api/list')
@login_required
def api_list():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    files_service.ensure_root_dir(root)
    try:
        return jsonify(files_service.list_dir(root.path, request.args.get('path', '')))
    except FileServiceError as e:
        return _err(e)


@files_bp.route('/api/download')
@login_required
def api_download():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    try:
        full = files_service.get_download_path(root.path, request.args.get('path', ''))
    except FileServiceError as e:
        return _err(e)
    return send_file(full, as_attachment=True)


@files_bp.route('/api/upload', methods=['POST'])
@login_required
def api_upload():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    rel_path = request.form.get('path', '')
    if _is_generated_path(root, rel_path):
        return _generated_readonly_response()
    try:
        return jsonify(files_service.save_uploads(
            root.path, rel_path, request.files.getlist('files'), root.can_write,
            rel_paths=request.form.getlist('paths')))
    except FileServiceError as e:
        return _err(e)


@files_bp.route('/api/rename', methods=['POST'])
@login_required
def api_rename():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    data = request.get_json(silent=True) or {}
    if _is_generated_path(root, data.get('path', '')):
        return _generated_readonly_response()
    try:
        return jsonify(files_service.rename(
            root.path, data.get('path', ''), data.get('new_name', ''), root.can_write))
    except FileServiceError as e:
        return _err(e)


@files_bp.route('/api/delete', methods=['POST'])
@login_required
def api_delete():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', []) or []
    if any(_is_generated_path(root, p) for p in paths):
        return _generated_readonly_response()
    try:
        return jsonify(files_service.delete(root.path, paths, root.can_write))
    except FileServiceError as e:
        return _err(e)


@files_bp.route('/api/mkdir', methods=['POST'])
@login_required
def api_mkdir():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    data = request.get_json(silent=True) or {}
    if _is_generated_path(root, data.get('path', '')):
        return _generated_readonly_response()
    try:
        return jsonify(files_service.mkdir(
            root.path, data.get('path', ''), data.get('name', ''), root.can_write))
    except FileServiceError as e:
        return _err(e)


@files_bp.route('/api/copy', methods=['POST'])
@login_required
def api_copy():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    data = request.get_json(silent=True) or {}
    if _is_generated_path(root, data.get('dest_dir', '')):
        return _generated_readonly_response()
    try:
        return jsonify(files_service.copy(
            root.path, data.get('sources', []), data.get('dest_dir', ''), root.can_write))
    except FileServiceError as e:
        return _err(e)


@files_bp.route('/api/move', methods=['POST'])
@login_required
def api_move():
    if not _can_use_browser(current_user):
        return jsonify({'error': 'Access denied'}), 403
    root = _request_root()
    if root is None:
        return jsonify({'error': 'Invalid root'}), 400
    data = request.get_json(silent=True) or {}
    sources = data.get('sources', []) or []
    dest_dir = data.get('dest_dir', '')
    # Block moving into — or out of — the read-only generated/ folder.
    if _is_generated_path(root, dest_dir) or any(_is_generated_path(root, s) for s in sources):
        return _generated_readonly_response()
    try:
        return jsonify(files_service.move(
            root.path, sources, dest_dir, root.can_write))
    except FileServiceError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Root management (super admin only)
# ---------------------------------------------------------------------------

@files_bp.route('/api/roots/add', methods=['POST'])
@login_required
def api_roots_add():
    if not getattr(current_user, 'is_super_admin', False):
        return jsonify({'error': 'Access denied'}), 403
    from app.files_service import allowed_drive, path_on_allowed_drive
    data = request.get_json(silent=True) or {}
    raw = (data.get('path') or '').strip().strip('"')
    if not raw:
        return jsonify({'error': 'Path is required'}), 400
    abs_path = os.path.abspath(raw)
    if not path_on_allowed_drive(abs_path):
        return jsonify({'error': f'Root must be on the {allowed_drive()} drive.'}), 400
    if not os.path.isdir(abs_path):
        return jsonify({'error': 'Path does not exist or is not a directory.'}), 400

    existing = {os.path.normcase(r.path)
                for r in RootRegistry(current_user, scope='admin').list()}
    if os.path.normcase(abs_path) in existing:
        return jsonify({'error': 'That root is already registered.'}), 409

    roots = files_service._load_extra_roots()
    roots.append(abs_path)
    files_service._save_extra_roots(roots)
    return jsonify({'success': True,
                    'roots': RootRegistry(current_user, scope='admin').list_dicts()})


@files_bp.route('/api/roots/remove', methods=['POST'])
@login_required
def api_roots_remove():
    if not getattr(current_user, 'is_super_admin', False):
        return jsonify({'error': 'Access denied'}), 403
    data = request.get_json(silent=True) or {}
    root_id = (data.get('id') or '').strip()
    if not root_id:
        return jsonify({'error': 'Root id is required'}), 400
    if not root_id.startswith('extra:'):
        return jsonify({'error': 'Only added roots can be removed.'}), 400
    target = os.path.normcase(root_id[len('extra:'):])
    roots = files_service._load_extra_roots()
    kept = [p for p in roots if os.path.normcase(p) != target]
    if len(kept) == len(roots):
        return jsonify({'error': 'Root not found.'}), 404
    files_service._save_extra_roots(kept)
    return jsonify({'success': True,
                    'roots': RootRegistry(current_user, scope='admin').list_dicts()})

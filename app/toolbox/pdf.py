"""
PDF Tool — routes and session staging for the Toolbox.

Staging: ``OUTPUT_PATH/.toolbox/<token>/`` (``session.json`` + ``sources/*.pdf``).
Processing primitives live in :mod:`app.pdf_tools`.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime

from flask import abort, current_app, jsonify, render_template, request, send_file
from flask_login import login_required

from app import pdf_tools
from app.toolbox import toolbox_bp
from app.toolbox.common import pdf_source_root, safe_filename, safe_join
from app.utils import admin_required

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r'^[0-9a-f]{8,40}$')
_ID_RE = re.compile(r'^[0-9a-f]{6,40}$')


# ==================== Staging ====================

def _staging_root() -> str:
    return os.path.join(current_app.config['OUTPUT_PATH'], '.toolbox')


def _token_dir(token: str) -> str:
    if not _TOKEN_RE.match(token or ''):
        raise ValueError('invalid session token')
    return os.path.join(_staging_root(), token)


def _sources_dir(token: str) -> str:
    return os.path.join(_token_dir(token), 'sources')


def _source_path(token: str, srcid: str) -> str:
    if not _ID_RE.match(srcid or ''):
        raise ValueError('invalid source id')
    return os.path.join(_sources_dir(token), f'{srcid}.pdf')


def _session_path(token: str) -> str:
    return os.path.join(_token_dir(token), 'session.json')


def _load_session(token: str) -> dict:
    with open(_session_path(token), 'r', encoding='utf-8') as f:
        data = json.load(f)
    data.setdefault('sources', {})
    data.setdefault('pages', [])
    return data


def _save_session(token: str, data: dict) -> None:
    path = _session_path(token)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _new_session() -> str:
    _cleanup_old()
    token = uuid.uuid4().hex
    os.makedirs(_sources_dir(token), exist_ok=True)
    _save_session(token, {'created_at': datetime.utcnow().isoformat(),
                          'sources': {}, 'pages': []})
    return token


def _cleanup_old(max_age_hours: float = 12.0) -> None:
    root = _staging_root()
    if not os.path.isdir(root):
        return
    cutoff = time.time() - max_age_hours * 3600.0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _resolver(token: str):
    return lambda srcid: _source_path(token, srcid)


def _numpy_ok() -> bool:
    try:
        from app import pdf_layout
        return pdf_layout.numpy_available()
    except Exception:
        return False


_ALLOWED_OPS = {'rotate', 'rotate_fine', 'crop', 'deskew', 'brightness',
                'contrast', 'sharpen', 'grayscale', 'bw'}


def _sanitize_ops(ops):
    clean = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        t = op.get('type')
        if t not in _ALLOWED_OPS:
            continue
        if t == 'rotate':
            clean.append({'type': t, 'deg': int(op.get('deg', 0)) % 360})
        elif t == 'rotate_fine':
            clean.append({'type': t, 'deg': float(op.get('deg', 0.0))})
        elif t == 'crop':
            box = op.get('box')
            if isinstance(box, (list, tuple)) and len(box) == 4:
                clean.append({'type': t, 'box': [float(v) for v in box]})
        elif t in ('brightness', 'contrast', 'sharpen'):
            clean.append({'type': t, 'factor': float(op.get('factor', 1.0))})
        elif t == 'bw':
            clean.append({'type': t,
                          'threshold': max(0, min(255, int(op.get('threshold', 160))))})
        else:
            clean.append({'type': t})
    return clean


def _select_pages(session, page_ids):
    by_id = {p['id']: p for p in session['pages']}
    if page_ids:
        return [by_id[i] for i in page_ids if i in by_id]
    return list(session['pages'])


def _build_export_bytes(token, session, page_ids, fmt, split_every):
    pages = _select_pages(session, page_ids)
    if not pages:
        raise ValueError('No pages selected to export.')
    default_dpi = int(current_app.config.get('TOOLBOX_DEFAULT_DPI', 200))
    return pdf_tools.export_pages(pages, _resolver(token), fmt=fmt,
                                  default_dpi=default_dpi,
                                  split_every=split_every)


def _export_meta(fmt, split_every):
    if fmt == 'zip' or (split_every and int(split_every) > 0):
        return 'zip', 'application/zip'
    return 'pdf', 'application/pdf'


# ==================== Pages ====================

@toolbox_bp.route('/pdf')
@login_required
@admin_required
def pdf_tool():
    root = pdf_source_root()
    return render_template(
        'admin_toolbox_pdf.html',
        raster_width=int(current_app.config.get('TOOLBOX_RASTER_WIDTH', 1700)),
        export_width=int(current_app.config.get('TOOLBOX_EXPORT_WIDTH', 2200)),
        default_dpi=int(current_app.config.get('TOOLBOX_DEFAULT_DPI', 200)),
        save_subdir=str(current_app.config.get('TOOLBOX_SAVE_SUBDIR', 'Saved')),
        pdf_source_available=bool(root and os.path.isdir(root)),
        numpy_available=_numpy_ok(),
    )


# ==================== API ====================

@toolbox_bp.route('/pdf/upload', methods=['POST'])
@login_required
@admin_required
def pdf_upload():
    token = (request.form.get('token') or '').strip()
    if token:
        if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
            return jsonify({'error': 'Session expired — reload the page.'}), 400
    else:
        token = _new_session()

    srcid = uuid.uuid4().hex[:12]
    dest = _source_path(token, srcid)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    upload = request.files.get('pdf')
    if upload is not None and upload.filename:
        if not upload.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Please upload a .pdf file.'}), 400
        upload.save(dest)
        filename = upload.filename
    else:
        rel = (request.form.get('server_path') or '').strip().strip('/').strip('\\')
        root = pdf_source_root()
        if not root or not os.path.isdir(root):
            return jsonify({'error': 'The server source-PDF folder is not configured.'}), 400
        full = safe_join(root, rel) if rel else None
        if not full or not os.path.isfile(full) or not full.lower().endswith('.pdf'):
            return jsonify({'error': 'Select a PDF (upload one or pick from the server).'}), 400
        shutil.copyfile(full, dest)
        filename = os.path.basename(full)

    try:
        import fitz  # type: ignore
        doc = fitz.open(dest)
        try:
            page_count = doc.page_count
        finally:
            doc.close()
    except Exception as e:
        try:
            os.remove(dest)
        except OSError:
            pass
        return jsonify({'error': f'Could not read PDF: {e}'}), 400

    session = _load_session(token)
    session['sources'][srcid] = {'filename': filename, 'page_count': page_count}
    _save_session(token, session)

    return jsonify({'token': token, 'srcid': srcid, 'filename': filename,
                    'page_count': page_count,
                    'pages': [{'index': i} for i in range(page_count)]})


@toolbox_bp.route('/pdf/add-pages', methods=['POST'])
@login_required
@admin_required
def pdf_add_pages():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    srcid = (data.get('srcid') or '').strip()
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400

    session = _load_session(token)
    src = session['sources'].get(srcid)
    if not src:
        return jsonify({'error': 'Unknown source — upload it again.'}), 400

    mode = (data.get('mode') or 'none').strip().lower()
    if mode not in pdf_tools.SPLIT_MODES:
        mode = 'none'
    try:
        pre_rotate = int(data.get('pre_rotate') or 0) % 360
    except (TypeError, ValueError):
        pre_rotate = 0
    default_dpi = int(current_app.config.get('TOOLBOX_DEFAULT_DPI', 200))
    try:
        dpi = int(data.get('dpi') or default_dpi)
    except (TypeError, ValueError):
        dpi = default_dpi
    dpi = max(72, min(600, dpi))
    filters = data.get('filters') or {}

    frags = pdf_tools.split_descriptors(src['page_count'], mode)
    appended = []
    for frag in frags:
        ops = pdf_tools.build_op_chain(pre_rotate, frag['ops'], filters)
        page = {'id': uuid.uuid4().hex[:12], 'src': srcid,
                'page': int(frag['page']), 'ops': ops, 'mode': mode,
                'dpi': dpi}
        session['pages'].append(page)
        appended.append(page)

    _save_session(token, session)
    return jsonify({'token': token, 'added': len(appended), 'pages': appended,
                    'total': len(session['pages'])})


@toolbox_bp.route('/pdf/reorder', methods=['POST'])
@login_required
@admin_required
def pdf_reorder():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    order = data.get('order') or []
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400
    session = _load_session(token)
    by_id = {p['id']: p for p in session['pages']}
    new_pages = [by_id[i] for i in order if i in by_id]
    for p in session['pages']:
        if p['id'] not in order:
            new_pages.append(p)
    session['pages'] = new_pages
    _save_session(token, session)
    return jsonify({'ok': True, 'total': len(session['pages'])})


@toolbox_bp.route('/pdf/page-ops', methods=['POST'])
@login_required
@admin_required
def pdf_page_ops():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    page_id = (data.get('page_id') or '').strip()
    ops = data.get('ops')
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400
    if not isinstance(ops, list):
        return jsonify({'error': 'ops must be a list'}), 400
    ops = _sanitize_ops(ops)
    session = _load_session(token)
    found = None
    for p in session['pages']:
        if p['id'] == page_id:
            p['ops'] = ops
            found = p
            break
    if not found:
        return jsonify({'error': 'page not found'}), 404
    _save_session(token, session)
    return jsonify({'ok': True, 'page': found})


@toolbox_bp.route('/pdf/page-delete', methods=['POST'])
@login_required
@admin_required
def pdf_page_delete():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    page_ids = set(data.get('page_ids') or [])
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400
    session = _load_session(token)
    session['pages'] = [p for p in session['pages'] if p['id'] not in page_ids]
    _save_session(token, session)
    return jsonify({'ok': True, 'total': len(session['pages'])})


@toolbox_bp.route('/pdf/duplicate', methods=['POST'])
@login_required
@admin_required
def pdf_duplicate():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    page_ids = list(data.get('page_ids') or [])
    after_id = (data.get('after_id') or '').strip() or None
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400
    session = _load_session(token)
    by_id = {p['id']: p for p in session['pages']}
    clones = []
    for pid in page_ids:
        src = by_id.get(pid)
        if not src:
            continue
        clone = {'id': uuid.uuid4().hex[:12], 'src': src['src'],
                 'page': src['page'], 'ops': list(src.get('ops') or []),
                 'mode': src.get('mode', 'none'), 'dpi': src.get('dpi')}
        clones.append(clone)
    if not clones:
        return jsonify({'error': 'Nothing to paste.'}), 400

    pages = session['pages']
    insert_at = len(pages)
    if after_id:
        for i, p in enumerate(pages):
            if p['id'] == after_id:
                insert_at = i + 1
                break
    session['pages'] = pages[:insert_at] + clones + pages[insert_at:]
    _save_session(token, session)
    return jsonify({'ok': True, 'pages': clones,
                    'order': [p['id'] for p in session['pages']],
                    'total': len(session['pages'])})


@toolbox_bp.route('/pdf/set-pages', methods=['POST'])
@login_required
@admin_required
def pdf_set_pages():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    incoming = data.get('pages')
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400
    if not isinstance(incoming, list):
        return jsonify({'error': 'pages must be a list'}), 400
    session = _load_session(token)
    srcs = session.get('sources') or {}
    default_dpi = int(current_app.config.get('TOOLBOX_DEFAULT_DPI', 200))
    clean = []
    for it in incoming:
        if not isinstance(it, dict):
            continue
        srcid = (it.get('src') or '').strip()
        src = srcs.get(srcid)
        if not src:
            continue
        try:
            page = int(it.get('page'))
        except (TypeError, ValueError):
            continue
        if page < 0 or page >= int(src.get('page_count') or 0):
            continue
        pid = (it.get('id') or '').strip()
        if not _ID_RE.match(pid):
            pid = uuid.uuid4().hex[:12]
        mode = (it.get('mode') or 'none').strip().lower()
        if mode not in pdf_tools.SPLIT_MODES:
            mode = 'none'
        try:
            dpi = max(72, min(600, int(it.get('dpi') or default_dpi)))
        except (TypeError, ValueError):
            dpi = default_dpi
        clean.append({'id': pid, 'src': srcid, 'page': page,
                      'ops': _sanitize_ops(it.get('ops') or []),
                      'mode': mode, 'dpi': dpi})
    session['pages'] = clean
    _save_session(token, session)
    return jsonify({'ok': True, 'total': len(clean)})


@toolbox_bp.route('/pdf/thumb/<token>/<page_id>.png')
@login_required
@admin_required
def pdf_thumb(token, page_id):
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return abort(404)
    session = _load_session(token)
    page = next((p for p in session['pages'] if p['id'] == page_id), None)
    if not page:
        return abort(404)
    width = None
    dpi = None
    dpi_arg = request.args.get('dpi')
    if dpi_arg:
        try:
            dpi = max(72, min(600, int(dpi_arg)))
        except (TypeError, ValueError):
            dpi = int(page.get('dpi') or current_app.config.get('TOOLBOX_DEFAULT_DPI', 200))
    else:
        try:
            width = max(120, min(2400, int(request.args.get('w', 360))))
        except (TypeError, ValueError):
            width = 360
    try:
        img = pdf_tools.render_page_image(_source_path(token, page['src']),
                                          int(page['page']), page.get('ops') or [],
                                          width_px=width, dpi=dpi)
    except Exception as e:
        logger.warning('toolbox thumb render failed: %s', e)
        return abort(404)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@toolbox_bp.route('/pdf/src-thumb/<token>/<srcid>/<int:idx>.png')
@login_required
@admin_required
def pdf_src_thumb(token, srcid, idx):
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return abort(404)
    try:
        width = max(120, min(2400, int(request.args.get('w', 300))))
    except (TypeError, ValueError):
        width = 300
    try:
        img = pdf_tools.render_page_image(_source_path(token, srcid), idx, [],
                                          width_px=width)
    except Exception as e:
        logger.warning('toolbox src-thumb render failed: %s', e)
        return abort(404)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@toolbox_bp.route('/pdf/export', methods=['POST'])
@login_required
@admin_required
def pdf_export():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400
    fmt = (data.get('fmt') or 'pdf').strip().lower()
    if fmt not in ('pdf', 'zip'):
        fmt = 'pdf'
    try:
        split_every = int(data.get('split_every') or 0)
    except (TypeError, ValueError):
        split_every = 0
    session = _load_session(token)
    try:
        blob = _build_export_bytes(token, session, data.get('page_ids'), fmt,
                                   split_every)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception('toolbox export failed')
        return jsonify({'error': f'Export failed: {e}'}), 500

    ext, mime = _export_meta(fmt, split_every)
    stem = safe_filename(data.get('filename'), 'toolbox_export')
    buf = io.BytesIO(blob)
    buf.seek(0)
    return send_file(buf, mimetype=mime, as_attachment=True,
                     download_name=f'{stem}.{ext}')


@toolbox_bp.route('/pdf/export-save', methods=['POST'])
@login_required
@admin_required
def pdf_export_save():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400

    root = pdf_source_root()
    if not root or not os.path.isdir(root):
        return jsonify({'error': 'The server source-PDF folder is not configured.'}), 400
    subdir = (data.get('dest') or '').strip().strip('/').strip('\\')
    if not subdir:
        subdir = str(current_app.config.get('TOOLBOX_SAVE_SUBDIR', 'Saved'))
    save_dir = safe_join(root, subdir) if subdir else root
    if not save_dir:
        return jsonify({'error': 'Invalid save directory.'}), 400
    os.makedirs(save_dir, exist_ok=True)
    subdir = os.path.relpath(save_dir, root).replace('\\', '/')

    fmt = (data.get('fmt') or 'pdf').strip().lower()
    if fmt not in ('pdf', 'zip'):
        fmt = 'pdf'
    try:
        split_every = int(data.get('split_every') or 0)
    except (TypeError, ValueError):
        split_every = 0
    ext, _mime = _export_meta(fmt, split_every)

    raw_name = (data.get('filename') or '').strip()
    if not raw_name:
        return jsonify({'error': 'Enter a file name.'}), 400
    stem = safe_filename(raw_name, '')
    if not stem:
        return jsonify({'error': 'Enter a valid file name.'}), 400
    dest = safe_join(save_dir, f'{stem}.{ext}')
    if not dest:
        return jsonify({'error': 'Invalid filename.'}), 400

    overwrite = str(data.get('overwrite') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    if os.path.exists(dest) and not overwrite:
        return jsonify({'exists': True,
                        'filename': f'{stem}.{ext}',
                        'subdir': subdir,
                        'error': f'"{stem}.{ext}" already exists in {subdir}.'}), 409

    session = _load_session(token)
    try:
        blob = _build_export_bytes(token, session, data.get('page_ids'), fmt,
                                   split_every)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception('toolbox export-save failed')
        return jsonify({'error': f'Export failed: {e}'}), 500

    with open(dest, 'wb') as f:
        f.write(blob)
    rel = os.path.relpath(dest, root).replace('\\', '/')
    return jsonify({'ok': True, 'saved': rel,
                    'path': dest, 'subdir': subdir})


@toolbox_bp.route('/pdf/mkdir', methods=['POST'])
@login_required
@admin_required
def pdf_mkdir():
    data = request.get_json(silent=True) or {}
    root = pdf_source_root()
    if not root or not os.path.isdir(root):
        return jsonify({'error': 'The server source-PDF folder is not configured.'}), 400
    parent_rel = (data.get('path') or '').strip().strip('/').strip('\\')
    name = (data.get('name') or '').strip()
    name = re.sub(r'[^\w\-. ]+', '_', name).strip('. ')
    if not name:
        return jsonify({'error': 'Enter a folder name.'}), 400
    parent = safe_join(root, parent_rel) if parent_rel else root
    if not parent or not os.path.isdir(parent):
        return jsonify({'error': 'Parent folder not found.'}), 400
    target = safe_join(parent, name)
    if not target:
        return jsonify({'error': 'Invalid folder name.'}), 400
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        return jsonify({'error': f'Could not create folder: {e}'}), 400
    rel = os.path.relpath(target, root).replace('\\', '/')
    return jsonify({'ok': True, 'rel_path': rel, 'name': name})


@toolbox_bp.route('/pdf/discard', methods=['POST'])
@login_required
@admin_required
def pdf_discard():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not _TOKEN_RE.match(token):
        return jsonify({'ok': False}), 400
    d = _token_dir(token)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    return jsonify({'ok': True})

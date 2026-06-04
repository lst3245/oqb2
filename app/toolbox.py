"""
Admin **Toolbox** — a home for self-service utilities (subject-admins + super
admins). The first tool is the **PDF Tool**: upload PDF(s), apply A3-booklet
splitting / deskew / rotation / brightness-sharpen-B&W, assemble pages in a
drag-reorder preview, then export (PDF or ZIP, download or save-to-server).

Processing primitives live in :mod:`app.pdf_tools`; this module is the Flask
blueprint + per-session staging on disk. Staging mirrors the PDF Batch Import
layout: ``OUTPUT_PATH/.toolbox/<token>/`` holds ``session.json`` and
``sources/<srcid>.pdf``. Tokens are regex-validated before any filesystem join.
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

from flask import (Blueprint, abort, current_app, jsonify, render_template,
                   request, send_file)
from flask_login import login_required

from app import pdf_tools
from app.utils import admin_required

logger = logging.getLogger(__name__)

toolbox_bp = Blueprint('toolbox', __name__, url_prefix='/admin/toolbox')

_TOKEN_RE = re.compile(r'^[0-9a-f]{8,40}$')
_ID_RE = re.compile(r'^[0-9a-f]{6,40}$')


# ==================== Staging helpers ====================

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
    """Return a ``resolve_path(srcid) -> abs pdf path`` closure for export."""
    return lambda srcid: _source_path(token, srcid)


# ==================== Server-PDF picking (under PDF_SOURCE_PATH) ====================

def _pdf_source_root() -> str:
    return os.path.abspath(current_app.config.get('PDF_SOURCE_PATH', ''))


def _safe_join(base: str, *paths) -> str | None:
    base = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base, *paths))
    if not os.path.normcase(target).startswith(os.path.normcase(base)):
        return None
    return target


def _safe_filename(name: str, fallback: str) -> str:
    name = re.sub(r'[^\w\-. ]+', '_', (name or '').strip())
    name = re.sub(r'\s+', '_', name).strip('._')
    return name[:80] or fallback


# ==================== Pages ====================

@toolbox_bp.route('/')
@login_required
@admin_required
def index():
    """Toolbox landing page — a grid of available tools."""
    return render_template('admin_toolbox.html')


@toolbox_bp.route('/pdf')
@login_required
@admin_required
def pdf_tool():
    """The PDF Tool page."""
    root = _pdf_source_root()
    return render_template(
        'admin_toolbox_pdf.html',
        raster_width=int(current_app.config.get('TOOLBOX_RASTER_WIDTH', 1700)),
        export_width=int(current_app.config.get('TOOLBOX_EXPORT_WIDTH', 2200)),
        save_subdir=str(current_app.config.get('TOOLBOX_SAVE_SUBDIR', 'Saved')),
        pdf_source_available=bool(root and os.path.isdir(root)),
        numpy_available=_numpy_ok(),
    )


def _numpy_ok() -> bool:
    try:
        from app import pdf_layout
        return pdf_layout.numpy_available()
    except Exception:
        return False


# ==================== API: upload a source PDF ====================

@toolbox_bp.route('/pdf/upload', methods=['POST'])
@login_required
@admin_required
def pdf_upload():
    """Stage one source PDF (uploaded or picked from PDF_SOURCE_PATH).

    Form fields: ``token`` (optional — created when absent), ``pdf`` (file)
    OR ``server_path`` (relative to PDF_SOURCE_PATH). Returns the source id +
    page count.
    """
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
        root = _pdf_source_root()
        if not root or not os.path.isdir(root):
            return jsonify({'error': 'The server source-PDF folder is not configured.'}), 400
        full = _safe_join(root, rel) if rel else None
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


# ==================== API: add processed pages to the preview ====================

@toolbox_bp.route('/pdf/add-pages', methods=['POST'])
@login_required
@admin_required
def pdf_add_pages():
    """Expand a source into page descriptors (split + op chain) and append
    them to the working set. Body: ``{token, srcid, mode, pre_rotate,
    filters}``."""
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
    filters = data.get('filters') or {}

    frags = pdf_tools.split_descriptors(src['page_count'], mode)
    appended = []
    for frag in frags:
        ops = pdf_tools.build_op_chain(pre_rotate, frag['ops'], filters)
        page = {'id': uuid.uuid4().hex[:12], 'src': srcid,
                'page': int(frag['page']), 'ops': ops, 'mode': mode}
        session['pages'].append(page)
        appended.append(page)

    _save_session(token, session)
    return jsonify({'token': token, 'added': len(appended), 'pages': appended,
                    'total': len(session['pages'])})


# ==================== API: reorder / edit / delete pages ====================

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
    # Keep any pages the client did not mention (defensive) at the end.
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
    """Replace one page's op chain (per-page processing). Body: ``{token,
    page_id, ops}``."""
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


_ALLOWED_OPS = {'rotate', 'rotate_fine', 'crop', 'deskew', 'brightness',
                'contrast', 'sharpen', 'grayscale', 'bw'}


def _sanitize_ops(ops):
    """Validate/normalise a client-supplied op list (defensive — never trust
    raw JSON for the renderer)."""
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
        else:  # deskew / grayscale
            clean.append({'type': t})
    return clean


# ==================== API: thumbnails ====================

@toolbox_bp.route('/pdf/thumb/<token>/<page_id>.png')
@login_required
@admin_required
def pdf_thumb(token, page_id):
    """Render a working-set page (with its op chain) to a PNG for the preview."""
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return abort(404)
    try:
        width = max(120, min(2400, int(request.args.get('w', 360))))
    except (TypeError, ValueError):
        width = 360
    session = _load_session(token)
    page = next((p for p in session['pages'] if p['id'] == page_id), None)
    if not page:
        return abort(404)
    try:
        img = pdf_tools.render_page_image(_source_path(token, page['src']),
                                          int(page['page']), page.get('ops') or [],
                                          width)
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
    """Render a raw source page (no ops) for the upload preview strip."""
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return abort(404)
    try:
        width = max(120, min(2400, int(request.args.get('w', 300))))
    except (TypeError, ValueError):
        width = 300
    try:
        img = pdf_tools.render_page_image(_source_path(token, srcid), idx, [], width)
    except Exception as e:
        logger.warning('toolbox src-thumb render failed: %s', e)
        return abort(404)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


# ==================== API: export ====================

def _select_pages(session, page_ids):
    by_id = {p['id']: p for p in session['pages']}
    if page_ids:
        return [by_id[i] for i in page_ids if i in by_id]
    return list(session['pages'])


def _build_export_bytes(token, session, page_ids, fmt, split_every):
    pages = _select_pages(session, page_ids)
    if not pages:
        raise ValueError('No pages selected to export.')
    width = int(current_app.config.get('TOOLBOX_EXPORT_WIDTH', 2200))
    return pdf_tools.export_pages(pages, _resolver(token), fmt=fmt,
                                  width_px=width, split_every=split_every)


def _export_meta(fmt, split_every):
    """Return ``(extension, mimetype)`` for the export request."""
    if fmt == 'zip' or (split_every and int(split_every) > 0):
        return 'zip', 'application/zip'
    return 'pdf', 'application/pdf'


@toolbox_bp.route('/pdf/export', methods=['POST'])
@login_required
@admin_required
def pdf_export():
    """Build and download the assembled pages. Body: ``{token, page_ids?,
    fmt:'pdf'|'zip', split_every?, filename?}``."""
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
    stem = _safe_filename(data.get('filename'), 'toolbox_export')
    buf = io.BytesIO(blob)
    buf.seek(0)
    return send_file(buf, mimetype=mime, as_attachment=True,
                     download_name=f'{stem}.{ext}')


@toolbox_bp.route('/pdf/export-save', methods=['POST'])
@login_required
@admin_required
def pdf_export_save():
    """Build the assembled pages and save them under
    ``PDF_SOURCE_PATH/<TOOLBOX_SAVE_SUBDIR>/``. Body: ``{token, page_ids?,
    fmt, split_every?, filename}``."""
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400

    root = _pdf_source_root()
    if not root or not os.path.isdir(root):
        return jsonify({'error': 'The server source-PDF folder is not configured.'}), 400
    subdir = str(current_app.config.get('TOOLBOX_SAVE_SUBDIR', 'Saved'))
    save_dir = _safe_join(root, subdir)
    if not save_dir:
        return jsonify({'error': 'Invalid save directory.'}), 400
    os.makedirs(save_dir, exist_ok=True)

    fmt = (data.get('fmt') or 'pdf').strip().lower()
    if fmt not in ('pdf', 'zip'):
        fmt = 'pdf'
    try:
        split_every = int(data.get('split_every') or 0)
    except (TypeError, ValueError):
        split_every = 0
    ext, _mime = _export_meta(fmt, split_every)

    stem = _safe_filename(data.get('filename'), 'toolbox_export')
    dest = _safe_join(save_dir, f'{stem}.{ext}')
    if not dest:
        return jsonify({'error': 'Invalid filename.'}), 400
    # Don't clobber an existing file — suffix _2, _3, ...
    if os.path.exists(dest):
        n = 2
        while os.path.exists(_safe_join(save_dir, f'{stem}_{n}.{ext}')):
            n += 1
        dest = _safe_join(save_dir, f'{stem}_{n}.{ext}')

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

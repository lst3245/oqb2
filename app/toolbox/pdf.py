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

from flask import (Response, abort, current_app, jsonify, render_template,
                   request, send_file)
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

_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
_ANNOT_MAX_PER_PAGE = 500
_ANNOT_MAX_INK_POINTS = 2000
_ANNOT_MAX_TEXT_LEN = 300


def _f01(v, default=0.0):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def _sanitize_annots(annots):
    """Re-validate a client-supplied annotation list (see pdf_tools docstring
    for the canonical shapes). Bad entries are dropped silently."""
    clean = []
    for a in annots or []:
        if not isinstance(a, dict):
            continue
        kind = a.get('kind')
        if kind not in ('redact', 'highlight', 'text', 'ink'):
            continue
        aid = (str(a.get('id') or '')).strip()[:40]
        if not re.match(r'^[0-9a-zA-Z_\-]{1,40}$', aid):
            aid = uuid.uuid4().hex[:12]
        color = a.get('color') if _HEX_COLOR_RE.match(str(a.get('color') or '')) else None
        item = {'id': aid, 'kind': kind}
        if a.get('pending'):
            item['pending'] = True
        if kind in ('redact', 'highlight'):
            rect = a.get('rect')
            if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
                continue
            item['rect'] = [_f01(v) for v in rect]
            item['color'] = color or ('#000000' if kind == 'redact' else '#ffff00')
            if kind == 'highlight':
                item['opacity'] = _f01(a.get('opacity', 0.4), 0.4) or 0.4
        elif kind == 'text':
            # Keep internal newlines (multi-line text), trim the outside.
            text = str(a.get('text') or '').strip()[:_ANNOT_MAX_TEXT_LEN]
            pos = a.get('pos')
            if not text or not (isinstance(pos, (list, tuple)) and len(pos) == 2):
                continue
            item['text'] = text
            item['pos'] = [_f01(pos[0]), _f01(pos[1])]
            try:
                item['size'] = max(0.004, min(0.25, float(a.get('size', 0.025))))
            except (TypeError, ValueError):
                item['size'] = 0.025
            if a.get('font') in ('sans', 'serif', 'mono'):
                item['font'] = a['font']
            item['color'] = color or '#d00000'
        elif kind == 'ink':
            pts = a.get('points')
            if not (isinstance(pts, (list, tuple)) and len(pts) >= 2):
                continue
            pp = []
            for p in pts[:_ANNOT_MAX_INK_POINTS]:
                if not (isinstance(p, (list, tuple)) and len(p) >= 2):
                    continue
                pp.append([_f01(p[0]), _f01(p[1])])
            if len(pp) < 2:
                continue
            item['points'] = pp
            try:
                item['width'] = max(0.0005, min(0.05, float(a.get('width', 0.004))))
            except (TypeError, ValueError):
                item['width'] = 0.004
            item['opacity'] = _f01(a.get('opacity', 1.0), 1.0) or 1.0
            item['color'] = color or '#0000ff'
        clean.append(item)
        if len(clean) >= _ANNOT_MAX_PER_PAGE:
            break
    return clean


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


def _build_export_bytes(token, session, page_ids, fmt, split_every,
                        output='digital'):
    pages = _select_pages(session, page_ids)
    if not pages:
        raise ValueError('No pages selected to export.')
    default_dpi = int(current_app.config.get('TOOLBOX_DEFAULT_DPI', 200))
    return pdf_tools.export_pages(pages, _resolver(token), fmt=fmt,
                                  default_dpi=default_dpi,
                                  split_every=split_every,
                                  output=output)


def _export_meta(fmt, split_every):
    if fmt == 'zip' or (split_every and int(split_every) > 0):
        return 'zip', 'application/zip'
    return 'pdf', 'application/pdf'


# ==================== Pages ====================

@toolbox_bp.route('/pdf')
@login_required
@admin_required
def pdf_tool():
    from app import pdf_text
    root = pdf_source_root()
    return render_template(
        'admin_toolbox_pdf.html',
        raster_width=int(current_app.config.get('TOOLBOX_RASTER_WIDTH', 1700)),
        export_width=int(current_app.config.get('TOOLBOX_EXPORT_WIDTH', 2200)),
        default_dpi=int(current_app.config.get('TOOLBOX_DEFAULT_DPI', 200)),
        save_subdir=str(current_app.config.get('TOOLBOX_SAVE_SUBDIR', 'Saved')),
        pdf_source_available=bool(root and os.path.isdir(root)),
        numpy_available=_numpy_ok(),
        ocr_available=pdf_text.ocr_available(
            current_app.config.get('TESSERACT_CMD', '')),
        ai_enabled=bool(current_app.config.get('AI_TOOLS_ENABLED', True)),
        ocr_dpi=int(current_app.config.get('TOOLBOX_OCR_DPI', 300)),
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
                'dpi': dpi, 'annots': []}
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
                 'mode': src.get('mode', 'none'), 'dpi': src.get('dpi'),
                 'annots': json.loads(json.dumps(src.get('annots') or []))}
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
                      'mode': mode, 'dpi': dpi,
                      'annots': _sanitize_annots(it.get('annots') or [])})
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
    # ``annots=0`` returns the clean page (annotation-editor background).
    want_annots = request.args.get('annots', '1') not in ('0', 'false', 'no')
    annots = page.get('annots') if want_annots else None
    try:
        img = pdf_tools.render_page_image(_source_path(token, page['src']),
                                          int(page['page']), page.get('ops') or [],
                                          width_px=width, dpi=dpi,
                                          annots=annots)
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
    output = (data.get('output') or 'digital').strip().lower()
    if output not in ('digital', 'image'):
        output = 'digital'
    try:
        split_every = int(data.get('split_every') or 0)
    except (TypeError, ValueError):
        split_every = 0
    session = _load_session(token)
    try:
        blob = _build_export_bytes(token, session, data.get('page_ids'), fmt,
                                   split_every, output)
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
    output = (data.get('output') or 'digital').strip().lower()
    if output not in ('digital', 'image'):
        output = 'digital'
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
                                   split_every, output)
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


# ==================== Find & Mark (text / OCR / LLM search) ====================

def _words_cache_dir(token: str) -> str:
    return os.path.join(_token_dir(token), 'words')


@toolbox_bp.route('/pdf/text-search', methods=['POST'])
@login_required
@admin_required
def pdf_text_search():
    """Locate phrases on working-set pages via the PDF text layer or OCR.

    Body: ``{token, page_ids?, engine: 'auto'|'digital'|'ocr',
    terms: [str, ...], fuzzy, threshold, case_sensitive}``.
    Returns ``{results: {page_id: [{rect, term}]}, engines: {page_id: str},
    skipped: [{page_id, reason}]}`` — rects are fractional post-ops coords.
    """
    from app import pdf_text

    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return jsonify({'error': 'Session expired — reload the page.'}), 400

    terms = [str(t or '').strip()[:300] for t in (data.get('terms') or [])]
    terms = [t for t in terms if t]
    if not terms:
        return jsonify({'error': 'Enter at least one search term.'}), 400

    engine = (data.get('engine') or 'auto').strip().lower()
    if engine not in pdf_text.ENGINES:
        engine = 'auto'
    fuzzy = bool(data.get('fuzzy', True))
    try:
        threshold = max(50, min(100, int(data.get('threshold') or 85)))
    except (TypeError, ValueError):
        threshold = 85
    case_sensitive = bool(data.get('case_sensitive', False))

    tess_cmd = pdf_text.resolve_tesseract(
        current_app.config.get('TESSERACT_CMD', ''))
    ocr_ok = pdf_text.ocr_available(
        current_app.config.get('TESSERACT_CMD', ''))
    if engine == 'ocr' and not ocr_ok:
        return jsonify({'error': 'OCR is not available on this server '
                                 '(install Tesseract or set TESSERACT_CMD).'}), 400

    ocr_dpi = int(current_app.config.get('TOOLBOX_OCR_DPI', 300))
    session = _load_session(token)
    pages = _select_pages(session, data.get('page_ids'))
    if not pages:
        return jsonify({'error': 'No pages to search.'}), 400

    cache_dir = _words_cache_dir(token)
    page_words = []
    engines_used = {}
    skipped = []
    for p in pages:
        src_path = _source_path(token, p['src'])
        ops = p.get('ops') or []
        use = None
        if engine in ('auto', 'digital') and pdf_text.digital_mappable(ops):
            try:
                if pdf_text.has_text_layer(src_path, int(p['page'])):
                    use = 'digital'
            except Exception as e:
                logger.warning('text-layer probe failed for %s: %s', p['id'], e)
        if use is None and engine in ('auto', 'ocr') and ocr_ok:
            use = 'ocr'
        if use is None:
            reason = ('no text layer and OCR unavailable' if engine == 'auto'
                      else 'no usable text layer'
                      if engine == 'digital' else 'OCR unavailable')
            skipped.append({'page_id': p['id'], 'reason': reason})
            continue
        try:
            words = pdf_text.get_page_words(cache_dir, p, src_path, use,
                                            ocr_dpi=ocr_dpi,
                                            tesseract_cmd=tess_cmd)
        except Exception as e:
            logger.warning('word extraction failed for %s: %s', p['id'], e)
            skipped.append({'page_id': p['id'], 'reason': str(e)})
            continue
        engines_used[p['id']] = use
        page_words.append((p['id'], words))

    results = pdf_text.find_matches(page_words, terms, fuzzy=fuzzy,
                                    threshold=threshold,
                                    case_sensitive=case_sensitive)
    results = {pid: boxes for pid, boxes in results.items() if boxes}
    return jsonify({'results': results, 'engines': engines_used,
                    'skipped': skipped,
                    'pages_scanned': len(page_words)})


@toolbox_bp.route('/pdf/llm-detect')
@login_required
@admin_required
def pdf_llm_detect():
    """SSE: vision-LLM region detection on working-set pages.

    Query params: ``token``, ``page_ids`` (csv), ``endpoint_id``,
    ``instruction`` (free-text request, e.g. built from the search phrases or
    a fully custom prompt), ``parallel`` (fan page round-trips out across the
    endpoint's ``max_concurrency`` — cloud endpoints only). Streams
    ``{type:'page', page_id, boxes:[{label, box:[x1,y1,x2,y2]}]}`` events
    (fractional coords, completion order when parallel), then ``done``.
    """
    from app import ai_prompts, llm_client, pdf_import
    from app.models import LLMConfig

    def _sse_error(msg):
        def gen():
            yield 'data: ' + json.dumps({'type': 'error', 'message': msg}) + '\n\n'
            yield 'data: ' + json.dumps({'type': 'done'}) + '\n\n'
        return Response(gen(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache',
                                 'X-Accel-Buffering': 'no'})

    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return _sse_error('AI features are disabled.')

    token = (request.args.get('token') or '').strip()
    if not _TOKEN_RE.match(token) or not os.path.isdir(_token_dir(token)):
        return _sse_error('Session expired — reload the page.')

    instruction = (request.args.get('instruction') or '').strip()[:2000]
    if not instruction:
        return _sse_error('Describe what to find first.')

    try:
        endpoint_id = int(request.args.get('endpoint_id', '0'))
    except (TypeError, ValueError):
        endpoint_id = 0
    if endpoint_id > 0:
        cfg = LLMConfig.query.get(endpoint_id)
        if cfg is None or not cfg.enabled or not cfg.supports_vision:
            return _sse_error('Selected LLM endpoint is unavailable or not '
                              'vision-capable.')
    else:
        cfg = llm_client.resolve_default_endpoint('PDF_IMPORT_DEFAULT_LLM')
        if cfg is None:
            return _sse_error('No vision-capable LLM endpoint is configured.')

    page_ids = [s for s in (request.args.get('page_ids') or '').split(',') if s]
    session = _load_session(token)
    pages = _select_pages(session, page_ids)
    if not pages:
        return _sse_error('No pages to scan.')

    coord_order = (current_app.config.get('PDF_IMPORT_COORD_ORDER', 'xyxy')
                   or 'xyxy')
    image_max_dim = int(current_app.config.get('LLM_IMAGE_MAX_DIM', 1600))
    system = ai_prompts.build_pdf_generic_system(instruction, coord_order)
    user_text = ai_prompts.build_pdf_generic_user_text(instruction, coord_order)

    want_parallel = (request.args.get('parallel', '0').strip().lower()
                     in ('1', 'true', 'yes', 'on'))
    workers = max(1, int(getattr(cfg, 'max_concurrency', 1) or 1))
    if not (want_parallel and getattr(cfg, 'kind', 'local') == 'cloud'
            and workers > 1):
        workers = 1

    app = current_app._get_current_object()
    job_id, cancel = pdf_import.new_job()
    src_paths = {p['id']: _source_path(token, p['src']) for p in pages}

    def _detect_one(p):
        img = pdf_tools.render_page_image(
            src_paths[p['id']], int(p['page']),
            p.get('ops') or [], width_px=image_max_dim)
        b64, mime = llm_client.prepare_image_from_pil(img, image_max_dim)
        text, _info = llm_client.chat(cfg, system, user_text,
                                      images=[(b64, mime)])
        return ai_prompts.parse_generic_boxes(
            text, img.size[0], img.size[1], coord_order)

    def generate():
        from app.parallel import run_parallel, CANCELLED
        with app.app_context():
            try:
                yield 'data: ' + json.dumps(
                    {'type': 'job', 'job_id': job_id, 'total': len(pages),
                     'workers': workers}) + '\n\n'
                if workers > 1:
                    yield 'data: ' + json.dumps(
                        {'type': 'info',
                         'message': f'Running {workers} pages in parallel.'}
                    ) + '\n\n'
                done = 0
                cancelled = False
                for r in run_parallel(app, cancel, pages, _detect_one,
                                      workers):
                    p = r['item']
                    if r['result'] is CANCELLED:
                        cancelled = True
                        continue
                    done += 1
                    if r['error'] is not None:
                        logger.warning('llm-detect failed for %s: %s',
                                       p['id'], r['error'])
                        yield 'data: ' + json.dumps(
                            {'type': 'error', 'page_id': p['id'],
                             'message': str(r['error']), 'current': done,
                             'total': len(pages)}) + '\n\n'
                        continue
                    yield 'data: ' + json.dumps(
                        {'type': 'page', 'page_id': p['id'],
                         'boxes': r['result'], 'current': done,
                         'total': len(pages)}) + '\n\n'
                if cancelled or cancel.is_set():
                    yield 'data: ' + json.dumps(
                        {'type': 'info', 'message': 'Cancelled.'}) + '\n\n'
                yield 'data: ' + json.dumps({'type': 'done'}) + '\n\n'
            finally:
                pdf_import.finish_job(job_id)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


@toolbox_bp.route('/pdf/llm-detect-cancel', methods=['POST'])
@login_required
@admin_required
def pdf_llm_detect_cancel():
    from app import pdf_import
    data = request.get_json(silent=True) or {}
    ok = pdf_import.cancel_job((data.get('job_id') or '').strip())
    return jsonify({'ok': ok})


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

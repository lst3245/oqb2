"""
AI Tools service: image proofreading + Markdown generation.

The two ``iter_*`` generators yield plain event dicts (``{type, message,
current?, total?, ...}``); the SSE routes in ``app/admin.py`` serialise
them as ``data: {json}\\n\\n``. Each generator takes a ``threading.Event``
``cancel`` flag that is checked between questions so a run can be stopped
server-side (to stop burning LLM tokens), unlike the legacy batch ops
that only disconnect the client.

All LLM access goes through ``app/llm_client.py`` (OpenAI-compatible).
"""
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime

from flask import current_app

from app import db, md_render
from app.models import Question, QuestionAsset
from app import ai_prompts
from app import llm_client

logger = logging.getLogger(__name__)


# ==================== Cancellation registry ====================
#
# Single-process assumption (same as the settings hot-reload): the cancel
# flag lives in memory, so a multi-worker deployment would need the cancel
# POST to reach the same worker running the stream.

_AI_CANCEL: 'dict[str, threading.Event]' = {}
_AI_LOCK = threading.Lock()


def new_job():
    """Register a new cancellable job; returns ``(job_id, cancel_event)``."""
    job_id = uuid.uuid4().hex
    ev = threading.Event()
    with _AI_LOCK:
        _AI_CANCEL[job_id] = ev
    return job_id, ev


def cancel_job(job_id: str) -> bool:
    """Signal a running job to stop. Returns True if the job was known."""
    with _AI_LOCK:
        ev = _AI_CANCEL.get(job_id)
    if ev is not None:
        ev.set()
        return True
    return False


def finish_job(job_id: str) -> None:
    """Drop a finished job's cancel flag from the registry."""
    with _AI_LOCK:
        _AI_CANCEL.pop(job_id, None)


# ==================== Slot / path helpers ====================

def _slot_img_parts(question_id, asset_type, version):
    """All IMG parts for a (question, asset_type, version) slot, ordered."""
    return (QuestionAsset.query
            .filter_by(question_id=question_id, asset_type=asset_type,
                       version=version, file_format='IMG')
            .order_by(QuestionAsset.part_number)
            .all())


def _qb_detail(qid):
    """Mirror of admin._extract_qb_detail without the import cycle."""
    parts = qid.split('_')
    return parts[2] if len(parts) >= 4 else 'UNKNOWN'


def _md_rel_path(question, version, asset_type):
    """Canonical relative path for a generated MD asset (mirrors
    admin.create_md_asset)."""
    filename = f"{question.qid}_{version}_{asset_type}.md"
    if question.source in ('DSE', 'CE', 'AL'):
        folder = '/'.join([question.subject, 'PP', question.source,
                           str(question.year), question.paper])
    else:
        folder = '/'.join([question.subject, 'QB', _qb_detail(question.qid)])
    return f"{folder}/{filename}"


def _abs(source_path, rel_path):
    return os.path.join(source_path, *rel_path.split('/'))


def _empty_reply_hint(info):
    """Turn an LLM response's finish_reason into an actionable hint for the
    common 'empty reply' failure modes."""
    info = info or {}
    fr = info.get('finish_reason')
    if fr == 'length':
        return (' — hit the output-token limit before producing text '
                '(raise "Max output tokens" on the endpoint, or the model is a '
                'reasoning model spending its budget on hidden thinking)')
    if fr == 'content_filter':
        return ' — blocked by the provider content filter'
    usage = info.get('usage') or {}
    ct = usage.get('completion_tokens')
    extra = f' (finish_reason={fr}, completion_tokens={ct})' if fr or ct is not None else ''
    return (' — the endpoint returned no text content. Verify the model is '
            'vision-capable and actually received the image' + extra)


# ==================== Image checking (proofreading) ====================
#
# Checking compares a TYPED version (EN/CH/BI) against a reference scan
# (typically ENO/CHO). Each selected format (IMG / MD / DOC) in a slot is
# proofread separately: IMG parts are sent as-is; MD and DOC are rendered
# to temp page images via Word COM before the vision call. Reference images
# use the official slot (IMG parts preferred, else rendered DOC/MD).
# check_state is written per format (all IMG part rows share one state).

class _RenderUnavailable(Exception):
    """Raised when a DOC/MD slot can't be rendered to images (no Word COM,
    missing file, render failure) — surfaced as a per-slot error/skip."""


class _LazyWord:
    """Open a Word COM session on first use and reuse it for the rest of a
    run. Pure-IMG checks never touch Word (and never take its global lock)."""

    def __init__(self, lock_timeout=600.0):
        self.lock_timeout = lock_timeout
        self._cm = None
        self._app = None

    def get(self):
        if self._app is not None:
            return self._app
        from app import word_com
        if not word_com.IS_AVAILABLE:
            return None
        self._cm = word_com.word_session(lock_timeout=self.lock_timeout)
        self._app = self._cm.__enter__()
        return self._app

    def close(self):
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                logger.exception('Word session close failed')
            self._cm = None
            self._app = None


def default_render_opts(config):
    """Build the DOC/MD → image render options from app config (mirrors the
    defaults used by batch_generate_images)."""
    return {
        'width': int(config.get('BATCH_IMG_DEFAULT_WIDTH',
                                config.get('DOC_THUMBNAIL_WIDTH', 1000))),
        'transparent': bool(config.get('THUMBNAIL_TRANSPARENT', False)),
        'whiteness': int(config.get('THUMBNAIL_WHITENESS_THRESHOLD', 250)),
        'padding': int(config.get('THUMBNAIL_BOTTOM_PADDING_PX', 24)),
        'symmetric': bool(config.get('THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP', False)),
        'lock_timeout': float(config.get('WORD_COM_LOCK_TIMEOUT', 600)),
    }


def _slot_any_assets(question_id, asset_type, version):
    """Every asset row (any format) for a (question, asset_type, version) slot."""
    return (QuestionAsset.query
            .filter_by(question_id=question_id, asset_type=asset_type, version=version)
            .order_by(QuestionAsset.file_format, QuestionAsset.part_number)
            .all())


def _render_source_to_pages(src_asset, source_path, render_opts, word):
    """Render one MD/DOC source asset to a list of cropped PIL pages via Word
    COM. Raises ``_RenderUnavailable`` when it can't be done."""
    from app import word_com, batch_image_gen
    if not word_com.IS_AVAILABLE:
        raise _RenderUnavailable('Word COM unavailable on this server (needs Windows + Word + pywin32)')
    app_word = word.get()
    if app_word is None:
        raise _RenderUnavailable('could not open a Word session')
    abs_path = _abs(source_path, src_asset.file_path)
    if not os.path.isfile(abs_path):
        raise _RenderUnavailable('source file missing on disk')
    kw = dict(width_px=render_opts.get('width', 1000),
              transparent=render_opts.get('transparent', False),
              whiteness_threshold=render_opts.get('whiteness', 250),
              bottom_padding_px=render_opts.get('padding', 24),
              symmetric_horizontal_crop=render_opts.get('symmetric', False))
    if src_asset.file_format == 'DOC':
        return batch_image_gen.render_doc_to_pages(app_word, abs_path, **kw)
    return batch_image_gen.render_md_to_pages(app_word, abs_path, **kw)


CHECK_FORMATS = ('IMG', 'MD', 'DOC')


def _slot_has_format(question_id, asset_type, version, file_format):
    """True when the slot has a checkable asset of ``file_format``."""
    if file_format == 'IMG':
        return bool(_slot_img_parts(question_id, asset_type, version))
    return (QuestionAsset.query
            .filter_by(question_id=question_id, asset_type=asset_type,
                       version=version, file_format=file_format)
            .first()) is not None


def _resolve_slot_images(question, asset_type, version, source_path,
                         image_max_dim, render_opts, word):
    """Images for the official reference side. Prefer IMG parts; otherwise
    render DOC (preferred) or MD to temp page images."""
    img_parts = _slot_img_parts(question.id, asset_type, version)
    if img_parts:
        return [llm_client.prepare_image(_abs(source_path, a.file_path), image_max_dim)
                for a in img_parts]
    doc = (QuestionAsset.query
           .filter_by(question_id=question.id, asset_type=asset_type,
                      version=version, file_format='DOC').first())
    md = (QuestionAsset.query
          .filter_by(question_id=question.id, asset_type=asset_type,
                     version=version, file_format='MD').first())
    src = doc or md
    if not src:
        return []
    pages = _render_source_to_pages(src, source_path, render_opts, word)
    return [llm_client.prepare_image_from_pil(im, image_max_dim) for im in pages]


def _resolve_format_images(question, asset_type, version, file_format,
                           source_path, image_max_dim, render_opts, word):
    """Images for one typed format, ready for the LLM."""
    if file_format == 'IMG':
        img_parts = _slot_img_parts(question.id, asset_type, version)
        if not img_parts:
            return []
        return [llm_client.prepare_image(_abs(source_path, a.file_path), image_max_dim)
                for a in img_parts]
    asset = (QuestionAsset.query
             .filter_by(question_id=question.id, asset_type=asset_type,
                        version=version, file_format=file_format)
             .first())
    if not asset:
        return []
    pages = _render_source_to_pages(asset, source_path, render_opts, word)
    return [llm_client.prepare_image_from_pil(im, image_max_dim) for im in pages]


def _write_format_check_state(question_id, asset_type, version, file_format,
                              state, result, now):
    """Write check fields to every row of ``file_format`` in the slot."""
    encoded = json.dumps(result, ensure_ascii=False) if result is not None else None
    rows = QuestionAsset.query.filter_by(
        question_id=question_id, asset_type=asset_type, version=version,
        file_format=file_format).all()
    for a in rows:
        a.check_state = state
        a.check_result = encoded
        a.checked_at = now
    db.session.commit()
    return len(rows)


def _aggregate_check_results(results):
    """Roll up per-format check results (issues > error > ok > skip)."""
    if not results:
        return {'status': 'skip', 'message': 'No formats to check'}
    msgs = [r['message'] for r in results]
    statuses = [r['status'] for r in results]
    if 'issues' in statuses:
        out, state = 'issues', 'issues'
    elif 'error' in statuses:
        out, state = 'error', None
    elif all(s == 'skip' for s in statuses):
        out, state = 'skip', None
    elif 'ok' in statuses:
        out, state = 'ok', 'ok'
    else:
        out, state = 'skip', None
    return {'status': out, 'state': state, 'message': ' | '.join(msgs)}


def check_slot_format(question, asset_type, typed_version, ref_version, file_format,
                      *, recheck, config, image_max_dim, source_path, render_opts, word):
    """Proofread ONE typed format in a slot against the reference slot.

    Returns ``{status, message, state?, file_format}``.
    """
    label = (f'{question.qid} / {asset_type} / {typed_version} / {file_format} '
             f'vs {ref_version}')

    if not _slot_has_format(question.id, asset_type, typed_version, file_format):
        return {'status': 'skip', 'message': f'{label} — no {file_format} asset',
                'file_format': file_format}
    ref_rows = _slot_any_assets(question.id, asset_type, ref_version)
    if not ref_rows:
        return {'status': 'skip', 'message': f'{label} — no {ref_version} reference asset',
                'file_format': file_format}

    fmt_rows = (QuestionAsset.query
                .filter_by(question_id=question.id, asset_type=asset_type,
                           version=typed_version, file_format=file_format)
                .all())
    if not recheck and any(a.check_state in ('ok', 'issues') for a in fmt_rows):
        return {'status': 'skip',
                'message': f'{label} — already checked (recheck off)',
                'file_format': file_format}

    try:
        typed_imgs = _resolve_format_images(
            question, asset_type, typed_version, file_format,
            source_path, image_max_dim, render_opts, word)
    except _RenderUnavailable as e:
        return {'status': 'error', 'message': f'{label} — render failed: {e}',
                'file_format': file_format}
    except Exception as e:
        logger.exception('Typed image resolve failed for %s', label)
        return {'status': 'error',
                'message': f'{label} — {typed_version} image load failed: {e}',
                'file_format': file_format}
    if not typed_imgs:
        return {'status': 'skip',
                'message': f'{label} — no usable {typed_version} {file_format} source',
                'file_format': file_format}

    try:
        ref_imgs = _resolve_slot_images(question, asset_type, ref_version,
                                        source_path, image_max_dim, render_opts, word)
    except _RenderUnavailable as e:
        return {'status': 'error', 'message': f'{label} — {ref_version} render failed: {e}',
                'file_format': file_format}
    except Exception as e:
        logger.exception('Reference image resolve failed for %s', label)
        return {'status': 'error',
                'message': f'{label} — {ref_version} image load failed: {e}',
                'file_format': file_format}
    if not ref_imgs:
        return {'status': 'skip',
                'message': f'{label} — no usable {ref_version} reference image/source',
                'file_format': file_format}

    fmt_note = ' (rendered from source)' if file_format in ('MD', 'DOC') else ''
    user_text = (
        ai_prompts.build_check_user_text(typed_version, ref_version, asset_type)
        + f"\n\nTyped format: {file_format}{fmt_note}."
        + f"\n\nImage order: the first {len(ref_imgs)} image(s) are the "
          f"OFFICIAL ({ref_version}) version; the remaining {len(typed_imgs)} "
          f"image(s) are the TYPED ({typed_version}) {file_format} to proofread."
    )
    try:
        text, info = llm_client.chat(config, ai_prompts.get_prompt('CHECK_SYSTEM'),
                                     user_text, ref_imgs + typed_imgs)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}',
                'file_format': file_format}

    if not (text or '').strip():
        hint = _empty_reply_hint(info)
        logger.warning('Empty check reply for %s; finish_reason=%s raw=%s',
                       label, (info or {}).get('finish_reason'),
                       str((info or {}).get('raw'))[:1000])
        return {'status': 'error',
                'message': f'{label} — model returned an empty reply{hint}',
                'file_format': file_format}

    parsed = ai_prompts.parse_check_result(text)
    now = datetime.utcnow()
    base_result = {'model': config.model_name, 'ref_version': ref_version,
                   'checked_by': 'ai', 'file_format': file_format}
    if parsed is None:
        state = 'error'
        result = {**base_result, 'status': 'error', 'issues': [],
                  'raw': (text or '')[:4000]}
        msg = f'{label} — unparseable model reply (stored raw)'
        out_status = 'error'
    else:
        state = parsed['status']
        result = {**base_result, 'status': state, 'issues': parsed['issues']}
        if state == 'ok':
            msg = f'{label} — OK (no issues)'
            out_status = 'ok'
        else:
            n = len(parsed['issues'])
            first = parsed['issues'][0]['description'] if parsed['issues'] else ''
            msg = f'{label} — {n} issue(s): {first[:160]}'
            out_status = 'issues'

    try:
        _write_format_check_state(question.id, asset_type, typed_version,
                                  file_format, state, result, now)
    except Exception as e:
        db.session.rollback()
        logger.exception('DB write failed for %s', label)
        return {'status': 'error', 'message': f'{label} — DB write failed: {e}',
                'file_format': file_format}

    return {'status': out_status, 'message': msg, 'state': state,
            'file_format': file_format}


def check_slot(question, asset_type, typed_version, ref_version, *, recheck,
               config, image_max_dim, source_path, render_opts, word,
               formats=None):
    """Proofread every present typed format in a slot (default IMG+MD+DOC)."""
    fmts = set(formats or CHECK_FORMATS) & set(CHECK_FORMATS)
    results = []
    for fmt in CHECK_FORMATS:
        if fmt not in fmts:
            continue
        results.append(check_slot_format(
            question, asset_type, typed_version, ref_version, fmt,
            recheck=recheck, config=config, image_max_dim=image_max_dim,
            source_path=source_path, render_opts=render_opts, word=word))
    return _aggregate_check_results(results)


def iter_check(qs, typed_version, ref_version, asset_types, formats, recheck,
               config, image_max_dim, source_path, cancel, render_opts=None,
               parallel=False, app=None, max_workers=1):
    """Proofread each selected (question, asset_type, format) against reference.

    Yields event dicts. Each work item calls ``check_slot_format``.
    """
    render_opts = render_opts or {}
    fmts = set(formats or CHECK_FORMATS) & set(CHECK_FORMATS)
    work = []
    for q in qs:
        for atype in asset_types:
            for fmt in CHECK_FORMATS:
                if fmt in fmts and _slot_has_format(q.id, atype, typed_version, fmt):
                    work.append((q, atype, fmt))
    total = len(work)
    fmt_label = '/'.join(f for f in CHECK_FORMATS if f in fmts)
    yield {'type': 'info',
           'message': f'Checking {typed_version} ({fmt_label}) against {ref_version} — '
                      f'{len(qs)} question(s), {total} check(s).'}

    ok = issues = skipped = errors = 0
    current = 0

    def _shape(res):
        nonlocal ok, issues, skipped, errors
        status = res['status']
        if status == 'ok':
            ok += 1; return 'success'
        if status == 'issues':
            issues += 1; return 'success'
        if status == 'skip':
            skipped += 1; return 'skip'
        errors += 1; return 'error'

    use_parallel = bool(parallel and app is not None and max_workers and max_workers > 1)

    if use_parallel:
        from app.parallel import run_parallel, CANCELLED

        def _worker(item):
            question, atype, fmt = item
            # Re-fetch into this thread's scoped session — the original objects
            # are bound to the request session and are unsafe to touch here.
            question = db.session.get(Question, question.id) or question
            # Per-worker Word session: opened lazily, released immediately so the
            # next thread can take the global COM lock.
            word = _LazyWord(render_opts.get('lock_timeout', 600))
            try:
                return check_slot_format(
                    question, atype, typed_version, ref_version, fmt,
                    recheck=recheck, config=config, image_max_dim=image_max_dim,
                    source_path=source_path, render_opts=render_opts, word=word)
            finally:
                word.close()

        for r in run_parallel(app, cancel, work, _worker, max_workers):
            if r['result'] is CANCELLED:
                continue
            current += 1
            question, atype, fmt = r['item']
            if r['error'] is not None:
                errors += 1
                label = f'{question.qid} / {atype} / {typed_version} / {fmt}'
                yield {'type': 'error', 'message': f'{label} — {r["error"]}',
                       'current': current, 'total': total}
                continue
            res = r['result']
            ev_type = _shape(res)
            ev = {'type': ev_type, 'message': res['message'],
                  'current': current, 'total': total}
            if res.get('state'):
                ev['state'] = res['state']
            yield ev
    else:
        word = _LazyWord(render_opts.get('lock_timeout', 600))
        try:
            for question, atype, fmt in work:
                if cancel.is_set():
                    yield {'type': 'info', 'message': 'Cancelled by user.',
                           'current': current, 'total': total}
                    break
                current += 1
                res = check_slot_format(
                    question, atype, typed_version, ref_version, fmt,
                    recheck=recheck, config=config, image_max_dim=image_max_dim,
                    source_path=source_path, render_opts=render_opts, word=word)
                ev_type = _shape(res)
                ev = {'type': ev_type, 'message': res['message'],
                      'current': current, 'total': total}
                if res.get('state'):
                    ev['state'] = res['state']
                yield ev
        finally:
            word.close()

    if not cancel.is_set():
        yield {
            'type': 'done',
            'message': f'Done. OK: {ok}, with issues: {issues}, '
                       f'skipped: {skipped}, errors: {errors}.',
            'current': total, 'total': total,
            'stats': {'ok': ok, 'issues': issues, 'skipped': skipped, 'errors': errors},
        }
    else:
        yield {'type': 'done', 'message': 'Stopped.', 'current': current, 'total': total,
               'stats': {'ok': ok, 'issues': issues, 'skipped': skipped, 'errors': errors}}


# ==================== Markdown generation ====================

def _box_is_useful(box):
    """A fractional box is worth cropping only if it's a real sub-region —
    not degenerate and not (almost) the whole page."""
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return False
    x1, y1, x2, y2 = box
    w, h = abs(x2 - x1), abs(y2 - y1)
    area = w * h
    return w > 0.03 and h > 0.03 and 0.01 < area < 0.92


def _embed_figures(md, src_assets, config, imgs, image_max_dim, source_path):
    """Replace each ``[FIGURE: ...]`` placeholder in ``md`` with an embedded
    image. When there is a single source image we ask the model to localise
    the figures and embed a cropped region; otherwise (or on any failure) we
    embed the whole source image. Returns the rewritten markdown.

    Pure-text questions have no placeholder and are returned unchanged — no
    image is embedded.
    """
    captions = ai_prompts.figure_captions(md)
    if not captions:
        return md  # no figure => markdown only

    # Try to localise figures for cropping (single-image slots only — mapping
    # boxes across multiple source parts is unreliable).
    boxes = []
    if len(src_assets) == 1:
        try:
            abs_path = _abs(source_path, src_assets[0].file_path)
            sw, sh = llm_client.sent_image_size(abs_path, image_max_dim)
            coord_order = str(
                current_app.config.get('PDF_IMPORT_COORD_ORDER', 'xyxy')
            ).strip().lower()
            btext, _info = llm_client.chat(
                config,
                ai_prompts.build_figure_box_system(coord_order),
                ai_prompts.build_figure_box_user_text(coord_order),
                imgs,
            )
            boxes = ai_prompts.parse_figure_boxes(
                btext, img_w=sw, img_h=sh, coord_order=coord_order,
            )
        except llm_client.LLMError:
            boxes = []
        except Exception:
            logger.exception('figure-box pass failed')
            boxes = []

    counter = {'i': 0}

    def _replace(m):
        i = counter['i']
        counter['i'] += 1
        caption = (m.group(1) or '').strip() or 'figure'
        data_uri = None
        # Crop when we have a usable box for this figure (single-image slot).
        if len(src_assets) == 1 and i < len(boxes) and _box_is_useful(boxes[i].get('box')):
            try:
                data_uri = llm_client.crop_image_data_uri(
                    _abs(source_path, src_assets[0].file_path), boxes[i]['box'])
            except Exception:
                data_uri = None
        # Fallback: embed the whole source part (matched by index when multi-part).
        if data_uri is None:
            part = src_assets[i] if i < len(src_assets) else src_assets[-1]
            try:
                data_uri = llm_client.read_image_data_uri(_abs(source_path, part.file_path))
            except Exception:
                logger.warning('Could not embed figure image')
                return m.group(0)  # leave the placeholder text in place
        return f'\n\n![{caption}]({data_uri})\n\n'

    return ai_prompts.FIGURE_RE.sub(_replace, md)


def generate_md_slot(question, asset_type, source_version, target_version, *,
                     embed_image, overwrite, config, image_max_dim,
                     md_max_bytes, source_path):
    """Generate (and persist) a Markdown asset for one (question, asset_type,
    target_version) slot from the source-version images.

    Returns a result dict ``{status, message, asset_id?}`` where ``status`` is
    one of ``created`` / ``updated`` / ``skip`` / ``error``. Shared by the SSE
    batch generator and the per-slot admin endpoint.
    """
    label = f'{question.qid} / {asset_type} / {source_version} -> {target_version} MD'

    src_assets = _slot_img_parts(question.id, asset_type, source_version)
    if not src_assets:
        return {'status': 'skip', 'message': f'{label} — no {source_version} source IMG'}

    existing = (QuestionAsset.query
                .filter_by(question_id=question.id, asset_type=asset_type,
                           version=target_version, file_format='MD')
                .first())
    if existing and not overwrite:
        return {'status': 'skip', 'message': f'{label} — MD exists (overwrite off)'}

    try:
        imgs = [llm_client.prepare_image(_abs(source_path, a.file_path), image_max_dim)
                for a in src_assets]
    except Exception as e:
        logger.exception('Image prep failed for %s', label)
        return {'status': 'error', 'message': f'{label} — image load failed: {e}'}

    user_text = ai_prompts.build_md_user_text(source_version, asset_type)
    try:
        text, info = llm_client.chat(config, ai_prompts.get_prompt('MD_SYSTEM'), user_text, imgs)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}'}

    md = ai_prompts.normalize_inline_math(ai_prompts.strip_md_fences(text))
    if not md.strip():
        hint = _empty_reply_hint(info)
        logger.warning('Empty MD reply for %s; finish_reason=%s raw=%s',
                       label, (info or {}).get('finish_reason'),
                       str((info or {}).get('raw'))[:1000])
        return {'status': 'error', 'message': f'{label} — model returned empty Markdown{hint}'}

    # Smart figures: embed only when a real diagram was detected; crop if we can.
    if embed_image:
        try:
            md = _embed_figures(md, src_assets, config, imgs, image_max_dim, source_path)
        except Exception:
            logger.exception('figure embedding failed for %s', label)

    payload = md.encode('utf-8')
    if len(payload) > md_max_bytes:
        return {'status': 'skip',
                'message': f'{label} — generated MD {len(payload)} bytes exceeds limit '
                           f'{md_max_bytes} (try turning off figure embedding)'}

    try:
        rel_path = _md_rel_path(question, target_version, asset_type)
        abs_path = _abs(source_path, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'wb') as f:
            f.write(payload)

        if existing:
            existing.file_path = rel_path
            asset = existing
        else:
            asset = QuestionAsset(
                question_id=question.id, asset_type=asset_type,
                file_format='MD', version=target_version,
                file_path=rel_path, part_number=1,
            )
            db.session.add(asset)
        db.session.commit()
        md_render.invalidate(asset.id)
    except Exception as e:
        db.session.rollback()
        logger.exception('MD write failed for %s', label)
        return {'status': 'error', 'message': f'{label} — write failed: {e}'}

    verb = 'updated' if existing else 'created'
    return {'status': 'updated' if existing else 'created',
            'message': f'{label} — {verb} MD ({len(payload)} bytes)',
            'asset_id': asset.id}


def iter_generate_md(qs, source_version, target_version, asset_types, overwrite,
                     embed_image, config, image_max_dim, md_max_bytes,
                     source_path, cancel, parallel=False, app=None, max_workers=1):
    """Transcribe each source IMG slot into a Markdown asset for the target
    slot. Yields event dicts; delegates each slot to ``generate_md_slot``.

    When ``parallel`` is set (cloud endpoints) the per-slot LLM round-trips fan
    out across ``max_workers`` threads — slots are fully independent (each writes
    its own MD file + DB row)."""
    work = [(q, atype) for q in qs for atype in asset_types]
    total = len(work)
    yield {'type': 'info',
           'message': f'Generating Markdown ({source_version} image -> '
                      f'{target_version} MD) — {len(qs)} question(s), {total} slot(s).'}

    created = skipped = errors = 0
    current = 0

    def _shape(res):
        nonlocal created, skipped, errors
        status = res['status']
        if status in ('created', 'updated'):
            created += 1; return 'success'
        if status == 'skip':
            skipped += 1; return 'skip'
        errors += 1; return 'error'

    def _worker(item):
        question, atype = item
        # Re-fetch into this thread's scoped session (see iter_check note).
        question = db.session.get(Question, question.id) or question
        return generate_md_slot(
            question, atype, source_version, target_version,
            embed_image=embed_image, overwrite=overwrite, config=config,
            image_max_dim=image_max_dim, md_max_bytes=md_max_bytes,
            source_path=source_path,
        )

    use_parallel = bool(parallel and app is not None and max_workers and max_workers > 1)

    if use_parallel:
        from app.parallel import run_parallel, CANCELLED
        for r in run_parallel(app, cancel, work, _worker, max_workers):
            if r['result'] is CANCELLED:
                continue
            current += 1
            question, atype = r['item']
            if r['error'] is not None:
                errors += 1
                yield {'type': 'error',
                       'message': f'{question.qid} / {atype} / {target_version} MD — {r["error"]}',
                       'current': current, 'total': total}
                continue
            ev_type = _shape(r['result'])
            yield {'type': ev_type, 'message': r['result']['message'],
                   'current': current, 'total': total}
    else:
        for question, atype in work:
            if cancel.is_set():
                yield {'type': 'info', 'message': 'Cancelled by user.',
                       'current': current, 'total': total}
                break
            current += 1
            res = _worker((question, atype))
            ev_type = _shape(res)
            yield {'type': ev_type, 'message': res['message'],
                   'current': current, 'total': total}

    if not cancel.is_set():
        yield {
            'type': 'done',
            'message': f'Done. Created/updated: {created}, skipped: {skipped}, errors: {errors}.',
            'current': total, 'total': total,
            'stats': {'created': created, 'skipped': skipped, 'errors': errors},
        }
    else:
        yield {'type': 'done', 'message': 'Stopped.', 'current': current, 'total': total,
               'stats': {'created': created, 'skipped': skipped, 'errors': errors}}


# ==================== Auto question tagging ====================
#
# Classify a question with an LLM and map the returned names back to the
# subject's Topic / Subtopic / Chapter / Subchapter IDs. ``suggest_tags`` is
# read-only (used by the single-question review flow in the edit modal);
# ``apply_tags`` writes the resolved IDs to the Question; ``iter_auto_tag`` is
# the SSE batch generator that does both per question.

# Embedded base64 image data-URIs in MD assets are huge and useless for
# tagging — strip them so the text we send the model stays small.
_DATA_URI_RE = re.compile(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+')


def _strip_data_uris(text, limit=8000):
    text = _DATA_URI_RE.sub('[embedded image]', text or '')
    if len(text) > limit:
        text = text[:limit] + '\n…[truncated]'
    return text


def _doc_thumb_png(asset_id):
    """Absolute path to a DOC asset's first-page PNG thumbnail, rendering it
    synchronously if it isn't cached yet. Returns None if unavailable (e.g.
    Word COM not present)."""
    from flask import current_app
    from app import doc_thumbnails
    if doc_thumbnails.thumbnail_exists(asset_id):
        return doc_thumbnails.thumbnail_path(asset_id)
    app = current_app._get_current_object()
    try:
        ok = doc_thumbnails.render_doc_thumbnail_sync(app, asset_id)
    except Exception:
        logger.exception('DOC thumbnail render failed for asset %s (tag input)', asset_id)
        ok = False
    return doc_thumbnails.thumbnail_path(asset_id) if ok else None


def _resolve_tag_inputs(question, versions, source_path, image_max_dim):
    """Gather LLM inputs for tagging ONE question. For QUE then SOL, try each
    requested version in priority order and take the FIRST available content:
    prefer IMG parts, else the MD text, else a DOC rendered to a PNG.

    Returns ``(images, text_blocks, found_que)`` where ``images`` is a list of
    ``(b64, mime)`` tuples and ``text_blocks`` a list of strings.
    """
    images = []
    text_blocks = []
    found_que = False

    for atype in ('QUE', 'SOL'):
        got = False
        for version in versions:
            # 1) IMG parts (preferred)
            img_parts = _slot_img_parts(question.id, atype, version)
            if img_parts:
                try:
                    for a in img_parts:
                        images.append(llm_client.prepare_image(
                            _abs(source_path, a.file_path), image_max_dim))
                    got = True
                    break
                except Exception:
                    logger.exception('Tag input IMG prep failed for q%s %s/%s',
                                     question.id, atype, version)
                    continue
            # 2) MD text
            md = (QuestionAsset.query
                  .filter_by(question_id=question.id, asset_type=atype,
                             version=version, file_format='MD')
                  .first())
            if md:
                try:
                    with open(_abs(source_path, md.file_path), 'r', encoding='utf-8') as f:
                        raw = f.read()
                    text_blocks.append(f'{atype} ({version}) Markdown:\n{_strip_data_uris(raw)}')
                    got = True
                    break
                except Exception:
                    logger.exception('Tag input MD read failed for q%s %s/%s',
                                     question.id, atype, version)
                    continue
            # 3) DOC -> thumbnail PNG
            doc = (QuestionAsset.query
                   .filter_by(question_id=question.id, asset_type=atype,
                              version=version, file_format='DOC')
                   .first())
            if doc:
                png = _doc_thumb_png(doc.id)
                if png:
                    try:
                        images.append(llm_client.prepare_image(png, image_max_dim))
                        got = True
                        break
                    except Exception:
                        logger.exception('Tag input DOC thumb prep failed for q%s %s/%s',
                                         question.id, atype, version)
                        continue
        if atype == 'QUE' and got:
            found_que = True

    return images, text_blocks, found_que


# ==================== Solve-based ANS / SOL / ANS Text ====================

SOLVE_TARGETS = ('ANS', 'SOL', 'ANS_TEXT')
SOLVE_ASSET_TARGETS = ('ANS', 'SOL')


def _read_md_asset(asset, source_path, *, limit=12000):
    with open(_abs(source_path, asset.file_path), 'r', encoding='utf-8') as f:
        raw = f.read()
    return _strip_data_uris(raw, limit=limit)


def _solve_add_slot_content(question, asset_type, version, images, text_blocks,
                            source_path, image_max_dim, render_opts, word,
                            *, label_prefix=None, prefer_text_for_md=True):
    """Append one slot's best available content to ``images`` / ``text_blocks``.

    Returns True when any usable content was found.
    """
    label_prefix = label_prefix or f'{asset_type} ({version})'

    img_parts = _slot_img_parts(question.id, asset_type, version)
    if img_parts:
        try:
            for a in img_parts:
                images.append(llm_client.prepare_image(_abs(source_path, a.file_path),
                                                       image_max_dim))
            text_blocks.append(f'{label_prefix}: attached as {len(img_parts)} image(s).')
            return True
        except Exception:
            logger.exception('Solve input IMG prep failed for q%s %s/%s',
                             question.id, asset_type, version)

    md = (QuestionAsset.query
          .filter_by(question_id=question.id, asset_type=asset_type,
                     version=version, file_format='MD')
          .first())
    if md and prefer_text_for_md:
        try:
            text_blocks.append(f'{label_prefix} Markdown:\n{_read_md_asset(md, source_path)}')
            return True
        except Exception:
            logger.exception('Solve input MD read failed for q%s %s/%s',
                             question.id, asset_type, version)

    doc = (QuestionAsset.query
           .filter_by(question_id=question.id, asset_type=asset_type,
                      version=version, file_format='DOC')
           .first())
    if doc:
        try:
            pages = _render_source_to_pages(doc, source_path, render_opts, word)
            for im in pages:
                images.append(llm_client.prepare_image_from_pil(im, image_max_dim))
            text_blocks.append(f'{label_prefix}: attached as {len(pages)} rendered DOC page image(s).')
            return bool(pages)
        except _RenderUnavailable:
            raise
        except Exception:
            logger.exception('Solve input DOC render failed for q%s %s/%s',
                             question.id, asset_type, version)

    if md and not prefer_text_for_md:
        try:
            pages = _render_source_to_pages(md, source_path, render_opts, word)
            for im in pages:
                images.append(llm_client.prepare_image_from_pil(im, image_max_dim))
            text_blocks.append(f'{label_prefix}: attached as {len(pages)} rendered MD page image(s).')
            return bool(pages)
        except _RenderUnavailable:
            raise
        except Exception:
            logger.exception('Solve input MD render failed for q%s %s/%s',
                             question.id, asset_type, version)

    return False


def _solve_gather_inputs(question, version, *, include_official_sol,
                         source_path, image_max_dim, render_opts, word):
    """Gather QUE context for a target version, plus optional ENO/CHO SOL."""
    images = []
    text_blocks = []
    found_que = _solve_add_slot_content(
        question, 'QUE', version, images, text_blocks, source_path,
        image_max_dim, render_opts, word,
        label_prefix=f'QUESTION ({version})',
    )
    if not found_que:
        return images, text_blocks, False

    if include_official_sol:
        for official in ('ENO', 'CHO'):
            _solve_add_slot_content(
                question, 'SOL', official, images, text_blocks, source_path,
                image_max_dim, render_opts, word,
                label_prefix=f'OFFICIAL SOLUTION ({official})',
            )
    return images, text_blocks, True


def _solve_gather_first_question(question, versions, *, include_official_sol,
                                 source_path, image_max_dim, render_opts, word):
    """Version-independent ANS Text uses the first selected version with QUE."""
    for version in versions:
        images, text_blocks, found = _solve_gather_inputs(
            question, version, include_official_sol=include_official_sol,
            source_path=source_path, image_max_dim=image_max_dim,
            render_opts=render_opts, word=word)
        if found:
            return version, images, text_blocks
    return None, [], []


def _solve_write_md_asset(question, asset_type, version, md, *, existing,
                          source_path, md_max_bytes, label):
    payload = ai_prompts.normalize_inline_math(ai_prompts.strip_md_fences(md)).encode('utf-8')
    if not payload.strip():
        return {'status': 'error', 'message': f'{label} — model returned empty Markdown'}
    if len(payload) > md_max_bytes:
        return {'status': 'skip',
                'message': f'{label} — generated MD {len(payload)} bytes exceeds limit {md_max_bytes}'}
    try:
        rel_path = _md_rel_path(question, version, asset_type)
        abs_path = _abs(source_path, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'wb') as f:
            f.write(payload)
        if existing:
            existing.file_path = rel_path
            asset = existing
        else:
            asset = QuestionAsset(
                question_id=question.id, asset_type=asset_type,
                file_format='MD', version=version, file_path=rel_path,
                part_number=1,
            )
            db.session.add(asset)
        db.session.commit()
        md_render.invalidate(asset.id)
    except Exception as e:
        db.session.rollback()
        logger.exception('Solve MD write failed for %s', label)
        return {'status': 'error', 'message': f'{label} — write failed: {e}'}
    verb = 'updated' if existing else 'created'
    return {'status': verb, 'message': f'{label} — {verb} MD ({len(payload)} bytes)',
            'asset_id': asset.id}


def solve_generate_slot(question, kind, version, *, include_official_sol,
                        overwrite, config, image_max_dim, md_max_bytes,
                        source_path, render_opts, word):
    """Solve a question and persist ANS/SOL Markdown for one target version."""
    kind = (kind or '').upper()
    label = f'{question.qid} / {version} / {kind} solve-generate'
    if kind not in SOLVE_ASSET_TARGETS:
        return {'status': 'error', 'message': f'{label} — kind must be ANS or SOL'}

    existing = (QuestionAsset.query
                .filter_by(question_id=question.id, asset_type=kind,
                           version=version, file_format='MD')
                .first())
    if existing and not overwrite:
        return {'status': 'skip', 'message': f'{label} — MD exists (overwrite off)'}

    try:
        images, text_blocks, found_que = _solve_gather_inputs(
            question, version, include_official_sol=include_official_sol,
            source_path=source_path, image_max_dim=image_max_dim,
            render_opts=render_opts, word=word)
    except _RenderUnavailable as e:
        return {'status': 'error', 'message': f'{label} — render failed: {e}'}
    if not found_que:
        return {'status': 'skip', 'message': f'{label} — no usable {version} QUE content'}

    user_text = ai_prompts.build_solve_gen_user_text(kind, version)
    if text_blocks:
        user_text += '\n\n' + '\n\n'.join(text_blocks)
    try:
        text, info = llm_client.chat(config, ai_prompts.get_prompt('SOLVE_GEN_SYSTEM'),
                                     user_text, images)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}'}
    if not (text or '').strip():
        hint = _empty_reply_hint(info)
        return {'status': 'error', 'message': f'{label} — model returned empty Markdown{hint}'}

    return _solve_write_md_asset(
        question, kind, version, text, existing=existing, source_path=source_path,
        md_max_bytes=md_max_bytes, label=label)


def generate_answer_text(question, *, source_versions, include_official_sol,
                         overwrite, config, image_max_dim, source_path,
                         render_opts, word):
    """Solve a question and write version-independent plaintext Question.answer."""
    label = f'{question.qid} / ANS Text solve-generate'
    if (question.answer or '').strip() and not overwrite:
        return {'status': 'skip', 'message': f'{label} — Answer Text exists (overwrite off)'}
    try:
        source_version, images, text_blocks = _solve_gather_first_question(
            question, source_versions, include_official_sol=include_official_sol,
            source_path=source_path, image_max_dim=image_max_dim,
            render_opts=render_opts, word=word)
    except _RenderUnavailable as e:
        return {'status': 'error', 'message': f'{label} — render failed: {e}'}
    if not source_version:
        return {'status': 'skip', 'message': f'{label} — no usable QUE content in selected versions'}

    user_text = ai_prompts.build_solve_gen_user_text('ANS_TEXT', source_version)
    if text_blocks:
        user_text += '\n\n' + '\n\n'.join(text_blocks)
    try:
        text, info = llm_client.chat(config, ai_prompts.get_prompt('SOLVE_GEN_SYSTEM'),
                                     user_text, images)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}'}
    answer = ai_prompts.strip_md_fences(text).strip()
    if not answer:
        hint = _empty_reply_hint(info)
        return {'status': 'error', 'message': f'{label} — model returned empty Answer Text{hint}'}
    try:
        was_existing = bool((question.answer or '').strip())
        question.answer = answer
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('Answer Text write failed for %s', label)
        return {'status': 'error', 'message': f'{label} — DB write failed: {e}'}
    status = 'updated' if was_existing else 'created'
    return {'status': status, 'message': f'{label} — {status}: {answer[:160]}',
            'answer': answer, 'source_version': source_version}


def _solve_existing_target(question, asset_type, version, file_format,
                           source_path, image_max_dim, render_opts, word):
    images = []
    text_blocks = []
    if file_format == 'IMG':
        parts = _slot_img_parts(question.id, asset_type, version)
        if not parts:
            return images, text_blocks, False
        for a in parts:
            images.append(llm_client.prepare_image(_abs(source_path, a.file_path),
                                                   image_max_dim))
        text_blocks.append(f'EXISTING TARGET {asset_type} ({version}) IMG: attached as {len(parts)} image(s).')
        return images, text_blocks, True
    asset = (QuestionAsset.query
             .filter_by(question_id=question.id, asset_type=asset_type,
                        version=version, file_format=file_format)
             .first())
    if not asset:
        return images, text_blocks, False
    if file_format == 'MD':
        text_blocks.append(f'EXISTING TARGET {asset_type} ({version}) Markdown:\n{_read_md_asset(asset, source_path)}')
        return images, text_blocks, True
    pages = _render_source_to_pages(asset, source_path, render_opts, word)
    for im in pages:
        images.append(llm_client.prepare_image_from_pil(im, image_max_dim))
    text_blocks.append(f'EXISTING TARGET {asset_type} ({version}) DOC: attached as {len(pages)} rendered page image(s).')
    return images, text_blocks, bool(pages)


def solve_check_slot_format(question, asset_type, version, file_format, *,
                            include_official_sol, config, image_max_dim,
                            source_path, render_opts, word):
    """Solve the QUE, then judge one existing ANS/SOL asset format."""
    label = f'{question.qid} / {version} / {asset_type} / {file_format} solve-check'
    if asset_type not in SOLVE_ASSET_TARGETS:
        return {'status': 'error', 'message': f'{label} — asset_type must be ANS or SOL',
                'file_format': file_format}
    if file_format not in CHECK_FORMATS:
        return {'status': 'error', 'message': f'{label} — invalid format',
                'file_format': file_format}
    if not _slot_has_format(question.id, asset_type, version, file_format):
        return {'status': 'skip', 'message': f'{label} — no {file_format} asset',
                'file_format': file_format}

    try:
        q_images, q_text_blocks, found_que = _solve_gather_inputs(
            question, version, include_official_sol=include_official_sol,
            source_path=source_path, image_max_dim=image_max_dim,
            render_opts=render_opts, word=word)
        target_images, target_text_blocks, found_target = _solve_existing_target(
            question, asset_type, version, file_format, source_path,
            image_max_dim, render_opts, word)
    except _RenderUnavailable as e:
        return {'status': 'error', 'message': f'{label} — render failed: {e}',
                'file_format': file_format}
    except Exception as e:
        logger.exception('Solve-check input prep failed for %s', label)
        return {'status': 'error', 'message': f'{label} — input prep failed: {e}',
                'file_format': file_format}
    if not found_que:
        return {'status': 'skip', 'message': f'{label} — no usable {version} QUE content',
                'file_format': file_format}
    if not found_target:
        return {'status': 'skip', 'message': f'{label} — no usable target content',
                'file_format': file_format}

    user_text = ai_prompts.build_solve_check_user_text(asset_type, version)
    blocks = q_text_blocks + target_text_blocks
    if blocks:
        user_text += '\n\n' + '\n\n'.join(blocks)
    try:
        text, info = llm_client.chat(config, ai_prompts.get_prompt('SOLVE_CHECK_SYSTEM'),
                                     user_text, q_images + target_images)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}',
                'file_format': file_format}
    if not (text or '').strip():
        hint = _empty_reply_hint(info)
        return {'status': 'error', 'message': f'{label} — model returned empty check result{hint}',
                'file_format': file_format}

    parsed = ai_prompts.parse_check_result(text)
    now = datetime.utcnow()
    base_result = {'model': config.model_name, 'ref_version': None,
                   'checked_by': 'ai', 'file_format': file_format,
                   'mode': 'solve'}
    if parsed is None:
        state = 'error'
        result = {**base_result, 'status': 'error', 'issues': [],
                  'raw': (text or '')[:4000]}
        msg = f'{label} — unparseable model reply (stored raw)'
        out_status = 'error'
    else:
        state = parsed['status']
        result = {**base_result, 'status': state, 'issues': parsed['issues']}
        if state == 'ok':
            msg = f'{label} — OK'
            out_status = 'ok'
        else:
            n = len(parsed['issues'])
            first = parsed['issues'][0]['description'] if parsed['issues'] else ''
            msg = f'{label} — {n} issue(s): {first[:160]}'
            out_status = 'issues'

    try:
        _write_format_check_state(question.id, asset_type, version, file_format,
                                  state, result, now)
    except Exception as e:
        db.session.rollback()
        logger.exception('Solve-check DB write failed for %s', label)
        return {'status': 'error', 'message': f'{label} — DB write failed: {e}',
                'file_format': file_format}
    return {'status': out_status, 'message': msg, 'state': state,
            'file_format': file_format}


def check_answer_text(question, *, source_versions, include_official_sol,
                      config, image_max_dim, source_path, render_opts, word):
    """Solve the QUE and check Question.answer. Does not persist check state."""
    label = f'{question.qid} / ANS Text solve-check'
    answer = (question.answer or '').strip()
    if not answer:
        return {'status': 'skip', 'message': f'{label} — no Answer Text'}
    try:
        source_version, images, text_blocks = _solve_gather_first_question(
            question, source_versions, include_official_sol=include_official_sol,
            source_path=source_path, image_max_dim=image_max_dim,
            render_opts=render_opts, word=word)
    except _RenderUnavailable as e:
        return {'status': 'error', 'message': f'{label} — render failed: {e}'}
    if not source_version:
        return {'status': 'skip', 'message': f'{label} — no usable QUE content in selected versions'}
    user_text = ai_prompts.build_solve_check_user_text('ANS_TEXT', source_version)
    text_blocks.append(f'EXISTING TARGET ANS_TEXT:\n{answer}')
    user_text += '\n\n' + '\n\n'.join(text_blocks)
    try:
        text, info = llm_client.chat(config, ai_prompts.get_prompt('SOLVE_CHECK_SYSTEM'),
                                     user_text, images)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}'}
    if not (text or '').strip():
        hint = _empty_reply_hint(info)
        return {'status': 'error', 'message': f'{label} — model returned empty check result{hint}'}
    parsed = ai_prompts.parse_check_result(text)
    if parsed is None:
        return {'status': 'error',
                'message': f'{label} — unparseable model reply: {(text or "")[:200]}'}
    if parsed['status'] == 'ok':
        return {'status': 'ok', 'message': f'{label} — OK', 'state': 'ok',
                'issues': []}
    first = parsed['issues'][0]['description'] if parsed['issues'] else ''
    return {'status': 'issues',
            'message': f'{label} — {len(parsed["issues"])} issue(s): {first[:160]}',
            'state': 'issues', 'issues': parsed['issues']}


def iter_solve_generate(qs, versions, targets, overwrite, include_official_sol,
                        config, image_max_dim, md_max_bytes, source_path, cancel,
                        render_opts=None, parallel=False, app=None, max_workers=1):
    render_opts = render_opts or {}
    targets = [t for t in targets if t in SOLVE_TARGETS]
    work = []
    for q in qs:
        for target in targets:
            if target == 'ANS_TEXT':
                work.append((q, target, None))
            else:
                for version in versions:
                    work.append((q, target, version))
    total = len(work)
    yield {'type': 'info',
           'message': f'Solve-generating {", ".join(targets)} for {len(qs)} question(s), '
                      f'{total} item(s); versions: {", ".join(versions)}.'}

    created = updated = skipped = errors = current = 0

    def _shape(res):
        nonlocal created, updated, skipped, errors
        status = res['status']
        if status == 'created':
            created += 1; return 'success'
        if status == 'updated':
            updated += 1; return 'success'
        if status == 'skip':
            skipped += 1; return 'skip'
        errors += 1; return 'error'

    def _worker(item):
        question, target, version = item
        question = db.session.get(Question, question.id) or question
        word = _LazyWord(render_opts.get('lock_timeout', 600))
        try:
            if target == 'ANS_TEXT':
                return generate_answer_text(
                    question, source_versions=versions,
                    include_official_sol=include_official_sol,
                    overwrite=overwrite, config=config, image_max_dim=image_max_dim,
                    source_path=source_path, render_opts=render_opts, word=word)
            return solve_generate_slot(
                question, target, version,
                include_official_sol=include_official_sol, overwrite=overwrite,
                config=config, image_max_dim=image_max_dim,
                md_max_bytes=md_max_bytes, source_path=source_path,
                render_opts=render_opts, word=word)
        finally:
            word.close()

    use_parallel = bool(parallel and app is not None and max_workers and max_workers > 1)
    if use_parallel:
        from app.parallel import run_parallel, CANCELLED
        for r in run_parallel(app, cancel, work, _worker, max_workers):
            if r['result'] is CANCELLED:
                continue
            current += 1
            question, target, version = r['item']
            if r['error'] is not None:
                errors += 1
                label = f'{question.qid} / {target}' + (f' / {version}' if version else '')
                yield {'type': 'error', 'message': f'{label} — {r["error"]}',
                       'current': current, 'total': total}
                continue
            ev_type = _shape(r['result'])
            yield {'type': ev_type, 'message': r['result']['message'],
                   'current': current, 'total': total}
    else:
        for item in work:
            if cancel.is_set():
                yield {'type': 'info', 'message': 'Cancelled by user.',
                       'current': current, 'total': total}
                break
            current += 1
            res = _worker(item)
            ev_type = _shape(res)
            yield {'type': ev_type, 'message': res['message'],
                   'current': current, 'total': total}

    stats = {'created': created, 'updated': updated, 'skipped': skipped, 'errors': errors}
    msg = (f'Done. Created: {created}, updated: {updated}, skipped: {skipped}, '
           f'errors: {errors}.') if not cancel.is_set() else 'Stopped.'
    yield {'type': 'done', 'message': msg, 'current': current if cancel.is_set() else total,
           'total': total, 'stats': stats}


def iter_solve_check(qs, versions, targets, formats, include_official_sol,
                     config, image_max_dim, source_path, cancel, render_opts=None,
                     parallel=False, app=None, max_workers=1):
    render_opts = render_opts or {}
    targets = [t for t in targets if t in SOLVE_TARGETS]
    fmts = set(formats or CHECK_FORMATS) & set(CHECK_FORMATS)
    work = []
    for q in qs:
        for target in targets:
            if target == 'ANS_TEXT':
                work.append((q, target, None, None))
                continue
            for version in versions:
                for fmt in CHECK_FORMATS:
                    if fmt in fmts and _slot_has_format(q.id, target, version, fmt):
                        work.append((q, target, version, fmt))
    total = len(work)
    yield {'type': 'info',
           'message': f'Solve-checking {", ".join(targets)} for {len(qs)} question(s), '
                      f'{total} item(s); versions: {", ".join(versions)}.'}

    ok = issues = skipped = errors = current = 0

    def _shape(res):
        nonlocal ok, issues, skipped, errors
        status = res['status']
        if status == 'ok':
            ok += 1; return 'success'
        if status == 'issues':
            issues += 1; return 'success'
        if status == 'skip':
            skipped += 1; return 'skip'
        errors += 1; return 'error'

    def _worker(item):
        question, target, version, fmt = item
        question = db.session.get(Question, question.id) or question
        word = _LazyWord(render_opts.get('lock_timeout', 600))
        try:
            if target == 'ANS_TEXT':
                return check_answer_text(
                    question, source_versions=versions,
                    include_official_sol=include_official_sol, config=config,
                    image_max_dim=image_max_dim, source_path=source_path,
                    render_opts=render_opts, word=word)
            return solve_check_slot_format(
                question, target, version, fmt,
                include_official_sol=include_official_sol, config=config,
                image_max_dim=image_max_dim, source_path=source_path,
                render_opts=render_opts, word=word)
        finally:
            word.close()

    use_parallel = bool(parallel and app is not None and max_workers and max_workers > 1)
    if use_parallel:
        from app.parallel import run_parallel, CANCELLED
        for r in run_parallel(app, cancel, work, _worker, max_workers):
            if r['result'] is CANCELLED:
                continue
            current += 1
            question, target, version, fmt = r['item']
            if r['error'] is not None:
                errors += 1
                label = f'{question.qid} / {target}' + (f' / {version} / {fmt}' if version else '')
                yield {'type': 'error', 'message': f'{label} — {r["error"]}',
                       'current': current, 'total': total}
                continue
            ev_type = _shape(r['result'])
            ev = {'type': ev_type, 'message': r['result']['message'],
                  'current': current, 'total': total}
            if r['result'].get('state'):
                ev['state'] = r['result']['state']
            yield ev
    else:
        for item in work:
            if cancel.is_set():
                yield {'type': 'info', 'message': 'Cancelled by user.',
                       'current': current, 'total': total}
                break
            current += 1
            res = _worker(item)
            ev_type = _shape(res)
            ev = {'type': ev_type, 'message': res['message'],
                  'current': current, 'total': total}
            if res.get('state'):
                ev['state'] = res['state']
            yield ev

    stats = {'ok': ok, 'issues': issues, 'skipped': skipped, 'errors': errors}
    msg = (f'Done. OK: {ok}, with issues: {issues}, skipped: {skipped}, '
           f'errors: {errors}.') if not cancel.is_set() else 'Stopped.'
    yield {'type': 'done', 'message': msg, 'current': current if cancel.is_set() else total,
           'total': total, 'stats': stats}


def _map_tag_names(question, parsed, fields):
    """Map the LLM's returned NAMES back to the subject's Topic / Subtopic /
    Chapter / Subchapter IDs (case-insensitive). Returns
    ``(suggestions, display, unmatched)`` where ``suggestions`` holds resolved
    IDs / scalar values keyed for write-back, ``display`` holds the resolved
    names for the UI, and ``unmatched`` lists names that didn't resolve.
    """
    from app.models import Topic, Subtopic, Chapter, Subchapter

    subject = question.subject
    suggestions = {}
    display = {}
    unmatched = []

    topics = Topic.query.filter_by(subject_id=subject).all()
    topic_by_name = {t.name.strip().lower(): t for t in topics}

    def _subs(rel):
        return rel.all() if hasattr(rel, 'all') else list(rel)

    # ---- scalars ----
    if 'q_type' in fields and parsed.get('q_type'):
        suggestions['q_type'] = parsed['q_type']
        display['q_type'] = parsed['q_type']
    if 'level' in fields and parsed.get('level') is not None:
        suggestions['level'] = parsed['level']
        display['level'] = parsed['level']
    if 'section' in fields and parsed.get('section'):
        suggestions['section'] = parsed['section']
        display['section'] = parsed['section']

    # ---- major topic ----
    resolved_major_topic = None
    if 'major_topic' in fields and parsed.get('major_topic'):
        t = topic_by_name.get(parsed['major_topic'].strip().lower())
        if t:
            resolved_major_topic = t
            suggestions['major_topic_id'] = t.id
            display['major_topic'] = t.name
        else:
            unmatched.append({'field': 'major_topic', 'name': parsed['major_topic']})

    eff_major_topic = resolved_major_topic
    if eff_major_topic is None and question.major_topic_id:
        eff_major_topic = Topic.query.get(question.major_topic_id)

    # ---- major subtopic ----
    if 'major_subtopic' in fields and parsed.get('major_subtopic'):
        low = parsed['major_subtopic'].strip().lower()
        found = None
        if eff_major_topic:
            for s in _subs(eff_major_topic.subtopics):
                if s.name.strip().lower() == low:
                    found = s
                    break
        if not found:
            found = (Subtopic.query.join(Topic, Subtopic.topic_id == Topic.id)
                     .filter(Topic.subject_id == subject)
                     .filter(db.func.lower(Subtopic.name) == low).first())
        if found:
            suggestions['major_subtopic_id'] = found.id
            display['major_subtopic'] = found.name
        else:
            unmatched.append({'field': 'major_subtopic', 'name': parsed['major_subtopic']})

    # ---- minor topics (M2M) ----
    if 'minor_topics' in fields and parsed.get('minor_topics'):
        ids = []
        names = []
        for nm in parsed['minor_topics']:
            t = topic_by_name.get(nm.strip().lower())
            if t:
                if resolved_major_topic and t.id == resolved_major_topic.id:
                    continue
                if t.id not in ids:
                    ids.append(t.id)
                    names.append(t.name)
            else:
                unmatched.append({'field': 'minor_topics', 'name': nm})
        if ids:
            suggestions['minor_topic_ids'] = ids
            display['minor_topics'] = names

    # ---- subtopics (M2M) ----
    if 'subtopics' in fields and parsed.get('subtopics'):
        scope_topic_ids = set()
        if eff_major_topic:
            scope_topic_ids.add(eff_major_topic.id)
        scope_topic_ids.update(suggestions.get('minor_topic_ids', []))
        ids = []
        names = []
        for nm in parsed['subtopics']:
            low = nm.strip().lower()
            found = None
            if scope_topic_ids:
                found = (Subtopic.query
                         .filter(Subtopic.topic_id.in_(scope_topic_ids))
                         .filter(db.func.lower(Subtopic.name) == low).first())
            if not found:
                found = (Subtopic.query.join(Topic, Subtopic.topic_id == Topic.id)
                         .filter(Topic.subject_id == subject)
                         .filter(db.func.lower(Subtopic.name) == low).first())
            if found:
                if found.id not in ids:
                    ids.append(found.id)
                    names.append(found.name)
            else:
                unmatched.append({'field': 'subtopics', 'name': nm})
        if ids:
            suggestions['subtopic_ids'] = ids
            display['subtopics'] = names

    # ---- chapter ----
    resolved_chapter = None
    if 'chapter' in fields and parsed.get('chapter'):
        low = parsed['chapter'].strip().lower()
        c = (Chapter.query.filter_by(subject_id=subject)
             .filter(db.func.lower(Chapter.name) == low).first())
        if c:
            resolved_chapter = c
            suggestions['chapter_id'] = c.id
            display['chapter'] = c.name
        else:
            unmatched.append({'field': 'chapter', 'name': parsed['chapter']})

    eff_chapter = resolved_chapter
    if eff_chapter is None and question.chapter_id:
        eff_chapter = Chapter.query.get(question.chapter_id)

    # ---- subchapter ----
    if 'subchapter' in fields and parsed.get('subchapter'):
        low = parsed['subchapter'].strip().lower()
        found = None
        if eff_chapter:
            for sc in _subs(eff_chapter.subchapters):
                if sc.name.strip().lower() == low:
                    found = sc
                    break
        if not found:
            found = (Subchapter.query.join(Chapter, Subchapter.chapter_id == Chapter.id)
                     .filter(Chapter.subject_id == subject)
                     .filter(db.func.lower(Subchapter.name) == low).first())
        if found:
            suggestions['subchapter_id'] = found.id
            display['subchapter'] = found.name
        else:
            unmatched.append({'field': 'subchapter', 'name': parsed['subchapter']})

    return suggestions, display, unmatched


def suggest_tags(question, versions, fields, config, image_max_dim, source_path):
    """Ask the LLM to classify ONE question and map the result to IDs. Pure
    read — does NOT write to the DB. Returns a dict:

      {ok, error?, suggestions, display, unmatched, raw}
    """
    from app.models import Subject

    fields = [f for f in (fields or []) if f in ai_prompts.TAG_FIELDS]
    if not fields:
        return {'ok': False, 'error': 'no fields selected', 'suggestions': {},
                'display': {}, 'unmatched': [], 'raw': ''}

    images, text_blocks, _found_que = _resolve_tag_inputs(
        question, versions, source_path, image_max_dim)
    if not images and not text_blocks:
        return {'ok': False,
                'error': 'no QUE/SOL content (IMG/MD/DOC) found in the selected version(s)',
                'suggestions': {}, 'display': {}, 'unmatched': [], 'raw': ''}

    subject_row = Subject.query.get(question.subject)
    subject_name = subject_row.name if subject_row else question.subject
    taxonomy = ai_prompts.build_tag_taxonomy(question.subject, fields)
    user_text = ai_prompts.build_tag_user_text(subject_name, fields, taxonomy)
    if text_blocks:
        user_text += '\n\n' + '\n\n'.join(text_blocks)

    text, info = llm_client.chat(config, ai_prompts.get_prompt('TAG_SYSTEM'),
                                 user_text, images)
    if not (text or '').strip():
        hint = _empty_reply_hint(info)
        return {'ok': False, 'error': f'model returned an empty reply{hint}',
                'suggestions': {}, 'display': {}, 'unmatched': [], 'raw': ''}

    parsed = ai_prompts.parse_tag_result(text)
    if parsed is None:
        return {'ok': False, 'error': 'unparseable model reply',
                'suggestions': {}, 'display': {}, 'unmatched': [],
                'raw': (text or '')[:4000]}

    suggestions, display, unmatched = _map_tag_names(question, parsed, fields)
    return {'ok': True, 'error': None, 'suggestions': suggestions,
            'display': display, 'unmatched': unmatched, 'raw': (text or '')[:4000],
            'model': config.model_name}


def apply_tags(question, suggestions, fields, overwrite):
    """Write resolved tag suggestions to ``question``. With ``overwrite`` off,
    only fields that are currently empty are filled. Caller commits. Returns
    the list of field keys actually applied."""
    from app.models import Topic, Subtopic, Subchapter

    fields = set(fields)
    applied = []

    def _empty(v):
        return v is None or v == ''

    if 'q_type' in fields and 'q_type' in suggestions and (overwrite or _empty(question.q_type)):
        question.q_type = suggestions['q_type']
        applied.append('q_type')
    if 'level' in fields and 'level' in suggestions and (overwrite or question.level is None):
        question.level = suggestions['level']
        applied.append('level')
    if 'section' in fields and 'section' in suggestions and (overwrite or _empty(question.section)):
        question.section = suggestions['section']
        applied.append('section')

    if 'major_topic' in fields and 'major_topic_id' in suggestions and (overwrite or question.major_topic_id is None):
        if question.major_topic_id != suggestions['major_topic_id']:
            # Major topic changed — the old major subtopic may no longer belong.
            question.major_subtopic_id = None
        question.major_topic_id = suggestions['major_topic_id']
        applied.append('major_topic')

    if 'major_subtopic' in fields and 'major_subtopic_id' in suggestions and (overwrite or question.major_subtopic_id is None):
        sub = Subtopic.query.get(suggestions['major_subtopic_id'])
        if sub and question.major_topic_id and sub.topic_id == question.major_topic_id:
            question.major_subtopic_id = sub.id
            applied.append('major_subtopic')

    if 'minor_topics' in fields and 'minor_topic_ids' in suggestions and (overwrite or len(question.minor_topics) == 0):
        topics = [t for t in (Topic.query.get(i) for i in suggestions['minor_topic_ids']) if t]
        if overwrite:
            question.minor_topics = topics
        else:
            for t in topics:
                if t not in question.minor_topics:
                    question.minor_topics.append(t)
        applied.append('minor_topics')

    if 'subtopics' in fields and 'subtopic_ids' in suggestions and (overwrite or len(question.subtopics) == 0):
        subs = [s for s in (Subtopic.query.get(i) for i in suggestions['subtopic_ids']) if s]
        if overwrite:
            question.subtopics = subs
        else:
            for s in subs:
                if s not in question.subtopics:
                    question.subtopics.append(s)
        applied.append('subtopics')

    if 'chapter' in fields and 'chapter_id' in suggestions and (overwrite or question.chapter_id is None):
        if question.chapter_id != suggestions['chapter_id']:
            question.subchapter_id = None
        question.chapter_id = suggestions['chapter_id']
        applied.append('chapter')

    if 'subchapter' in fields and 'subchapter_id' in suggestions and (overwrite or question.subchapter_id is None):
        sc = Subchapter.query.get(suggestions['subchapter_id'])
        if sc and question.chapter_id and sc.chapter_id == question.chapter_id:
            question.subchapter_id = sc.id
            applied.append('subchapter')

    return applied


def _auto_tag_one(question, versions, fields, overwrite, config, image_max_dim,
                  source_path):
    """Suggest + apply tags for a single question. Returns a normalised result
    dict ``{status: 'tagged'|'skip'|'error', message}``. Pure per-item unit
    shared by the sequential and parallel branches of ``iter_auto_tag``."""
    label = question.qid
    try:
        res = suggest_tags(question, versions, fields, config,
                           image_max_dim, source_path)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}'}
    except Exception as e:
        logger.exception('Auto-tag suggest failed for %s', label)
        return {'status': 'error', 'message': f'{label} — {e}'}

    if not res.get('ok'):
        return {'status': 'skip',
                'message': f'{label} — {res.get("error") or "no suggestion"}'}

    try:
        applied = apply_tags(question, res['suggestions'], fields, overwrite)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('Auto-tag apply failed for %s', label)
        return {'status': 'error', 'message': f'{label} — DB write failed: {e}'}

    unmatched = res.get('unmatched') or []
    unmatched_note = (f' (unmatched: {", ".join(u["name"] for u in unmatched)})'
                      if unmatched else '')
    if applied:
        return {'status': 'tagged',
                'message': f'{label} — applied: {", ".join(applied)}{unmatched_note}'}
    extra = unmatched_note or (' (fields already filled; overwrite off)'
                               if not overwrite else '')
    return {'status': 'skip', 'message': f'{label} — nothing applied{extra}'}


def iter_auto_tag(qs, versions, fields, overwrite, config, image_max_dim,
                  source_path, cancel, parallel=False, app=None, max_workers=1):
    """SSE generator: auto-tag each question (suggest + apply). Mirrors
    ``iter_check`` (job/info/skip/success/error/done + cancel between
    questions).

    When ``parallel`` is set (cloud endpoints) the per-question LLM round-trips
    fan out across ``max_workers`` threads; each question's ``apply_tags`` +
    commit runs in its own thread-local session."""
    fields = [f for f in (fields or []) if f in ai_prompts.TAG_FIELDS]
    total = len(qs)
    yield {'type': 'info',
           'message': (f'Auto-tagging {total} question(s) — fields: '
                       f'{", ".join(fields) or "(none)"}; versions: '
                       f'{", ".join(versions)}; overwrite: '
                       f'{"on" if overwrite else "off"}.')}

    tagged = skipped = errors = 0
    current = 0

    EV = {'tagged': 'success', 'skip': 'skip', 'error': 'error'}

    def _shape(res):
        nonlocal tagged, skipped, errors
        status = res['status']
        if status == 'tagged':
            tagged += 1
        elif status == 'skip':
            skipped += 1
        else:
            errors += 1
        return EV.get(status, 'error')

    def _worker(question):
        # Re-fetch into this thread's scoped session — apply_tags mutates the
        # question and commits, so it must belong to this thread's session.
        question = db.session.get(Question, question.id) or question
        return _auto_tag_one(question, versions, fields, overwrite, config,
                             image_max_dim, source_path)

    use_parallel = bool(parallel and app is not None and max_workers and max_workers > 1)

    if use_parallel:
        from app.parallel import run_parallel, CANCELLED
        for r in run_parallel(app, cancel, qs, _worker, max_workers):
            if r['result'] is CANCELLED:
                continue
            current += 1
            if r['error'] is not None:
                errors += 1
                yield {'type': 'error',
                       'message': f'{r["item"].qid} — {r["error"]}',
                       'current': current, 'total': total}
                continue
            ev_type = _shape(r['result'])
            yield {'type': ev_type, 'message': r['result']['message'],
                   'current': current, 'total': total}
    else:
        for question in qs:
            if cancel.is_set():
                yield {'type': 'info', 'message': 'Cancelled by user.',
                       'current': current, 'total': total}
                break
            current += 1
            res = _worker(question)
            ev_type = _shape(res)
            yield {'type': ev_type, 'message': res['message'],
                   'current': current, 'total': total}

    if not cancel.is_set():
        yield {'type': 'done',
               'message': f'Done. Tagged: {tagged}, skipped: {skipped}, errors: {errors}.',
               'current': total, 'total': total,
               'stats': {'tagged': tagged, 'skipped': skipped, 'errors': errors}}
    else:
        yield {'type': 'done', 'message': 'Stopped.', 'current': current, 'total': total,
               'stats': {'tagged': tagged, 'skipped': skipped, 'errors': errors}}

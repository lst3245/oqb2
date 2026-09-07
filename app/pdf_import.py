"""
PDF Batch Import — turn uploaded DSE question/solution PDFs into per-question
cropped IMG assets via a vision LLM.

Pipeline
--------
1. ``stage`` — save the uploaded QUE/SOL PDFs under
   ``OUTPUT_PATH/.pdf_import/<token>/`` and rasterise every page to a
   high-resolution PNG (PyMuPDF / fitz). Page metadata is written to
   ``meta.json``.
2. ``iter_detect`` — for each page, send a single downscaled image to a
   vision LLM and ask it for a tight bounding box + printed question number
   per question (one image per call keeps a small local model within its
   context window). Detected boxes are accumulated into ``plan.json``.
   Optional pass-2 ``iter_split_detect`` splits each whole-question crop
   into a stem + lettered parts.
3. (optional) the admin reviews / edits the plan in the browser.
4. ``iter_commit`` — group boxes by label (``5``, ``5a``, ``23-24``), crop
   each box, and create ``Question`` + ``QuestionAsset`` rows via
   ``ensure_question``. A question that spans two pages becomes a multi-part
   IMG asset.

Heavy lifting (atomic disk + DB write, canonical path building, DOC-thumbnail
lifecycle) is delegated to :mod:`app.batch_image_gen`. PDF rasterisation
mirrors :func:`app.batch_image_gen._pdf_to_cropped_images`.

Cancellation mirrors :mod:`app.ai_tools`: a per-job ``threading.Event`` is
checked between pages / questions so a long run can be stopped server-side.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime

from flask import current_app

from app import db, pdf_tools

logger = logging.getLogger(__name__)


# ==================== Paper-prefix parsing ====================

# A "paper prefix" is a QID without the trailing question number, e.g.
# ``MATC_DSE_2012_P1``. QB sources are out of scope for the PDF import tool.
PREFIX_PATTERN = re.compile(
    r'^(?P<subj>[A-Z0-9]+)_(?P<source>DSE|CE|AL)_(?P<year>\d{4})_(?P<paper>P[A-Za-z0-9]+)$'
)


def parse_paper_prefix(prefix: str):
    """Parse a paper prefix into ``(meta_dict, error)``.

    ``meta_dict`` = ``{subject, source, year:int, paper}`` on success;
    ``error`` is a human-readable string on failure.
    """
    prefix = (prefix or '').strip().upper()
    m = PREFIX_PATTERN.match(prefix)
    if not m:
        return None, ('Invalid paper name. Expected SUBJECT_SOURCE_YEAR_PAPER '
                      '(e.g. MATC_DSE_2012_P1).')
    d = m.groupdict()
    return {
        'subject': d['subj'],
        'source': d['source'],
        'year': int(d['year']),
        'paper': d['paper'],
    }, None


def guess_paper_name(config, pdf_path: str, filename: str, subjects,
                     image_max_dim: int):
    """Best-guess the SUBJECT_SOURCE_YEAR_PAPER paper code for a PDF using a
    vision LLM, from its file name and rasterised first page.

    ``config`` is an LLMConfig, ``pdf_path`` an absolute path to the PDF,
    ``subjects`` an iterable of allowed subject codes. Returns
    ``(paper_or_None, raw_text)``. The returned code is validated against
    :data:`PREFIX_PATTERN`; an unparseable / invalid reply yields ``None``.
    Raises on transport failure.
    """
    import tempfile

    from app import ai_prompts, llm_client

    # Rasterise the first page to a temp PNG, then send it downscaled.
    tmp_dir = tempfile.mkdtemp(prefix='pdfguess_')
    try:
        import fitz  # type: ignore
        pdf = fitz.open(pdf_path)
        try:
            if pdf.page_count == 0:
                return None, ''
            page = pdf.load_page(0)
            base_width = page.rect.width or 595.0
            zoom = max(0.1, 1700 / base_width)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            png_path = os.path.join(tmp_dir, 'page1.png')
            pix.save(png_path)
        finally:
            pdf.close()

        b64, mime = llm_client.prepare_image(png_path, image_max_dim)
        system = ai_prompts.build_pdf_paper_name_system(endpoint_id=config.id)
        user_text = ai_prompts.build_pdf_paper_name_user_text(
            filename, subjects, endpoint_id=config.id)
        text, _info = llm_client.chat(config, system, user_text,
                                      images=[(b64, mime)])
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    paper, _conf = ai_prompts.parse_paper_name(text or '')
    if paper:
        meta, err = parse_paper_prefix(paper)
        if err:
            return None, (text or '')
    return paper, (text or '')


# ==================== Cancellation registry ====================
#
# Single-process assumption (same caveat as the AI Tools cancel registry and
# the settings hot-reload): the cancel flag lives in memory, so a multi-worker
# deployment needs the cancel POST to reach the worker running the stream.

_PDF_CANCEL: 'dict[str, threading.Event]' = {}
_PDF_LOCK = threading.Lock()


def new_job():
    """Register a new cancellable job; returns ``(job_id, cancel_event)``."""
    job_id = uuid.uuid4().hex
    ev = threading.Event()
    with _PDF_LOCK:
        _PDF_CANCEL[job_id] = ev
    return job_id, ev


def cancel_job(job_id: str) -> bool:
    """Signal a running job to stop. Returns True if the job was known."""
    with _PDF_LOCK:
        ev = _PDF_CANCEL.get(job_id)
    if ev is not None:
        ev.set()
        return True
    return False


def finish_job(job_id: str) -> None:
    """Drop a finished job's cancel flag from the registry."""
    with _PDF_LOCK:
        _PDF_CANCEL.pop(job_id, None)


# ==================== Staging dir helpers ====================

_TOKEN_RE = re.compile(r'^[0-9a-f]{8,40}$')


def staging_root() -> str:
    """Root directory for all PDF-import staging dirs (under the System tree)."""
    base = current_app.config.get('SYSTEM_PATH') or current_app.config['OUTPUT_PATH']
    return os.path.join(base, '.pdf_import')


def token_dir(token: str) -> str:
    """Absolute path to one staging dir; validates the token shape to keep
    the value safe for filesystem joins (no traversal)."""
    if not _TOKEN_RE.match(token or ''):
        raise ValueError('invalid staging token')
    return os.path.join(staging_root(), token)


def page_png_path(token: str, kind: str, index: int) -> str:
    """Absolute path to a staged page PNG (kind = ``que`` | ``sol``)."""
    if kind not in ('que', 'sol'):
        raise ValueError('invalid kind')
    return os.path.join(token_dir(token), kind, f'page_{int(index) + 1:04d}.png')


def _meta_path(token: str) -> str:
    return os.path.join(token_dir(token), 'meta.json')


def _plan_path(token: str) -> str:
    return os.path.join(token_dir(token), 'plan.json')


def load_meta(token: str) -> dict:
    with open(_meta_path(token), 'r', encoding='utf-8') as f:
        return json.load(f)


def save_meta(token: str, meta: dict) -> None:
    """Persist staging metadata atomically."""
    path = _meta_path(token)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f)
    os.replace(tmp, path)


def save_plan(token: str, plan: dict) -> None:
    with open(_plan_path(token), 'w', encoding='utf-8') as f:
        json.dump(plan, f)


def load_plan(token: str) -> dict:
    try:
        with open(_plan_path(token), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    plan = {'que': data.get('que') or [], 'sol': data.get('sol') or []}
    for kind in ('que', 'sol'):
        plan[kind] = [coerce_plan_item(it) for it in plan[kind]
                      if isinstance(it, dict)]
    return plan


def coerce_plan_item(item: dict) -> dict:
    """Fill canonical ``label`` + integer ``qno`` on an exam plan item.

    Legacy items that only have integer ``qno`` become ``label='5'``. Generic
    free-text labels that are not QNO tokens are left unchanged.
    """
    from app.hierarchy import normalize_plan_label, parse_qno_token

    out = dict(item)
    raw = out.get('label')
    if raw is None or str(raw).strip() == '':
        raw = out.get('qno')
    lab = normalize_plan_label(raw)
    if lab:
        out['label'] = lab
        parsed = parse_qno_token(lab)
        if parsed:
            out['qno'] = parsed.qno
    elif out.get('qno') is not None:
        try:
            out['qno'] = int(out['qno'])
            if not out.get('label'):
                out['label'] = str(out['qno'])
        except (TypeError, ValueError):
            pass
    return out


def plan_item_label(item: dict) -> str | None:
    """Canonical exam label for a plan item, or ``None``."""
    from app.hierarchy import normalize_plan_label
    raw = item.get('label')
    if raw is None or str(raw).strip() == '':
        raw = item.get('qno')
    return normalize_plan_label(raw)


def map_crop_box_to_page(parent_box, crop_box):
    """Map a crop-relative 0..1 box onto the parent page-fraction box."""
    x1, y1, x2, y2 = [float(v) for v in parent_box]
    w, h = x2 - x1, y2 - y1
    cx1, cy1, cx2, cy2 = [float(v) for v in crop_box]
    return [
        x1 + cx1 * w,
        y1 + cy1 * h,
        x1 + cx2 * w,
        y1 + cy2 * h,
    ]


def sanitize_plan(raw, *, generic: bool = False) -> dict:
    """Validate a browser-posted plan into ``{que: [...], sol: [...]}``."""
    from app.hierarchy import normalize_plan_label, parse_qno_token

    clean = {'que': [], 'sol': []}
    roles = {'stem', 'part', 'question'}
    for kind in ('que', 'sol'):
        for item in (raw.get(kind) or []):
            if not isinstance(item, dict):
                continue
            box = item.get('box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                box = [float(v) for v in box]
                page = int(item.get('page', 0))
            except (ValueError, TypeError):
                continue
            if generic:
                label = item.get('label')
                label = str(label).strip() if label not in (None, '') else None
                clean[kind].append({'page': page, 'label': label, 'box': box})
                continue
            raw_lab = item.get('label')
            if raw_lab is None or str(raw_lab).strip() == '':
                raw_lab = item.get('qno')
            parsed = parse_qno_token(raw_lab) if raw_lab not in (None, '') else None
            label = normalize_plan_label(parsed.token) if parsed else None
            qno = parsed.qno if parsed else None
            role = item.get('role') if item.get('role') in roles else None
            source_label = None
            sl = item.get('source_label')
            if sl not in (None, ''):
                source_label = normalize_plan_label(sl) or str(sl).strip()
            source_box = None
            sb = item.get('source_box')
            if isinstance(sb, (list, tuple)) and len(sb) == 4:
                try:
                    source_box = [float(v) for v in sb]
                except (ValueError, TypeError):
                    source_box = None
            source_page = item.get('source_page')
            try:
                source_page = int(source_page) if source_page is not None else None
            except (TypeError, ValueError):
                source_page = None
            row = {'page': page, 'qno': qno, 'label': label, 'box': box}
            if role:
                row['role'] = role
            if source_label:
                row['source_label'] = source_label
            if source_box:
                row['source_box'] = source_box
            if source_page is not None:
                row['source_page'] = source_page
            clean[kind].append(row)
    return clean


def discard(token: str) -> bool:
    """Remove a staging dir. Returns True if it existed."""
    d = token_dir(token)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def cleanup_old(max_age_hours: float = 6.0) -> None:
    """Best-effort purge of staging dirs older than ``max_age_hours``."""
    root = staging_root()
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


# ==================== Rasterisation ====================

def rasterize_pdf(pdf_path: str, out_dir: str, width_px: int,
                  deskew: bool = False, pre_rotate: int = 0,
                  split_mode: str = 'none', filters: dict = None,
                  workers: int = 1,
                  mode1_pages_per_student: int = None) -> list:
    """Rasterise every page of ``pdf_path`` to ``page_NNNN.png`` in
    ``out_dir``. Returns a list of ``{index, filename, width, height}``.

    Rasterisation + all per-page processing is delegated to the shared
    :mod:`app.pdf_tools` primitives (the same ones the PDF Toolbox uses), so
    Batch PDF Import gets A3 splitting / pre-rotate / image filters for free:

    * ``deskew``      — straighten scanned pages (legacy flag; merged into
      ``filters``). Needs NumPy; silently skipped (warned) if absent.
    * ``pre_rotate``  — rotate every page 90/180/270° before splitting.
    * ``split_mode``  — ``'none'`` / ``'simple'`` / ``'mode1'`` / ``'mode2'``
      A3-booklet split; Mode 1 can use ``mode1_pages_per_student`` to drop
      blank/padded booklet slots. Splitting just yields more staged pages (the
      LLM detector runs per page, so this is transparent downstream).
    * ``filters``     — ``{deskew, brightness, contrast, sharpen, grayscale,
      bw, bw_threshold}`` image adjustments.
    * ``workers``     — render this many pages concurrently (CPU-bound page
      render + NumPy filters). Each worker opens its OWN ``fitz`` document —
      MuPDF is not safe to share a ``Document`` across threads, but independent
      opens render in parallel and release the GIL during rendering/NumPy
      (mirrors the parallel OCR path's per-worker ``fitz.open``). ``<= 1`` (or a
      single page) keeps the original sequential loop.

    Output pages are renumbered contiguously in final reading order so the
    rest of the pipeline (page PNG paths, meta indices) is unaffected.
    """
    import fitz  # type: ignore

    from app import pdf_tools

    os.makedirs(out_dir, exist_ok=True)

    filt = dict(filters or {})
    if deskew:
        filt['deskew'] = True

    pre_rotate = int(pre_rotate or 0) % 360
    split_mode = (split_mode or 'none').strip().lower()
    mode1_pages_per_student = pdf_tools.mode1_pages_per_student(
        mode1_pages_per_student)

    # One cheap open just to enumerate the page fragments + op chains.
    pdf = fitz.open(pdf_path)
    try:
        frags = pdf_tools.split_descriptors(
            pdf.page_count, split_mode, mode1_pages_per_student)
    finally:
        pdf.close()

    tasks = [{'out_index': out_index, 'page': int(frag['page']),
              'ops': pdf_tools.build_op_chain(pre_rotate, frag['ops'], filt)}
             for out_index, frag in enumerate(frags)]

    def _render_one(task):
        # Own document per worker (see docstring): never share a fitz.Document
        # across threads.
        doc = fitz.open(pdf_path)
        try:
            page = doc.load_page(task['page'])
            img = pdf_tools.rasterize_page(page, width_px)
        finally:
            doc.close()
        try:
            img = pdf_tools.apply_ops(img, task['ops'])
        except Exception as e:  # pragma: no cover — best-effort processing
            logger.warning('PDF import page processing failed for page %s: %s',
                           task['page'] + 1, e)
        fname = f"page_{task['out_index'] + 1:04d}.png"
        img.save(os.path.join(out_dir, fname), format='PNG')
        return {'index': task['out_index'], 'filename': fname,
                'width': img.width, 'height': img.height}

    n = len(tasks)
    workers = max(1, min(int(workers or 1), n or 1))

    pages: list = [None] * n
    if workers <= 1 or n <= 1:
        for t in tasks:
            rec = _render_one(t)
            pages[rec['index']] = rec
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_render_one, t) for t in tasks]
            for fut in concurrent.futures.as_completed(futures):
                rec = fut.result()  # re-raises a render failure (aborts staging)
                pages[rec['index']] = rec
    return pages


def stage(que_storage, sol_storage, meta_in: dict, raster_width: int,
          deskew: bool = False, pre_rotate: int = 0,
          split_mode: str = 'none', filters: dict = None,
          workers: int = None, mode1_pages_per_student: int = None):
    """Save the uploaded PDFs and rasterise their pages.

    ``que_storage`` / ``sol_storage`` are Werkzeug ``FileStorage`` objects (or
    None). ``meta_in`` carries the parsed paper prefix + version. ``deskew`` /
    ``pre_rotate`` / ``split_mode`` / ``filters`` / ``mode1_pages_per_student``
    drive the shared pre-processing (see :func:`rasterize_pdf`). ``workers`` is
    the page-rasterisation concurrency; ``None`` resolves it from
    ``PDF_IMPORT_RASTER_WORKERS`` (capped by the CPU count). Returns
    ``(token, meta)`` where ``meta`` is the persisted JSON.
    """
    if workers is None:
        try:
            workers = int(current_app.config.get('PDF_IMPORT_RASTER_WORKERS', 4))
        except (TypeError, ValueError):
            workers = 4
    workers = max(1, min(int(workers or 1), os.cpu_count() or 1))

    cleanup_old()
    token = uuid.uuid4().hex
    base = token_dir(token)
    os.makedirs(base, exist_ok=True)

    meta = dict(meta_in)
    meta['created_at'] = datetime.utcnow().isoformat()
    meta['deskew'] = bool(deskew)
    meta['pre_rotate'] = int(pre_rotate or 0) % 360
    meta['split_mode'] = (split_mode or 'none').strip().lower()
    meta['mode1_pages_per_student'] = pdf_tools.mode1_pages_per_student(
        mode1_pages_per_student)
    meta['filters'] = dict(filters or {})
    meta['que'] = None
    meta['sol'] = None

    for kind, storage in (('que', que_storage), ('sol', sol_storage)):
        if storage is None or not getattr(storage, 'filename', ''):
            continue
        kind_dir = os.path.join(base, kind)
        os.makedirs(kind_dir, exist_ok=True)
        pdf_path = os.path.join(kind_dir, 'source.pdf')
        storage.save(pdf_path)
        pages = rasterize_pdf(pdf_path, kind_dir, raster_width, deskew=deskew,
                              pre_rotate=pre_rotate, split_mode=split_mode,
                              filters=filters, workers=workers,
                              mode1_pages_per_student=meta[
                                  'mode1_pages_per_student'])
        meta[kind] = {'filename': storage.filename, 'pages': pages}

    with open(_meta_path(token), 'w', encoding='utf-8') as f:
        json.dump(meta, f)
    return token, meta


# ==================== LLM detection ====================

def _sent_image_size(png_path: str, image_max_dim: int):
    """Return the ``(w, h)`` the LLM actually sees — delegates to
    :func:`llm_client.sent_image_size`."""
    from app import llm_client
    return llm_client.sent_image_size(png_path, image_max_dim)


DETECT_METHODS = ('llm', 'refine', 'segment')


def detect_page(config, png_path: str, atype: str, image_max_dim: int,
                method: str = 'llm', mode: str = 'exam', instruction: str = '',
                generic_prompt: bool = False):
    """Detect the question/solution regions on one page.

    ``mode`` is ``'exam'`` (the default — question/solution detection) or
    ``'generic'`` (no exam context; the model is asked to find regions
    matching the free-text ``instruction``). In generic mode the ``segment``
    method is not meaningful and falls back to ``llm``; returned boxes carry a
    ``label`` instead of a ``qno``.

    ``generic_prompt`` lets EXAM mode borrow the context-free generic prompt
    (driven by ``instruction``) while still returning exam-shaped boxes — used
    to extract questions from non-exam material (e.g. a textbook) and import
    them with auto-numbered question numbers. The returned boxes then carry
    ``qno: None`` (the caller assigns the running number).

    ``method``:
      * ``'llm'``     - the model returns tight boxes (original behaviour).
      * ``'refine'``  - the model returns boxes, then classical CV snaps each
        box to the printed content (recovers chopped text / marks / figures,
        drops answer-space margins). See :func:`app.pdf_layout.refine_box`.
      * ``'segment'`` - the model returns only each item's START y; classical
        CV derives the boxes from the whitespace gaps. See
        :func:`app.pdf_layout.segment_page`.

    For ``refine`` / ``segment`` the side edges are only tightened on QUE pages
    (``shrink_sides``); SOL pages keep full width so right-hand marking
    side-notes are never trimmed.

    Returns ``(boxes, raw_text)`` where ``boxes`` is a list of
    ``{qno, label, box:[x1,y1,x2,y2], continues_prev, continues_next}`` (fractional
    coords; ``label`` is the canonical token without ``Q``) and ``raw_text`` is
    the model's verbatim reply (kept for the debug view). Raises on transport
    failure or when an assisted method is requested without NumPy.
    """
    from app import ai_prompts, llm_client

    method = (method or 'llm').strip().lower()
    if method not in DETECT_METHODS:
        method = 'llm'
    mode = (mode or 'exam').strip().lower()
    coord_order = str(current_app.config.get('PDF_IMPORT_COORD_ORDER', 'xyxy')).strip().lower()
    shrink_sides = (atype == 'QUE')

    b64, mime = llm_client.prepare_image(png_path, image_max_dim)
    sw, sh = _sent_image_size(png_path, image_max_dim)

    assist_pad = max(0.0, float(current_app.config.get('PDF_IMPORT_ASSIST_PAD_PCT', 0.6))) / 100.0
    refine_grow = max(0.0, float(current_app.config.get('PDF_IMPORT_REFINE_GROW_PCT', 3.5))) / 100.0

    if mode == 'generic' or generic_prompt:
        # Context-free prompt: the model finds regions matching the user
        # request. 'segment' (anchor) detection is exam-specific, so it
        # collapses to llm here. In generic MODE each box keeps its label; for
        # an exam run borrowing this prompt the box is exam-shaped with
        # qno=None (the caller assigns the running question number).
        system = ai_prompts.build_pdf_generic_system(instruction, coord_order,
                                                     endpoint_id=config.id)
        user_text = ai_prompts.build_pdf_generic_user_text(
            instruction, coord_order, endpoint_id=config.id)
        text, _info = llm_client.chat(config, system, user_text, images=[(b64, mime)])
        gboxes = ai_prompts.parse_generic_boxes(text, img_w=sw, img_h=sh,
                                                coord_order=coord_order)
        if method == 'refine':
            from app import pdf_layout
            gray = pdf_layout.load_gray(png_path)
            for g in gboxes:
                try:
                    g['box'] = pdf_layout.refine_box(gray, g['box'],
                                                     shrink_sides=(shrink_sides and mode != 'generic'),
                                                     grow_frac=refine_grow,
                                                     pad_frac=assist_pad)
                except Exception as e:  # pragma: no cover — keep the LLM box
                    logger.warning('pdf-import generic refine_box failed: %s', e)
        if mode == 'generic':
            boxes = [{'label': g.get('label'), 'box': g['box']} for g in gboxes]
        else:
            boxes = [{'qno': None, 'label': None, 'box': g['box'],
                      'continues_prev': False, 'continues_next': False} for g in gboxes]
        return boxes, (text or '')

    if method == 'segment':
        from app import pdf_layout
        system = ai_prompts.build_pdf_anchor_system(atype, endpoint_id=config.id)
        user_text = ai_prompts.build_pdf_anchor_user_text(atype,
                                                          endpoint_id=config.id)
        text, _info = llm_client.chat(config, system, user_text, images=[(b64, mime)])
        anchors = ai_prompts.parse_question_anchors(text, img_h=sh,
                                                    coord_order=coord_order)
        gray = pdf_layout.load_gray(png_path)
        seg = pdf_layout.segment_page(gray, anchors, shrink_sides=shrink_sides,
                                      pad_frac=assist_pad)
        from app.hierarchy import normalize_plan_label
        boxes = []
        for s in seg:
            qno = s.get('qno')
            boxes.append({
                'qno': qno,
                'label': normalize_plan_label(qno),
                'box': s['box'],
                'continues_prev': False,
                'continues_next': False,
            })
        return boxes, (text or '')

    # 'llm' or 'refine': the model returns full boxes.
    system = ai_prompts.build_pdf_box_system(atype, coord_order,
                                             endpoint_id=config.id)
    user_text = ai_prompts.build_pdf_box_user_text(atype, coord_order,
                                                   endpoint_id=config.id)
    text, _info = llm_client.chat(config, system, user_text, images=[(b64, mime)])
    boxes = ai_prompts.parse_question_boxes(text, img_w=sw, img_h=sh,
                                            coord_order=coord_order)

    if method == 'refine':
        from app import pdf_layout
        gray = pdf_layout.load_gray(png_path)
        for b in boxes:
            try:
                b['box'] = pdf_layout.refine_box(gray, b['box'],
                                                 shrink_sides=shrink_sides,
                                                 grow_frac=refine_grow,
                                                 pad_frac=assist_pad)
            except Exception as e:  # pragma: no cover — keep the LLM box
                logger.warning('pdf-import refine_box failed: %s', e)

    return boxes, (text or '')


# ==================== Cropping ====================

def crop_page(png_path: str, box, pad_frac: float = 0.006,
              trim_white: bool = True, whiteness_threshold: int = 250,
              min_px: int = 8):
    """Crop the high-res page PNG to the fractional ``box`` ``[x1,y1,x2,y2]``.

    A small fractional pad is added first; when ``trim_white`` is on, the
    result is then tightened to its non-white content (so a slightly loose
    LLM box doesn't leave a wide white border, and trailing blank answer
    space below a question is dropped). White-trimming never removes content,
    so solution side-notes are preserved. Returns a PIL ``Image`` (RGB).

    Raises ``ValueError`` on a degenerate box so the caller can record an
    error for that question rather than writing a broken crop.
    """
    from PIL import Image, ImageChops

    x1, y1, x2, y2 = box
    x1, x2 = sorted((float(x1), float(x2)))
    y1, y2 = sorted((float(y1), float(y2)))
    x1 = max(0.0, x1 - pad_frac); y1 = max(0.0, y1 - pad_frac)
    x2 = min(1.0, x2 + pad_frac); y2 = min(1.0, y2 + pad_frac)

    with Image.open(png_path) as im:
        im.load()
        if im.mode != 'RGB':
            im = im.convert('RGB')
        w, h = im.size
        left, top = int(x1 * w), int(y1 * h)
        right, bottom = int(x2 * w), int(y2 * h)
        if right - left < min_px or bottom - top < min_px:
            raise ValueError('degenerate crop box')
        crop = im.crop((left, top, right, bottom))

        if trim_white:
            threshold = max(0, min(255, int(whiteness_threshold)))
            ref = Image.new('RGB', crop.size, (threshold, threshold, threshold))
            darkness = ImageChops.subtract(ref, ImageChops.darker(crop, ref))
            bbox = darkness.getbbox()
            if bbox is not None:
                pad = 8
                cl = max(0, bbox[0] - pad)
                ct = max(0, bbox[1] - pad)
                cr = min(crop.size[0], bbox[2] + pad)
                cb = min(crop.size[1], bbox[3] + pad)
                if cr - cl >= min_px and cb - ct >= min_px:
                    crop = crop.crop((cl, ct, cr, cb))
        # Force the (lazy) crop to materialise its own pixel buffer before the
        # source image's context manager closes its file pointer, so the
        # returned image is fully independent.
        crop.load()
        return crop


# ==================== Processed-PDF / ZIP export ====================

def pages_to_pdf_bytes(token: str, kind: str) -> bytes:
    """Assemble the staged page PNGs for ``kind`` back into a single PDF and
    return its bytes. The staged PNGs already reflect any deskew applied at
    rasterisation, so this is the "deskewed / processed" PDF. Raises
    ``ValueError`` when the kind has no staged pages."""
    import io
    from PIL import Image

    meta = load_meta(token)
    info = meta.get(kind)
    if not info or not info.get('pages'):
        raise ValueError('no staged pages for this side')

    images = []
    try:
        for p in info['pages']:
            path = page_png_path(token, kind, p['index'])
            im = Image.open(path)
            im.load()
            if im.mode != 'RGB':
                im = im.convert('RGB')
            images.append(im)
        if not images:
            raise ValueError('no pages')
        buf = io.BytesIO()
        images[0].save(buf, format='PDF', save_all=True,
                       append_images=images[1:])
        return buf.getvalue()
    finally:
        for im in images:
            try:
                im.close()
            except Exception:
                pass


def _safe_filename(name: str, fallback: str) -> str:
    """Sanitise a label into a filesystem-safe stem (no extension)."""
    name = re.sub(r'[^\w\-. ]+', '_', (name or '').strip())
    name = re.sub(r'\s+', '_', name).strip('._')
    return name[:60] or fallback


def export_zip_bytes(token: str, kind: str, items, pad_frac: float = 0.0) -> bytes:
    """Crop every ``item`` ({page, label, box}) out of the staged page PNGs
    and return a ZIP archive of PNGs (Generic Extraction download).

    No white-trimming is applied so the user gets exactly the region they
    selected (plus the optional ``pad_frac``). Filenames use each item's
    label, de-duplicated, falling back to ``page<NN>_<i>``.
    """
    import io
    import zipfile

    buf = io.BytesIO()
    used = {}
    count = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, it in enumerate(items or []):
            box = it.get('box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                page = int(it.get('page', 0))
            except (ValueError, TypeError):
                continue
            png = page_png_path(token, kind, page)
            try:
                crop = crop_page(png, [float(v) for v in box], pad_frac=pad_frac,
                                 trim_white=False)
            except Exception as e:  # pragma: no cover — skip a bad region
                logger.warning('pdf-import zip crop failed (region %s): %s', i, e)
                continue
            stem = _safe_filename(it.get('label'), f'page{page + 1:02d}_{i + 1}')
            n = used.get(stem, 0) + 1
            used[stem] = n
            fname = f'{stem}.png' if n == 1 else f'{stem}_{n}.png'
            img_buf = io.BytesIO()
            crop.save(img_buf, format='PNG')
            zf.writestr(fname, img_buf.getvalue())
            count += 1
    if count == 0:
        raise ValueError('no regions to export')
    return buf.getvalue()


# ==================== SSE generators ====================

def _empty_stats():
    return {'pages': 0, 'questions': 0}


def _check_method_available(method: str):
    """Return an error string if ``method`` needs NumPy and it's missing,
    else None. Lets iter_detect fail fast with one clear message instead of
    erroring on every page."""
    if (method or 'llm') in ('refine', 'segment'):
        try:
            from app import pdf_layout
            if not pdf_layout.numpy_available():
                return ('LLM-assisted detection needs NumPy, which is not '
                        'installed. Install it (pip install "numpy>=1.26") or '
                        'use the "LLM only" method.')
        except Exception:  # pragma: no cover
            return 'LLM-assisted detection module failed to load.'
    return None


def iter_detect(app, cancel, token: str, config, image_max_dim: int,
                debug: bool = False, method: str = 'llm',
                parallel: bool = False, max_workers: int = 1):
    """Generator yielding detection progress events (one LLM call per page).

    Accumulates the detected boxes into ``plan.json`` so a later commit can
    read them even without the browser echoing them back. When ``debug`` is
    set, each page's verbatim model output is logged and attached to the
    page event so coordinate problems can be diagnosed in the browser.
    ``method`` selects LLM-only vs an assisted CV method (see
    :func:`detect_page`).

    When ``parallel`` is set (cloud endpoints) the per-page LLM round-trips fan
    out across ``max_workers`` threads. Boxes are accumulated on the consumer
    thread as results arrive; for custom-prompt exam runs the auto question
    numbers are assigned once at the end in reading order (page, top-Y) so the
    numbering is deterministic regardless of completion order.
    """
    meta = load_meta(token)
    is_generic = (meta.get('mode') == 'generic')
    # Exam runs may borrow the context-free prompt (custom_prompt) to extract
    # questions from non-exam material, importing them with auto-numbered Qs.
    custom_prompt = (not is_generic) and bool(meta.get('custom_prompt'))
    instruction = meta.get('instruction') or ''
    kinds = [k for k in ('que', 'sol')
             if meta.get(k) and meta[k].get('pages')]
    total = sum(len(meta[k]['pages']) for k in kinds)

    plan = {'que': [], 'sol': []}
    if total == 0:
        save_plan(token, plan)
        yield {'type': 'error', 'message': 'No pages to process.'}
        yield {'type': 'done', 'message': 'Nothing to detect.', 'current': 0,
               'total': 0, 'stats': _empty_stats(), 'plan': plan}
        return

    method = (method or 'llm').strip().lower()
    method_err = _check_method_available(method)
    if method_err:
        save_plan(token, plan)
        yield {'type': 'error', 'message': method_err}
        yield {'type': 'done', 'message': 'Detection aborted.', 'current': 0,
               'total': total, 'stats': _empty_stats(), 'plan': plan}
        return

    method_label = {'llm': 'LLM only', 'refine': 'LLM assisted (refine)',
                    'segment': 'LLM assisted (segment)'}.get(method, method)
    yield {'type': 'info',
           'message': (f'Detecting questions across {total} page(s) with model '
                       f'{config.model_name} [{method_label}]...'),
           'current': 0, 'total': total}

    # Flat work list (one LLM call per page) shared by both branches.
    work = []
    for kind in kinds:
        atype = 'QUE' if kind == 'que' else 'SOL'
        for p in meta[kind]['pages']:
            work.append({'kind': kind, 'atype': atype, 'idx': p['index']})

    def _ingest(kind, idx, boxes):
        """Accumulate one page's boxes into the plan (consumer-thread only)."""
        from app.hierarchy import normalize_plan_label, parse_qno_token
        for b in boxes:
            if is_generic:
                plan[kind].append({'page': idx, 'label': b.get('label'), 'box': b['box']})
            elif custom_prompt:
                # qno assigned at the end in reading order (see _number_custom).
                plan[kind].append({'page': idx, 'qno': None, 'label': None,
                                   'box': b['box']})
            else:
                qno = b.get('qno')
                label = b.get('label') or normalize_plan_label(qno)
                if not label and qno is not None:
                    parsed = parse_qno_token(qno)
                    label = parsed.token[1:] if parsed else str(qno)
                if qno is None and label:
                    parsed = parse_qno_token(label)
                    if parsed:
                        qno = parsed.qno
                # Keep the continuation flags transiently so _stitch_continuations
                # can re-link multi-page questions in reading order regardless of
                # the (possibly parallel) page-completion order.
                plan[kind].append({'page': idx, 'qno': qno, 'label': label,
                                   'box': b['box'],
                                   '_cp': bool(b.get('continues_prev')),
                                   '_cn': bool(b.get('continues_next'))})

    def _page_event(kind, atype, idx, boxes, raw):
        page_ev = {'kind': kind, 'index': idx, 'boxes': boxes}
        if debug:
            logger.info('pdf-import raw (%s page %s):\n%s', kind, idx + 1, raw)
            page_ev['raw'] = (raw or '')[:6000]
        noun = 'region(s)' if is_generic else 'question region(s)'
        label = 'Page' if is_generic else atype
        return {'type': 'success',
                'message': f'{label} {idx + 1}: found {len(boxes)} {noun}.',
                'page': page_ev}

    def _finalize_detect():
        """Resolve question numbers once ALL pages are collected, so the result
        is independent of the order pages finished detecting (critical under
        parallel detection). For a custom-prompt run that means a clean 1..N
        reading-order sequence; for a standard run it means re-linking
        multi-page questions via the continuation flags. Generic mode keeps its
        labels. Finally the transient continuation flags are dropped so
        ``plan.json`` stays ``{page, qno, label, box}``.
        """
        from app.hierarchy import normalize_plan_label, parse_qno_token
        if custom_prompt:
            for kind in ('que', 'sol'):
                ordered = sorted(plan[kind], key=lambda it: (it['page'], it['box'][1]))
                for i, it in enumerate(ordered, 1):
                    it['qno'] = i
                    it['label'] = str(i)
        elif not is_generic:
            _stitch_continuations(plan)
        for k in ('que', 'sol'):
            for it in plan[k]:
                it.pop('_cp', None)
                it.pop('_cn', None)
                if is_generic:
                    continue
                lab = normalize_plan_label(it.get('label') or it.get('qno'))
                if lab:
                    it['label'] = lab
                    parsed = parse_qno_token(lab)
                    if parsed:
                        it['qno'] = parsed.qno

    def _worker(item):
        png = page_png_path(token, item['kind'], item['idx'])
        boxes, raw = detect_page(config, png, item['atype'], image_max_dim,
                                 method, mode=meta.get('mode', 'exam'),
                                 instruction=instruction,
                                 generic_prompt=custom_prompt)
        return {'boxes': boxes, 'raw': raw}

    done = 0
    qcount = 0
    use_parallel = bool(parallel and max_workers and max_workers > 1)

    if use_parallel:
        from app.parallel import run_parallel, CANCELLED
        for r in run_parallel(app, cancel, work, _worker, max_workers):
            if r['result'] is CANCELLED:
                continue
            done += 1
            kind, atype, idx = r['item']['kind'], r['item']['atype'], r['item']['idx']
            if r['error'] is not None:
                logger.warning('pdf-import detect failed (%s page %s): %s', kind, idx + 1, r['error'])
                yield {'type': 'error',
                       'message': f'{atype} page {idx + 1}: detection failed ({r["error"]}).',
                       'current': done, 'total': total,
                       'page': {'kind': kind, 'index': idx, 'boxes': []}}
                continue
            boxes, raw = r['result']['boxes'], r['result']['raw']
            _ingest(kind, idx, boxes)
            qcount += len(boxes)
            ev = _page_event(kind, atype, idx, boxes, raw)
            ev['current'] = done; ev['total'] = total
            yield ev
    else:
        for item in work:
            kind, atype, idx = item['kind'], item['atype'], item['idx']
            if cancel.is_set():
                _finalize_detect()
                save_plan(token, plan)
                yield {'type': 'done', 'message': 'Detection cancelled.',
                       'current': done, 'total': total,
                       'stats': {'pages': done, 'questions': qcount},
                       'plan': plan}
                return
            png = page_png_path(token, kind, idx)
            try:
                boxes, raw = detect_page(config, png, atype, image_max_dim,
                                         method, mode=meta.get('mode', 'exam'),
                                         instruction=instruction,
                                         generic_prompt=custom_prompt)
            except Exception as e:  # transport / parse failure for this page
                done += 1
                logger.warning('pdf-import detect failed (%s page %s): %s', kind, idx + 1, e)
                yield {'type': 'error',
                       'message': f'{atype} page {idx + 1}: detection failed ({e}).',
                       'current': done, 'total': total,
                       'page': {'kind': kind, 'index': idx, 'boxes': []}}
                continue
            _ingest(kind, idx, boxes)
            qcount += len(boxes)
            done += 1
            ev = _page_event(kind, atype, idx, boxes, raw)
            ev['current'] = done; ev['total'] = total
            yield ev

    _finalize_detect()
    save_plan(token, plan)
    if cancel.is_set():
        yield {'type': 'done', 'message': 'Detection cancelled.',
               'current': done, 'total': total,
               'stats': {'pages': done, 'questions': qcount},
               'plan': plan}
    else:
        yield {'type': 'done',
               'message': f'Detection complete: {qcount} question region(s) across {total} page(s).',
               'current': total, 'total': total,
               'stats': {'pages': total, 'questions': qcount},
               'plan': plan}


def detect_single_page(config, token: str, kind: str, index: int,
                       image_max_dim: int, method: str = 'llm'):
    """Re-run detection for a single page (the review-mode 'Re-run page'
    button), optionally with a different ``method``. Returns
    ``(boxes, raw_text)``. Raises a clear error if an assisted method is
    requested without NumPy."""
    err = _check_method_available(method)
    if err:
        raise RuntimeError(err)
    meta = load_meta(token)
    atype = 'QUE' if kind == 'que' else 'SOL'
    png = page_png_path(token, kind, index)
    custom_prompt = (meta.get('mode') != 'generic') and bool(meta.get('custom_prompt'))
    return detect_page(config, png, atype, image_max_dim, method,
                       mode=meta.get('mode', 'exam'),
                       instruction=meta.get('instruction', ''),
                       generic_prompt=custom_prompt)


def _stitch_continuations(plan: dict):
    """Re-link multi-page questions for a standard exam plan, in place.

    The model sees ONE page at a time, so a question that spills onto the next
    page is reported as a separate top-of-page region flagged ``continues_prev``
    (and the page above is flagged ``continues_next``). Walking each side in
    reading order ``(page, top-Y)``, the topmost region of a page that is a
    continuation inherits the qno **and label** of the box immediately before
    it — so the tail joins the right question regardless of the order pages
    were detected (parallel detection returns pages out of order). The link
    only crosses a page boundary (``cur.page > prev.page``); within-page boxes
    are untouched. Chains (a question spanning 3+ pages) resolve because we
    process in order.

    Relies on the transient ``_cp`` / ``_cn`` keys set by ``iter_detect`` /
    pass-2; boxes without a usable predecessor qno/label are left as-is.
    """
    for kind in ('que', 'sol'):
        items = sorted(plan.get(kind, []) or [],
                       key=lambda it: (it.get('page', 0), it['box'][1]))
        for i in range(1, len(items)):
            cur, prev = items[i], items[i - 1]
            if not (cur.get('page', 0) > prev.get('page', 0)
                    and (cur.get('_cp') or prev.get('_cn'))):
                continue
            if prev.get('label'):
                cur['label'] = prev['label']
            if prev.get('qno') is not None:
                cur['qno'] = prev['qno']
            if prev.get('role') and not cur.get('role'):
                cur['role'] = prev['role']
            if prev.get('source_label') and not cur.get('source_label'):
                cur['source_label'] = prev['source_label']
            # A continuation page has no question number in the margin, so
            # the model boxes only the indented body and x1 drifts right.
            # Pages of one paper share a layout: inherit the head's x-span.
            pb, cb = prev.get('box'), cur.get('box')
            if (isinstance(pb, (list, tuple)) and len(pb) == 4
                    and isinstance(cb, (list, tuple)) and len(cb) == 4):
                cur['box'] = [float(pb[0]), float(cb[1]),
                              float(pb[2]), float(cb[3])]


def _label_sort_key(label):
    from app.hierarchy import parse_qno_token, part_segments
    p = parse_qno_token(label)
    if not p:
        return (10**9, 0, 99, str(label or ''))
    depth = 0 if not p.part_path else len(part_segments(p.part_path))
    return (p.qno, p.qno_end or p.qno, depth, p.part_path or '')


def _is_part_label(label) -> bool:
    from app.hierarchy import parse_qno_token
    p = parse_qno_token(label)
    return bool(p and p.part_path)


def _parent_key(label):
    from app.hierarchy import format_qno_token, parse_qno_token
    p = parse_qno_token(label)
    if not p:
        return None
    return format_qno_token(p.qno, p.qno_end, None)[1:]


def _same_question(label, parent_label) -> bool:
    from app.hierarchy import parse_qno_token
    a, b = parse_qno_token(label), parse_qno_token(parent_label)
    return bool(a and b and a.qno == b.qno and a.qno_end == b.qno_end)


def _group_plan(plan: dict):
    """Group plan boxes by (kind, label) into ordered commit groups.

    Returns a list of ``(kind, atype, label, parts)`` where ``parts`` is the
    list of ``{page, box}`` ordered by (page, top-Y). Roots/stems (no part
    path) come before lettered parts so ``ensure_question`` can create
    parents first.
    """
    groups = []
    for kind in ('que', 'sol'):
        atype = 'QUE' if kind == 'que' else 'SOL'
        by_label: dict[str, list] = {}
        for item in plan.get(kind, []) or []:
            label = plan_item_label(item)
            if not label:
                continue
            box = item.get('box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            by_label.setdefault(label, []).append(
                {'page': int(item.get('page', 0)),
                 'box': [float(v) for v in box]})
        for label in sorted(by_label.keys(), key=_label_sort_key):
            parts = sorted(by_label[label],
                           key=lambda it: (it['page'], it['box'][1]))
            groups.append((kind, atype, label, parts))
    return groups


def _que_label_set(plan: dict) -> set:
    return {plan_item_label(it) for it in (plan.get('que') or [])
            if plan_item_label(it)}


def _resolve_sol_commit_label(label: str, que_labels: set):
    """Map a SOL plan label onto a QUE node.

    Exact match wins. An unmatched lettered part is dropped (leaf SOL stays
    empty). A whole-question SOL with no QUE counterpart attaches to the stem.
    """
    from app.hierarchy import format_qno_token, parse_qno_token
    if label in que_labels:
        return label
    parsed = parse_qno_token(label)
    if not parsed:
        return None
    if parsed.part_path:
        return None
    return format_qno_token(parsed.qno, parsed.qno_end, None)[1:]


def iter_commit(app, cancel, token: str, plan: dict, versions,
                overwrite: bool, source_path: str, trim_white: bool = False):
    """Generator yielding commit progress events: crop each grouped question
    region and create ``Question`` + ``QuestionAsset`` (IMG) rows.

    ``versions`` maps each kind to its target asset version, e.g.
    ``{'que': 'ENO', 'sol': 'EN'}`` so question and solution images can be
    imported under different versions. A bare string is accepted for backward
    compatibility and applied to both kinds.

    Labels may be ``5``, ``5a``, or ``23-24``. ``ensure_question`` creates
    missing ancestors. Unmatched lettered SOL groups are skipped; a leftover
    whole-question SOL attaches to the stem.
    """
    if isinstance(versions, str):
        versions = {'que': versions, 'sol': versions}
    from app.hierarchy import ensure_question, parse_qno_token
    from app.ingestor import determine_question_type
    from app.batch_image_gen import replace_img_assets, slot_has_img

    meta = load_meta(token)
    subject = meta['subject']
    source = meta['source']
    year = meta['year']
    paper = meta['paper']
    whiteness = int(app.config.get('THUMBNAIL_WHITENESS_THRESHOLD', 250))
    crop_pad = max(0.0, float(app.config.get('PDF_IMPORT_CROP_PAD_PCT', 0.6))) / 100.0

    que_labels = _que_label_set(plan)
    groups = _group_plan(plan)
    total = len(groups)
    if total == 0:
        yield {'type': 'error', 'message': 'No question regions to import. '
               'Make sure every region has a question number.'}
        yield {'type': 'done', 'message': 'Nothing to import.', 'current': 0,
               'total': 0, 'stats': {'questions_created': 0, 'assets_written': 0,
                                     'skipped': 0, 'errors': 0}}
        return

    ver_note = '/'.join(f'{k.upper()}:{versions.get(k)}'
                        for k in ('que', 'sol') if versions.get(k))
    yield {'type': 'info',
           'message': f'Importing {total} question slot(s) for {subject}_{source}_{year}_{paper} ({ver_note})...',
           'current': 0, 'total': total}

    created_q = assets_written = skipped = errors = 0
    done = 0
    q_type = determine_question_type(subject, source, paper)
    for (kind, atype, label, parts) in groups:
        if cancel.is_set():
            yield {'type': 'done', 'message': 'Import cancelled.',
                   'current': done, 'total': total,
                   'stats': {'questions_created': created_q,
                             'assets_written': assets_written,
                             'skipped': skipped, 'errors': errors}}
            return

        version = versions.get(kind)
        commit_label = label
        if kind == 'sol':
            commit_label = _resolve_sol_commit_label(label, que_labels)
            if not commit_label:
                skipped += 1
                done += 1
                yield {'type': 'skip',
                       'message': f'SOL Q{label}: no matching question part — skipped.',
                       'current': done, 'total': total}
                continue

        parsed = parse_qno_token(commit_label)
        if not parsed:
            skipped += 1
            done += 1
            yield {'type': 'skip',
                   'message': f'{atype} {commit_label}: invalid question number — skipped.',
                   'current': done, 'total': total}
            continue
        qid = f'{subject}_{source}_{year}_{paper}_{parsed.token}'
        try:
            question, created = ensure_question(
                subject, source, parsed.token, year=year, paper=paper,
                q_type=q_type)
            db.session.commit()
            is_new = created
            if created:
                created_q += 1

            if slot_has_img(question.id, atype, version) and not overwrite:
                skipped += 1
                done += 1
                yield {'type': 'skip',
                       'message': f'{qid} {atype} {version}: already has image(s) — skipped (enable Overwrite to replace).',
                       'current': done, 'total': total}
                continue

            imgs = []
            for prt in parts:
                png = page_png_path(token, kind, prt['page'])
                imgs.append(crop_page(png, prt['box'], pad_frac=crop_pad,
                                      trim_white=trim_white,
                                      whiteness_threshold=whiteness))

            res = replace_img_assets(question, atype, version, imgs,
                                     stitch=False, source_path=source_path)
            assets_written += res['wrote']
            done += 1
            extra = ' (new question)' if is_new else ''
            part_note = f' ({res["wrote"]} parts)' if res['wrote'] > 1 else ''
            yield {'type': 'success',
                   'message': f'{qid} {atype} {version}: saved image{part_note}{extra}.',
                   'current': done, 'total': total}
        except Exception as e:
            db.session.rollback()
            errors += 1
            done += 1
            logger.exception('pdf-import commit failed for %s %s', qid, atype)
            yield {'type': 'error',
                   'message': f'{qid} {atype}: {e}',
                   'current': done, 'total': total}

    yield {'type': 'done',
           'message': (f'Import complete: {assets_written} image(s) saved, '
                       f'{created_q} new question(s), {skipped} skipped, {errors} error(s).'),
           'current': total, 'total': total,
           'stats': {'questions_created': created_q,
                     'assets_written': assets_written,
                     'skipped': skipped, 'errors': errors}}


def detect_parts(config, crop_png: str, atype: str, image_max_dim: int,
                 expected_labels=None):
    """Pass-2: detect stem + lettered parts on one already-cropped question.

    ``crop_png`` is an absolute path. ``atype`` is ``QUE`` or ``SOL``.
    ``expected_labels`` (SOL) is a list like ``['stem', 'a', 'ci']``.
    Returns ``(boxes, raw_text)`` where each box is crop-relative 0..1 with
    ``label`` ``stem`` or a letter path.
    """
    from app import ai_prompts, llm_client

    coord_order = str(current_app.config.get(
        'PDF_IMPORT_COORD_ORDER', 'xyxy')).strip().lower()
    b64, mime = llm_client.prepare_image(crop_png, image_max_dim)
    sw, sh = _sent_image_size(crop_png, image_max_dim)
    system = ai_prompts.build_pdf_part_system(
        atype, expected_labels, coord_order, endpoint_id=config.id)
    user_text = ai_prompts.build_pdf_part_user_text(
        atype, expected_labels, coord_order, endpoint_id=config.id)
    text, _info = llm_client.chat(config, system, user_text,
                                  images=[(b64, mime)])
    boxes = ai_prompts.parse_part_boxes(text, img_w=sw, img_h=sh,
                                        coord_order=coord_order)
    return boxes, (text or '')


def expected_part_labels_for(parent_label, que_items) -> list:
    """Relative pass-2 labels (``stem``, ``a``, ``ci``) already on the QUE side."""
    from app.hierarchy import parse_qno_token
    parent = parse_qno_token(parent_label)
    if not parent:
        return []
    seen = []
    for it in que_items or []:
        lab = plan_item_label(it)
        p = parse_qno_token(lab)
        if not p or p.qno != parent.qno or p.qno_end != parent.qno_end:
            continue
        if not p.part_path:
            rel = 'stem'
        else:
            pp = parent.part_path or ''
            cp = p.part_path or ''
            if pp and not cp.startswith(pp):
                continue
            rel = cp[len(pp):] or 'stem'
        if rel not in seen:
            seen.append(rel)
    return seen


def original_group_parts(items, parent_label) -> list:
    """Page crops to send to pass-2 for ``parent_label``.

    Prefers stored ``source_box`` / ``source_page`` (already split); otherwise
    the unsplit items whose label is exactly the parent.
    """
    from app.hierarchy import normalize_plan_label
    parent = normalize_plan_label(parent_label) or parent_label
    sourced = []
    for it in items or []:
        src = (normalize_plan_label(it.get('source_label'))
               if it.get('source_label') else None)
        if src == parent:
            sourced.append(it)
    if sourced:
        seen = set()
        parts = []
        for it in sourced:
            sb = it.get('source_box')
            if not (isinstance(sb, (list, tuple)) and len(sb) == 4):
                continue
            sp = it.get('source_page', it.get('page', 0))
            try:
                sp = int(sp)
                box = [float(v) for v in sb]
            except (TypeError, ValueError):
                continue
            key = (sp, tuple(box))
            if key in seen:
                continue
            seen.add(key)
            parts.append({'page': sp, 'box': box})
        if parts:
            return sorted(parts, key=lambda p: (p['page'], p['box'][1]))
    exact = []
    for it in items or []:
        if plan_item_label(it) != parent:
            continue
        box = it.get('box')
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        exact.append({'page': int(it.get('page', 0)),
                      'box': [float(v) for v in box]})
    return sorted(exact, key=lambda p: (p['page'], p['box'][1]))


def _write_split_crop(token, kind, page, box) -> str:
    d = os.path.join(token_dir(token), '_split_crops')
    os.makedirs(d, exist_ok=True)
    img = crop_page(page_png_path(token, kind, page), box,
                    pad_frac=0.0, trim_white=False)
    dest = os.path.join(d, f'{kind}_{page}_{uuid.uuid4().hex[:8]}.png')
    img.save(dest, 'PNG')
    return dest


def full_width_child_box(parent_box, crop_box):
    """Map a crop-relative pass-2 box onto the page, keeping the parent's
    horizontal span.

    Pass 2 only needs to segment a question vertically; the stem and every
    part share the parent's left/right edges so they stay aligned with each
    other and with the side's uniform width.
    """
    x1, _y1, x2, _y2 = [float(v) for v in parent_box]
    mapped = map_crop_box_to_page(parent_box, crop_box)
    return [x1, mapped[1], x2, mapped[3]]


def _llm_error_cls():
    from app.llm_client import LLMError
    return LLMError


def _split_one_group(token, kind, parent_label, parts, config, image_max_dim,
                     expected_labels=None, debug: bool = False):
    """Run pass-2 on each page crop of one question. Returns (children|None, raws)."""
    from app.hierarchy import compose_part_label, parse_qno_token
    atype = 'QUE' if kind == 'que' else 'SOL'
    children = []
    raws = []
    for prt in parts:
        crop_path = None
        try:
            crop_path = _write_split_crop(token, kind, prt['page'], prt['box'])
            try:
                boxes, raw = detect_parts(config, crop_path, atype, image_max_dim,
                                          expected_labels=expected_labels)
            except _llm_error_cls() as e:
                # One retry: cloud gateways occasionally stall a single
                # request past the endpoint timeout; the call is idempotent.
                logger.warning('pdf-import pass-2 %s Q%s: %s — retrying once',
                               kind, parent_label, e)
                boxes, raw = detect_parts(config, crop_path, atype, image_max_dim,
                                          expected_labels=expected_labels)
            if debug:
                raws.append(raw)
            parsed_p = parse_qno_token(parent_label)
            for b in boxes:
                child_label = compose_part_label(parent_label, b.get('label'))
                if not child_label:
                    continue
                parsed_c = parse_qno_token(child_label)
                is_stem_box = (parsed_c and parsed_p
                               and parsed_c.part_path == parsed_p.part_path)
                children.append({
                    'page': int(prt['page']),
                    'qno': parsed_c.qno if parsed_c else None,
                    'label': child_label,
                    'box': full_width_child_box(prt['box'], b['box']),
                    'role': 'stem' if is_stem_box else 'part',
                    'source_label': parent_label,
                    'source_page': int(prt['page']),
                    'source_box': [float(v) for v in prt['box']],
                    '_cp': bool(b.get('continues_prev')),
                    '_cn': bool(b.get('continues_next')),
                })
        except Exception:
            logger.exception('pdf-import pass-2 failed for %s Q%s',
                             kind, parent_label)
        finally:
            if crop_path:
                try:
                    os.remove(crop_path)
                except OSError:
                    pass
    if not any(it.get('role') == 'part' for it in children):
        return None, raws
    return children, raws


def _replace_group(items, parent_label, new_items):
    from app.hierarchy import normalize_plan_label
    parent = normalize_plan_label(parent_label) or parent_label
    kept = []
    for it in items or []:
        lab = plan_item_label(it)
        src = (normalize_plan_label(it.get('source_label'))
               if it.get('source_label') else None)
        if src == parent or (lab and _same_question(lab, parent)):
            continue
        kept.append(it)
    return kept + list(new_items or [])


def _split_parent_labels(items, filter_set=None):
    """Parent labels to run pass-2 on, in reading order.

    With no filter, only unsplit whole-question boxes. With a filter, those
    parents are re-split even if they already have part boxes.
    """
    from app.hierarchy import normalize_plan_label
    seen = []
    seen_set = set()
    ordered = sorted(
        items or [],
        key=lambda x: (x.get('page', 0),
                       (x.get('box') or [0, 0, 0, 0])[1]))
    if filter_set is not None:
        for it in ordered:
            src = (normalize_plan_label(it.get('source_label'))
                   if it.get('source_label') else None)
            lab = plan_item_label(it)
            parent = src or (lab if lab and not _is_part_label(lab) else None)
            if parent and parent in filter_set and parent not in seen_set:
                seen_set.add(parent)
                seen.append(parent)
        return seen
    for it in ordered:
        if it.get('source_label'):
            continue
        lab = plan_item_label(it)
        if not lab or _is_part_label(lab):
            continue
        if lab not in seen_set:
            seen_set.add(lab)
            seen.append(lab)
    return seen


def _strip_split_flags(plan: dict) -> dict:
    for k in ('que', 'sol'):
        for it in plan.get(k) or []:
            it.pop('_cp', None)
            it.pop('_cn', None)
    return plan


def iter_split_detect(app, cancel, token: str, config, image_max_dim: int,
                      kinds=None, labels_filter=None, debug: bool = False,
                      parallel: bool = False, max_workers: int = 1):
    """SSE generator: pass-2 part split on each whole-question crop.

    ``kinds`` is ``'que'``, ``'sol'``, or ``'both'`` / ``None``.
    ``labels_filter`` is an optional iterable of parent labels to re-split.
    QUE is processed before SOL so SOL can use QUE labels as ``expected_labels``.

    With ``parallel`` (cloud endpoints), the crops of one side fan out across
    ``app.parallel.run_parallel``; QUE finishes before SOL starts so the SOL
    ``expected_labels`` come from the already-split QUE plan. Plan mutation
    happens only on this consumer thread.
    """
    from app.hierarchy import normalize_plan_label

    meta = load_meta(token)
    if meta.get('mode') == 'generic':
        yield {'type': 'error',
               'message': 'Part split is only available for exam papers.'}
        yield {'type': 'done', 'message': 'Aborted.', 'current': 0, 'total': 0,
               'plan': {'que': [], 'sol': []}}
        return

    plan = load_plan(token)
    if kinds in (None, '', 'both'):
        kind_list = [k for k in ('que', 'sol') if plan.get(k)]
    elif kinds in ('que', 'sol'):
        kind_list = [kinds]
    else:
        kind_list = [k for k in ('que', 'sol') if k in str(kinds)]

    filter_set = None
    if labels_filter:
        filter_set = set()
        for raw in labels_filter:
            lab = normalize_plan_label(raw)
            if lab:
                filter_set.add(lab)

    work = []
    for kind in kind_list:
        parents = _split_parent_labels(plan.get(kind) or [], filter_set)
        for parent in parents:
            parts = original_group_parts(plan.get(kind) or [], parent)
            if parts:
                work.append((kind, parent, parts))

    total = len(work)
    if total == 0:
        yield {'type': 'info',
               'message': 'No whole-question regions to split into parts.'}
        yield {'type': 'done', 'message': 'Nothing to split.', 'current': 0,
               'total': 0, 'plan': plan}
        return

    yield {'type': 'info',
           'message': (f'Splitting {total} question crop(s) into parts with '
                       f'{config.model_name}...'),
           'current': 0, 'total': total}

    done = 0
    use_parallel = bool(parallel and max_workers and max_workers > 1)

    def _emit(kind, parent, children):
        if children:
            plan[kind] = _replace_group(plan.get(kind) or [], parent, children)
            return {'type': 'success',
                    'message': f'{kind.upper()} Q{parent}: {len(children)} part region(s).'}
        return {'type': 'skip',
                'message': (f'{kind.upper()} Q{parent}: no lettered parts '
                            'detected — kept as one question.')}

    if use_parallel:
        from app.parallel import run_parallel, CANCELLED

        for kind in kind_list:
            side = [w for w in work if w[0] == kind]
            if not side:
                continue
            # expected_labels are read from the plan before this side fans out;
            # QUE is fully merged before SOL starts.
            items = []
            for _k, parent, parts in side:
                expected = None
                if kind == 'sol':
                    expected = expected_part_labels_for(parent, plan.get('que') or [])
                items.append({'kind': kind, 'parent': parent, 'parts': parts,
                              'expected': expected})

            def _worker(item):
                children, _raws = _split_one_group(
                    token, item['kind'], item['parent'], item['parts'],
                    config, image_max_dim,
                    expected_labels=item['expected'], debug=debug)
                return children

            cancelled = False
            for r in run_parallel(app, cancel, items, _worker, max_workers):
                if r['result'] is CANCELLED:
                    cancelled = True
                    continue
                done += 1
                it = r['item']
                if r['error'] is not None:
                    logger.warning('pdf-import pass-2 failed (%s Q%s): %s',
                                   it['kind'], it['parent'], r['error'])
                    yield {'type': 'error',
                           'message': f'{it["kind"].upper()} Q{it["parent"]}: part split failed ({r["error"]}).',
                           'current': done, 'total': total}
                    continue
                ev = _emit(it['kind'], it['parent'], r['result'])
                ev['current'] = done
                ev['total'] = total
                yield ev
            if cancelled or cancel.is_set():
                _stitch_continuations(plan)
                plan = _strip_split_flags(plan)
                save_plan(token, plan)
                yield {'type': 'done', 'message': 'Split cancelled.',
                       'current': done, 'total': total, 'plan': plan}
                return
    else:
        for kind, parent, parts in work:
            if cancel.is_set():
                save_plan(token, _strip_split_flags(plan))
                yield {'type': 'done', 'message': 'Split cancelled.',
                       'current': done, 'total': total, 'plan': plan}
                return
            expected = None
            if kind == 'sol':
                expected = expected_part_labels_for(parent, plan.get('que') or [])
            children, _raws = _split_one_group(
                token, kind, parent, parts, config, image_max_dim,
                expected_labels=expected, debug=debug)
            done += 1
            ev = _emit(kind, parent, children)
            ev['current'] = done
            ev['total'] = total
            yield ev

    _stitch_continuations(plan)
    plan = _strip_split_flags(plan)
    save_plan(token, plan)
    yield {'type': 'done', 'message': 'Part split complete.',
           'current': total, 'total': total, 'plan': plan}

"""Stage and commit IMG crops that split one question into a stem + parts.

Used by the dedicated Split-into-parts page. Auto-detect stitches every
QUE image for a version into one PNG (already done at stage time), then
runs PDF import pass 2 (``detect_parts``) once on that stitch. Optional
``find_parent`` adds pass 1 for a full exam page. Staging lives under
``SYSTEM_PATH/.question_split/<token>/``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone

from flask import current_app
from PIL import Image

from app import db, storage
from app.batch_image_gen import replace_img_assets, stitch_vertically
from app.hierarchy import HierarchyError, ensure_question, format_qno_token, parse_qid, parse_qno_token
from app.models import Question, QuestionAsset
from app.pdf_import import crop_page
from app.utils import VERSIONS

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r'^[0-9a-f]{8,40}$')
MAX_AGE_HOURS = 6.0


def staging_root() -> str:
    root = storage.safe_join(storage.system_path(), '.question_split')
    if root is None:
        raise RuntimeError('split staging path escaped SYSTEM_PATH')
    os.makedirs(root, exist_ok=True)
    return root


def token_dir(token: str) -> str:
    if not TOKEN_RE.match(token or ''):
        raise ValueError('invalid split token')
    d = storage.safe_join(staging_root(), token)
    if d is None:
        raise ValueError('invalid split token path')
    return d


def cleanup_old(max_age_hours: float = MAX_AGE_HOURS) -> None:
    root = staging_root()
    cutoff = time.time() - max_age_hours * 3600.0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def discard(token: str) -> None:
    try:
        d = token_dir(token)
    except ValueError:
        return
    shutil.rmtree(d, ignore_errors=True)


def load_meta(token: str) -> dict:
    path = os.path.join(token_dir(token), 'meta.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _source_abs(rel_path: str) -> str | None:
    parts = [p for p in rel_path.replace('\\', '/').split('/') if p]
    return storage.safe_join(storage.source_path(), *parts)


def que_formats(question: Question) -> set[str]:
    return {
        a.file_format
        for a in QuestionAsset.query.filter_by(question_id=question.id, asset_type='QUE')
    }


def stage_question(question: Question) -> dict:
    """Copy/stitch QUE IMGs into a staging dir. Returns meta (includes token)."""
    formats = que_formats(question)
    if 'MD' in formats or 'DOC' in formats:
        raise ValueError('Split requires an IMG question image. MD/DOC QUE cannot be cropped.')
    if 'IMG' not in formats:
        raise ValueError('This question has no IMG QUE to split.')

    cleanup_old()
    token = uuid.uuid4().hex
    dest = token_dir(token)
    os.makedirs(dest, exist_ok=True)

    versions_meta = {}
    for version in VERSIONS:
        assets = (
            QuestionAsset.query.filter_by(
                question_id=question.id, asset_type='QUE',
                version=version, file_format='IMG',
            )
            .order_by(QuestionAsset.part_number)
            .all()
        )
        if not assets:
            continue
        images = []
        for a in assets:
            abs_path = _source_abs(a.file_path)
            if not abs_path or not os.path.isfile(abs_path):
                continue
            im = Image.open(abs_path)
            im.load()
            if im.mode != 'RGB':
                im = im.convert('RGB')
            images.append(im)
        if not images:
            continue
        page_recs = []
        y_top = 0
        for i, im in enumerate(images):
            page_name = (f'{version}.png' if len(images) == 1
                         else f'{version}_p{i + 1}.png')
            page_path = os.path.join(dest, page_name)
            if len(images) > 1 or i == 0:
                im.save(page_path, format='PNG')
            page_recs.append({
                'filename': page_name,
                'width': im.width,
                'height': im.height,
                'y_top': y_top,
            })
            y_top += im.height
        stitched = stitch_vertically(images) if len(images) > 1 else images[0]
        filename = f'{version}.png'
        out_path = os.path.join(dest, filename)
        stitched.save(out_path, format='PNG')
        versions_meta[version] = {
            'filename': filename,
            'width': stitched.width,
            'height': stitched.height,
            'pages': page_recs,
        }

    if not versions_meta:
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError('No readable IMG QUE files were found on disk.')

    meta = {
        'token': token,
        'question_id': question.id,
        'qid': question.qid,
        'versions': versions_meta,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    with open(os.path.join(dest, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f)
    return meta


def staged_image_path(token: str, version: str) -> str | None:
    meta = load_meta(token)
    info = (meta.get('versions') or {}).get(version)
    if not info:
        return None
    path = storage.safe_join(token_dir(token), info['filename'])
    return path


def _staged_pages(token: str, version: str):
    """Resolve stitch path + per-page records for a staged version."""
    meta = load_meta(token)
    versions = list((meta.get('versions') or {}).keys())
    version = (version or '').strip().upper()
    if not version or version not in (meta.get('versions') or {}):
        version = versions[0] if versions else ''
    if not version:
        raise ValueError('No staged image for this session.')
    info = (meta.get('versions') or {}).get(version) or {}
    stitch_path = staged_image_path(token, version)
    if not stitch_path or not os.path.isfile(stitch_path):
        raise ValueError('Staged image is missing; reload the split page.')
    stitch_w = int(info.get('width') or 0)
    stitch_h = int(info.get('height') or 0)
    if stitch_w <= 0 or stitch_h <= 0:
        with Image.open(stitch_path) as im:
            stitch_w, stitch_h = im.size
    pages = list(info.get('pages') or [])
    if not pages:
        pages = [{'filename': info.get('filename'), 'width': stitch_w,
                  'height': stitch_h, 'y_top': 0}]
    return version, info, stitch_path, stitch_w, stitch_h, pages


def iter_detect_boxes(token: str, version: str, config, image_max_dim: int,
                      method: str = 'llm', find_parent: bool = False,
                      cancel=None):
    """Yield SSE-shaped events: one PDF-import pass 2 on the staged stitch.

    Multi-image QUE (reconstructed part crops, a multi-page WHOLE, …) is
    stacked at stage time. Detect always runs on that single stitch so the
    model sees the whole question — never each source PNG as its own page.
    ``done`` carries ``boxes`` / ``raw`` in stitch coordinates. ``cancel``
    is a ``threading.Event`` from ``pdf_import.new_job``.
    """
    from app.pdf_import import merge_part_boxes_by_label, split_question_png

    _version, _info, stitch_path, _sw, _sh, pages = _staged_pages(
        token, version)
    nsrc = max(1, len(pages))
    if cancel is not None and cancel.is_set():
        yield {'type': 'error', 'message': 'Cancelled.',
               'current': 0, 'total': 1}
        yield {'type': 'done', 'message': 'Cancelled.', 'boxes': [],
               'raw': '', 'current': 1, 'total': 1}
        return

    if nsrc > 1:
        prefix = f'Stitched {nsrc} images into one. '
    else:
        prefix = ''
    step = ('Finding the question, then stem and parts'
            if find_parent else 'Detecting stem and lettered parts')
    yield {'type': 'info', 'message': f'{prefix}{step}…',
           'current': 1, 'total': 1}
    try:
        boxes, raw = split_question_png(
            config, stitch_path, image_max_dim, method=method,
            find_parent=find_parent)
    except Exception as e:
        logger.exception('split auto-detect failed')
        yield {'type': 'error', 'message': str(e),
               'current': 1, 'total': 1}
        yield {'type': 'done',
               'message': f'Detection failed: {e}',
               'boxes': [], 'raw': '', 'current': 1, 'total': 1}
        return

    out = merge_part_boxes_by_label(boxes)
    stems = sum(1 for b in out if b.get('label') == 'stem')
    nparts = len(out) - stems
    found = []
    if stems:
        found.append('a stem')
    if nparts:
        found.append(f'{nparts} part{"s" if nparts != 1 else ""}')
    yield {'type': 'success',
           'message': f'Found {", ".join(found) or "no boxes"}.',
           'current': 1, 'total': 1}
    if not out:
        msg = 'No lettered parts found — draw a stem box and part boxes by hand.'
    else:
        msg = (f'Found {"a stem and " if stems else ""}'
               f'{nparts} part{"s" if nparts != 1 else ""}. '
               'Adjust if needed, then commit.')
    yield {'type': 'done', 'message': msg, 'boxes': out,
           'raw': raw or '', 'current': 1, 'total': 1}


def detect_boxes(token: str, version: str, config, image_max_dim: int,
                 method: str = 'llm', find_parent: bool = False):
    """Run PDF-import pass 2 on the staged stitch (multi-image QUE stacked first).

    Returns ``(boxes, raw)`` where each box is
    ``{label, box:[x1,y1,x2,y2]}`` in fractional 0..1 coords of the stitch
    (``label`` is ``stem`` or a letter path).
    """
    boxes, raw = [], ''
    for ev in iter_detect_boxes(
            token, version, config, image_max_dim, method=method,
            find_parent=find_parent):
        if ev.get('type') == 'done':
            boxes, raw = ev.get('boxes') or [], ev.get('raw') or ''
    return boxes, raw


def _copy_tags(src: Question, dst: Question) -> None:
    dst.major_topic_id = src.major_topic_id
    dst.major_subtopic_id = src.major_subtopic_id
    dst.chapter_id = src.chapter_id
    dst.subchapter_id = src.subchapter_id
    dst.level = src.level
    dst.q_type = src.q_type
    dst.section = src.section
    dst.minor_topics = list(src.minor_topics)
    dst.subtopics = list(src.subtopics)


def _clear_leaf_tags(q: Question) -> None:
    q.major_topic_id = None
    q.major_subtopic_id = None
    q.chapter_id = None
    q.subchapter_id = None
    q.level = None
    q.q_type = None
    q.minor_topics.clear()
    q.subtopics.clear()


def _normalize_boxes(raw_boxes, parent_qid: str) -> list[dict]:
    parsed = parse_qid(parent_qid)
    if not parsed:
        raise ValueError('Parent QID is invalid')
    if parsed.qno.qno_end:
        raise ValueError('Cannot split a range stem with this tool; split a single question image.')
    out = []
    seen = set()
    has_stem = False
    for item in raw_boxes or []:
        label = str(item.get('label') or '').strip().lower()
        box = item.get('box')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError(f'Invalid box for label {label!r}')
        box = [float(x) for x in box]
        if label == 'stem':
            has_stem = True
            token = parsed.qno.token
        else:
            if not label.isalpha():
                raise ValueError(f'Invalid part label {label!r}')
            new_path = (parsed.qno.part_path or '') + label
            try:
                token = format_qno_token(parsed.qno.qno, None, new_path)
            except HierarchyError as e:
                raise ValueError(str(e)) from e
            if not parse_qno_token(token):
                raise ValueError(f'Invalid part label {label!r}')
        if label in seen:
            raise ValueError(f'Duplicate label {label!r}')
        seen.add(label)
        out.append({'label': label, 'token': token, 'box': box})
    if not has_stem:
        raise ValueError('Draw a box labelled "stem" for the shared background.')
    if len(out) < 2:
        raise ValueError('Draw the stem plus at least one part.')
    return out


def commit_split(question: Question, token: str, raw_boxes, *,
                 copy_tags: bool = False, versions: list | None = None) -> dict:
    meta = load_meta(token)
    if meta.get('question_id') != question.id:
        raise ValueError('Staging token does not match this question.')
    boxes = _normalize_boxes(raw_boxes, question.qid)
    parsed = parse_qid(question.qid)
    available = list((meta.get('versions') or {}).keys())
    if versions:
        use_versions = [v for v in versions if v in available]
    else:
        use_versions = available
    if not use_versions:
        raise ValueError('No staged versions to crop.')

    source_path = current_app.config['SOURCE_PATH']
    created = []
    part_rows = {}
    for item in boxes:
        if item['label'] == 'stem':
            part_rows['stem'] = question
            continue
        child, was_new = ensure_question(
            parsed.subject, parsed.source, item['token'],
            year=parsed.year, paper=parsed.paper, detail=parsed.detail,
        )
        part_rows[item['label']] = child
        if was_new:
            created.append(child.qid)
        if copy_tags:
            _copy_tags(question, child)
    db.session.flush()

    if not getattr(question, 'parent_id', None):
        from app.batch_image_gen import slot_has_img
        for version in use_versions:
            if slot_has_img(question.id, 'WHOLE', version):
                continue
            png_path = staged_image_path(token, version)
            if not png_path or not os.path.isfile(png_path):
                continue
            im = Image.open(png_path)
            im.load()
            if im.mode != 'RGB':
                im = im.convert('RGB')
            replace_img_assets(
                question, 'WHOLE', version, [im], stitch=False,
                source_path=source_path,
            )

    cropped = 0
    for version in use_versions:
        png_path = staged_image_path(token, version)
        if not png_path or not os.path.isfile(png_path):
            continue
        for item in boxes:
            img = crop_page(png_path, item['box'])
            target = part_rows[item['label']]
            replace_img_assets(
                target, 'QUE', version, [img], stitch=False,
                source_path=source_path,
            )
            cropped += 1

    _clear_leaf_tags(question)
    db.session.commit()
    discard(token)
    return {
        'created': created,
        'cropped': cropped,
        'parts': [item['label'] for item in boxes],
    }

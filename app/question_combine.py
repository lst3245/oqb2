"""Combine a stem's parts back into one question.

Two paths:

- **Restore** (root with a WHOLE archive): copy WHOLE → QUE, reconstruct
  ANS/SOL from the subtree, then delete descendants.
- **Reconstruct** (nested stem, or root with no archive): fold each node's
  IMG QUE/ANS/SOL onto the survivor in tree order (optionally stitched),
  optionally snapshot that QUE as WHOLE on a root, then delete descendants.

Range stems are refused. WHOLE is never written onto a nested stem.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from flask import current_app
from PIL import Image

from app import db
from app.batch_image_gen import replace_img_assets
from app.hierarchy import (
    ancestors, descendants, is_stem, sort_key, subtree_deepest_first,
)
from app.models import Question, QuestionAsset
from app.utils import VERSIONS

logger = logging.getLogger(__name__)

WORKING_TYPES = ('QUE', 'ANS', 'SOL')


class CombineError(ValueError):
    """User-facing combine failure (400/409)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def is_root_question(q) -> bool:
    return not getattr(q, 'parent_id', None) and getattr(q, 'parent', None) is None


def collapse_maximal_stems(questions: Iterable) -> list:
    """If both a stem and a descendant are selected, keep only the ancestor."""
    selected = list(questions)
    ids = {getattr(q, 'id', id(q)) for q in selected}
    out = []
    for q in selected:
        skip = False
        for anc in ancestors(q):
            if getattr(anc, 'id', id(anc)) in ids:
                skip = True
                break
        if not skip:
            out.append(q)
    return out


def subtree_preorder(stem) -> list:
    """``stem`` then descendants in ``sort_key`` order."""
    kids = sorted(descendants(stem), key=sort_key)
    return [stem] + kids


def choose_mode(stem, *, has_whole: bool) -> str:
    """``'restore'`` or ``'reconstruct'``. Raises CombineError if refused."""
    if getattr(stem, 'qno_end', None):
        raise CombineError(
            'Cannot combine a range stem (for example Q23-24). '
            'Delete or detach the MC items individually.',
            status=409)
    if not is_stem(stem):
        raise CombineError('This question has no parts to combine.')
    if is_root_question(stem) and has_whole:
        return 'restore'
    return 'reconstruct'


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


def _first_tagged_leaf(stem: Question):
    for n in sorted(descendants(stem), key=sort_key):
        if is_stem(n):
            continue
        if n.major_topic_id or n.q_type or n.level:
            return n
    for n in sorted(descendants(stem), key=sort_key):
        if not is_stem(n):
            return n
    return None


def _has_whole(question: Question) -> bool:
    return QuestionAsset.query.filter_by(
        question_id=question.id, asset_type='WHOLE', file_format='IMG',
    ).first() is not None


def _abs_source(rel: str, source_path: str) -> str | None:
    from app import storage
    parts = [p for p in str(rel).replace('\\', '/').split('/') if p]
    return storage.safe_join(source_path, *parts)


def _load_imgs(question: Question, asset_type: str, version: str,
               source_path: str) -> list:
    assets = (
        QuestionAsset.query.filter_by(
            question_id=question.id, asset_type=asset_type,
            version=version, file_format='IMG',
        )
        .order_by(QuestionAsset.part_number)
        .all()
    )
    images = []
    for a in assets:
        path = _abs_source(a.file_path, source_path)
        if not path or not os.path.isfile(path):
            continue
        im = Image.open(path)
        im.load()
        if im.mode not in ('RGB', 'RGBA'):
            im = im.convert('RGB')
        images.append(im.copy())
    return images


def _collect_subtree_imgs(stem: Question, asset_type: str, version: str,
                          source_path: str) -> list:
    pages = []
    for node in subtree_preorder(stem):
        pages.extend(_load_imgs(node, asset_type, version, source_path))
    return pages


def _md_doc_on_descendants(stem: Question) -> list[dict]:
    out = []
    for n in descendants(stem):
        for a in n.assets:
            if a.file_format in ('MD', 'DOC') and a.asset_type in WORKING_TYPES:
                out.append({
                    'qid': n.qid,
                    'asset_type': a.asset_type,
                    'version': a.version,
                    'format': a.file_format,
                })
    return out


def _img_counts(question: Question, asset_type: str) -> dict:
    counts = {}
    rows = QuestionAsset.query.filter_by(
        question_id=question.id, asset_type=asset_type, file_format='IMG',
    ).all()
    for a in rows:
        counts[a.version] = counts.get(a.version, 0) + 1
    return counts


def preview_combine(question: Question) -> dict:
    """Dry payload for the confirm UI. No writes."""
    has_whole = _has_whole(question)
    try:
        mode = choose_mode(question, has_whole=has_whole)
    except CombineError as e:
        return {
            'ok': False,
            'error': str(e),
            'status': e.status,
            'qid': question.qid,
            'id': question.id,
        }
    desc = sorted(descendants(question), key=sort_key)
    tag_src = _first_tagged_leaf(question)
    whole_counts = _img_counts(question, 'WHOLE') if has_whole else {}
    recon_counts = {}
    for atype in WORKING_TYPES:
        per_ver = {}
        for node in subtree_preorder(question):
            for ver, n in _img_counts(node, atype).items():
                per_ver[ver] = per_ver.get(ver, 0) + n
        if per_ver:
            recon_counts[atype] = per_ver
    return {
        'ok': True,
        'id': question.id,
        'qid': question.qid,
        'mode': mode,
        'is_root': is_root_question(question),
        'has_whole': has_whole,
        'whole_pages': whole_counts,
        'reconstruct_pages': recon_counts,
        'descendants': [{'id': n.id, 'qid': n.qid, 'part': n.part} for n in desc],
        'descendant_count': len(desc),
        'tag_source_qid': tag_src.qid if tag_src is not None else None,
        'md_doc_lost': _md_doc_on_descendants(question),
        'can_save_whole': mode == 'reconstruct' and is_root_question(question) and not has_whole,
    }


def _apply_tags_and_clear_flag(stem: Question) -> None:
    src = _first_tagged_leaf(stem)
    if src is not None:
        if not stem.major_topic_id:
            _copy_tags(src, stem)
        if not (stem.answer or '').strip() and (src.answer or '').strip():
            stem.answer = src.answer
        if not (stem.comment or '').strip() and (src.comment or '').strip():
            stem.comment = src.comment
    stem.needs_prev_parts = False


def _delete_descendants(stem: Question, source_path: str) -> int:
    """Delete descendant rows and their files. Survivor is kept."""
    from app import md_render
    nodes = subtree_deepest_first(list(descendants(stem)))
    files_deleted = 0
    doc_ids, md_ids = [], []
    for n in nodes:
        for asset in list(n.assets):
            if asset.file_format == 'DOC':
                doc_ids.append(asset.id)
            elif asset.file_format == 'MD':
                md_ids.append(asset.id)
            path = _abs_source(asset.file_path, source_path)
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                    files_deleted += 1
                except OSError:
                    logger.warning('Could not remove %s', path)
        db.session.delete(n)
    db.session.commit()
    if doc_ids:
        try:
            from app import doc_thumbnails
            for aid in doc_ids:
                doc_thumbnails.on_doc_asset_deleted(aid)
        except Exception:
            pass
    for aid in md_ids:
        try:
            md_render.invalidate(aid)
        except Exception:
            pass
    return files_deleted


def combine_parts(question: Question, *, stitch: bool = False,
                  save_whole: bool = False) -> dict:
    """Restore or reconstruct, then delete descendants. Commits."""
    source_path = current_app.config['SOURCE_PATH']
    has_whole = _has_whole(question)
    mode = choose_mode(question, has_whole=has_whole)
    desc_nodes = sorted(descendants(question), key=sort_key)
    descendant_qids = [n.qid for n in desc_nodes]
    descendant_ids = [n.id for n in desc_nodes]
    if not descendant_qids:
        raise CombineError('This question has no parts to combine.')

    wrote = {}
    if mode == 'restore':
        for version in VERSIONS:
            pages = _load_imgs(question, 'WHOLE', version, source_path)
            if not pages:
                continue
            res = replace_img_assets(
                question, 'QUE', version, pages, stitch=False,
                source_path=source_path)
            wrote.setdefault('QUE', {})[version] = res['wrote']
        for atype in ('ANS', 'SOL'):
            for version in VERSIONS:
                pages = _collect_subtree_imgs(
                    question, atype, version, source_path)
                if not pages:
                    continue
                res = replace_img_assets(
                    question, atype, version, pages, stitch=stitch,
                    source_path=source_path)
                wrote.setdefault(atype, {})[version] = res['wrote']
    else:
        for atype in WORKING_TYPES:
            for version in VERSIONS:
                pages = _collect_subtree_imgs(
                    question, atype, version, source_path)
                if not pages:
                    continue
                res = replace_img_assets(
                    question, atype, version, pages, stitch=stitch,
                    source_path=source_path)
                wrote.setdefault(atype, {})[version] = res['wrote']
                if (save_whole and atype == 'QUE'
                        and is_root_question(question)):
                    # Snapshot the unstitched tree-order pages. `pages` is
                    # still the pre-stitch list even when QUE was stitched.
                    replace_img_assets(
                        question, 'WHOLE', version, pages,
                        stitch=False, source_path=source_path)

    _apply_tags_and_clear_flag(question)
    db.session.commit()
    files_deleted = _delete_descendants(question, source_path)
    return {
        'ok': True,
        'mode': mode,
        'qid': question.qid,
        'id': question.id,
        'deleted_qids': descendant_qids,
        'deleted_ids': descendant_ids,
        'files_deleted': files_deleted,
        'wrote': wrote,
        'stitched': bool(stitch),
    }


def combine_many(questions: list[Question], *, stitch: bool = False,
                 save_whole: bool = False) -> list[dict]:
    """Collapse overlapping stems, then combine each. Per-row errors."""
    results = []
    for q in collapse_maximal_stems(questions):
        try:
            results.append(combine_parts(q, stitch=stitch, save_whole=save_whole))
        except CombineError as e:
            results.append({
                'ok': False, 'qid': q.qid, 'id': q.id,
                'error': str(e), 'status': e.status,
            })
        except Exception as e:
            logger.exception('combine failed for %s', q.qid)
            db.session.rollback()
            results.append({
                'ok': False, 'qid': q.qid, 'id': q.id,
                'error': str(e), 'status': 500,
            })
    return results

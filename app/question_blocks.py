"""Shared blocks (ADR-015): move a page of a question's QUE into a new block.

Repairs questions imported before blocks existed, where text printed between
two parts (ICT 2025 P1B Q7: the spreadsheet passage before (c)) was merged
into the root's QUE as page 2. Files are rewritten through
``replace_img_assets`` so ``question_assets`` and disk stay in sync.
"""
from __future__ import annotations

from flask import current_app
from PIL import Image

from app import db, storage
from app.batch_image_gen import replace_img_assets
from app.hierarchy import (
    BLOCK_MARK, HierarchyError, block_end_error, ensure_question,
    format_qno_token, is_block, normalize_block_end, parse_qid, part_segments,
    pick_block, set_block_end,
)
from app.models import Question, QuestionAsset
from app.utils import VERSIONS


class BlockError(ValueError):
    pass


def _que_rows(question, version):
    return (QuestionAsset.query
            .filter_by(question_id=question.id, asset_type='QUE',
                       version=version, file_format='IMG')
            .order_by(QuestionAsset.part_number)
            .all())


def _load(rel_path: str) -> Image.Image:
    path = storage.safe_join(storage.source_path(), *rel_path.replace('\\', '/').split('/'))
    if not path:
        raise BlockError(f'Asset path escapes SOURCE_PATH: {rel_path}')
    # copy() so the file handle is closed before replace_img_assets deletes it.
    with Image.open(path) as im:
        im.load()
        return im.copy()


def _page_list(page, pages) -> list[int]:
    raw = pages if pages else [page]
    try:
        out = sorted({int(p) for p in raw})
    except (TypeError, ValueError):
        raise BlockError('Pages must be numbers.')
    if not out or out[0] < 1:
        raise BlockError('Pick the QUE page(s) to move.')
    return out


def new_block_members(kids, anchor: str, end: str | None = None) -> list:
    """Pure: the parts at one level (``kids`` = the question's children, with
    their blocks' children) that a new block ``~anchor`` ending at ``end``
    would own, in print order. Raises :class:`BlockError` on a bad end."""
    blocks = [c for c in kids if is_block(c)]
    level = [c for c in kids if not is_block(c)]
    for b in blocks:
        level += list(b.children)
    new_seg = BLOCK_MARK + anchor
    rivals = [b.part for b in blocks] + [new_seg]
    err = block_end_error(new_seg, end, rivals)
    if err:
        raise BlockError(err)
    ends = {b.part: getattr(b, 'block_end', None) for b in blocks}
    ends[new_seg] = end
    return sorted((c for c in level if c.part and pick_block(rivals, c.part, ends) == new_seg),
                  key=lambda c: c.part_sort or 0)


def plan_move_page(question: Question, page: int | None, anchor: str,
                   pages: list | None = None, end: str | None = None) -> dict:
    """Dry run: what moving QUE page ``page`` (or every page in ``pages``, a
    block that spans several crops) into block ``~anchor`` does. ``end``
    (optional) is the last part the block owns (``hierarchy.set_block_end``).
    Raises :class:`BlockError` when it cannot be done."""
    page_list = _page_list(page, pages)
    end = normalize_block_end(end)
    parsed = parse_qid(question.qid)
    if not parsed or parsed.qno.qno_end or is_block(question):
        raise BlockError('Only a question or a lettered part with parts can get a shared intro.')
    anchor = (anchor or '').strip().lower().strip('()~ ')
    if not anchor or part_segments(anchor) != [anchor]:
        raise BlockError('Pick the part the shared intro is printed before, e.g. c.')
    try:
        token = format_qno_token(parsed.qno.qno, None,
                                 (parsed.qno.part_path or '') + BLOCK_MARK + anchor)
    except HierarchyError as e:
        raise BlockError(str(e)) from e
    block_qid = f'{parsed.prefix}_{token}'
    if Question.query.filter_by(qid=block_qid).first():
        raise BlockError(f'{block_qid} already exists.')

    moving = new_block_members(list(question.children), anchor, end)
    if not moving:
        raise BlockError(f'{question.qid} has no part ({anchor}) or later to share this text.')

    versions, skipped, warnings = [], [], []
    for v in VERSIONS:
        rows = _que_rows(question, v)
        if not rows:
            continue
        if page_list[-1] > len(rows) or len(rows) <= len(page_list):
            skipped.append(v)
        else:
            versions.append(v)
    if not versions:
        label = ', '.join(map(str, page_list))
        raise BlockError(f'No version of {question.qid} has QUE page {label} '
                         'with another page left behind.')
    other = {a.file_format for a in QuestionAsset.query.filter_by(
        question_id=question.id, asset_type='QUE') if a.file_format != 'IMG'}
    if other:
        warnings.append(f'{"/".join(sorted(other))} QUE is not split; edit it by hand.')
    return {
        'qid': question.qid,
        'block_qid': block_qid,
        'block_token': token,
        'anchor': anchor,
        'end': end,
        'page': page_list[0],
        'pages': page_list,
        'moving': [c.qid for c in moving],
        'versions': versions,
        'skipped_versions': skipped,
        'warnings': warnings,
    }


def move_page_to_block(question: Question, page: int | None, anchor: str,
                       pages: list | None = None, end: str | None = None) -> dict:
    """Create the block, move QUE page ``page`` / ``pages`` (every IMG
    version that has them) from ``question`` to the block, and re-home the
    covered parts (only up to ``end`` when given)."""
    plan = plan_move_page(question, page, anchor, pages=pages, end=end)
    moving_idx = {p - 1 for p in plan['pages']}
    parsed = parse_qid(question.qid)
    loaded = {}
    for v in plan['versions']:
        loaded[v] = [_load(r.file_path) for r in _que_rows(question, v)]

    block, _created = ensure_question(
        parsed.subject, parsed.source, plan['block_token'],
        year=parsed.year, paper=parsed.paper, detail=parsed.detail,
    )
    if plan['end']:
        try:
            set_block_end(block, plan['end'])
        except HierarchyError as e:
            raise BlockError(str(e)) from e
    db.session.commit()
    source_path = current_app.config['SOURCE_PATH']
    for v, imgs in loaded.items():
        moved = [im for i, im in enumerate(imgs) if i in moving_idx]
        rest = [im for i, im in enumerate(imgs) if i not in moving_idx]
        replace_img_assets(block, 'QUE', v, moved, stitch=False, source_path=source_path)
        replace_img_assets(question, 'QUE', v, rest, stitch=False, source_path=source_path)
    plan['block_id'] = block.id
    plan['moved_parts'] = [c.qid for c in Question.query.filter_by(parent_id=block.id)]
    return plan

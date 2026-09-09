"""PDF Batch Import — AI agent layer.

Sits on top of the deterministic tools in :mod:`app.pdf_import` and turns
"upload a past paper" into a reviewed ``plan.json`` with the least human
effort. The pipeline is a fixed state machine; every LLM step returns strict
JSON, every mutation of the plan is done by code, and anything the agent is
not sure about ends up in an **attention list** instead of a silent guess::

    A0 prep     captioned page thumbnails ("PAGE n" strip)
    A1 outline  whole-paper structure: page kinds, question -> pages, part tree
    A2 locate   iter_detect with per-page expected labels; reconcile vs outline
    A3 segment  iter_split_detect with the outline's part tree as hints
    A4 verify   redraw each question's boxes, ask for a critique, apply the
                deterministic fixes, bounded repair rounds
    out         plan.json (roles derived, depends_prev flags), attention.json,
                outline.json, agent_log.jsonl

Nothing here touches the library or the DB: the human still clicks Commit.
Pure helpers (outline merge/check, reconcile, fix application) have no Flask
or LLM dependency so they are unit-tested in ``tests/test_pdf_agent.py``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from flask import current_app

from app import pdf_import
from app.hierarchy import (
    compose_part_label,
    label_is_ancestor,
    normalize_plan_label,
    parse_qno_token,
    part_segments,
)

logger = logging.getLogger(__name__)

# Page kinds the outline may return; only ``question`` / ``other`` pages are
# sent to pass-1 detection (saves calls on instructions / blank / formula
# sheets). A page that the outline lists a question on is always detected.
SKIP_PAGE_KINDS = frozenset({'blank', 'instructions', 'answer_sheet', 'formula'})

SEVERITIES = ('info', 'warn', 'error')

# Fraction of the smaller box's height two same-page siblings may overlap
# before the upper one is trimmed.
OVERLAP_TOLERANCE = 0.25
# Boxes shorter than this fraction of the page are almost certainly noise.
MIN_BOX_HEIGHT = 0.008


class BudgetExhausted(RuntimeError):
    """Raised when the per-run LLM call budget is used up."""


@dataclass
class Budget:
    max_calls: int
    used: int = 0

    def charge(self, n: int = 1) -> None:
        if self.used + n > self.max_calls:
            raise BudgetExhausted(
                f'LLM call budget of {self.max_calls} exhausted')
        self.used += n

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)


@dataclass
class Attention:
    items: list = field(default_factory=list)

    def add(self, kind, label, page, severity, reason, stage):
        if severity not in SEVERITIES:
            severity = 'warn'
        self.items.append({
            'kind': kind,
            'label': label,
            'page': page,            # 0-based page index or None
            'severity': severity,
            'reason': str(reason)[:400],
            'stage': stage,
        })

    def counts(self) -> dict:
        out = {s: 0 for s in SEVERITIES}
        for it in self.items:
            out[it['severity']] += 1
        return out


# --------------------------------------------------------------------------
# Pure helpers (no Flask, no LLM)
# --------------------------------------------------------------------------

def flatten_part_tree(parts, prefix: str = '') -> list:
    """``[{label:'a', parts:[{label:'i'}]}, {label:'b'}]`` → ``['a','ai','b']``."""
    out = []
    for p in parts or []:
        lab = (p.get('label') or '').strip().lower() if isinstance(p, dict) else ''
        if not lab:
            continue
        path = prefix + lab
        out.append(path)
        out.extend(flatten_part_tree(p.get('parts') if isinstance(p, dict) else None,
                                     path))
    return out


def expected_relative_labels(question: dict) -> list:
    """Relative pass-2 labels for an outline question: ``['stem', 'a', 'ai',
    'b']``; ``[]`` when the question has no lettered parts."""
    flat = flatten_part_tree((question or {}).get('parts'))
    return (['stem'] + flat) if flat else []


def outline_questions_on_page(outline: dict, page_no: int) -> list:
    """Labels of outline questions that list 1-based ``page_no``."""
    out = []
    for q in (outline or {}).get('questions') or []:
        if page_no in (q.get('pages') or []):
            out.append(q['label'])
    return out


def _merge_part_trees(a, b):
    """Union of two nested part lists, order preserved (a first)."""
    out = []
    index = {}
    for src in (a or [], b or []):
        for p in src:
            lab = p.get('label')
            if not lab:
                continue
            if lab in index:
                index[lab]['parts'] = _merge_part_trees(
                    index[lab].get('parts'), p.get('parts'))
            else:
                node = {'label': lab, 'parts': list(p.get('parts') or [])}
                index[lab] = node
                out.append(node)
    return out


def merge_outline(running: dict | None, batch: dict | None) -> dict:
    """Merge one outline batch into the running outline.

    Pages are replaced by page number; questions are matched by label with
    page lists unioned and part trees merged; paper metadata fills blanks.
    """
    running = running or {'pages': [], 'questions': [], 'paper': {}}
    if not batch:
        return running
    pages = {p['page']: p for p in running.get('pages') or []}
    for p in batch.get('pages') or []:
        pages[p['page']] = p
    questions = {q['label']: dict(q) for q in running.get('questions') or []}
    order = list(questions.keys())
    for q in batch.get('questions') or []:
        lab = q['label']
        if lab in questions:
            cur = questions[lab]
            cur['pages'] = sorted(set(cur.get('pages') or []) | set(q.get('pages') or []))
            cur['parts'] = _merge_part_trees(cur.get('parts'), q.get('parts'))
            if cur.get('marks') is None and q.get('marks') is not None:
                cur['marks'] = q['marks']
            deps = list(cur.get('depends_prev') or [])
            for d in q.get('depends_prev') or []:
                if d not in deps:
                    deps.append(d)
            cur['depends_prev'] = deps
        else:
            questions[lab] = dict(q)
            order.append(lab)
    paper = dict(running.get('paper') or {})
    for k, v in (batch.get('paper') or {}).items():
        if paper.get(k) is None and v is not None:
            paper[k] = v
    return {
        'pages': [pages[k] for k in sorted(pages)],
        'questions': [questions[k] for k in order],
        'paper': paper,
    }


def _question_sort_key(label):
    p = parse_qno_token(label)
    return (p.qno, p.qno_end or p.qno) if p else (10**9, 0)


def check_outline(outline: dict, total_pages: int) -> list:
    """Deterministic sanity checks. Returns a list of ``(severity, message,
    label_or_None)`` tuples; an empty list means the outline is coherent."""
    warnings = []
    qs = list((outline or {}).get('questions') or [])
    if not qs:
        warnings.append(('error', 'The outline found no questions.', None))
        return warnings
    page_kind = {p['page']: p['kind'] for p in outline.get('pages') or []}
    covered = set()
    seen_numbers = set()
    prev_first_page = 0
    for q in sorted(qs, key=lambda q: _question_sort_key(q['label'])):
        lab = q['label']
        p = parse_qno_token(lab)
        pages = q.get('pages') or []
        if not pages:
            warnings.append(('warn', f'Q{lab} has no page list.', lab))
        for pg in pages:
            if pg < 1 or pg > total_pages:
                warnings.append(('warn', f'Q{lab} lists page {pg}, outside 1-{total_pages}.', lab))
            elif page_kind.get(pg) in SKIP_PAGE_KINDS:
                warnings.append(('warn', f'Q{lab} is placed on page {pg}, which the outline marks as {page_kind[pg]}.', lab))
        if pages:
            first = min(pages)
            if first < prev_first_page:
                warnings.append(('warn', f'Q{lab} starts on page {first}, before the previous question.', lab))
            prev_first_page = max(prev_first_page, first)
            if len(pages) > 1 and pages != list(range(min(pages), max(pages) + 1)):
                warnings.append(('warn', f'Q{lab} pages {pages} are not consecutive.', lab))
        if p:
            lo, hi = p.qno, p.qno_end or p.qno
            for n in range(lo, hi + 1):
                if n in seen_numbers and not p.qno_end:
                    warnings.append(('warn', f'Question number {n} appears more than once.', lab))
                seen_numbers.add(n)
            covered.update(pages)
    if seen_numbers:
        lo, hi = min(seen_numbers), max(seen_numbers)
        missing = [n for n in range(lo, hi + 1) if n not in seen_numbers]
        if missing:
            warnings.append(('warn', 'Question numbers missing from the outline: '
                             + ', '.join(str(n) for n in missing) + '.', None))
    for pg, kind in page_kind.items():
        if kind == 'question' and pg not in covered:
            warnings.append(('info', f'Page {pg} is marked as a question page but no question is listed on it.', None))
    return warnings


def reconcile_pages(outline: dict, items, total_pages: int) -> dict:
    """Compare pass-1 results with the outline, per page.

    ``items`` are plan items of one side (0-based ``page``). Returns
    ``{'missing': [(label, page_index)], 'extra': [(label, page_index)]}``.
    A label is only *missing* on a page when it appears nowhere on that side
    (continuation pages legitimately carry the same label as the head).
    """
    found_by_page = {}
    found_anywhere = set()
    for it in items or []:
        lab = pdf_import.plan_item_label(it)
        if not lab:
            continue
        found_by_page.setdefault(int(it.get('page', 0)), set()).add(lab)
        found_anywhere.add(lab)
    expected_any = set()
    missing = []
    for q in (outline or {}).get('questions') or []:
        lab = q['label']
        expected_any.add(lab)
        pages = [pg for pg in (q.get('pages') or []) if 1 <= pg <= total_pages]
        if not pages:
            continue
        if lab not in found_anywhere:
            missing.append((lab, min(pages) - 1))
    extra = []
    for pg, labs in found_by_page.items():
        for lab in sorted(labs, key=_question_sort_key):
            # a continuation with a label is fine; an unexpected number is not
            if lab not in expected_any:
                extra.append((lab, pg))
    return {'missing': missing, 'extra': extra}


def assign_unlabelled_from_outline(items, outline: dict) -> int:
    """Give label-less continuation boxes the outline's question for that
    page when exactly one question spans the page boundary. Returns the
    number of items fixed."""
    fixed = 0
    for it in items or []:
        if pdf_import.plan_item_label(it):
            continue
        page_no = int(it.get('page', 0)) + 1
        cands = [q for q in (outline or {}).get('questions') or []
                 if page_no in (q.get('pages') or []) and (page_no - 1) in (q.get('pages') or [])]
        if len(cands) == 1:
            it['label'] = cands[0]['label']
            p = parse_qno_token(it['label'])
            it['qno'] = p.qno if p else None
            fixed += 1
    return fixed


def relative_label(item, parent_label: str):
    """``stem`` / ``a`` / ``dii`` for one plan item relative to
    ``parent_label``; ``None`` when the item does not belong to it."""
    parent = parse_qno_token(parent_label)
    lab = pdf_import.plan_item_label(item)
    if not parent or not lab:
        return None
    if lab == parent_label:
        return 'stem'
    if label_is_ancestor(parent_label, lab):
        c = parse_qno_token(lab)
        pp = parent.part_path or ''
        return (c.part_path or '')[len(pp):] or 'stem'
    return None


def relative_labels_present(items, parent_label: str) -> list:
    """Relative labels (``stem``, ``a``, ``dii``) of the plan items that make
    up question ``parent_label`` (itself + descendants), de-duplicated."""
    out = []
    for it in items or []:
        rel = relative_label(it, parent_label)
        if rel and rel not in out:
            out.append(rel)
    return out


def _rel_is_ancestor(parent: str, child: str) -> bool:
    """Relative-label ancestor: ``d`` owns ``di`` / ``dii``; ``i`` does not
    own ``ii`` (roman tokens are atomic)."""
    ps = part_segments(parent)
    cs = part_segments(child)
    return bool(ps) and len(cs) > len(ps) and cs[:len(ps)] == ps


def compare_parts(expected_rel, actual_rel) -> tuple:
    """``(missing, extra)`` relative labels. ``stem`` is never *missing* (a
    question may legitimately have no shared text) and never *extra*.

    A grouping letter that exists only as a parent in the outline (Q5 (a)
    going straight to (i)(ii)(iii) with no intro text) is not missing when
    at least one of its children was detected.
    """
    exp = [x for x in (expected_rel or []) if x != 'stem']
    act = [x for x in (actual_rel or []) if x != 'stem']
    missing = []
    for x in exp:
        if x in act:
            continue
        if any(_rel_is_ancestor(x, a) for a in act):
            continue
        missing.append(x)
    extra = [x for x in act if x not in exp]
    return missing, extra



def fix_overlaps(items, pool=None) -> int:
    """Trim same-page overlapping siblings (upper box's bottom to the lower
    box's top) and drop degenerate boxes. Mutates ``items`` (and removes
    dropped boxes from ``pool`` — the full side list — when given); returns
    the number of boxes changed."""
    changed = 0
    by_page = {}
    for it in items or []:
        by_page.setdefault(int(it.get('page', 0)), []).append(it)
    for page_items in by_page.values():
        page_items.sort(key=lambda it: it['box'][1])
        for i in range(len(page_items) - 1):
            up, low = page_items[i], page_items[i + 1]
            u_top, u_bot = up['box'][1], up['box'][3]
            l_top, l_bot = low['box'][1], low['box'][3]
            # nested stem legitimately contains nothing: a stem box must not
            # cover its own parts, so overlap rules apply to every pair.
            overlap = min(u_bot, l_bot) - max(u_top, l_top)
            if overlap <= 0:
                continue
            smaller = max(1e-6, min(u_bot - u_top, l_bot - l_top))
            if overlap / smaller > OVERLAP_TOLERANCE:
                new_bot = max(u_top + MIN_BOX_HEIGHT, l_top)
                if abs(new_bot - u_bot) > 1e-6:
                    up['box'] = [up['box'][0], u_top, up['box'][2], new_bot]
                    changed += 1
    for it in list(items or []):
        if it['box'][3] - it['box'][1] < MIN_BOX_HEIGHT:
            items.remove(it)
            if pool is not None and it in pool:
                pool.remove(it)
            changed += 1
    return changed


def apply_verify_fixes(items, parent_label: str, verify: dict, legend,
                       ignore_missing=None) -> dict:
    """Apply the deterministic fixes from a verify reply to one question's
    plan items (same side). ``legend`` is ``[(number, item)]`` as rendered.

    ``ignore_missing`` is a set of relative labels that live on *other*
    pages of this question — a per-page visual check must not treat them
    as missing or trigger a whole-question redetect.

    Returns ``{'changed': int, 'redetect': [notes], 'unresolved': [issues],
    'depends': [labels]}``. Never invents boxes: ``missing`` and ``redetect``
    become a redetect note; ``relabel`` / ``drop`` / ``extend_*`` are applied
    in place.
    """
    by_num = {int(n): it for n, it in legend}
    changed = 0
    redetect = []
    unresolved = []
    for issue in (verify or {}).get('issues') or []:
        fix = issue.get('fix') or 'none'
        target = by_num.get(issue.get('box')) if issue.get('box') is not None else None
        if target is None and issue.get('label'):
            want = compose_part_label(parent_label, issue['label'])
            target = next((it for it in items
                           if pdf_import.plan_item_label(it) == want), None)
        note = f'{issue.get("problem")} on {issue.get("label") or "?"}: {issue.get("note") or ""}'.strip()
        if fix == 'relabel' and target is not None and issue.get('new_label'):
            new = compose_part_label(parent_label, issue['new_label'])
            if new and new != pdf_import.plan_item_label(target):
                target['label'] = new
                p = parse_qno_token(new)
                target['qno'] = p.qno if p else target.get('qno')
                changed += 1
            continue
        if fix == 'drop' and target is not None:
            if target in items:
                items.remove(target)
                changed += 1
            continue
        if fix in ('extend_top', 'extend_bottom') and target is not None:
            same_page = sorted((it for it in items
                                if int(it.get('page', 0)) == int(target.get('page', 0))),
                               key=lambda it: it['box'][1])
            idx = same_page.index(target) if target in same_page else -1
            x1, y1, x2, y2 = target['box']
            if fix == 'extend_top':
                new_y1 = same_page[idx - 1]['box'][3] if idx > 0 else max(0.0, y1 - 0.02)
                if new_y1 < y1:
                    target['box'] = [x1, new_y1, x2, y2]
                    changed += 1
            else:
                new_y2 = (same_page[idx + 1]['box'][1]
                          if 0 <= idx < len(same_page) - 1 else min(1.0, y2 + 0.02))
                if new_y2 > y2:
                    target['box'] = [x1, y1, x2, new_y2]
                    changed += 1
            continue
        if fix == 'redetect' or (issue.get('problem') == 'missing'):
            rel = str(issue.get('label') or '').strip().lower()
            if ignore_missing and rel in ignore_missing:
                continue
            redetect.append(note)
            continue
        unresolved.append(issue)
    depends = list((verify or {}).get('depends_prev') or [])
    return {'changed': changed, 'redetect': redetect,
            'unresolved': unresolved, 'depends': depends}


def mark_depends_prev(items, parent_label: str, rel_labels) -> int:
    """Set ``depends_prev`` on the items whose label is ``parent + rel``.
    A part with no earlier sibling cannot depend on one, so ``a`` / ``i``
    never get the flag. Returns the number of items flagged."""
    n = 0
    for rel in rel_labels or []:
        segs = part_segments(rel)
        if not segs or segs[-1] in ('a', 'i'):
            continue
        want = compose_part_label(parent_label, rel)
        if not want:
            continue
        for it in items or []:
            if pdf_import.plan_item_label(it) == want and not it.get('depends_prev'):
                it['depends_prev'] = True
                n += 1
    return n


def question_items(items, parent_label: str) -> list:
    """Plan items for ``parent_label`` and its descendants, reading order."""
    out = [it for it in items or []
           if pdf_import.plan_item_label(it) == parent_label
           or label_is_ancestor(parent_label, pdf_import.plan_item_label(it) or '')]
    return sorted(out, key=lambda it: (int(it.get('page', 0)), it['box'][1]))


def _pages_in(items):
    seen = []
    for it in items:
        pg = int(it.get('page', 0))
        if pg not in seen:
            seen.append(pg)
    return seen


def labels_on_other_pages(items, parent_label: str, page: int) -> list:
    """Relative labels of this question that appear on a page other than
    ``page`` (0-based). Used so a per-page visual check does not report
    those parts as missing."""
    out = []
    for it in items or []:
        if int(it.get('page', 0)) == int(page):
            continue
        rel = relative_label(it, parent_label)
        if rel and rel not in out:
            out.append(rel)
    return out


# --------------------------------------------------------------------------
# Rendering helpers (PIL)
# --------------------------------------------------------------------------

_PALETTE = [
    (220, 53, 69), (13, 110, 253), (25, 135, 84), (253, 126, 20),
    (111, 66, 193), (32, 201, 151), (214, 51, 132), (108, 117, 125),
]


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def captioned_thumbnail(png_path: str, page_no: int, max_dim: int):
    """Downscaled page image with a white "PAGE n" caption strip on top.
    Returns ``(b64, mime)`` for :func:`llm_client.chat`."""
    from PIL import Image, ImageDraw
    from app import llm_client
    with Image.open(png_path) as im:
        im.load()
        im = im.convert('RGB')
        w, h = im.size
        scale = min(1.0, max_dim / float(max(w, h))) if max_dim else 1.0
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            w, h = im.size
        strip = max(28, int(h * 0.045))
        canvas = Image.new('RGB', (w, h + strip), (255, 255, 255))
        canvas.paste(im, (0, strip))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, w, strip - 1], fill=(20, 20, 20))
        draw.text((10, max(2, strip // 6)), f'PAGE {page_no}',
                  fill=(255, 255, 0), font=_font(max(14, int(strip * 0.6))))
    return llm_client.prepare_image_from_pil(canvas, max_dim + strip)


def render_check_image(png_path: str, items, max_dim: int):
    """Crop the page to the union of ``items`` (plus margin) and draw each
    item's box with a legend number. Returns ``((b64, mime), legend)`` where
    ``legend`` is ``[(number, item)]`` in reading order."""
    from PIL import Image, ImageDraw
    from app import llm_client
    if not items:
        raise ValueError('nothing to render')
    x1 = min(it['box'][0] for it in items)
    y1 = min(it['box'][1] for it in items)
    x2 = max(it['box'][2] for it in items)
    y2 = max(it['box'][3] for it in items)
    pad = 0.012
    x1, y1 = max(0.0, x1 - pad), max(0.0, y1 - pad)
    x2, y2 = min(1.0, x2 + pad), min(1.0, y2 + pad)
    with Image.open(png_path) as im:
        im.load()
        im = im.convert('RGB')
        W, H = im.size
        left, top = int(x1 * W), int(y1 * H)
        right, bottom = max(left + 8, int(x2 * W)), max(top + 8, int(y2 * H))
        crop = im.crop((left, top, right, bottom))
        crop.load()
    cw, ch = crop.size
    draw = ImageDraw.Draw(crop)
    lw = max(2, cw // 400)
    font = _font(max(16, cw // 45))
    legend = []
    ordered = sorted(items, key=lambda it: it['box'][1])
    for n, it in enumerate(ordered, 1):
        bx1, by1, bx2, by2 = it['box']
        px1 = int((bx1 - x1) / (x2 - x1) * cw)
        py1 = int((by1 - y1) / (y2 - y1) * ch)
        px2 = int((bx2 - x1) / (x2 - x1) * cw)
        py2 = int((by2 - y1) / (y2 - y1) * ch)
        colour = _PALETTE[(n - 1) % len(_PALETTE)]
        draw.rectangle([px1, py1, px2, py2], outline=colour, width=lw)
        tag = str(n)
        tw = max(22, int(font.size * 0.7 * len(tag)) + 10)
        th = int(font.size * 1.3)
        draw.rectangle([px1, py1, px1 + tw, py1 + th], fill=colour)
        draw.text((px1 + 5, py1 + 1), tag, fill=(255, 255, 255), font=font)
        legend.append((n, it))
    return llm_client.prepare_image_from_pil(crop, max_dim), legend


# --------------------------------------------------------------------------
# Staging files
# --------------------------------------------------------------------------

def _outline_path(token):
    return os.path.join(pdf_import.token_dir(token), 'outline.json')


def _attention_path(token):
    return os.path.join(pdf_import.token_dir(token), 'attention.json')


def _log_path(token):
    return os.path.join(pdf_import.token_dir(token), 'agent_log.jsonl')


def load_outline(token: str):
    try:
        with open(_outline_path(token), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_attention(token: str) -> list:
    try:
        with open(_attention_path(token), 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


class _Log:
    def __init__(self, token, debug):
        self.path = _log_path(token)
        self.debug = debug
        try:
            os.remove(self.path)
        except OSError:
            pass

    def write(self, stage, **data):
        rec = {'t': round(time.time(), 3), 'stage': stage}
        rec.update(data)
        try:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
        except OSError:
            pass


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def _ev(type_, message, stage, current=None, total=None, **extra):
    ev = {'type': type_, 'message': message, 'stage': stage}
    if current is not None:
        ev['current'] = current
    if total is not None:
        ev['total'] = total
    ev.update(extra)
    return ev


def _pages_for(meta, kind):
    return [p['index'] for p in ((meta.get(kind) or {}).get('pages') or [])]


def iter_agent(app, cancel, token: str, config, image_max_dim: int,
               parallel: bool = False, max_workers: int = 1,
               debug: bool = False, frame_snap: bool = False):
    """SSE generator: run the whole agent pipeline on a staged session.

    Events are ``{type, message, stage, current?, total?}`` with ``type`` in
    ``info | success | skip | error | attention | done``; ``done`` carries
    ``plan``, ``attention``, ``outline`` (summary) and ``calls``.
    """
    from app import ai_prompts, llm_client

    cfg = app.config
    batch_pages = max(1, int(cfg.get('PDF_AGENT_OUTLINE_BATCH_PAGES', 6)))
    thumb_dim = max(300, int(cfg.get('PDF_AGENT_THUMB_MAX_DIM', 900)))
    max_rounds = max(0, int(cfg.get('PDF_AGENT_MAX_REPAIR_ROUNDS', 2)))
    budget = Budget(max_calls=max(10, int(cfg.get('PDF_AGENT_MAX_LLM_CALLS', 150))))
    attention = Attention()
    log = _Log(token, debug)

    meta = pdf_import.load_meta(token)
    if meta.get('mode') == 'generic':
        yield _ev('error', 'The AI agent only handles exam papers (not generic extraction).', 'prep')
        yield _ev('done', 'Aborted.', 'prep', 0, 0, plan=pdf_import.load_plan(token),
                  attention=[], calls=0)
        return
    kinds = [k for k in ('que', 'sol') if _pages_for(meta, k)]
    if not kinds:
        yield _ev('error', 'No pages staged.', 'prep')
        yield _ev('done', 'Aborted.', 'prep', 0, 0, plan={'que': [], 'sol': []},
                  attention=[], calls=0)
        return

    outlines = {}

    def _cancelled():
        return cancel.is_set()

    def _finish(message):
        plan = pdf_import.load_plan(token)
        for k in ('que', 'sol'):
            pdf_import.apply_derived_roles(plan.get(k) or [])
        pdf_import.save_plan(token, plan)
        _save_json(_attention_path(token), attention.items)
        log.write('done', calls=budget.used, attention=attention.counts())
        return _ev('done', message, 'done', 1, 1, plan=plan,
                   attention=attention.items, calls=budget.used,
                   outline={k: _outline_summary(outlines.get(k)) for k in kinds})

    # ---------------------------------------------------------------- A1
    try:
        for kind in kinds:
            atype = 'QUE' if kind == 'que' else 'SOL'
            page_idx = _pages_for(meta, kind)
            total_pages = len(page_idx)
            batches = [page_idx[i:i + batch_pages] for i in range(0, total_pages, batch_pages)]
            yield _ev('info', f'{atype}: reading the whole paper ({total_pages} page(s), '
                              f'{len(batches)} outline call(s))...', 'outline', 0, len(batches))
            running = None
            for bi, batch in enumerate(batches, 1):
                if _cancelled():
                    yield _finish('Agent cancelled.')
                    return
                images = [captioned_thumbnail(pdf_import.page_png_path(token, kind, idx),
                                              idx + 1, thumb_dim) for idx in batch]
                system = ai_prompts.build_pdf_agent_outline_system(atype, endpoint_id=config.id)
                user = ai_prompts.build_pdf_agent_outline_user_text(
                    atype, [idx + 1 for idx in batch], total_pages, running,
                    endpoint_id=config.id)
                parsed = None
                for attempt in (1, 2):
                    budget.charge()
                    try:
                        text, _info = llm_client.chat(config, system, user, images=images)
                    except Exception as e:  # transport failure → retry once
                        log.write('outline', kind=kind, batch=bi, attempt=attempt, error=str(e))
                        if attempt == 2:
                            attention.add(kind, None, batch[0], 'error',
                                          f'Outline call for pages {batch[0]+1}-{batch[-1]+1} failed: {e}',
                                          'outline')
                        continue
                    parsed = ai_prompts.parse_agent_outline(text)
                    log.write('outline', kind=kind, batch=bi, attempt=attempt,
                              parsed=parsed, raw=(text if debug else None))
                    if parsed:
                        break
                    if attempt == 2:
                        attention.add(kind, None, batch[0], 'error',
                                      f'Outline reply for pages {batch[0]+1}-{batch[-1]+1} was not valid JSON.',
                                      'outline')
                running = merge_outline(running, parsed)
                found = [q['label'] for q in (parsed or {}).get('questions') or []]
                yield _ev('success' if parsed else 'error',
                          f'{atype} pages {batch[0]+1}-{batch[-1]+1}: '
                          + (f'questions {", ".join(found)}' if found else 'no questions listed'),
                          'outline', bi, len(batches))
            running = running or {'pages': [], 'questions': [], 'paper': {}}
            outlines[kind] = running
            for sev, msg, lab in check_outline(running, total_pages):
                attention.add(kind, lab, None, sev, msg, 'outline')
                yield _ev('attention', f'{atype} outline: {msg}', 'outline')
            _save_json(_outline_path(token), outlines)
            yield _ev('info', f'{atype} outline: {len(running["questions"])} question(s); '
                              + _describe_parts(running), 'outline')
    except BudgetExhausted as e:
        attention.add(None, None, None, 'error', f'{e} during outline.', 'outline')
        yield _finish('Budget exhausted during outline.')
        return

    # ---------------------------------------------------------------- A2
    expected_by_page = {}
    page_filter = set()
    for kind in kinds:
        outline = outlines.get(kind) or {}
        page_kind = {p['page']: p['kind'] for p in outline.get('pages') or []}
        for idx in _pages_for(meta, kind):
            labs = outline_questions_on_page(outline, idx + 1)
            if labs:
                expected_by_page[(kind, idx)] = labs
                page_filter.add((kind, idx))
            elif page_kind.get(idx + 1, 'question') not in SKIP_PAGE_KINDS:
                page_filter.add((kind, idx))
            # else: outline says this page has no questions → skip the call
    if not page_filter:
        page_filter = {(k, idx) for k in kinds for idx in _pages_for(meta, k)}
    try:
        budget.charge(len(page_filter))
    except BudgetExhausted as e:
        attention.add(None, None, None, 'error', f'{e} before page detection.', 'locate')
        yield _finish('Budget exhausted before detection.')
        return
    skipped = sum(len(_pages_for(meta, k)) for k in kinds) - len(page_filter)
    yield _ev('info', f'Locating questions on {len(page_filter)} page(s)'
                      + (f' ({skipped} non-question page(s) skipped)' if skipped else '')
                      + '...', 'locate', 0, len(page_filter))
    for ev in pdf_import.iter_detect(app, cancel, token, config, image_max_dim,
                                     debug=debug, method='llm', parallel=parallel,
                                     max_workers=max_workers,
                                     expected_by_page=expected_by_page,
                                     page_filter=page_filter,
                                     frame_snap=frame_snap):
        if ev.get('type') == 'done':
            break
        ev.setdefault('stage', 'locate')
        ev.pop('page', None)
        yield ev
    if _cancelled():
        yield _finish('Agent cancelled.')
        return

    # reconcile
    plan = pdf_import.load_plan(token)
    for kind in kinds:
        atype = 'QUE' if kind == 'que' else 'SOL'
        outline = outlines.get(kind) or {}
        total_pages = len(_pages_for(meta, kind))
        n_fixed = assign_unlabelled_from_outline(plan.get(kind) or [], outline)
        if n_fixed:
            yield _ev('info', f'{atype}: labelled {n_fixed} continuation box(es) from the outline.', 'locate')
        rec = reconcile_pages(outline, plan.get(kind) or [], total_pages)
        for lab, pg in rec['missing']:
            if _cancelled():
                break
            try:
                budget.charge()
            except BudgetExhausted as e:
                attention.add(kind, lab, pg, 'error', f'Q{lab} not detected; {e}.', 'locate')
                continue
            try:
                boxes, raw = pdf_import.detect_single_page(
                    config, token, kind, pg, image_max_dim, 'llm',
                    expected_labels=expected_by_page.get((kind, pg)))
            except Exception as e:
                boxes, raw = [], ''
                log.write('locate-redo', kind=kind, page=pg, error=str(e))
            log.write('locate-redo', kind=kind, page=pg, label=lab,
                      boxes=[b.get('label') for b in boxes], raw=(raw if debug else None))
            hit = [b for b in boxes if normalize_plan_label(b.get('label') or b.get('qno')) == lab]
            if hit:
                b = hit[0]
                p = parse_qno_token(lab)
                plan[kind].append({'page': pg, 'qno': p.qno if p else None,
                                   'label': lab, 'box': b['box']})
                yield _ev('success', f'{atype} Q{lab}: recovered on page {pg+1} after a second look.', 'locate')
            else:
                attention.add(kind, lab, pg, 'error',
                              f'Q{lab} is in the outline (page {pg+1}) but no box was detected. Draw it by hand.',
                              'locate')
                yield _ev('attention', f'{atype} Q{lab}: not found on page {pg+1}.', 'locate')
        for lab, pg in rec['extra']:
            attention.add(kind, lab, pg, 'warn',
                          f'Q{lab} was detected on page {pg+1} but is not in the paper outline — check the number.',
                          'locate')
            yield _ev('attention', f'{atype} Q{lab} on page {pg+1} is not in the outline.', 'locate')
        pdf_import.apply_derived_roles(plan.get(kind) or [])
    pdf_import.save_plan(token, plan)

    # ---------------------------------------------------------------- A3
    # Expected part trees are keyed by label and shared by both sides: the
    # marking scheme mirrors the question paper, so the QUE outline wins and
    # the SOL outline only fills labels QUE does not know.
    expected_by_parent = {}
    for kind in ('que', 'sol'):
        for q in (outlines.get(kind) or {}).get('questions') or []:
            rel = expected_relative_labels(q)
            if rel and q['label'] not in expected_by_parent:
                expected_by_parent[q['label']] = rel
    parents = []
    for kind in kinds:
        present = {pdf_import.plan_item_label(it) for it in plan.get(kind) or []}
        for lab in sorted(expected_by_parent, key=_question_sort_key):
            if lab in present:
                parents.append((kind, lab))
    # questions the outline does not know about but that pass 1 found as
    # whole questions: still try a split (no hint) — cheap insurance.
    outlined = {q['label'] for k in kinds
                for q in (outlines.get(k) or {}).get('questions') or []}
    for kind in kinds:
        known = {lab for k, lab in parents if k == kind}
        for it in plan.get(kind) or []:
            lab = pdf_import.plan_item_label(it)
            p = parse_qno_token(lab) if lab else None
            if (p and not p.part_path and not p.qno_end
                    and lab not in known and lab not in outlined):
                parents.append((kind, lab))
                known.add(lab)
    labels_filter = sorted({lab for _k, lab in parents}, key=_question_sort_key)
    if labels_filter:
        try:
            budget.charge(len(parents))
        except BudgetExhausted as e:
            attention.add(None, None, None, 'error', f'{e} before part split.', 'segment')
            yield _finish('Budget exhausted before part split.')
            return
        yield _ev('info', f'Splitting {len(parents)} question(s) into parts...', 'segment',
                  0, len(parents))
        for ev in pdf_import.iter_split_detect(
                app, cancel, token, config, image_max_dim, kinds='both',
                labels_filter=labels_filter, debug=debug, parallel=parallel,
                max_workers=max_workers, expected_by_parent=expected_by_parent,
                frame_snap=frame_snap):
            if ev.get('type') == 'done':
                break
            ev.setdefault('stage', 'segment')
            yield ev
        if _cancelled():
            yield _finish('Agent cancelled.')
            return
    else:
        yield _ev('info', 'No multi-part questions to split.', 'segment')

    # ---------------------------------------------------------------- A4
    plan = pdf_import.load_plan(token)
    verify_targets = [(k, lab) for k, lab in parents if k in kinds]
    total_v = len(verify_targets)
    if max_rounds > 0 and total_v:
        yield _ev('info', f'Checking {total_v} question(s) visually (up to {max_rounds} round(s) each)...',
                  'verify', 0, total_v)
    for vi, (kind, parent) in enumerate(verify_targets, 1):
        if _cancelled():
            break
        atype = 'QUE' if kind == 'que' else 'SOL'
        items = question_items(plan.get(kind) or [], parent)
        expected_rel = expected_by_parent.get(parent) or []
        # deterministic checks first
        if fix_overlaps(items, pool=plan.get(kind)):
            yield _ev('info', f'{atype} Q{parent}: trimmed overlapping boxes.', 'verify', vi, total_v)
        missing, extra = compare_parts(expected_rel, relative_labels_present(items, parent))
        if extra:
            attention.add(kind, parent, _pages_in(items)[0] if items else None, 'warn',
                          f'Q{parent}: parts {", ".join(extra)} were detected but are not in the outline.',
                          'segment')
        if missing and expected_rel:
            attention.add(kind, parent, _pages_in(items)[0] if items else None, 'error',
                          f'Q{parent}: expected part(s) {", ".join(missing)} were not found.',
                          'segment')
        if max_rounds <= 0 or not items:
            continue
        resolved = False
        for rnd in range(1, max_rounds + 1):
            if _cancelled():
                break
            round_changed = False
            redetect_notes = []
            deps = []
            pages_here = _pages_in(items)
            for pg in pages_here:
                page_items = [it for it in items if int(it.get('page', 0)) == pg]
                elsewhere = labels_on_other_pages(items, parent, pg)
                page_expected = relative_labels_present(page_items, parent) or [
                    x for x in expected_rel if x not in elsewhere]
                try:
                    budget.charge()
                except BudgetExhausted as e:
                    attention.add(kind, parent, pg, 'warn', f'Q{parent} not verified: {e}.', 'verify')
                    resolved = True  # stop trying
                    break
                yield _ev('info',
                          f'{atype} Q{parent}: checking page {pg + 1} '
                          f'(round {rnd}, {len(page_items)} box(es))...',
                          'verify', vi, total_v)
                try:
                    image, legend = render_check_image(
                        pdf_import.page_png_path(token, kind, pg), page_items, image_max_dim)
                    legend_txt = [(n, relative_label(it, parent) or '?') for n, it in legend]
                    page_note = ''
                    if elsewhere:
                        page_note = (
                            f'This is one page of a multi-page question. '
                            f'Parts on other pages ({", ".join(elsewhere)}) '
                            f'are not missing — do not report them and do not '
                            f'ask for redetect because of them. ')
                    system = ai_prompts.build_pdf_agent_verify_system(atype, endpoint_id=config.id)
                    user = ai_prompts.build_pdf_agent_verify_user_text(
                        legend_txt, page_expected, endpoint_id=config.id,
                        page_note=page_note)
                    text, _info = llm_client.chat(config, system, user, images=[image])
                    verify = ai_prompts.parse_agent_verify(text)
                except Exception as e:
                    log.write('verify', kind=kind, parent=parent, page=pg, round=rnd, error=str(e))
                    attention.add(kind, parent, pg, 'warn', f'Q{parent}: visual check failed ({e}).', 'verify')
                    continue
                log.write('verify', kind=kind, parent=parent, page=pg, round=rnd,
                          result=verify, raw=(text if debug else None))
                if not verify:
                    attention.add(kind, parent, pg, 'warn', f'Q{parent}: visual check reply was not valid JSON.', 'verify')
                    continue
                deps.extend(verify.get('depends_prev') or [])
                if verify['ok']:
                    continue
                res = apply_verify_fixes(plan[kind], parent, verify, legend,
                                         ignore_missing=elsewhere)
                round_changed = round_changed or res['changed'] > 0
                redetect_notes.extend(res['redetect'])
                for iss in res['unresolved']:
                    attention.add(kind, compose_part_label(parent, iss.get('label') or 'stem') or parent,
                                  pg, 'warn', f'Q{parent} {iss.get("label") or ""}: {iss.get("problem")} — {iss.get("note")}',
                                  'verify')
            if deps:
                mark_depends_prev(plan[kind], parent, deps)
            if redetect_notes:
                try:
                    budget.charge(len(_pages_in(items)))
                except BudgetExhausted as e:
                    attention.add(kind, parent, None, 'warn', f'Q{parent}: {e}; parts left as detected.', 'verify')
                    break
                pdf_import.save_plan(token, plan)
                note = ('Previous attempt problems: ' + ' | '.join(redetect_notes[:4])
                        + '. Fix these; keep every expected part.')
                for ev in pdf_import.iter_split_detect(
                        app, cancel, token, config, image_max_dim, kinds=kind,
                        labels_filter=[parent], debug=debug, parallel=False,
                        max_workers=1, expected_by_parent=expected_by_parent,
                        note_by_parent={parent: note}, frame_snap=frame_snap):
                    if ev.get('type') == 'done':
                        break
                plan = pdf_import.load_plan(token)
                round_changed = True
                yield _ev('info', f'{atype} Q{parent}: re-split after check (round {rnd}).', 'verify', vi, total_v)
            items = question_items(plan.get(kind) or [], parent)
            if not round_changed:
                resolved = True
                yield _ev('success', f'{atype} Q{parent}: boxes verified.', 'verify', vi, total_v)
                break
            pdf_import.apply_derived_roles(plan.get(kind) or [])
            pdf_import.save_plan(token, plan)
        if not resolved:
            missing, _extra = compare_parts(expected_rel, relative_labels_present(items, parent))
            sev = 'error' if missing else 'warn'
            attention.add(kind, parent, _pages_in(items)[0] if items else None, sev,
                          f'Q{parent}: still had issues after {max_rounds} repair round(s) — please review its boxes.',
                          'verify')
            yield _ev('attention', f'{atype} Q{parent}: needs a human look.', 'verify', vi, total_v)
        pdf_import.save_plan(token, plan)
        _save_json(_attention_path(token), attention.items)
    # outline-declared dependencies (wording-based) also flag parts
    for kind in kinds:
        for q in (outlines.get(kind) or {}).get('questions') or []:
            if q.get('depends_prev'):
                mark_depends_prev(plan.get(kind) or [], q['label'], q['depends_prev'])
    pdf_import._stitch_continuations(plan)
    plan = pdf_import._strip_split_flags(plan)
    pdf_import.save_plan(token, plan)

    counts = attention.counts()
    yield _finish(
        f'Agent finished: {len(plan.get("que") or [])} QUE / {len(plan.get("sol") or [])} SOL '
        f'region(s); {counts["error"]} error(s), {counts["warn"]} warning(s) to review; '
        f'{budget.used} model call(s).')


def _describe_parts(outline) -> str:
    n_multi = sum(1 for q in outline.get('questions') or [] if q.get('parts'))
    n_parts = sum(len(flatten_part_tree(q.get('parts'))) for q in outline.get('questions') or [])
    return f'{n_multi} with lettered parts ({n_parts} part label(s) in total)'


def _outline_summary(outline):
    if not outline:
        return None
    return {
        'questions': [{'label': q['label'], 'pages': q.get('pages'),
                       'parts': flatten_part_tree(q.get('parts')),
                       'depends_prev': q.get('depends_prev') or []}
                      for q in outline.get('questions') or []],
        'pages': outline.get('pages') or [],
        'paper': outline.get('paper') or {},
    }


# --------------------------------------------------------------------------
# Background job + spectator SSE
# --------------------------------------------------------------------------
# Verify of a multi-page question can sit inside ``llm_client.chat`` for
# well over a minute with no yield. Browsers / proxies then drop the
# EventSource, and Flask raises GeneratorExit on the next yield — which
# used to abort the rest of the pipeline and leave no attention.json.
# The work now runs on a daemon thread keyed by staging token; the SSE
# route is a spectator that heartbeats every 15 s and can re-attach.

_AGENT_JOBS = {}
_AGENT_JOBS_LOCK = threading.Lock()
_HEARTBEAT_SEC = 15


def _push_job_event(job, ev):
    with job['cv']:
        job['events'].append(ev)
        job['cv'].notify_all()


def agent_job(token: str):
    """Return the in-memory job dict for ``token``, or None."""
    with _AGENT_JOBS_LOCK:
        return _AGENT_JOBS.get(token)


def start_agent_job(app, token: str, endpoint_id: int, image_max_dim: int,
                    parallel: bool = False, max_workers: int = 1,
                    debug: bool = False, restart: bool = False,
                    frame_snap: bool = False):
    """Start (or attach to) the agent for a staging token.

    Returns ``(job_id, attached)``. ``attached`` is True when a run was
    already in progress and ``restart`` was false. ``restart`` cancels the
    live run and starts a new one.
    """
    existing = None
    with _AGENT_JOBS_LOCK:
        existing = _AGENT_JOBS.get(token)
        if existing and not existing['finished'] and not restart:
            return existing['job_id'], True

    if existing and not existing['finished'] and restart:
        existing['cancel'].set()
        existing['thread'].join(timeout=5)

    job_id, cancel = pdf_import.new_job()
    job = {
        'job_id': job_id,
        'cancel': cancel,
        'events': [],
        'cv': threading.Condition(),
        'finished': False,
        'thread': None,
    }
    with _AGENT_JOBS_LOCK:
        _AGENT_JOBS[token] = job

    def _worker():
        with app.app_context():
            try:
                from app.models import LLMConfig
                live_cfg = LLMConfig.query.get(endpoint_id)
                if live_cfg is None or not live_cfg.enabled:
                    _push_job_event(job, _ev('error', 'LLM endpoint disappeared.', 'prep'))
                    _push_job_event(job, _ev('done', 'Aborted.', 'prep', 0, 0,
                                             plan=pdf_import.load_plan(token),
                                             attention=[], calls=0))
                    return
                live_cfg._batch = True
                for ev in iter_agent(
                        app, cancel, token, live_cfg, image_max_dim,
                        parallel=parallel, max_workers=max_workers,
                        debug=debug, frame_snap=frame_snap):
                    _push_job_event(job, ev)
                    if ev.get('type') == 'done':
                        break
            except Exception as e:
                logger.exception('PDF import agent worker aborted')
                _push_job_event(job, _ev('error', f'Aborted: {e}', 'verify'))
                _push_job_event(job, _ev('done', 'Aborted.', 'verify'))
            finally:
                with job['cv']:
                    job['finished'] = True
                    job['cv'].notify_all()
                pdf_import.finish_job(job_id)

    t = threading.Thread(target=_worker, daemon=True,
                         name=f'pdf-agent-{token[:8]}')
    job['thread'] = t
    t.start()
    return job_id, False


def iter_job_events(token: str):
    """Yield stored events, then live ones, then heartbeats while the
    worker is still running. Safe for a late-attaching SSE client."""
    job = agent_job(token)
    if job is None:
        yield _ev('error', 'No AI agent run is in progress for this session.', 'prep')
        yield _ev('done', 'Aborted.', 'prep')
        return
    idx = 0
    while True:
        with job['cv']:
            if idx < len(job['events']):
                batch = job['events'][idx:]
                idx = len(job['events'])
            elif job['finished']:
                return
            else:
                job['cv'].wait(timeout=_HEARTBEAT_SEC)
                if idx < len(job['events']):
                    continue
                if job['finished']:
                    return
                batch = [_ev('heartbeat', '', job.get('events')[-1].get('stage', 'verify')
                             if job['events'] else 'verify')]
        for ev in batch:
            yield ev
            if ev.get('type') == 'done':
                return

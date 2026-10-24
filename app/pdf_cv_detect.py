"""
Classical (non-LLM) question detection for PDF Batch Import.

``method='cv'`` never calls a vision model. It:

1. Optionally deskews the staged page (the caller writes the straightened PNG
   so crops stay aligned).
2. Uses the printed page frame when one exists.
3. Finds question-start anchors in the left number column (projection, then
   a per-line gap search that skips a blank left gutter). Optional local
   Tesseract reads those blobs; without it the numbers are guessed 1..N and
   confidence stays low.
4. Builds one box per anchor with :func:`app.pdf_layout.segment_page`, then
   snaps ink with :func:`app.pdf_layout.refine_box` without crossing the next
   anchor.
5. Applies subject heuristics (see :data:`SUBJECT_PROFILES`) and scores
   confidence. A low score is a signal for the UI to *offer* an LLM re-run;
   this module never calls one itself.

Geometry is fractional ``[x1, y1, x2, y2]``.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Page or item score below this (unless the caller overrides) is "low".
CV_LOW_CONFIDENCE = 0.55

# Subject id → profile. Unknown subjects use ``auto`` (every reliable
# per-box rule, no penalty for a layout the profile cannot prove).
# ``cq`` skips the "this does not look like four options" penalty — M1/M2
# have no multiple-choice section, so a four-line working must not be
# treated as a failed MCQ. SOL always uses ``sol``.
SUBJECT_PROFILES = {
    'MATC': 'auto',   # compulsory maths: Section A MC + Section B CQ
    'MATH': 'auto',
    'MAT1': 'cq',
    'MAT2': 'cq',
    'M1': 'cq',
    'M2': 'cq',
    'PHY': 'auto',
    'CHEM': 'auto',
    'BIO': 'auto',
    'ICT': 'cq',
    'ECON': 'auto',
    'BAFS': 'auto',
    'ENG': 'cq',
    'ENGL': 'cq',
    'CHIN': 'cq',
    'CLIT': 'cq',
}

_PROFILES = ('auto', 'mcq', 'cq', 'sol')


def resolve_profile(subject_id: str | None, atype: str = 'QUE',
                    paper: str | None = None) -> str:
    """Pick the post-rule profile for one page.

    ``paper`` is accepted so a future caller can special-case a paper code;
    it is not used yet (P2 is not MC for every subject). SOL is always
    ``sol``. Unknown subjects are ``auto``.
    """
    del paper  # reserved; see the docstring
    if (atype or 'QUE').strip().upper() == 'SOL':
        return 'sol'
    sid = (subject_id or '').strip().upper()
    if sid in SUBJECT_PROFILES:
        return SUBJECT_PROFILES[sid]
    return 'auto'


def _require_numpy():
    from app import pdf_layout
    if not pdf_layout.numpy_available():
        raise RuntimeError(
            'NumPy is required for classical CV detection and deskew. '
            'Install it (pip install "numpy>=1.26") and restart the server.')
    return pdf_layout.np


def _bool_runs(mask):
    """Inclusive ``(start, end)`` runs where a 1-D bool mask is True."""
    idx = mask.nonzero()[0]
    if idx.size == 0:
        return []
    runs = []
    s = p = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i != p + 1:
            runs.append((s, p))
            s = i
        p = i
    runs.append((s, p))
    return runs


def _merge_runs(runs, gap: int):
    if not runs:
        return []
    out = [list(runs[0])]
    for a, b in runs[1:]:
        if a - out[-1][1] <= gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def coerce_frame_box(frame):
    """Normalise a frame to ``[x1, y1, x2, y2]`` or ``None``.

    Accepts a consolidated meta entry ``{'box': [...]}``, a
    :func:`app.pdf_layout.detect_page_frame` dict, or a bare 4-list.
    """
    if frame is None or frame is False:
        return None
    if isinstance(frame, (list, tuple)) and len(frame) == 4:
        try:
            box = [float(v) for v in frame]
        except (TypeError, ValueError):
            return None
        return box if box[2] > box[0] + 0.05 and box[3] > box[1] + 0.05 else None
    if isinstance(frame, dict):
        if frame.get('box'):
            return coerce_frame_box(frame['box'])
        left, right = frame.get('left'), frame.get('right')
        if left is None or right is None:
            return None
        top = frame.get('top')
        bottom = frame.get('bottom')
        return coerce_frame_box([
            left,
            0.0 if top is None else top,
            right,
            1.0 if bottom is None else bottom,
        ])
    return None


def _window(h, w, frame_box, margin=0.02):
    if frame_box:
        x0 = int(round(max(0.0, frame_box[0]) * w))
        y0 = int(round(max(0.0, frame_box[1]) * h))
        x1 = int(round(min(1.0, frame_box[2]) * w))
        y1 = int(round(min(1.0, frame_box[3]) * h))
    else:
        x0, y0 = int(margin * w), int(margin * h)
        x1, y1 = int((1.0 - margin) * w), int((1.0 - margin) * h)
    x1 = min(w, max(x0 + 8, x1))
    y1 = min(h, max(y0 + 8, y1))
    return x0, y0, x1, y1


def _dot_gap_px(width: int) -> int:
    """Merge a digit and its following dot, not a word space."""
    return max(4, int(width * 0.006))


def _number_gap_px(width: int) -> int:
    """Indent between a question number and its stem.

    Larger than a word space, smaller than the blank gutter of a digital
    HKEAA page. ``width`` is the content window in pixels.
    """
    return max(8, int(width * 0.012))


def _merged_mask_runs(mask, merge_gap: int):
    """Inclusive runs of a 1-D bool mask, joining gaps of at most ``merge_gap``."""
    return _merge_runs(_bool_runs(mask), merge_gap)


def _first_content_column(col, thr: float = 0.006, min_run: int = 4) -> int:
    """Skip an empty left gutter. ``col`` is a 1-D ink fraction."""
    for a, b in _bool_runs(col > thr):
        if b - a + 1 >= min_run:
            return a
    idx = (col > 0.001).nonzero()[0]
    return int(idx[0]) if idx.size else 0


def _projection_number_column(col, width, x0, x1):
    """Leftmost narrow column with an empty vertical gap before the stem.

    Returns ``(nx0, nx1)`` or ``None``. A heading that crosses the indent
    fills this gap; the caller then tries :func:`_row_digit_column`.
    """
    np = _require_numpy()
    search_w = max(8, int(width * 0.32))
    search = np.convolve(col[:search_w], np.ones(3) / 3.0, mode='same')
    ink = search > 0.004
    runs = [(a, b) for a, b in _bool_runs(ink) if 4 <= (b - a + 1) <= int(width * 0.10)]
    min_gap = max(6, int(width * 0.012))
    for a, b in runs:
        gap_at = b + 1
        while gap_at < search_w and search[gap_at] <= 0.0025:
            gap_at += 1
        gap = gap_at - (b + 1)
        if gap < min_gap:
            continue
        after = col[gap_at:min(width, gap_at + max(12, int(width * 0.08)))]
        if after.size == 0 or float(after.mean()) <= 0.003:
            continue
        return x0 + a, min(x1, x0 + b + 1)
    return None


def _row_digit_column(dark, x0, y0, x1, y1):
    """Digit column from per-line gaps, ignoring a blank gutter and headings.

    A question line is a narrow blob, then a clear gap, then the stem. A
    section heading that starts in the same x and runs across the indent has
    no such gap, so it does not vote. The leftmost digit-tall cluster wins
    (option letters sit further right). Returns ``(nx0, nx1)`` or ``None``.
    """
    h = dark.shape[0]
    width = x1 - x0
    if width < 40 or y1 - y0 < 20:
        return None
    dot_gap = _dot_gap_px(width)
    min_gap = _number_gap_px(width)
    max_w = max(16, int(width * 0.06))
    search_w = max(24, int(width * 0.45))
    min_run = max(6, int(0.006 * h))
    votes = []  # (y, abs_a, abs_b)
    ys = dark[y0:y1, x0:x0 + search_w].any(axis=1).nonzero()[0]
    for rel_y in ys.tolist():
        y = y0 + int(rel_y)
        row = dark[y, x0:x0 + search_w]
        runs = _merged_mask_runs(row, dot_gap)
        if not runs:
            continue
        a, b = runs[0]
        if b - a + 1 > max_w or b - a + 1 < 3:
            continue
        if len(runs) > 1:
            gap = runs[1][0] - b - 1
        else:
            rest = dark[y, x0 + b + 1:x1]
            nxt = rest.nonzero()[0]
            if nxt.size == 0:
                continue
            gap = int(nxt[0])
        if gap < min_gap:
            continue
        votes.append((y, x0 + a, x0 + b + 1))
    if not votes:
        return None
    # Question numbers are the leftmost column of digit-tall blobs. Option
    # letters (A. B. C. D.) match the same gap but sit further right, and a
    # short heading fragment must not stretch the column into the stem.
    link = max(18, int(width * 0.012))
    clusters = []
    for vote in sorted(votes, key=lambda v: (v[1], v[0])):
        placed = False
        for cl in clusters:
            if abs(vote[1] - cl[0]) <= link:
                cl[1].append(vote)
                starts = sorted(v[1] for v in cl[1])
                cl[0] = starts[len(starts) // 2]
                placed = True
                break
        if not placed:
            clusters.append([vote[1], [vote]])
    chosen = None
    for med, items in clusters:
        items.sort()
        tall = []
        cur = [items[0]]
        for vote in items[1:]:
            if vote[0] <= cur[-1][0] + 2:
                cur.append(vote)
            else:
                span = cur[-1][0] - cur[0][0] + 1
                if span >= min_run and len(cur) >= max(6, span // 2):
                    tall.append(cur)
                cur = [vote]
        span = cur[-1][0] - cur[0][0] + 1
        if span >= min_run and len(cur) >= max(6, span // 2):
            tall.append(cur)
        if not tall:
            continue
        if chosen is None or med < chosen[0]:
            chosen = (med, tall)
    if chosen is None:
        return None
    tall_votes = [v for run in chosen[1] for v in run]
    pad = max(4, int(width * 0.008))
    nx0 = min(v[1] for v in tall_votes) - 2
    nx1 = max(v[2] for v in tall_votes) + pad
    return max(x0, nx0), min(x1, nx1)


def _band_has_number_gap(dark, y_a, y_b, nx0, x_right, content_w) -> bool:
    """True when this band is a narrow number plus a gap, not a heading."""
    if y_b < y_a or x_right - nx0 < 4 or content_w < 20:
        return False
    present = dark[y_a:y_b + 1, nx0:x_right].any(axis=0)
    runs = _merged_mask_runs(present, _dot_gap_px(content_w))
    if len(runs) < 2:
        return False
    a, b = runs[0]
    if b - a + 1 > max(16, int(content_w * 0.06)):
        return False
    return (runs[1][0] - b - 1) >= _number_gap_px(content_w)


def find_number_column(dark, x0, y0, x1, y1):
    """Return ``(nx0, nx1, confident)`` pixel bounds of the question-number column.

    Prefers a narrow ink run separated from the stem by a clear vertical gap
    (vertical rules ignored). When a heading crosses that gap, per-line gaps
    lock onto the digit column and skip an empty left gutter. Otherwise the
    fallback strip starts at the first real ink, not at the page edge, and
    ``confident`` is False.
    """
    np = _require_numpy()
    sub = dark[y0:y1, x0:x1]
    height, width = sub.shape
    if height < 20 or width < 20:
        return x0, x0 + max(8, width // 8), False
    col = sub.mean(axis=0).astype(np.float64)
    col[col > 0.35] = 0.0  # full-height rules are not a number column
    found = _projection_number_column(col, width, x0, x1)
    if found:
        return found[0], found[1], True
    voted = _row_digit_column(dark, x0, y0, x1, y1)
    if voted and voted[1] - voted[0] >= 4:
        return voted[0], voted[1], True
    start = _first_content_column(col)
    fallback = max(12, int(width * 0.08))
    return x0 + start, min(x1, x0 + start + fallback), False


def _count_blobs(mask, limit: int = 12) -> int:
    """4-connected component count, stopping once ``limit`` is passed."""
    h, w = mask.shape
    if h == 0 or w == 0 or not mask.any():
        return 0
    seen = mask.copy()
    seen[:] = False
    n = 0
    ys, xs = mask.nonzero()
    for y, x in zip(ys.tolist(), xs.tolist()):
        if seen[y, x]:
            continue
        n += 1
        if n > limit:
            return n
        stack = [(y, x)]
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
    return n


def find_question_anchors(dark, x0, y0, x1, y1, nx0, nx1):
    """Question-start anchors inside the number column.

    Each anchor is a digit-sized blob (one short line, few connected
    components, not a horizontal rule, not a centred header) with stem ink
    to its right. ``y`` / ``y_bottom`` / ``x0`` are page fractions.
    """
    np = _require_numpy()
    h, w = dark.shape
    if nx1 - nx0 < 3 or y1 - y0 < 8:
        return []
    strip = dark[y0:y1, nx0:nx1]
    # A printed digit is only a few dark pixels wide, and a "5" can thin out
    # mid-glyph. 12% of the column splits it below the minimum line height.
    row_need = 3
    row_ink = strip.sum(axis=1) >= row_need
    runs = _merge_runs(_bool_runs(row_ink), gap=3)
    content_w = max(1, x1 - x0)
    anchors = []
    min_h = max(6, int(0.007 * h))
    max_h = int(0.055 * h)
    for a, b in runs:
        height = b - a + 1
        if height < min_h or height > max_h:
            continue
        band = dark[y0 + a:y0 + b + 1, x0:x1]
        if band.size == 0:
            continue
        row_span = []
        for r in range(band.shape[0]):
            cols = band[r].nonzero()[0]
            row_span.append(0 if cols.size == 0 else int(cols[-1] - cols[0] + 1))
        med_span = float(np.median(row_span)) if row_span else 0.0
        # A hairline rule, not a line of body text. 1.2% of a 3500 px page
        # is taller than the type, so the cutoff is the ruling thickness.
        if height <= _rule_run_px(h) and med_span > 0.55 * content_w:
            continue  # ruling through the number column
        cols = band.any(axis=0).nonzero()[0]
        if cols.size == 0:
            continue
        left_gap = float(cols[0]) / content_w
        right_gap = float(content_w - 1 - cols[-1]) / content_w
        # A digit in the number column has a wide gutter to its left on a
        # digital page; that is not a centred heading. A heading whose ink
        # does not sit in the column never reaches this loop.
        ink_left = x0 + int(cols[0])
        in_column = ink_left <= nx1
        if (not in_column) and left_gap > 0.15 and right_gap > 0.12:
            continue  # centred section header
        body = dark[y0 + a:y0 + b + 1, nx1:x1]
        body_ink = int(body.sum()) if body.size else 0
        if body_ink >= 12:
            # Heading text that starts in the column has no indent gap.
            if not _band_has_number_gap(dark, y0 + a, y0 + b, nx0, x1, content_w):
                continue
        else:
            # The number is on its own line (the stem starts further down).
            look = min(y1, y0 + b + 1 + max(24, int(0.18 * h)))
            below = dark[y0 + b + 1:look, x0:x1]
            if below.size == 0 or int(below.sum()) < 30:
                continue
        piece = strip[a:b + 1]
        blobs = _count_blobs(piece, limit=8)
        if blobs < 1 or blobs > 8:
            continue
        scols = piece.any(axis=0).nonzero()[0]
        if scols.size == 0:
            continue
        num_w = int(scols[-1] - scols[0] + 1)
        if num_w > int(0.10 * w):
            continue
        anchors.append({
            'y': (y0 + a) / h,
            'y_bottom': (y0 + b + 1) / h,
            'x0': (nx0 + int(scols[0])) / w,
            'x1': (nx0 + int(scols[-1]) + 1) / w,
            'qno': None,
            'ocr': False,
            'assumed': False,
            'continues_prev': False,
        })
    return anchors


def suppress_indented_anchors(anchors):
    """Drop a marker that sits just under a question number but further right.

    Lettered parts ``(a)`` ``(b)`` are indented relative to the printed
    question number. Two real questions share the number column, so a close
    pair at the same x is kept.
    """
    kept = []
    for a in sorted(anchors or [], key=lambda z: z['y']):
        if kept:
            prev = kept[-1]
            dy = float(a['y']) - float(prev['y'])
            dx = float(a.get('x0', 0)) - float(prev.get('x0', 0))
            if 0 <= dy < 0.07 and dx > 0.012:
                continue
        kept.append(a)
    return kept


def _clean_qno(value):
    try:
        q = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= q <= 200:
        return q
    return None


def _assign_qnos(anchors, readings):
    """Write ``qno`` / ``ocr`` / ``assumed`` onto anchors (no continuation rows)."""
    readings = [_clean_qno(v) for v in (readings or [])]
    if len(readings) != len(anchors):
        readings = [None] * len(anchors)
    if anchors and all(q is not None for q in readings):
        for a, q in zip(anchors, readings):
            a['qno'] = q
            a['ocr'] = True
            a['assumed'] = False
        return 'ocr'
    vals = list(readings)
    if any(v is not None for v in vals):
        changed = True
        while changed:
            changed = False
            for i in range(1, len(vals) - 1):
                if vals[i] is None and vals[i - 1] is not None and vals[i + 1] is not None:
                    if vals[i + 1] == vals[i - 1] + 2:
                        vals[i] = vals[i - 1] + 1
                        changed = True
        if vals and all(v is not None for v in vals):
            for a, q in zip(anchors, vals):
                a['qno'] = q
                a['ocr'] = True
                a['assumed'] = False
            return 'ocr'
    n = 0
    for a in anchors:
        n += 1
        a['qno'] = n
        a['ocr'] = False
        a['assumed'] = True
    return 'assumed'


def _read_qno_tesseract(gray, anchor, tesseract_cmd: str = ''):
    """OCR one number-column blob. ``None`` when Tesseract is missing or unsure."""
    try:
        from app.pdf_text import resolve_tesseract
        cmd = resolve_tesseract(tesseract_cmd or '')
        if not cmd:
            return None
        import pytesseract
        from PIL import Image
    except Exception:
        return None
    h, w = gray.shape[:2]
    x0 = max(0, int(anchor['x0'] * w) - 4)
    x1 = min(w, int(anchor.get('x1', anchor['x0'] + 0.03) * w) + 8)
    y0 = max(0, int(anchor['y'] * h) - 3)
    y1 = min(h, int(anchor.get('y_bottom', anchor['y'] + 0.03) * h) + 3)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    crop = gray[y0:y1, x0:x1]
    try:
        im = Image.fromarray(crop).resize(
            (max(1, crop.shape[1] * 3), max(1, crop.shape[0] * 3)))
        pytesseract.pytesseract.tesseract_cmd = cmd
        text = pytesseract.image_to_string(
            im, config='--psm 7 -c tessedit_char_whitelist=0123456789')
    except Exception as e:
        logger.info('pdf-import cv OCR skipped a blob: %s', e)
        return None
    m = re.search(r'\d{1,3}', text or '')
    return _clean_qno(m.group(0)) if m else None


def _has_leading_body(dark, x0, y0, x1, nx0, nx1, first_y_px):
    """True when ink above the first number looks like a spilled stem.

    The number column in that strip is empty (otherwise it would be its own
    anchor) and the ink is not a centred header.
    """
    np = _require_numpy()
    h = dark.shape[0]
    if first_y_px is None or first_y_px - y0 < int(0.03 * h):
        return False
    strip = dark[y0:first_y_px, nx0:nx1]
    body = dark[y0:first_y_px, nx1:x1]
    if strip.size == 0 or body.size == 0:
        return False
    if float(strip.mean()) > 0.02:
        return False
    if float(body.mean()) < 0.008:
        return False
    cols = body.any(axis=0).nonzero()[0]
    if cols.size == 0:
        return False
    bw = body.shape[1]
    left_gap = float(cols[0]) / bw
    right_gap = float(bw - 1 - cols[-1]) / bw
    if left_gap > 0.20 and right_gap > 0.15:
        return False
    # A thin full-width run is a ruling. A thick dark band is a spilled stem
    # (synthetic stems are solid bars well over half the content width).
    row_frac = body.mean(axis=1)
    thin = _rule_run_px(h)
    text_px = 0
    i = 0
    n = int(row_frac.shape[0])
    while i < n:
        frac = float(row_frac[i])
        if frac < 0.01:
            i += 1
            continue
        if frac > 0.45:
            j = i
            while j < n and float(row_frac[j]) > 0.45:
                j += 1
            if j - i > thin:
                text_px += j - i
            i = j
            continue
        text_px += 1
        i += 1
    return text_px >= 4


def _rule_run_px(page_h: int) -> int:
    """Tallest run (px) still treated as a printed ruling.

    A thicker dark band is text. 0.3% of the page height stays under a line
    of body text and above a hairline rule, including the 3 px rulings and
    8 px stems used by the synthetic pages.
    """
    return max(2, int(0.003 * int(page_h)))


def _overlap_frac(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area_a = max(1e-9, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1e-9, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(area_a, area_b)


def _add_reason(box, reason: str):
    reasons = box.setdefault('reasons', [])
    if reason not in reasons:
        reasons.append(reason)


def score_boxes(boxes, *, threshold: float = CV_LOW_CONFIDENCE,
                extra_reasons=None) -> dict:
    """Page confidence from per-item scores plus sequence / overlap checks.

    Mutates item ``confidence`` / ``reasons`` when an overlap is found.
    ``low`` is true when the page score or any item is below ``threshold``.
    """
    reasons = []
    for r in extra_reasons or []:
        if r not in reasons:
            reasons.append(r)
    if not boxes:
        if 'no_boxes' not in reasons:
            reasons.append('no_anchors')
        return {'confidence': 0.0, 'low': True, 'reasons': reasons}

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            bi, bj = boxes[i].get('box'), boxes[j].get('box')
            if not bi or not bj:
                continue
            if _overlap_frac(bi, bj) > 0.25:
                boxes[i]['confidence'] = min(float(boxes[i].get('confidence', 0.5)), 0.35)
                boxes[j]['confidence'] = min(float(boxes[j].get('confidence', 0.5)), 0.35)
                _add_reason(boxes[i], 'overlap')
                _add_reason(boxes[j], 'overlap')
                if 'overlap' not in reasons:
                    reasons.append('overlap')

    qnos = [int(b['qno']) for b in boxes
            if b.get('qno') is not None and not b.get('continues_prev')]
    if len(qnos) >= 2:
        diffs = [qnos[k + 1] - qnos[k] for k in range(len(qnos) - 1)]
        if any(d <= 0 for d in diffs):
            reasons.append('qno_repeat')
        elif any(d != 1 for d in diffs):
            reasons.append('qno_gap')
    if any(b.get('assumed') for b in boxes) and 'qno_assumed' not in reasons:
        reasons.append('qno_assumed')
    for b in boxes:
        for r in b.get('reasons') or []:
            if r not in reasons:
                reasons.append(r)

    page = min(float(b.get('confidence', 0.5)) for b in boxes)
    if 'qno_gap' in reasons or 'qno_repeat' in reasons:
        page = min(page, 0.4)
    if 'overlap' in reasons:
        page = min(page, 0.45)
    if 'empty_ink' in reasons:
        page = min(page, 0.2)
    if 'qno_assumed' in reasons:
        page = min(page, 0.5)
    if 'number_column_guessed' in reasons and 'qno_assumed' not in reasons:
        page = min(page, 0.62)
    if 'custom_prompt_ignored' in reasons:
        page = min(page, 0.45)
    if 'long_unnumbered_head' in reasons:
        page = min(page, 0.4)
    page = round(max(0.0, min(1.0, page)), 3)
    low = page < float(threshold) or any(
        float(b.get('confidence', 0.0)) < float(threshold) for b in boxes)
    return {'confidence': page, 'low': bool(low), 'reasons': reasons}


def _clip_box(box):
    x1, y1, x2, y2 = [float(v) for v in box]
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    return [max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1)),
            max(0.0, min(1.0, x2)), max(0.0, min(1.0, y2))]


def _refine_within(gray, box, y_lo, y_hi, shrink_sides, pad_frac, grow_frac):
    """``refine_box`` with pixels outside ``[y_lo, y_hi]`` blanked.

    The snap can recover a marks line in the gap under the stem, and it
    cannot grab the next question.
    """
    from app import pdf_layout
    np = pdf_layout.np
    h, w = gray.shape[:2]
    work = np.array(gray, copy=True)
    r_lo = int(round(max(0.0, y_lo) * h))
    r_hi = int(round(min(1.0, y_hi) * h))
    if r_lo > 0:
        work[:r_lo, :] = 255
    if r_hi < h:
        work[r_hi:, :] = 255
    try:
        return pdf_layout.refine_box(
            work, box, shrink_sides=shrink_sides,
            grow_frac=grow_frac, pad_frac=pad_frac)
    except Exception as e:
        logger.warning('pdf-import cv refine_box failed: %s', e)
        return list(box)


def _extend_marks(dark, box, y_limit):
    """Pull the bottom down to a short right-hand blob (a marks tag) that
    the full-width row threshold dropped."""
    h, w = dark.shape
    x1, y1, x2, y2 = box
    y_start = int(round(y2 * h))
    y_end = int(round(min(y_limit, y2 + 0.045) * h))
    if y_end - y_start < 2:
        return box
    rx1 = int(round((x1 + 0.62 * (x2 - x1)) * w))
    rx2 = int(round(x2 * w))
    rx1 = max(0, min(w - 1, rx1))
    rx2 = max(rx1 + 1, min(w, rx2))
    region = dark[y_start:y_end, rx1:rx2]
    if region.size == 0:
        return box
    rows = region.sum(axis=1)
    # A full-width rule has ink on the left too; skip those rows.
    full = dark[y_start:y_end, int(x1 * w):int(x2 * w)]
    hit = []
    for i, count in enumerate(rows.tolist()):
        if count < 6:
            continue
        if full.size and float(full[i].mean()) > 0.40:
            continue
        hit.append(i)
    if not hit:
        return box
    new_y2 = min(y_limit - 0.001, (y_start + hit[-1] + 1) / h + 0.004)
    if new_y2 <= y2:
        return box
    return [x1, y1, x2, new_y2]


def _trim_trailing_rules(dark, box):
    """Move the bottom up off blank rows and thin horizontal rules.

    A thick dark band is text (a synthetic stem is a solid bar), not a rule.
    Only a run a few pixels tall is treated as a ruling.
    """
    h, w = dark.shape
    x1, y1, x2, y2 = box
    r1 = int(round(y1 * h))
    r2 = int(round(y2 * h))
    c1 = int(round(x1 * w))
    c2 = int(round(x2 * w))
    if r2 - r1 < 8 or c2 - c1 < 4:
        return box
    sub = dark[r1:r2, c1:c2]
    row_frac = sub.mean(axis=1)
    thin = _rule_run_px(h)
    i = sub.shape[0] - 1
    while i > 4:
        frac = float(row_frac[i])
        if frac < 0.004:
            i -= 1
            continue
        if frac > 0.45:
            j = i
            while j > 0 and float(row_frac[j]) > 0.45:
                j -= 1
            if i - j <= thin:
                i = j
                continue
        break
    new_y2 = min(1.0, (r1 + i + 1) / h + 0.003)
    if new_y2 < y2 - 0.002:
        return [x1, y1, x2, new_y2]
    return box


def _line_stats(dark, box):
    """Text-line runs inside ``box``: ``{a, b, left, width}`` in pixels of the crop."""
    h, w = dark.shape
    x1, y1, x2, y2 = box
    r1, r2 = int(round(y1 * h)), int(round(y2 * h))
    c1, c2 = int(round(x1 * w)), int(round(x2 * w))
    if r2 - r1 < 8 or c2 - c1 < 4:
        return []
    sub = dark[r1:r2, c1:c2]
    frac = sub.mean(axis=1)
    ink = (frac >= 0.012) & (frac <= 0.55)
    runs = _merge_runs(_bool_runs(ink), gap=2)
    bw = sub.shape[1]
    out = []
    for a, b in runs:
        if b - a + 1 < 4:
            continue
        cols = sub[a:b + 1].any(axis=0).nonzero()[0]
        if cols.size == 0:
            continue
        out.append({
            'a': a, 'b': b,
            'top': (r1 + a) / h,
            'bottom': (r1 + b + 1) / h,
            'left': float(cols[0]) / bw,
            'width': float(cols[-1] - cols[0] + 1) / bw,
        })
    return out


def _trim_centered_footer(dark, box):
    """Drop a centred line sitting under the question with a gap above it
    (a section header that fell into the previous band)."""
    lines = _line_stats(dark, box)
    if len(lines) < 2:
        return box
    last, prev = lines[-1], lines[-2]
    gap = last['top'] - prev['bottom']
    line_h = max(0.005, last['bottom'] - last['top'])
    if gap < line_h * 0.8:
        return box
    # Centred in the box: both side gaps are large. ``left`` is the gap
    # before the ink; the right gap is 1 - left - width.
    right_gap = 1.0 - last['left'] - last['width']
    mid = last['left'] + 0.5 * last['width']
    # Short option lines sit left of centre inside a full-width frame, so a
    # large right gap alone is not a footer. The ink's midpoint has to be
    # near the middle of the box.
    if (abs(mid - 0.5) <= 0.08 and last['left'] > 0.18
            and right_gap > 0.12 and last['width'] < 0.55):
        return [box[0], box[1], box[2], max(box[1] + 0.02, prev['bottom'] + 0.004)]
    return box


def _option_block(dark, box) -> bool:
    """True when the last four text lines look like options A–D."""
    lines = _line_stats(dark, box)
    if len(lines) < 4:
        return False
    last = lines[-4:]
    indents = [ln['left'] for ln in last]
    if max(indents) - min(indents) > 0.05:
        return False
    if any(ln['width'] > 0.72 for ln in last):
        return False
    gaps = [last[i + 1]['top'] - last[i]['bottom'] for i in range(3)]
    if min(gaps) < -0.002:
        return False
    if max(gaps) > max(0.02, 3.0 * (min(gaps) + 0.004)):
        return False
    return True


def _band_is_blank_ruling(dark, box) -> bool:
    """True when the box is empty once thin horizontal rules are ignored."""
    h, w = dark.shape
    x1, y1, x2, y2 = box
    r1, r2 = int(round(max(0.0, y1) * h)), int(round(min(1.0, y2) * h))
    c1, c2 = int(round(max(0.0, x1) * w)), int(round(min(1.0, x2) * w))
    if r2 - r1 < 2 or c2 - c1 < 2:
        return True
    sub = dark[r1:r2, c1:c2]
    row_frac = sub.mean(axis=1)
    thin = _rule_run_px(h)
    keep = [True] * sub.shape[0]
    i = 0
    while i < sub.shape[0]:
        if float(row_frac[i]) > 0.45:
            j = i
            while j < sub.shape[0] and float(row_frac[j]) > 0.45:
                j += 1
            if j - i <= thin:
                for k in range(i, j):
                    keep[k] = False
            i = j
        else:
            i += 1
    kept = [sub[k] for k in range(sub.shape[0]) if keep[k]]
    if not kept:
        return True
    np = _require_numpy()
    remain = np.stack(kept, axis=0)
    return float(remain.mean()) < 0.004


def _apply_x(box, frame_box, frame_inset, nx0, page_w, shrink_sides, pad_frac, content_right):
    x1, y1, x2, y2 = box
    if frame_box:
        left = frame_box[0] + frame_inset
        right = frame_box[2] - frame_inset
        if right - left > 0.1:
            return [left, y1, right, y2]
    if shrink_sides:
        num_left = max(0.0, (nx0 / page_w) - pad_frac)
        right = min(0.98, content_right)
        # segment_page is full-bleed, so a box still on the page edge is the
        # gutter, not ink. Sit it on the number column and the content.
        # A tighter box is only pulled left as far as the number column —
        # never onto the indented stem — and is not left short of the content.
        if x1 < num_left - 0.01:
            x1 = num_left
        else:
            x1 = min(x1, num_left)
        if x2 > right + 0.01:
            x2 = right
        else:
            x2 = max(x2, right)
        return [x1, y1, x2, y2]
    # SOL / full width: the content window, not the ink bbox.
    return [max(0.0, (nx0 / page_w) - pad_frac), y1, min(1.0, content_right), y2]


def detect_cv_page(gray, *, atype: str = 'QUE', frame=None, subject_id=None,
                   paper=None, profile: str | None = None, shrink_sides=None,
                   pad_frac: float = 0.006, grow_frac: float = 0.02,
                   frame_inset: float = 0.005,
                   low_confidence: float = CV_LOW_CONFIDENCE,
                   use_ocr: bool = True, tesseract_cmd: str = '',
                   ocr_reader=None, generic_prompt: bool = False) -> dict:
    """Detect numbered question boxes on one grayscale page. No model call.

    ``ocr_reader(anchor) -> int|None`` overrides Tesseract (tests). When OCR
    is off or reads nothing, question numbers are 1..N in reading order and
    the page is low-confidence (``qno_assumed``).

    Returns ``boxes`` in the same shape as ``detect_page`` plus per-item
    ``confidence`` / ``reasons``, and page-level ``confidence``, ``low``,
    ``reasons``, ``profile``, ``anchors``, ``debug``.
    """
    from app import pdf_layout
    np = _require_numpy()
    if getattr(gray, 'ndim', 0) != 2:
        raise ValueError('detect_cv_page expects a 2-D grayscale array')
    profile = (profile or resolve_profile(subject_id, atype, paper)).strip().lower()
    if profile not in _PROFILES:
        profile = 'auto'
    if shrink_sides is None:
        shrink_sides = (atype or 'QUE').strip().upper() != 'SOL'
    # SOL keeps the full content width (marking notes sit on the right).
    if profile == 'sol':
        shrink_sides = False

    h, w = gray.shape[:2]
    dark = gray < 200
    extra = []
    fbox = coerce_frame_box(frame)
    if fbox is None and frame is not False:
        try:
            fbox = coerce_frame_box(pdf_layout.detect_page_frame(gray))
        except Exception as e:
            logger.info('pdf-import cv frame detection skipped: %s', e)
            fbox = None
    x0, y0, x1, y1 = _window(h, w, fbox)
    nx0, nx1, col_ok = find_number_column(dark, x0, y0, x1, y1)
    if not col_ok:
        extra.append('number_column_guessed')
    anchors = suppress_indented_anchors(
        find_question_anchors(dark, x0, y0, x1, y1, nx0, nx1))

    readings = []
    if anchors:
        if use_ocr:
            reader = ocr_reader or (
                lambda a: _read_qno_tesseract(gray, a, tesseract_cmd))
            for a in anchors:
                try:
                    readings.append(reader(a))
                except Exception as e:
                    logger.info('pdf-import cv OCR reader failed: %s', e)
                    readings.append(None)
        else:
            readings = [None] * len(anchors)
        how = _assign_qnos(anchors, readings)
        if how == 'assumed':
            extra.append('qno_assumed')
    else:
        extra.append('no_anchors')

    items = []
    if anchors and _has_leading_body(dark, x0, y0, x1, nx0, nx1, int(anchors[0]['y'] * h)):
        head = {
            'y': y0 / h,
            'y_bottom': anchors[0]['y'],
            'x0': nx0 / w,
            'qno': None,
            'ocr': False,
            'assumed': False,
            'continues_prev': True,
        }
        if anchors[0]['y'] - (y0 / h) > 0.45:
            extra.append('long_unnumbered_head')
        items.append(head)
    items.extend(anchors)

    seg = []
    if items:
        seg = pdf_layout.segment_page(
            gray,
            [{'qno': a.get('qno'), 'y': a['y']} for a in items],
            shrink_sides=False, pad_frac=pad_frac)

    pairs = []
    if len(seg) == len(items):
        pairs = list(zip(items, seg))
    else:
        used = set()
        for s in seg:
            best = None
            for i, a in enumerate(items):
                if i in used:
                    continue
                d = abs(float(s['box'][1]) - float(a['y']))
                if best is None or d < best[0]:
                    best = (d, i)
            if best and best[0] < 0.08:
                used.add(best[1])
                pairs.append((items[best[1]], s))

    content_right = (x1 / w) if not shrink_sides else min(1.0, x1 / w)
    # Right edge for unframed QUE: the ink of the stems, computed once.
    if shrink_sides and not fbox:
        colsum = dark[y0:y1, x0:x1].sum(axis=0)
        ink_cols = (colsum > max(2, int(0.004 * (y1 - y0)))).nonzero()[0]
        if ink_cols.size:
            content_right = min(1.0, (x0 + int(ink_cols[-1]) + 1) / w + pad_frac)

    boxes = []
    for anchor, seg_item in pairs:
        try:
            idx = items.index(anchor)
        except ValueError:
            idx = -1
        y_lo = float(anchor['y']) - 0.012
        if idx >= 0 and idx + 1 < len(items):
            y_hi = float(items[idx + 1]['y'])
        elif fbox:
            y_hi = float(fbox[3])
        else:
            y_hi = y1 / h
        box = _clip_box(seg_item['box'])
        box[1] = max(box[1], y_lo)
        box[3] = min(max(box[3], box[1] + 0.01), y_hi)
        box = _refine_within(gray, box, y_lo, y_hi, False, pad_frac, grow_frac)
        box = _clip_box(box)
        box[3] = min(box[3], y_hi - 0.001)
        if profile in ('auto', 'cq', 'mcq'):
            box = _extend_marks(dark, box, y_hi)
        box = _trim_trailing_rules(dark, box)
        box = _trim_centered_footer(dark, box)
        box = _apply_x(box, fbox, frame_inset, nx0, w, shrink_sides, pad_frac, content_right)
        box = _clip_box(box)
        if box[2] - box[0] < 0.04 or box[3] - box[1] < 0.012:
            continue
        if _band_is_blank_ruling(dark, box):
            if 'dropped_blank_ruling' not in extra:
                extra.append('dropped_blank_ruling')
            continue
        reasons = []
        if anchor.get('continues_prev'):
            conf = 0.35 if 'long_unnumbered_head' in extra else 0.72
            reasons.append('continuation')
            qno = None
            assumed = False
        elif anchor.get('assumed'):
            conf = 0.48
            reasons.append('qno_assumed')
            qno = anchor.get('qno')
            assumed = True
        else:
            conf = 0.90
            qno = anchor.get('qno')
            assumed = False
        gray_box = pdf_layout.has_ink(gray, box)
        if not gray_box:
            conf = 0.15
            reasons.append('empty_ink')
        if profile == 'mcq' and not anchor.get('continues_prev') and not _option_block(dark, box):
            conf = min(conf, 0.50)
            reasons.append('mcq_options_unclear')
        label = str(qno) if qno is not None else None
        boxes.append({
            'qno': qno,
            'label': label,
            'box': [round(float(v), 4) for v in box],
            'continues_prev': bool(anchor.get('continues_prev')),
            'continues_next': False,
            'confidence': round(conf, 3),
            'reasons': reasons,
            'assumed': assumed,
        })

    if generic_prompt:
        extra.append('custom_prompt_ignored')
    scored = score_boxes(boxes, threshold=low_confidence, extra_reasons=extra)
    # ``assumed`` is internal; the plan only needs confidence.
    for b in boxes:
        b.pop('assumed', None)
    debug_lines = [
        f"cv profile={profile} frame={'yes' if fbox else 'no'} "
        f"column={'ok' if col_ok else 'guessed'} anchors={len(anchors)} "
        f"confidence={scored['confidence']} low={int(scored['low'])}",
        'reasons: ' + (','.join(scored['reasons']) or '-'),
    ]
    for b in boxes:
        debug_lines.append(
            f"qno={b.get('qno')} box={b['box']} c={b['confidence']} "
            f"{','.join(b.get('reasons') or [])}")
    return {
        'boxes': boxes,
        'anchors': anchors,
        'confidence': scored['confidence'],
        'low': scored['low'],
        'reasons': scored['reasons'],
        'profile': profile,
        'frame': fbox,
        'debug': '\n'.join(debug_lines),
    }

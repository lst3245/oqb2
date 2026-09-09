"""
Classical-CV layout helpers for PDF Batch Import (no LLM).

These back the "LLM assisted" detection sub-modes and the scan deskew:

  * ``deskew_image``  - straighten a skewed/rotated scanned page before any
    cropping, by maximising the variance of the horizontal projection profile
    over a small angle sweep.
  * ``refine_box``    - tighten/expand a single LLM bounding box to the actual
    printed content using projection profiles (recovers chopped text, a marks
    line just below, or a figure just outside; drops blank answer-space
    margins).
  * ``segment_page``  - given the LLM's per-question START anchors (just a y per
    question), derive each question's true top/bottom (and optionally
    left/right) from the whitespace gaps between blocks.
  * ``detect_page_frame`` - find the printed rectangle border (the "answers in
    the margin will not be marked" frame on DSE answer books) so question
    crops can share one left edge / width across pages of a scanned paper.

All geometry is fractional ``[x1, y1, x2, y2]`` (0..1) to match the rest of the
pipeline (resolution-independent: the LLM sees a downscaled page, crops are cut
from the high-res page). NumPy is required; callers should treat ImportError /
RuntimeError as "assisted mode unavailable" and fall back to plain LLM
detection (or skip deskew).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import numpy as np
    _NUMPY_OK = True
except Exception:  # pragma: no cover - numpy genuinely optional
    np = None
    _NUMPY_OK = False


def numpy_available() -> bool:
    """True when NumPy imported — assisted detection / deskew can run."""
    return _NUMPY_OK


def _require_numpy():
    if not _NUMPY_OK:
        raise RuntimeError(
            'NumPy is required for LLM-assisted PDF detection / deskew. '
            'Install it (pip install "numpy>=1.26") and restart the server.')


# ==================== Pixel helpers ====================

def load_gray(png_path: str):
    """Load a page PNG as a 2D uint8 grayscale ndarray (alpha flattened onto
    white, matching llm_client.prepare_image)."""
    _require_numpy()
    from PIL import Image
    with Image.open(png_path) as im:
        im.load()
        has_alpha = im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info)
        if has_alpha:
            rgba = im.convert('RGBA')
            bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
            bg.alpha_composite(rgba)
            im = bg.convert('L')
        elif im.mode != 'L':
            im = im.convert('L')
        return np.asarray(im, dtype=np.uint8)


def _otsu_threshold(gray) -> int:
    """Otsu's threshold (0..255) for a uint8 grayscale array."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = float(gray.size)
    if total <= 0:
        return 200
    omega = np.cumsum(hist)
    mu = np.cumsum(hist * np.arange(256))
    mu_t = mu[-1]
    denom = omega * (total - omega)
    denom[denom == 0] = 1e-9
    sigma_b2 = (mu_t * omega - mu) ** 2 / denom
    return int(np.argmax(sigma_b2))


def _dark_mask(gray, threshold=None):
    """Boolean array, True where the pixel is 'content' (darker than the
    threshold). Otsu by default, clamped so a near-blank page (where Otsu
    lands near 0/255) doesn't turn all-white into all-dark or vice versa."""
    if threshold is None:
        t = int(min(max(_otsu_threshold(gray), 60), 230))
    else:
        t = int(threshold)
    return gray < t


def _content_extent(profile, noise):
    """First and last indices where ``profile`` exceeds ``noise``; (None, None)
    when the whole profile is below the noise floor (blank)."""
    idx = np.where(profile > noise)[0]
    if idx.size == 0:
        return None, None
    return int(idx[0]), int(idx[-1])


# ==================== Deskew ====================

def deskew_image(img, max_angle: float = 6.0, coarse_step: float = 1.0,
                 fine_step: float = 0.2, search_width: int = 1000,
                 min_angle: float = 0.3):
    """Return a deskewed copy of ``img`` (PIL Image, RGB).

    Estimates the small skew angle by maximising the variance of the
    horizontal projection profile (row-sum of dark pixels) of the binarised
    page over a coarse-then-fine sweep in ``[-max_angle, +max_angle]`` degrees,
    then rotates the full-resolution page by that angle with a white fill.
    Returns the page unchanged when the best angle is within ``min_angle``
    degrees of 0 (avoids needless resampling blur). ``expand=False`` keeps the
    output dimensions identical to the input (so cached page width/height stay
    valid)."""
    _require_numpy()
    from PIL import Image

    base = img.convert('L')
    w, h = base.size
    if w > search_width:
        small = base.resize((search_width, max(1, int(h * search_width / w))))
    else:
        small = base

    def _score(angle: float) -> float:
        rot = small.rotate(angle, resample=Image.BILINEAR, expand=False, fillcolor=255)
        arr = np.asarray(rot, dtype=np.uint8)
        rowdark = (arr < 200).sum(axis=1).astype(np.float64)
        return float(rowdark.var())

    best_a, best_s = 0.0, _score(0.0)
    a = -max_angle
    while a <= max_angle + 1e-9:
        s = _score(a)
        if s > best_s:
            best_s, best_a = s, a
        a += coarse_step
    lo, hi = best_a - coarse_step, best_a + coarse_step
    a = lo
    while a <= hi + 1e-9:
        s = _score(a)
        if s > best_s:
            best_s, best_a = s, a
        a += fine_step

    rgb = img if img.mode == 'RGB' else img.convert('RGB')
    if abs(best_a) < min_angle:
        return rgb
    return rgb.rotate(best_a, resample=Image.BICUBIC, expand=False,
                      fillcolor=(255, 255, 255))


# ==================== Box refinement (method = 'refine') ====================

def refine_box(gray, box, shrink_sides: bool = True, grow_frac: float = 0.035,
               pad_frac: float = 0.006, threshold=None):
    """Refine a fractional ``box`` ``[x1,y1,x2,y2]`` to the printed content.

    The box is first expanded by ``grow_frac`` on every side to form a search
    window (so chopped text, a marks line just below, or a figure just outside
    the model's box can be recovered), then tightened back to the actual
    content inside that window via projection profiles (dropping blank answer
    space and over-wide margins).

    ``shrink_sides=False`` (used for SOL pages) leaves the left/right edges at
    the model's box so right-hand marking side-notes are never trimmed; only
    the top/bottom are refined. Returns a new fractional box, or the original
    on a degenerate / blank result."""
    _require_numpy()
    H, W = gray.shape[:2]
    dark = _dark_mask(gray, threshold)

    x1, y1, x2, y2 = (float(v) for v in box)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))

    wx1 = max(0.0, x1 - grow_frac)
    wy1 = max(0.0, y1 - grow_frac)
    wx2 = min(1.0, x2 + grow_frac)
    wy2 = min(1.0, y2 + grow_frac)
    L, T = int(wx1 * W), int(wy1 * H)
    R, B = int(wx2 * W), int(wy2 * H)
    if R - L < 4 or B - T < 4:
        return [x1, y1, x2, y2]

    sub = dark[T:B, L:R]
    sub_w = sub.shape[1]

    rowsum = sub.sum(axis=1)
    rt, rb = _content_extent(rowsum, max(2, int(0.01 * sub_w)))
    if rt is None:
        return [x1, y1, x2, y2]
    new_top = (T + rt) / H
    new_bot = (T + rb + 1) / H

    if shrink_sides:
        colsum = sub[rt:rb + 1, :].sum(axis=0)
        cl, cr = _content_extent(colsum, max(2, int(0.01 * (rb - rt + 1))))
        if cl is None:
            new_left, new_right = x1, x2
        else:
            new_left = (L + cl) / W
            new_right = (L + cr + 1) / W
    else:
        new_left, new_right = x1, x2

    new_left = max(0.0, new_left - pad_frac)
    new_top = max(0.0, new_top - pad_frac)
    new_right = min(1.0, new_right + pad_frac)
    new_bot = min(1.0, new_bot + pad_frac)
    if new_right - new_left < 0.01 or new_bot - new_top < 0.01:
        return [x1, y1, x2, y2]
    return [new_left, new_top, new_right, new_bot]


# ==================== Anchor segmentation (method = 'segment') ====================

def segment_page(gray, anchors, shrink_sides: bool = True,
                 pad_frac: float = 0.006, threshold=None):
    """Derive one box per question from START-y ``anchors``.

    ``anchors`` = list of ``{'qno': int|None, 'y': float}`` (fractional y of
    where each question/solution begins), any order. Each question's band runs
    from its anchor down to the next anchor (or the page bottom); the band is
    then trimmed to its actual content top/bottom (removing trailing answer
    space) and, when ``shrink_sides``, to its content columns. Returns
    ``[{'qno', 'box':[x1,y1,x2,y2]}]`` in top-to-bottom reading order."""
    _require_numpy()
    H, W = gray.shape[:2]
    dark = _dark_mask(gray, threshold)

    pts = sorted(
        ({'qno': a.get('qno'),
          'y': min(max(float(a.get('y', 0.0)), 0.0), 1.0)}
         for a in (anchors or []) if isinstance(a, dict)),
        key=lambda a: a['y'])

    out = []
    for i, a in enumerate(pts):
        top_f = max(0.0, a['y'] - 0.01)  # small lift so the number isn't clipped
        bot_f = pts[i + 1]['y'] if i + 1 < len(pts) else 1.0
        T, B = int(top_f * H), int(bot_f * H)
        if B - T < 4:
            continue
        band = dark[T:B, :]
        rt, rb = _content_extent(band.sum(axis=1), max(2, int(0.01 * W)))
        if rt is None:
            continue
        new_top = (T + rt) / H
        new_bot = (T + rb + 1) / H
        if shrink_sides:
            colsum = band[rt:rb + 1, :].sum(axis=0)
            cl, cr = _content_extent(colsum, max(2, int(0.01 * (rb - rt + 1))))
            if cl is None:
                new_left, new_right = 0.0, 1.0
            else:
                new_left, new_right = cl / W, (cr + 1) / W
        else:
            new_left, new_right = 0.0, 1.0
        new_left = max(0.0, new_left - pad_frac)
        new_top = max(0.0, new_top - pad_frac)
        new_right = min(1.0, new_right + pad_frac)
        new_bot = min(1.0, new_bot + pad_frac)
        if new_right - new_left < 0.01 or new_bot - new_top < 0.01:
            continue
        out.append({'qno': a['qno'], 'box': [new_left, new_top, new_right, new_bot]})
    return out


# ==================== Printed page frame ====================

FRAME_KEYS = ('left', 'top', 'right', 'bottom')


def _dilate_1d(mask, radius: int, axis: int):
    """Boolean max-filter of ``mask`` along ``axis`` with ``radius`` px each
    side (no wrap-around). Lets a slightly skewed 1-px rule land in a single
    column/row bin instead of smearing over several."""
    out = mask.copy()
    n = mask.shape[axis]
    for k in range(1, max(0, int(radius)) + 1):
        if k >= n:
            break
        if axis == 1:
            out[:, k:] |= mask[:, :-k]
            out[:, :-k] |= mask[:, k:]
        else:
            out[k:, :] |= mask[:-k, :]
            out[:-k, :] |= mask[k:, :]
    return out


def _runs(indices):
    """Group a sorted 1-D index array into ``(start, end)`` inclusive runs."""
    runs = []
    if indices.size == 0:
        return runs
    s = p = int(indices[0])
    for i in indices[1:]:
        i = int(i)
        if i != p + 1:
            runs.append((s, p))
            s = i
        p = i
    runs.append((s, p))
    return runs


def _rule_candidates(dark, axis: int, min_score: float, bands: int = 3,
                     dilate_px: int = 8, max_thick_frac: float = 0.02,
                     edge_frac: float = 0.01):
    """Thin, (nearly) full-length straight rules perpendicular to ``axis``.

    ``axis=1`` looks for vertical rules (scores columns), ``axis=0`` for
    horizontal ones (scores rows). A column's score is the MINIMUM dark
    fraction over ``bands`` equal slices of its length, so a full-height frame
    rail scores high while text columns, short answer lines and the scanner
    shadow that fades out do not. Returns ``[(start, end, coverage_profile)]``
    where ``coverage_profile`` is the dark fraction along the rule (used by the
    caller to measure the rule's extent)."""
    dil = _dilate_1d(dark, dilate_px, axis)
    n_along = dil.shape[0] if axis == 1 else dil.shape[1]
    n_across = dil.shape[1] if axis == 1 else dil.shape[0]
    scores = None
    for b in range(bands):
        lo = b * n_along // bands
        hi = (b + 1) * n_along // bands if b < bands - 1 else n_along
        if hi <= lo:
            continue
        sl = dil[lo:hi, :] if axis == 1 else dil[:, lo:hi]
        s = sl.mean(axis=0 if axis == 1 else 1)
        scores = s if scores is None else np.minimum(scores, s)
    if scores is None:
        return []
    idx = np.where(scores >= min_score)[0]
    # The dilation widens a 1-px rule by ``dilate_px`` on each side, so the
    # cap must include that or thin rails on narrow pages (A3-split halves,
    # ~850 px) are dropped as "too thick".
    max_thick = max(2, int(n_across * max_thick_frac)) + 2 * int(dilate_px)
    lo_ok, hi_ok = n_across * edge_frac, n_across * (1.0 - edge_frac)
    out = []
    for a, b in _runs(idx):
        if (b - a + 1) > max_thick or a < lo_ok or b > hi_ok:
            continue
        band = dil[:, a:b + 1] if axis == 1 else dil[a:b + 1, :]
        cover = band.mean(axis=1 if axis == 1 else 0)
        out.append((a, b, cover))
    return out


def _extent(cover, thresh: float = 0.5, gap_frac: float = 0.01):
    """Start/end of the LONGEST run where ``cover >= thresh``, bridging gaps
    up to ``gap_frac`` of the length (a rule broken by a scan speckle). Using
    the longest run rather than first/last index keeps stray dark pixels at
    the page edge (scanner shadow, margin text) from stretching the extent."""
    idx = np.where(cover >= thresh)[0]
    if idx.size == 0:
        return None
    gap = max(1, int(len(cover) * gap_frac))
    best = None
    s = p = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - p > gap:
            if best is None or (p - s) > (best[1] - best[0]):
                best = (s, p)
            s = i
        p = i
    if best is None or (p - s) > (best[1] - best[0]):
        best = (s, p)
    return best


def detect_page_frame(gray, min_score: float = 0.45, match_frac: float = 0.02,
                      min_span_frac: float = 0.45, dark_threshold: int = 160):
    """Locate the printed rectangle frame of a scanned exam page.

    Returns ``{'left', 'top', 'right', 'bottom'}`` as page fractions (inner
    edge of each rail) with ``None`` for rails that could not be confirmed, or
    ``None`` when nothing frame-like is found. Purely geometric (NumPy only):

    1. Thin full-length vertical and horizontal rules are collected with
       :func:`_rule_candidates`.
    2. A vertical rail is accepted only if some horizontal rule STARTS (for a
       left rail) or ENDS (for a right rail) within ``match_frac`` of it — the
       frame's top/bottom edge meets its sides, whereas a scanner shadow or a
       stray line has no perpendicular partner. The same test (with the roles
       swapped) accepts top/bottom rails.
    3. When several rails qualify on one side, the outermost one wins.

    Partial results are expected on faint or broken scans; callers consolidate
    across the pages of one paper (see ``pdf_import.consolidate_frames``).
    """
    _require_numpy()
    H, W = gray.shape
    if H < 50 or W < 50:
        return None
    dark = gray < int(dark_threshold)
    vert = _rule_candidates(dark, axis=1, min_score=min_score)
    horiz = _rule_candidates(dark, axis=0, min_score=min_score)
    if not vert and not horiz:
        return None

    def _spans(rules, along_len):
        spans = []
        for a, b, cover in rules:
            ext = _extent(cover)
            if ext and (ext[1] - ext[0] + 1) >= along_len * min_span_frac:
                spans.append((a, b, ext[0], ext[1]))
        return spans

    vspans = _spans(vert, H)    # (x_a, x_b, y_start, y_end)
    hspans = _spans(horiz, W)   # (y_a, y_b, x_start, x_end)
    tol_x, tol_y = W * match_frac, H * match_frac

    def _count(v, targets, tol):
        return sum(1 for t in targets if abs(v - t) <= tol)

    h_starts = [s[2] for s in hspans]
    h_ends = [s[3] for s in hspans]
    h_rows = [(s[0] + s[1]) / 2.0 for s in hspans]
    v_starts = [s[2] for s in vspans]
    v_ends = [s[3] for s in vspans]
    v_cols = [(s[0] + s[1]) / 2.0 for s in vspans]

    def _pick(spans, side_ok, ends, corner_rows, tol, ctol, outer_key):
        """Best rail for one side: most perpendicular rules meeting it, plus
        a bonus when its own two ends sit on perpendicular rules (a real
        frame corner). Ties go to the outermost candidate."""
        best = None
        for a, b, s, e in spans:
            if not side_ok(a, b):
                continue
            score = _count((a + b) / 2.0, ends, tol)
            if score == 0:
                continue
            score += 2 * (_count(s, corner_rows, ctol) > 0)
            score += 2 * (_count(e, corner_rows, ctol) > 0)
            key = (score, outer_key(a, b))
            if best is None or key > best[0]:
                best = (key, a, b)
        return best

    left = _pick(vspans, lambda a, b: a < W * 0.5, h_starts, h_rows,
                 tol_x, tol_y, lambda a, b: -a)
    right = _pick(vspans, lambda a, b: b > W * 0.5, h_ends, h_rows,
                  tol_x, tol_y, lambda a, b: b)
    top = _pick(hspans, lambda a, b: a < H * 0.5, v_starts, v_cols,
                tol_y, tol_x, lambda a, b: -a)
    bottom = _pick(hspans, lambda a, b: b > H * 0.5, v_ends, v_cols,
                   tol_y, tol_x, lambda a, b: b)

    frame = {
        'left': (left[2] + 1) / W if left else None,
        'right': right[1] / W if right else None,
        'top': (top[2] + 1) / H if top else None,
        'bottom': bottom[1] / H if bottom else None,
    }
    if frame['left'] is not None and frame['right'] is not None \
            and frame['right'] - frame['left'] < 0.3:
        return None
    if all(v is None for v in frame.values()):
        return None
    return {k: (round(float(v), 4) if v is not None else None)
            for k, v in frame.items()}

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

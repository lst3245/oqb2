"""
Word extraction + fuzzy phrase matching for the PDF Tool's **Find & Mark**.

Two extraction engines, both producing word boxes as **fractions 0..1 of the
post-ops visible page** (y down) so they line up exactly with the rendered
thumbnails and the annotation editor:

* ``digital`` — PyMuPDF ``page.get_text('words')`` on the source page, mapped
  through the descriptor's geometry ops (90° rotates + fractional crop).
  Only available when the page has a text layer and no geometry-warping
  raster op (``deskew`` / ``rotate_fine``).
* ``ocr``     — Tesseract (via pytesseract) on the post-ops rendered image,
  so no coordinate mapping is needed. Requires a local Tesseract install
  (auto-detected, or ``TESSERACT_CMD`` in ``.env``).

Matching is a sliding-window fuzzy phrase matcher (rapidfuzz) over the
concatenated word stream of all searched pages, so a phrase wrapped across
lines **or across two pages** still matches; the emitted boxes are grouped
per page + text line.

Extracted words are cached per page in the staging dir (``words/<id>.json``,
keyed by an ops/engine signature) so repeated searches don't re-OCR.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

# Ops that warp geometry in a way we cannot map digital text through.
GEOMETRY_BREAKERS = frozenset({'deskew', 'rotate_fine'})

ENGINES = ('auto', 'digital', 'ocr')


# ==================== Tesseract resolution ====================

_TESS_RESOLVED = None   # cached: '' = unavailable, else abs path / cmd name


def _candidate_tesseract_paths():
    yield os.path.join(os.getenv('LOCALAPPDATA', ''), 'Programs',
                       'Tesseract-OCR', 'tesseract.exe')
    yield r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    yield r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'


def resolve_tesseract(configured: str = '') -> str:
    """Return a usable tesseract command (abs path or PATH name) or ``''``.

    ``configured`` is the ``TESSERACT_CMD`` config value (highest priority).
    The result is cached for the process lifetime.
    """
    global _TESS_RESOLVED
    if _TESS_RESOLVED is not None:
        return _TESS_RESOLVED

    cmd = ''
    configured = (configured or '').strip()
    if configured and os.path.isfile(configured):
        cmd = configured
    else:
        for p in _candidate_tesseract_paths():
            if p and os.path.isfile(p):
                cmd = p
                break
        else:
            # Maybe it's simply on PATH.
            import shutil
            found = shutil.which('tesseract')
            if found:
                cmd = found
    _TESS_RESOLVED = cmd
    return cmd


def ocr_available(configured: str = '') -> bool:
    """True when both pytesseract and a Tesseract binary are present."""
    try:
        import pytesseract  # type: ignore  # noqa: F401
    except Exception:
        return False
    return bool(resolve_tesseract(configured))


# ==================== Geometry mapping (digital engine) ====================

def _rot_point(x, y, deg):
    """Rotate a fractional point (y down) by ``deg`` clockwise."""
    deg = int(deg) % 360
    if deg == 90:
        return 1.0 - y, x
    if deg == 180:
        return 1.0 - x, 1.0 - y
    if deg == 270:
        return y, 1.0 - x
    return x, y


def _map_rect_through_ops(rect, ops):
    """Map a fractional rect (pre-ops visible page space) through the geometry
    ops in order. Returns the post-ops fractional rect, or ``None`` when the
    rect falls (mostly) outside a crop."""
    x1, y1, x2, y2 = rect
    for op in (ops or []):
        t = op.get('type')
        if t == 'rotate':
            deg = int(op.get('deg', 0)) % 360
            ax, ay = _rot_point(x1, y1, deg)
            bx, by = _rot_point(x2, y2, deg)
            x1, x2 = sorted((ax, bx))
            y1, y2 = sorted((ay, by))
        elif t == 'crop':
            box = op.get('box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            cx1, cx2 = sorted((float(box[0]), float(box[2])))
            cy1, cy2 = sorted((float(box[1]), float(box[3])))
            cw, ch = max(cx2 - cx1, 1e-6), max(cy2 - cy1, 1e-6)
            # Drop words whose centre is outside the crop.
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            if not (cx1 <= mx <= cx2 and cy1 <= my <= cy2):
                return None
            x1 = max(0.0, min(1.0, (x1 - cx1) / cw))
            x2 = max(0.0, min(1.0, (x2 - cx1) / cw))
            y1 = max(0.0, min(1.0, (y1 - cy1) / ch))
            y2 = max(0.0, min(1.0, (y2 - cy1) / ch))
        # all other ops are geometry-neutral (colour filters)
    return [x1, y1, x2, y2]


def digital_mappable(ops) -> bool:
    """True when the op chain contains no geometry-warping raster op."""
    return not any(o.get('type') in GEOMETRY_BREAKERS for o in (ops or []))


# ==================== Word extraction ====================

def extract_words_digital(pdf_path: str, page_index: int, ops):
    """Words from the PDF text layer, mapped to post-ops fractional coords.

    Returns a list of ``{text, x1, y1, x2, y2}`` (possibly empty — scanned
    page). Raises ``ValueError`` when the ops cannot be mapped.
    """
    if not digital_mappable(ops):
        raise ValueError('page has geometry-warping ops — use OCR')
    import fitz  # type: ignore

    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_index)
        vr = page.rect                       # visible (rotation-aware) rect
        rmat = page.rotation_matrix          # unrotated -> visible
        out = []
        for w in page.get_text('words'):
            text = (w[4] or '').strip()
            if not text:
                continue
            r = fitz.Rect(w[0], w[1], w[2], w[3]) * rmat
            r.normalize()
            fr = [(r.x0 - vr.x0) / vr.width, (r.y0 - vr.y0) / vr.height,
                  (r.x1 - vr.x0) / vr.width, (r.y1 - vr.y0) / vr.height]
            mapped = _map_rect_through_ops(fr, ops)
            if mapped is None:
                continue
            out.append({'text': text, 'x1': mapped[0], 'y1': mapped[1],
                        'x2': mapped[2], 'y2': mapped[3]})
        return out
    finally:
        doc.close()


# Orientation-detection thresholds. A "good" word is a confident, word-like
# token; a page that yields too few of them (or a poor good/total ratio) is
# treated as possibly-rotated and re-examined at 90/180/270°. Word COUNT
# alone is a bad signal — a sideways scan still produces hundreds of
# low-confidence GIBBERISH tokens — so confidence is the real discriminator.
_OCR_CONF_MIN = 60          # a word counts as "good" at/above this confidence
_OCR_ORIENT_MIN_WORDS = 8   # fewer good words than this ⇒ inspect orientations
_OCR_GOOD_RATIO = 0.35      # good/total below this ⇒ inspect orientations


def _ocr_pass(img, tesseract_cmd: str = ''):
    """Single Tesseract pass over a PIL image → fractional word boxes.

    Each box also carries its ``conf`` (0–100) so callers can score the
    orientation; :func:`extract_words_ocr` strips it before returning.
    """
    import pytesseract  # type: ignore

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    w, h = img.size
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    out = []
    n = len(data.get('text') or [])
    for i in range(n):
        text = (data['text'][i] or '').strip()
        try:
            conf = float(data['conf'][i])
        except (TypeError, ValueError):
            conf = -1
        if conf < 0 or not text:
            continue
        left, top = data['left'][i], data['top'][i]
        width, height = data['width'][i], data['height'][i]
        out.append({'text': text, 'conf': conf,
                    'x1': left / w, 'y1': top / h,
                    'x2': (left + width) / w, 'y2': (top + height) / h})
    return out


def _good_word_count(words):
    """Number of confident, word-like tokens (the orientation quality score)."""
    return sum(1 for w in words
               if w.get('conf', 0) >= _OCR_CONF_MIN and len(w['text']) >= 2
               and any(c.isalpha() for c in w['text']))


def _detect_osd_rotation(img, tesseract_cmd: str = '', min_conf: float = 1.0):
    """Tesseract OSD page-orientation probe → clockwise degrees to upright
    (0/90/180/270), or 0 when unavailable / not confident. Used only as a
    tie-breaker between orientations that score equally well."""
    import pytesseract  # type: ignore

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        rot = int(osd.get('rotate', 0)) % 360
        conf = float(osd.get('orientation_conf', 0) or 0)
    except Exception:
        return 0
    return rot if rot in (90, 180, 270) and conf >= min_conf else 0


def rotate_rect_frac(rect, deg):
    """Rotate a fractional ``[x1,y1,x2,y2]`` rect ``deg`` clockwise within the
    unit square, returning a normalised rect. ``deg`` is 0/90/180/270."""
    ax, ay = _rot_point(rect[0], rect[1], deg)
    bx, by = _rot_point(rect[2], rect[3], deg)
    return [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]


def _strip_conf(words):
    for w in words:
        w.pop('conf', None)
    return words


def extract_words_ocr(img, tesseract_cmd: str = '', auto_orient: bool = True):
    """OCR words over a PIL image → ``(words, display_rot)``.

    Words are returned in **reading-upright** fractional coords (text reads
    left-to-right, so boxes are tight). ``display_rot`` is the clockwise
    degrees needed to map a reading-space box back onto the page as it is
    DISPLAYED (0 for an already-upright page; 90/180/270 for a sideways
    scan). Keeping matching in upright space and rotating only the final
    result boxes means line grouping and the tight/loose margins act on the
    correct axes — otherwise a rotated page's boxes get the gap-bridging
    margin on the wrong axis and look fat.

    When ``auto_orient`` is set and the upright pass looks like it is NOT
    reading real text (few confident words, or a poor confident/total ratio —
    the signature of a sideways scan, which still yields lots of low-confidence
    gibberish), the image is also OCR'd at 90/180/270° and the orientation
    with the most confident words wins (Tesseract OSD breaks ties).
    """
    up = _ocr_pass(img, tesseract_cmd)
    if not auto_orient:
        return _strip_conf(up), 0

    up_good = _good_word_count(up)
    ratio = up_good / len(up) if up else 0.0
    # Confidently reading upright text → fast path, no extra passes / OSD.
    if up_good >= _OCR_ORIENT_MIN_WORDS and ratio >= _OCR_GOOD_RATIO:
        return _strip_conf(up), 0

    osd_rot = _detect_osd_rotation(img, tesseract_cmd)
    scored = [(0, up, up_good)]
    for rot in (90, 180, 270):
        # PIL rotate is counter-clockwise for positive angles; rotate(-rot)
        # turns the image clockwise by ``rot`` to test that orientation.
        words = _ocr_pass(img.rotate(-rot, expand=True), tesseract_cmd)
        scored.append((rot, words, _good_word_count(words)))

    # Most confident orientation wins; ties prefer OSD's pick, then "no
    # rotation" (so a genuinely upright page is never needlessly rotated).
    best_rot, best_words, _ = max(
        scored,
        key=lambda it: (it[2],
                        1 if it[0] and it[0] == osd_rot else 0,
                        1 if it[0] == 0 else 0))
    # Boxes are tight in this reading-upright orientation; the caller maps
    # the final match rects back to display space via display_rot.
    display_rot = (360 - best_rot) % 360 if best_rot else 0
    return _strip_conf(best_words), display_rot


def has_text_layer(pdf_path: str, page_index: int) -> bool:
    """Cheap probe: does the source page carry any extractable words?"""
    import fitz  # type: ignore
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_index)
        for w in page.get_text('words'):
            if (w[4] or '').strip():
                return True
        return False
    finally:
        doc.close()


# ==================== Per-page cache ====================

# Bump when word-extraction behaviour changes so stale per-page caches from
# an older build are not reused (the signature is content-of-ops, not pixels).
_WORDS_SIG_VERSION = 4


def _words_sig(engine: str, ops, ocr_dpi: int, auto_orient: bool) -> str:
    payload = json.dumps([_WORDS_SIG_VERSION, engine,
                          ocr_dpi if engine == 'ocr' else 0,
                          bool(auto_orient) if engine == 'ocr' else False,
                          ops or []], sort_keys=True)
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]


def get_page_words(cache_dir: str, desc, src_path: str, engine: str,
                   ocr_dpi: int = 300, tesseract_cmd: str = '',
                   auto_orient: bool = True):
    """Extract (or load cached) words for one page descriptor.

    ``engine`` must be ``'digital'`` or ``'ocr'`` (resolved by the caller).
    Returns ``(words, display_rot)`` — words in reading-upright fractional
    coords, and the clockwise degrees to map a box back onto the displayed
    page (0 unless an OCR page was auto-rotated; see :func:`extract_words_ocr`).
    """
    ops = desc.get('ops') or []
    sig = _words_sig(engine, ops, ocr_dpi, auto_orient)
    path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"{desc['id']}.json")
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            if cached.get('sig') == sig:
                return cached.get('words') or [], int(cached.get('display_rot') or 0)
        except (OSError, ValueError):
            pass

    if engine == 'digital':
        words = extract_words_digital(src_path, int(desc['page']), ops)
        display_rot = 0
    else:
        from app import pdf_tools
        img = pdf_tools.render_page_image(src_path, int(desc['page']), ops,
                                          dpi=ocr_dpi)
        words, display_rot = extract_words_ocr(img, tesseract_cmd,
                                               auto_orient=auto_orient)

    if path:
        try:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'sig': sig, 'engine': engine, 'words': words,
                           'display_rot': display_rot}, f)
            os.replace(tmp, path)
        except OSError:
            pass
    return words, display_rot


# ==================== Fuzzy phrase matching ====================

def _group_lines(words):
    """Group matched words into text lines (same page assumed). Two words
    share a line when their vertical centres are within ~45% of the taller
    word's height (stricter than half a line so adjacent lines never merge
    into one tall box)."""
    groups = []
    for wd in words:
        h = max(1e-4, wd['y2'] - wd['y1'])
        cy = (wd['y1'] + wd['y2']) / 2.0
        placed = False
        for g in groups:
            gh = max(1e-4, g['y2'] - g['y1'])
            gcy = (g['y1'] + g['y2']) / 2.0
            if abs(cy - gcy) < 0.45 * max(h, gh):
                g['x1'] = min(g['x1'], wd['x1'])
                g['y1'] = min(g['y1'], wd['y1'])
                g['x2'] = max(g['x2'], wd['x2'])
                g['y2'] = max(g['y2'], wd['y2'])
                placed = True
                break
        if not placed:
            groups.append({'x1': wd['x1'], 'y1': wd['y1'],
                           'x2': wd['x2'], 'y2': wd['y2']})
    return groups


def find_matches(page_words, terms, fuzzy: bool = True, threshold: int = 85,
                 case_sensitive: bool = False):
    """Sliding-window phrase search over a multi-page word stream.

    ``page_words`` — ordered list of ``(page_id, words)``.
    ``terms``      — list of search strings (one entry per term).

    Returns ``{page_id: [{'rect': [x1,y1,x2,y2], 'term': index}, ...]}``.
    A window may span a page boundary — the match then emits one box per
    page/line. Matched windows are consumed (no overlapping re-match).
    """
    try:
        from rapidfuzz import fuzz  # type: ignore
    except Exception:
        fuzz = None
        if fuzzy:
            logger.warning('rapidfuzz unavailable — falling back to exact match.')
            fuzzy = False

    stream = []
    for pid, words in page_words:
        for wd in words:
            stream.append((pid, wd))

    results = {pid: [] for pid, _ in page_words}
    threshold = max(0, min(100, int(threshold)))

    for ti, raw_term in enumerate(terms):
        term = (raw_term or '').strip()
        if not term:
            continue
        cmp_term = term if case_sensitive else term.lower()
        tokens = cmp_term.split()
        L = len(tokens)
        if not L or L > len(stream):
            continue
        target = ' '.join(tokens)
        i = 0
        while i <= len(stream) - L:
            seg_words = stream[i:i + L]
            seg = ' '.join(w['text'] for _, w in seg_words)
            if not case_sensitive:
                seg = seg.lower()
            if fuzzy and fuzz is not None:
                ok = fuzz.ratio(seg, target) >= threshold
            else:
                ok = seg == target
            if ok:
                # Group the matched words per page, then per line.
                by_page = {}
                for pid, wd in seg_words:
                    by_page.setdefault(pid, []).append(wd)
                for pid, wds in by_page.items():
                    heights = sorted(w['y2'] - w['y1'] for w in wds)
                    med_h = heights[len(heights) // 2] if heights else 0.01
                    # Horizontal margin bridges inter-word gaps; vertical
                    # margin stays tiny so the box hugs the text line
                    # (digital boxes already include ascender/descender).
                    mx = min(0.01, max(0.001, 0.25 * med_h))
                    my = min(0.004, max(0.0005, 0.06 * med_h))
                    for g in _group_lines(wds):
                        rect = [max(0.0, g['x1'] - mx),
                                max(0.0, g['y1'] - my),
                                min(1.0, g['x2'] + mx),
                                min(1.0, g['y2'] + my)]
                        results.setdefault(pid, []).append(
                            {'rect': rect, 'term': ti})
                i += L
            else:
                i += 1
    return results

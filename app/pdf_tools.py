"""
Shared PDF processing primitives for the **PDF Toolbox** (``app/toolbox/pdf.py``)
and the **Batch PDF Import** (``app/pdf_import.py``).

The unit of work is a **page descriptor** — a pointer to one source-PDF page
plus an ordered list of ops applied in sequence::

    {
      "id":   "<uuid hex>",      # assigned by the toolbox session
      "src":  "<source id>",     # key into the toolbox session's sources
      "page": <int 0-based>,     # page index in that source PDF
      "ops":  [ {op}, ... ]      # applied in order
    }

Ops (``type`` + params)::

    {"type":"rotate",      "deg": 90|180|270}     # vector-safe (90 multiples)
    {"type":"crop",        "box":[x1,y1,x2,y2]}   # vector-safe, fractional 0..1
    {"type":"deskew"}                              # raster-only (NumPy)
    {"type":"rotate_fine", "deg": <float>}         # raster-only (arbitrary angle)
    {"type":"brightness",  "factor": <float>}      # raster-only
    {"type":"contrast",    "factor": <float>}      # raster-only
    {"type":"sharpen",     "factor": <float>}      # raster-only
    {"type":"grayscale"}                           # raster-only
    {"type":"bw",          "threshold": <int>}     # raster-only

A descriptor may also carry an ``annots`` list (redact / highlight / text /
ink marks). Annotation coordinates are **fractional 0..1 in the post-ops
visible page space** (y down)::

    {"id":"..","kind":"redact",    "rect":[x1,y1,x2,y2], "color":"#000000"}
    {"id":"..","kind":"erase",     "rect":[x1,y1,x2,y2]}                    # white redaction
    {"id":"..","kind":"highlight", "rect":[x1,y1,x2,y2], "color":"#ffff00", "opacity":0.4}
    {"id":"..","kind":"text",      "pos":[x,y], "text":"..", "size":0.025, "color":"#d00000"}
    {"id":"..","kind":"ink",       "points":[[x,y],..], "color":"#0000ff", "width":0.004, "opacity":1.0}
    {"id":"..","kind":"image",     "rect":[x1,y1,x2,y2], "data":"data:image/png;base64,.."}

``pending: true`` marks search results awaiting user review (rendered with an
orange outline on thumbnails; excluded from export until accepted).

A page is exported **losslessly** (vector, via pypdf cropbox / ``/Rotate``)
only when its ops are vector-safe (crop-only OR a single rotate, never the two
combined — that hits pypdf's rotation/mediabox coordinate pitfalls). Any
raster-only op, or a crop+rotate combo, forces a rasterise. Preview thumbnails
always rasterise (so the user sees the true result); the thumbnail renderer and
the raster-export renderer share :func:`render_page_image`.

All heavy libs (fitz / PIL / pypdf) are imported lazily so importing this
module never fails on a box that is missing one of them.
"""
from __future__ import annotations

import io
import logging
import zipfile

logger = logging.getLogger(__name__)


# ==================== Op classification ====================

# Ops that cannot be represented in the source PDF's vector space and therefore
# force the page to be rasterised before export.
RASTER_ONLY_OPS = frozenset(
    {'deskew', 'rotate_fine', 'brightness', 'contrast', 'sharpen',
     'grayscale', 'bw'})

# Canonical fractional crop boxes for the left / right half of an A3 sheet.
LEFT_HALF = [0.0, 0.0, 0.5, 1.0]
RIGHT_HALF = [0.5, 0.0, 1.0, 1.0]

SPLIT_MODES = ('none', 'simple', 'mode1', 'mode2')
MODE1_DEFAULT_PAGES_PER_STUDENT = 4
MODE1_MAX_PAGES_PER_STUDENT = 200


def is_vector_safe(ops) -> bool:
    """True when ``ops`` can be applied losslessly in PDF vector space.

    Vector-safe = no raster-only op AND not a crop+rotate combination
    (crop-only, rotate-only, or no ops are all fine).
    """
    types = [o.get('type') for o in (ops or [])]
    if any(t in RASTER_ONLY_OPS for t in types):
        return False
    if 'crop' in types and 'rotate' in types:
        return False
    return True


# ==================== Rasterisation + filters ====================

def rasterize_page(fitz_page, width_px: int = None, dpi: int = None):
    """Rasterise one PyMuPDF page to a PIL RGB Image.

    Supply EITHER ``dpi`` (page-size independent — preferred for export /
    processing, so A4 and A3 render at the same physical quality) OR
    ``width_px`` (fixed pixel width — used for small preview thumbnails).
    Mirrors the zoom/pixmap pattern used across the project so all
    rasterisation looks identical. The page's own ``/Rotate`` is honoured by
    PyMuPDF.
    """
    import fitz  # type: ignore
    from PIL import Image

    if dpi:
        zoom = max(0.05, float(dpi) / 72.0)  # PDF user space is 72 dpi
    else:
        base_width = fitz_page.rect.width or 595.0  # A4 width pts fallback
        zoom = max(0.1, float(width_px or 1700) / base_width)
    pix = fitz_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    return img


def apply_ops(img, ops):
    """Apply an ordered op chain to a PIL Image, returning a new RGB Image."""
    from PIL import Image, ImageEnhance

    out = img if img.mode == 'RGB' else img.convert('RGB')
    for op in (ops or []):
        t = op.get('type')
        if t == 'rotate':
            deg = int(op.get('deg', 0)) % 360
            if deg:
                # PIL rotates counter-clockwise for positive angles; negate so
                # a positive ``deg`` is a clockwise ("rotate right") turn,
                # matching pypdf's clockwise ``/Rotate``.
                out = out.rotate(-deg, expand=True, fillcolor=(255, 255, 255))
        elif t == 'rotate_fine':
            deg = float(op.get('deg', 0.0))
            if abs(deg) > 1e-6:
                out = out.rotate(-deg, resample=Image.BICUBIC, expand=True,
                                 fillcolor=(255, 255, 255))
        elif t == 'crop':
            out = _crop_fractional(out, op.get('box'))
        elif t == 'deskew':
            try:
                from app import pdf_layout
                if pdf_layout.numpy_available():
                    out = pdf_layout.deskew_image(out)
                else:
                    logger.warning('pdf_tools deskew skipped — NumPy unavailable.')
            except Exception as e:  # pragma: no cover — best-effort
                logger.warning('pdf_tools deskew failed: %s', e)
        elif t == 'brightness':
            out = ImageEnhance.Brightness(out).enhance(float(op.get('factor', 1.0)))
        elif t == 'contrast':
            out = ImageEnhance.Contrast(out).enhance(float(op.get('factor', 1.0)))
        elif t == 'sharpen':
            out = ImageEnhance.Sharpness(out).enhance(float(op.get('factor', 1.0)))
        elif t == 'grayscale':
            out = out.convert('L').convert('RGB')
        elif t == 'bw':
            th = max(0, min(255, int(op.get('threshold', 160))))
            g = out.convert('L')
            out = g.point(lambda p, _t=th: 255 if p >= _t else 0).convert('RGB')
    return out


def _crop_fractional(img, box):
    """Crop ``img`` to a fractional ``[x1,y1,x2,y2]`` box (0..1, y from top)."""
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return img
    w, h = img.size
    x1, y1, x2, y2 = (float(v) for v in box)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    left = max(0, min(int(round(x1 * w)), w - 1))
    right = max(left + 1, min(int(round(x2 * w)), w))
    top = max(0, min(int(round(y1 * h)), h - 1))
    bottom = max(top + 1, min(int(round(y2 * h)), h))
    return img.crop((left, top, right, bottom))


# ==================== Annotations (redact / highlight / text / ink) ====================

ANNOT_KINDS = ('redact', 'highlight', 'text', 'ink')

# Default visual parameters (fractions are of page HEIGHT).
ANNOT_DEFAULT_HL_OPACITY = 0.4
ANNOT_DEFAULT_TEXT_SIZE = 0.025
ANNOT_DEFAULT_INK_WIDTH = 0.004


def hex_to_rgb(color, default=(0, 0, 0)):
    """``#rrggbb`` (or ``#rgb``) → (r, g, b) ints, falling back to ``default``."""
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        try:
            return tuple(max(0, min(255, int(c))) for c in color[:3])
        except (TypeError, ValueError):
            return default
    if not isinstance(color, str):
        return default
    s = color.strip().lstrip('#')
    if len(s) == 3:
        s = ''.join(ch * 2 for ch in s)
    if len(s) != 6:
        return default
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _annot_rect_px(rect, w, h):
    """Fractional ``[x1,y1,x2,y2]`` → integer pixel box, or None if degenerate."""
    if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in rect)
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    box = (int(round(x1 * w)), int(round(y1 * h)),
           int(round(x2 * w)), int(round(y2 * h)))
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        return None
    return box


# Text-annotation font families: token -> candidate TTF files (raster path)
# and PDF base-14 font name (digital path in _fitz_annotated_page_bytes).
ANNOT_FONT_FILES = {
    'sans': ('arial.ttf', 'DejaVuSans.ttf', 'segoeui.ttf'),
    'serif': ('times.ttf', 'DejaVuSerif.ttf', 'georgia.ttf'),
    'mono': ('cour.ttf', 'DejaVuSansMono.ttf', 'consola.ttf'),
}
ANNOT_FONT_PDF = {'sans': 'helv', 'serif': 'tiro', 'mono': 'cour'}


def _annot_font(px, family: str = 'sans'):
    """Best-effort scalable font for burned-in text annotations."""
    from PIL import ImageFont
    px = max(6, int(px))
    names = ANNOT_FONT_FILES.get(family) or ANNOT_FONT_FILES['sans']
    for name in names + ANNOT_FONT_FILES['sans']:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)  # Pillow >= 10.1
    except TypeError:  # pragma: no cover — very old Pillow
        return ImageFont.load_default()


def _annot_image_bytes(a):
    """Decode an image annotation's data URL → raw bytes (None on failure)."""
    import base64

    data = a.get('data')
    if not isinstance(data, str) or not data.startswith('data:image/'):
        return None
    try:
        return base64.b64decode(data.split(',', 1)[1])
    except (ValueError, IndexError):
        return None


def apply_annotations(img, annots):
    """Composite an ``annots`` list onto a PIL Image (post-ops space).

    Returns a new RGB Image; the input is unchanged. Pending annotations get
    an orange outline so search results are recognisable on thumbnails.
    """
    if not annots:
        return img
    from PIL import Image, ImageDraw

    base = img.convert('RGBA')
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = base.size
    pend_w = max(2, h // 500)

    for a in annots:
        if not isinstance(a, dict):
            continue
        kind = a.get('kind')
        rgb = hex_to_rgb(a.get('color'),
                         (0, 0, 0) if kind == 'redact' else
                         (255, 255, 0) if kind == 'highlight' else (208, 0, 0))
        outline_box = None
        if kind == 'redact':
            box = _annot_rect_px(a.get('rect'), w, h)
            if not box:
                continue
            draw.rectangle(box, fill=rgb + (255,))
            outline_box = box
        elif kind == 'erase':
            # Content removal: a plain white box (page background).
            box = _annot_rect_px(a.get('rect'), w, h)
            if not box:
                continue
            draw.rectangle(box, fill=(255, 255, 255, 255))
            outline_box = box
        elif kind == 'highlight':
            box = _annot_rect_px(a.get('rect'), w, h)
            if not box:
                continue
            try:
                op = float(a.get('opacity', ANNOT_DEFAULT_HL_OPACITY))
            except (TypeError, ValueError):
                op = ANNOT_DEFAULT_HL_OPACITY
            op = max(0.05, min(1.0, op))
            draw.rectangle(box, fill=rgb + (int(op * 255),))
            outline_box = box
        elif kind == 'text':
            pos = a.get('pos')
            text = str(a.get('text') or '')
            if not (isinstance(pos, (list, tuple)) and len(pos) == 2 and text):
                continue
            try:
                x, y = float(pos[0]) * w, float(pos[1]) * h
                size = float(a.get('size', ANNOT_DEFAULT_TEXT_SIZE)) * h
            except (TypeError, ValueError):
                continue
            font = _annot_font(size, a.get('font', 'sans'))
            # Per-line drawing with 1.2em spacing — matches the editor (Konva)
            # and the digital export.
            bounds = None
            for li, line in enumerate(text.split('\n')):
                if not line.strip():
                    continue
                ly = y + 1.2 * size * li
                draw.text((x, ly), line, fill=rgb + (255,), font=font)
                tb = draw.textbbox((x, ly), line, font=font)
                bounds = tb if bounds is None else (
                    min(bounds[0], tb[0]), min(bounds[1], tb[1]),
                    max(bounds[2], tb[2]), max(bounds[3], tb[3]))
            if a.get('pending') and bounds:
                outline_box = bounds
        elif kind == 'image':
            box = _annot_rect_px(a.get('rect'), w, h)
            raw = _annot_image_bytes(a)
            if not box or not raw:
                continue
            try:
                import io as _io
                stamp = Image.open(_io.BytesIO(raw)).convert('RGBA')
                bw = max(1, int(box[2] - box[0]))
                bh = max(1, int(box[3] - box[1]))
                stamp = stamp.resize((bw, bh), Image.LANCZOS)
                overlay.paste(stamp, (int(box[0]), int(box[1])), stamp)
            except Exception:
                continue
            outline_box = box
        elif kind == 'ink':
            pts = a.get('points')
            if not (isinstance(pts, (list, tuple)) and len(pts) >= 2):
                continue
            try:
                px = [(float(p[0]) * w, float(p[1]) * h) for p in pts]
                lw = max(1, int(round(float(a.get('width', ANNOT_DEFAULT_INK_WIDTH)) * h)))
                op = float(a.get('opacity', 1.0))
            except (TypeError, ValueError, IndexError):
                continue
            op = max(0.05, min(1.0, op))
            draw.line(px, fill=rgb + (int(op * 255),), width=lw, joint='curve')
            # Round the stroke ends.
            r = lw / 2.0
            for cx, cy in (px[0], px[-1]):
                draw.ellipse((cx - r, cy - r, cx + r, cy + r),
                             fill=rgb + (int(op * 255),))
            if a.get('pending'):
                xs = [p[0] for p in px]
                ys = [p[1] for p in px]
                outline_box = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)
        else:
            continue

        if a.get('pending') and outline_box:
            draw.rectangle(outline_box, outline=(255, 140, 0, 255), width=pend_w)

    return Image.alpha_composite(base, overlay).convert('RGB')


# ==================== A3 split / reorder descriptors ====================

def mode1_pages_per_student(value=None) -> int:
    """Clamp the Mode-1 pages-per-student setting to a practical range."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = MODE1_DEFAULT_PAGES_PER_STUDENT
    return max(1, min(MODE1_MAX_PAGES_PER_STUDENT, n))


def _mode1_chunk_order(start: int, scan_pages: int, _crop):
    """Reading order for one folded booklet chunk of ``scan_pages`` A3 sides."""
    out = []
    for off in range(scan_pages):
        use_right = (off % 2 == 0)
        out.append({'page': start + off,
                    'ops': _crop(RIGHT_HALF if use_right else LEFT_HALF)})
    for off in range(scan_pages - 1, -1, -1):
        use_right = (off % 2 == 1)
        out.append({'page': start + off,
                    'ops': _crop(RIGHT_HALF if use_right else LEFT_HALF)})
    return out


def split_descriptors(num_pages: int, mode: str,
                      mode1_pages_per_student_value=None):
    """Return a list of ``{page, ops}`` fragments for ``mode``.

    ``ops`` here only ever holds the half-page crop (or nothing). The caller
    layers any pre-rotate / filter ops around these. Modes:

    * ``none``   — one fragment per page, no crop.
    * ``simple`` — split every A3 page down the middle: left then right.
    * ``mode1``  — folded individual copies/booklets. ``pages per student``
      defaults to 4, so the legacy two-A3-side chunk still emits
      ``p(i)_R, p(i+1)_L, p(i+1)_R, p(i)_L``. Other counts use
      ``ceil(pages/2)`` A3 sides per student and drop padded trailing halves
      from each chunk (for example, 7 keeps pages 1-7 and skips page 8).
    * ``mode2``  — destapled A4 booklet stack: reorder the split halves back
      into chronological reading order.
    """
    mode = (mode or 'none').strip().lower()
    n = max(0, int(num_pages))

    def _crop(box):
        return [{'type': 'crop', 'box': list(box)}]

    if mode == 'simple':
        out = []
        for i in range(n):
            out.append({'page': i, 'ops': _crop(LEFT_HALF)})
            out.append({'page': i, 'ops': _crop(RIGHT_HALF)})
        return out

    if mode == 'mode1':
        out = []
        pages_per_student = mode1_pages_per_student(
            mode1_pages_per_student_value)
        scan_pages_per_student = (pages_per_student + 1) // 2
        i = 0
        while i + scan_pages_per_student <= n:
            out.extend(_mode1_chunk_order(
                i, scan_pages_per_student, _crop)[:pages_per_student])
            i += scan_pages_per_student
        if i < n:
            logger.warning(
                'split_descriptors mode1: %s trailing A3 page(s) do not make '
                'a complete %s-page student chunk; dropping them.',
                n - i, pages_per_student)
        return out

    if mode == 'mode2':
        total = n * 2
        ordered = [None] * total
        for i in range(n):
            left = {'page': i, 'ops': _crop(LEFT_HALF)}
            right = {'page': i, 'ops': _crop(RIGHT_HALF)}
            if i % 2 == 0:
                ordered[total - i - 1] = left
                ordered[i] = right
            else:
                ordered[i] = left
                ordered[total - i - 1] = right
        return [f for f in ordered if f is not None]

    # 'none' / unknown
    return [{'page': i, 'ops': []} for i in range(n)]


def build_op_chain(pre_rotate: int, frag_ops, filters):
    """Compose the full ordered op chain for one fragment.

    Order: pre-rotate (whole page) → split crop → image filters. ``filters`` is
    a dict like ``{deskew, brightness, contrast, sharpen, grayscale, bw}``.
    """
    chain = []
    pr = int(pre_rotate or 0) % 360
    if pr:
        chain.append({'type': 'rotate', 'deg': pr})
    chain.extend(frag_ops or [])
    chain.extend(filters_to_ops(filters))
    return chain


def filters_to_ops(filters):
    """Translate a filters dict into raster ops (skipping no-op values)."""
    ops = []
    f = filters or {}
    if f.get('deskew'):
        ops.append({'type': 'deskew'})
    fine = float(f.get('rotate_fine', 0) or 0)
    if abs(fine) > 1e-6:
        ops.append({'type': 'rotate_fine', 'deg': fine})
    for key, op_type in (('brightness', 'brightness'),
                         ('contrast', 'contrast'),
                         ('sharpen', 'sharpen')):
        val = f.get(key)
        if val is not None and abs(float(val) - 1.0) > 1e-6:
            ops.append({'type': op_type, 'factor': float(val)})
    if f.get('grayscale'):
        ops.append({'type': 'grayscale'})
    if f.get('bw'):
        ops.append({'type': 'bw',
                    'threshold': int(f.get('bw_threshold', 160) or 160)})
    return ops


# ==================== Single-page render (thumbnails + raster export) ====================

def render_page_image(pdf_path: str, page_index: int, ops, width_px: int = None,
                      dpi: int = None, annots=None):
    """Open ``pdf_path``, rasterise page ``page_index`` (by ``dpi`` or
    ``width_px``) and apply ``ops``. Returns a PIL RGB Image. Used by the
    thumbnail route (width) and the raster export path (dpi). ``annots`` (if
    given) are composited on top after the op chain."""
    import fitz  # type: ignore

    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError('page index out of range')
        page = doc.load_page(page_index)
        img = rasterize_page(page, width_px=width_px, dpi=dpi)
    finally:
        doc.close()
    img = apply_ops(img, ops)
    if annots:
        img = apply_annotations(img, annots)
    return img


def process_pdf_to_images(pdf_path: str, width_px: int, pre_rotate: int = 0,
                          split_mode: str = 'none', filters=None,
                          mode1_pages_per_student_value=None):
    """Rasterise + (optionally) rotate / split / filter every page of a PDF.

    Returns a list of PIL RGB Images in final reading order. Used by the Batch
    PDF Import staging step so its scanned pages can be pre-processed with the
    same primitives as the Toolbox.
    """
    import fitz  # type: ignore

    doc = fitz.open(pdf_path)
    try:
        page_count = doc.page_count
        frags = split_descriptors(page_count, split_mode,
                                  mode1_pages_per_student_value)
        images = []
        for frag in frags:
            ops = build_op_chain(pre_rotate, frag['ops'], filters)
            page = doc.load_page(frag['page'])
            img = rasterize_page(page, width_px)
            images.append(apply_ops(img, ops))
        return images
    finally:
        doc.close()


# ==================== Export (hybrid vector / raster) ====================

def _pil_image_to_pdf_reader(img, dpi: float = None):
    """Save a PIL Image as a one-page PDF at ``dpi`` (so the page lands at a
    sensible physical size) and return a fresh pypdf PdfReader over its bytes.
    When ``dpi`` is None, fall back to assuming the image is ~A4 wide."""
    import pypdf  # type: ignore

    buf = io.BytesIO()
    res = float(dpi) if dpi else max(72.0, img.size[0] / 8.27)
    img.save(buf, format='PDF', resolution=res)
    return pypdf.PdfReader(io.BytesIO(buf.getvalue()))


def _apply_vector_crop(page, box):
    """Set a pypdf page's media/crop box to the fractional ``box`` (origin
    top-left, y downward). Assumes the page has no inherent ``/Rotate``."""
    mb = page.mediabox
    lx, by = float(mb.left), float(mb.bottom)
    rx, ty = float(mb.right), float(mb.top)
    w, h = rx - lx, ty - by
    x1, y1, x2, y2 = (float(v) for v in box)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    new_left = lx + x1 * w
    new_right = lx + x2 * w
    new_top = ty - y1 * h
    new_bottom = ty - y2 * h
    page.mediabox.lower_left = (new_left, new_bottom)
    page.mediabox.upper_right = (new_right, new_top)
    page.cropbox.lower_left = (new_left, new_bottom)
    page.cropbox.upper_right = (new_right, new_top)


def export_pages(pages, resolve_path, fmt: str = 'pdf', default_dpi: int = 200,
                 split_every=None, output: str = 'digital',
                 compress=None) -> bytes:
    """Assemble ``pages`` (page descriptors) into a downloadable artefact.

    ``resolve_path(src_id) -> abs pdf path``. Each descriptor may carry its own
    ``dpi`` (chosen when the page was added); ``default_dpi`` is the fallback
    for pages without one. ``fmt``:

    * ``'pdf'``  — one combined PDF (hybrid vector/raster). When ``split_every``
      is a positive int, instead returns a ZIP of N-page PDFs (Mode-1
      per-student split).
    * ``'zip'``  — a ZIP of per-page PNGs.

    ``output``:

    * ``'digital'`` (default) — vector pages stay vector. Annotated vector-safe
      pages go through PyMuPDF: redactions are applied with
      ``apply_redactions`` (the underlying text/images are truly removed) and
      highlights / text / ink are drawn on top of the still-digital page.
    * ``'image'`` — every page is rasterised (annotations burned in), so
      redacted content is removed at the pixel level and cannot be recovered.

    Pending annotations (unreviewed Find & Mark results) are excluded — only
    accepted marks are exported.

    ``compress`` (PDF outputs only) is ``None`` or a dict:
    ``{'preset': 'light'|'medium'|'strong'}`` recompresses/downsamples images
    once; ``{'target_bytes': int}`` retries down a quality ladder until the
    file fits (best effort — per part for split ZIPs). See
    :func:`compress_pdf_bytes`.

    Falls back to an all-raster PDF when pypdf is unavailable.
    """
    pages = [_drop_pending_annots(p) for p in (pages or [])]
    if not pages:
        raise ValueError('no pages to export')

    force_raster = (output or 'digital').strip().lower() == 'image'

    if fmt == 'zip':
        return _export_png_zip(pages, resolve_path, default_dpi)

    try:
        import pypdf  # type: ignore  # noqa: F401
        have_pypdf = True
    except Exception:
        have_pypdf = False

    if not have_pypdf:
        logger.warning('pypdf unavailable — exporting an all-raster PDF.')
        return compress_pdf_bytes(
            _export_raster_pdf(pages, resolve_path, default_dpi), compress)

    if split_every and int(split_every) > 0:
        return _export_split_pdf_zip(pages, resolve_path, default_dpi,
                                     int(split_every), force_raster, compress)

    writer = _new_writer()
    src_bytes_cache: dict = {}
    for desc in pages:
        _add_page(writer, desc, resolve_path, default_dpi, src_bytes_cache,
                  force_raster=force_raster)
    buf = io.BytesIO()
    writer.write(buf)
    return compress_pdf_bytes(buf.getvalue(), compress)


# Image-recompression settings per preset (PyMuPDF rewrite_images semantics:
# images whose effective DPI exceeds ``dpi_threshold`` are resampled to
# ``dpi_target``; ``quality`` is the JPEG quality; threshold None = never
# resample, recompress only).
COMPRESS_PRESETS = {
    'light':  {'dpi_threshold': None, 'dpi_target': 0, 'quality': 80},
    'medium': {'dpi_threshold': 200, 'dpi_target': 150, 'quality': 65},
    'strong': {'dpi_threshold': 150, 'dpi_target': 100, 'quality': 40},
}

# Quality ladder for target-size mode, mildest first.
_COMPRESS_SIZE_LADDER = (
    {'dpi_threshold': None, 'dpi_target': 0, 'quality': 85},
    {'dpi_threshold': 250, 'dpi_target': 200, 'quality': 75},
    {'dpi_threshold': 200, 'dpi_target': 150, 'quality': 65},
    {'dpi_threshold': 150, 'dpi_target': 120, 'quality': 50},
    {'dpi_threshold': 120, 'dpi_target': 96, 'quality': 40},
    {'dpi_threshold': 100, 'dpi_target': 72, 'quality': 25},
)


def _rewrite_pdf_images(blob: bytes, params: dict) -> bytes:
    """One recompression pass over ``blob`` with rewrite_images ``params``."""
    import fitz  # type: ignore

    doc = fitz.open(stream=blob, filetype='pdf')
    try:
        doc.rewrite_images(dpi_threshold=params.get('dpi_threshold'),
                           dpi_target=params.get('dpi_target') or 0,
                           quality=int(params.get('quality') or 0))
        return doc.tobytes(deflate=True, garbage=4)
    finally:
        doc.close()


def compress_pdf_bytes(blob: bytes, compress) -> bytes:
    """Optionally shrink a PDF by recompressing / downsampling its images.

    ``compress`` is ``None`` (no-op), ``{'preset': name}`` (one pass with
    :data:`COMPRESS_PRESETS`) or ``{'target_bytes': n}`` (walk the quality
    ladder until the result fits, best effort). Never returns something
    larger than the input; failures fall back to the original bytes.
    """
    if not compress or not isinstance(compress, dict):
        return blob

    target = compress.get('target_bytes')
    if target:
        target = int(target)
        if len(blob) <= target:
            return blob
        best = blob
        for params in _COMPRESS_SIZE_LADDER:
            try:
                out = _rewrite_pdf_images(blob, params)
            except Exception as e:
                logger.warning('PDF compression pass failed: %s', e)
                continue
            if len(out) < len(best):
                best = out
            if len(best) <= target:
                break
        if len(best) > target:
            logger.info('PDF compression: target %d bytes not reached '
                        '(best %d bytes).', target, len(best))
        return best

    preset = COMPRESS_PRESETS.get(str(compress.get('preset') or '').lower())
    if not preset:
        return blob
    try:
        out = _rewrite_pdf_images(blob, preset)
    except Exception as e:
        logger.warning('PDF compression failed: %s', e)
        return blob
    return out if len(out) < len(blob) else blob


def _drop_pending_annots(desc):
    """Copy of ``desc`` without pending (unreviewed) annotations."""
    annots = desc.get('annots') or []
    kept = [a for a in annots if not (isinstance(a, dict) and a.get('pending'))]
    if len(kept) == len(annots):
        return desc
    out = dict(desc)
    out['annots'] = kept
    return out


def _page_dpi(desc, default_dpi: int) -> int:
    """Resolution for one page descriptor (its own ``dpi`` or the fallback)."""
    try:
        d = int(desc.get('dpi') or 0)
    except (TypeError, ValueError):
        d = 0
    return d if d > 0 else int(default_dpi or 200)


def _new_writer():
    import pypdf  # type: ignore
    return pypdf.PdfWriter()


def _src_bytes(resolve_path, src_id, cache):
    if src_id not in cache:
        with open(resolve_path(src_id), 'rb') as f:
            cache[src_id] = f.read()
    return cache[src_id]


def _add_page(writer, desc, resolve_path, default_dpi, src_bytes_cache,
              force_raster: bool = False):
    """Add one descriptor to ``writer`` as a vector page when safe, else as a
    rasterised image page (at the page's chosen DPI). Annotated vector-safe
    pages are routed through PyMuPDF (true redaction + vector overlays) unless
    ``force_raster`` requests the flattened image output."""
    import pypdf  # type: ignore

    ops = desc.get('ops') or []
    annots = desc.get('annots') or []
    crop = next((o for o in ops if o.get('type') == 'crop'), None)
    rotates = [o for o in ops if o.get('type') == 'rotate']

    if not force_raster and is_vector_safe(ops):
        if annots:
            try:
                data = _fitz_annotated_page_bytes(resolve_path(desc['src']),
                                                  int(desc['page']), ops, annots)
                reader = pypdf.PdfReader(io.BytesIO(data))
                writer.add_page(reader.pages[0])
                return
            except _RasterFallback:
                pass
            except Exception as e:  # pragma: no cover — fall back to raster
                logger.warning('annotated vector add failed for %s p%s (%s) — '
                               'rasterising.', desc.get('src'),
                               desc.get('page'), e)
        else:
            try:
                data = _src_bytes(resolve_path, desc['src'], src_bytes_cache)
                reader = pypdf.PdfReader(io.BytesIO(data))
                page = reader.pages[int(desc['page'])]
                inherent_rot = int(page.rotation or 0) % 360
                if crop and inherent_rot != 0:
                    # Cropping a page that already carries a /Rotate is
                    # ambiguous in mediabox space — rasterise to stay correct.
                    raise _RasterFallback()
                if crop:
                    _apply_vector_crop(page, crop['box'])
                for r in rotates:
                    page.rotate(int(r.get('deg', 0)))
                writer.add_page(page)
                return
            except _RasterFallback:
                pass
            except Exception as e:  # pragma: no cover — fall back to raster
                logger.warning('vector add failed for %s p%s (%s) — rasterising.',
                               desc.get('src'), desc.get('page'), e)

    dpi = _page_dpi(desc, default_dpi)
    img = render_page_image(resolve_path(desc['src']), int(desc['page']),
                            ops, dpi=dpi, annots=annots)
    reader = _pil_image_to_pdf_reader(img, dpi=dpi)
    writer.add_page(reader.pages[0])


def _fitz_annotated_page_bytes(pdf_path: str, page_index: int, ops,
                               annots) -> bytes:
    """Build a one-page PDF with ``ops`` (vector-safe only) applied via
    PyMuPDF geometry and ``annots`` rendered digitally.

    Redactions use ``add_redact_annot`` + ``apply_redactions`` so the covered
    text/images are **removed from the content stream**, not just hidden.
    Highlights / ink / text are drawn as vector overlays. Annotation coords
    are fractional in the final visible page space; drawing commands need the
    unrotated space, so everything is mapped through ``derotation_matrix``.
    """
    import fitz  # type: ignore

    src = fitz.open(pdf_path)
    try:
        nd = fitz.open()
        nd.insert_pdf(src, from_page=page_index, to_page=page_index)
    finally:
        src.close()
    try:
        pg = nd[0]

        crop = next((o for o in ops if o.get('type') == 'crop'), None)
        deg = sum(int(o.get('deg', 0)) for o in ops
                  if o.get('type') == 'rotate') % 360
        if crop:
            if int(pg.rotation or 0) % 360 != 0:
                raise _RasterFallback()  # parity with the pypdf rule
            b = crop.get('box') or [0, 0, 1, 1]
            x1, x2 = sorted((float(b[0]), float(b[2])))
            y1, y2 = sorted((float(b[1]), float(b[3])))
            r0 = pg.rect
            new_box = fitz.Rect(r0.x0 + x1 * r0.width, r0.y0 + y1 * r0.height,
                                r0.x0 + x2 * r0.width, r0.y0 + y2 * r0.height)
            pg.set_cropbox(new_box)
        if deg:
            pg.set_rotation((int(pg.rotation or 0) + deg) % 360)

        vr = pg.rect  # visible (rotation-aware) rect
        dmat = pg.derotation_matrix

        def _vis_rect(fr):
            fx1, fx2 = sorted((float(fr[0]), float(fr[2])))
            fy1, fy2 = sorted((float(fr[1]), float(fr[3])))
            r = fitz.Rect(vr.x0 + fx1 * vr.width, vr.y0 + fy1 * vr.height,
                          vr.x0 + fx2 * vr.width, vr.y0 + fy2 * vr.height)
            r = r * dmat
            r.normalize()
            return r

        def _vis_point(fx, fy):
            return fitz.Point(vr.x0 + float(fx) * vr.width,
                              vr.y0 + float(fy) * vr.height) * dmat

        def _rgb01(color, default):
            return tuple(c / 255.0 for c in hex_to_rgb(color, default))

        # 1. True redaction (content removal) first. ``erase`` is redaction
        #    with a white (page-background) fill — content is removed without
        #    leaving a visible censor box.
        redacted = False
        for a in annots:
            kind = a.get('kind')
            if kind not in ('redact', 'erase'):
                continue
            rect = a.get('rect')
            if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
                continue
            fill = ((1, 1, 1) if kind == 'erase'
                    else _rgb01(a.get('color'), (0, 0, 0)))
            pg.add_redact_annot(_vis_rect(rect), fill=fill)
            redacted = True
        if redacted:
            pg.apply_redactions()
            # Redacting image-based (scanned) pages re-encodes the page image
            # as raw/flate, which can inflate a 1 MB scan to tens of MB.
            # Recompress to JPEG (no resampling) to keep the size sane.
            try:
                nd.rewrite_images(quality=85)
            except Exception:  # pragma: no cover — older PyMuPDF
                pass

        # 2. Vector overlays.
        for a in annots:
            kind = a.get('kind')
            if kind == 'image':
                rect = a.get('rect')
                raw = _annot_image_bytes(a)
                if not raw or not (isinstance(rect, (list, tuple))
                                   and len(rect) == 4):
                    continue
                try:
                    pg.insert_image(_vis_rect(rect), stream=raw,
                                    rotate=int(pg.rotation or 0),
                                    overlay=True, keep_proportion=False)
                except Exception as e:
                    logger.warning('image annot insert failed: %s', e)
            elif kind == 'highlight':
                rect = a.get('rect')
                if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
                    continue
                try:
                    op = float(a.get('opacity', ANNOT_DEFAULT_HL_OPACITY))
                except (TypeError, ValueError):
                    op = ANNOT_DEFAULT_HL_OPACITY
                pg.draw_rect(_vis_rect(rect),
                             fill=_rgb01(a.get('color'), (255, 255, 0)),
                             color=None, fill_opacity=max(0.05, min(1.0, op)),
                             overlay=True)
            elif kind == 'ink':
                pts = a.get('points')
                if not (isinstance(pts, (list, tuple)) and len(pts) >= 2):
                    continue
                try:
                    fpts = [_vis_point(p[0], p[1]) for p in pts]
                    width = max(0.2, float(a.get('width', ANNOT_DEFAULT_INK_WIDTH))
                                * vr.height)
                    op = max(0.05, min(1.0, float(a.get('opacity', 1.0))))
                except (TypeError, ValueError, IndexError):
                    continue
                pg.draw_polyline(fpts, color=_rgb01(a.get('color'), (0, 0, 255)),
                                 width=width, lineCap=1, lineJoin=1,
                                 stroke_opacity=op, overlay=True)
            elif kind == 'text':
                pos = a.get('pos')
                text = str(a.get('text') or '')
                if not (isinstance(pos, (list, tuple)) and len(pos) == 2
                        and text):
                    continue
                try:
                    size = float(a.get('size', ANNOT_DEFAULT_TEXT_SIZE)) * vr.height
                except (TypeError, ValueError):
                    continue
                size = max(4.0, size)
                base_font = ANNOT_FONT_PDF.get(a.get('font'), 'helv')
                color = _rgb01(a.get('color'), (208, 0, 0))
                rot = int(pg.rotation or 0)
                # ``pos`` is the text's top-left; insert_text wants a baseline.
                # Multi-line text is drawn line by line (1.2 line height, the
                # same as Konva's default in the editor).
                for li, line in enumerate(text.split('\n')):
                    if not line.strip():
                        continue
                    pt = _vis_point(
                        float(pos[0]),
                        float(pos[1]) + (0.8 + 1.2 * li) * size / vr.height)
                    try:
                        line.encode('latin-1')
                        fontname = base_font
                    except UnicodeEncodeError:
                        fontname = 'china-t'  # built-in CJK fallback
                    pg.insert_text(pt, line, fontsize=size, fontname=fontname,
                                   color=color, rotate=rot, overlay=True)

        return nd.tobytes(deflate=True, garbage=3)
    finally:
        nd.close()


class _RasterFallback(Exception):
    """Internal signal: this page must be rasterised, not added as vector."""


def _export_raster_pdf(pages, resolve_path, default_dpi) -> bytes:
    """All-raster PDF via Pillow (pypdf-free fallback)."""
    from PIL import Image  # noqa: F401

    imgs, first_dpi = [], None
    for d in pages:
        dpi = _page_dpi(d, default_dpi)
        if first_dpi is None:
            first_dpi = dpi
        imgs.append(render_page_image(resolve_path(d['src']), int(d['page']),
                                      d.get('ops') or [], dpi=dpi,
                                      annots=d.get('annots')))
    buf = io.BytesIO()
    imgs[0].save(buf, format='PDF', save_all=True, append_images=imgs[1:],
                 resolution=float(first_dpi or 200))
    return buf.getvalue()


def _export_png_zip(pages, resolve_path, default_dpi) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, desc in enumerate(pages):
            img = render_page_image(resolve_path(desc['src']),
                                    int(desc['page']), desc.get('ops') or [],
                                    dpi=_page_dpi(desc, default_dpi),
                                    annots=desc.get('annots'))
            img_buf = io.BytesIO()
            img.save(img_buf, format='PNG')
            zf.writestr(f'page_{i + 1:03d}.png', img_buf.getvalue())
    return buf.getvalue()


def _export_split_pdf_zip(pages, resolve_path, default_dpi, split_every,
                          force_raster: bool = False, compress=None) -> bytes:
    """ZIP of PDFs, ``split_every`` pages each (Mode-1 per-student output).

    ``compress`` applies to each part (a size target is per file)."""
    buf = io.BytesIO()
    src_bytes_cache: dict = {}
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        idx = 0
        chunk = 0
        while idx < len(pages):
            chunk += 1
            group = pages[idx:idx + split_every]
            writer = _new_writer()
            for desc in group:
                _add_page(writer, desc, resolve_path, default_dpi,
                          src_bytes_cache, force_raster=force_raster)
            pdf_buf = io.BytesIO()
            writer.write(pdf_buf)
            zf.writestr(f'part_{chunk:03d}.pdf',
                        compress_pdf_bytes(pdf_buf.getvalue(), compress))
            idx += split_every
    return buf.getvalue()

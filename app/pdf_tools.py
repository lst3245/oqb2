"""
Shared PDF processing primitives for the **PDF Toolbox** (``app/toolbox.py``)
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

def rasterize_page(fitz_page, width_px: int):
    """Rasterise one PyMuPDF page to a PIL RGB Image at ``width_px`` wide.

    Mirrors the zoom/pixmap pattern used across the project
    (``pdf_import.rasterize_pdf`` / ``batch_image_gen._pdf_to_cropped_images``)
    so all rasterisation looks identical. The page's own ``/Rotate`` is
    honoured by PyMuPDF.
    """
    import fitz  # type: ignore
    from PIL import Image

    base_width = fitz_page.rect.width or 595.0  # A4 width pts fallback
    zoom = max(0.1, float(width_px) / base_width)
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


# ==================== A3 split / reorder descriptors ====================

def split_descriptors(num_pages: int, mode: str):
    """Return a list of ``{page, ops}`` fragments for ``mode``.

    ``ops`` here only ever holds the half-page crop (or nothing). The caller
    layers any pre-rotate / filter ops around these. Modes:

    * ``none``   — one fragment per page, no crop.
    * ``simple`` — split every A3 page down the middle: left then right.
    * ``mode1``  — folded individual copies (2 A3 sheets → 4 pages/student):
      per chunk ``(i, i+1)`` emit ``p(i)_R, p(i+1)_L, p(i+1)_R, p(i)_L``.
      A trailing odd page is dropped (warned).
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
        i = 0
        while i + 1 < n:
            out.append({'page': i,     'ops': _crop(RIGHT_HALF)})  # page 1
            out.append({'page': i + 1, 'ops': _crop(LEFT_HALF)})   # page 2
            out.append({'page': i + 1, 'ops': _crop(RIGHT_HALF)})  # page 3
            out.append({'page': i,     'ops': _crop(LEFT_HALF)})   # page 4
            i += 2
        if n % 2 == 1:
            logger.warning('split_descriptors mode1: odd page count (%s) — '
                           'dropping trailing page.', n)
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

def render_page_image(pdf_path: str, page_index: int, ops, width_px: int):
    """Open ``pdf_path``, rasterise page ``page_index`` at ``width_px`` and
    apply ``ops``. Returns a PIL RGB Image. Used by the thumbnail route and the
    raster export path."""
    import fitz  # type: ignore

    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError('page index out of range')
        page = doc.load_page(page_index)
        img = rasterize_page(page, width_px)
    finally:
        doc.close()
    return apply_ops(img, ops)


def process_pdf_to_images(pdf_path: str, width_px: int, pre_rotate: int = 0,
                          split_mode: str = 'none', filters=None):
    """Rasterise + (optionally) rotate / split / filter every page of a PDF.

    Returns a list of PIL RGB Images in final reading order. Used by the Batch
    PDF Import staging step so its scanned pages can be pre-processed with the
    same primitives as the Toolbox.
    """
    import fitz  # type: ignore

    doc = fitz.open(pdf_path)
    try:
        page_count = doc.page_count
        frags = split_descriptors(page_count, split_mode)
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

def _pil_image_to_pdf_reader(img):
    """Save a PIL Image as a one-page PDF (sized ~A4-width) and return a fresh
    pypdf PdfReader over its bytes."""
    import pypdf  # type: ignore

    buf = io.BytesIO()
    # ~8.27in (A4 width) so pages land at a sensible physical size regardless
    # of pixel width.
    res = max(72.0, img.size[0] / 8.27)
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


def export_pages(pages, resolve_path, fmt: str = 'pdf', width_px: int = 2200,
                 split_every=None) -> bytes:
    """Assemble ``pages`` (page descriptors) into a downloadable artefact.

    ``resolve_path(src_id) -> abs pdf path``. ``fmt``:

    * ``'pdf'``  — one combined PDF (hybrid vector/raster). When ``split_every``
      is a positive int, instead returns a ZIP of N-page PDFs (Mode-1
      per-student split).
    * ``'zip'``  — a ZIP of per-page PNGs.

    Falls back to an all-raster PDF when pypdf is unavailable.
    """
    pages = list(pages or [])
    if not pages:
        raise ValueError('no pages to export')

    if fmt == 'zip':
        return _export_png_zip(pages, resolve_path, width_px)

    try:
        import pypdf  # type: ignore  # noqa: F401
        have_pypdf = True
    except Exception:
        have_pypdf = False

    if not have_pypdf:
        logger.warning('pypdf unavailable — exporting an all-raster PDF.')
        return _export_raster_pdf(pages, resolve_path, width_px)

    if split_every and int(split_every) > 0:
        return _export_split_pdf_zip(pages, resolve_path, width_px,
                                     int(split_every))

    writer = _new_writer()
    src_bytes_cache: dict = {}
    for desc in pages:
        _add_page(writer, desc, resolve_path, width_px, src_bytes_cache)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _new_writer():
    import pypdf  # type: ignore
    return pypdf.PdfWriter()


def _src_bytes(resolve_path, src_id, cache):
    if src_id not in cache:
        with open(resolve_path(src_id), 'rb') as f:
            cache[src_id] = f.read()
    return cache[src_id]


def _add_page(writer, desc, resolve_path, width_px, src_bytes_cache):
    """Add one descriptor to ``writer`` as a vector page when safe, else as a
    rasterised image page."""
    import pypdf  # type: ignore

    ops = desc.get('ops') or []
    crop = next((o for o in ops if o.get('type') == 'crop'), None)
    rotates = [o for o in ops if o.get('type') == 'rotate']

    if is_vector_safe(ops):
        try:
            data = _src_bytes(resolve_path, desc['src'], src_bytes_cache)
            reader = pypdf.PdfReader(io.BytesIO(data))
            page = reader.pages[int(desc['page'])]
            inherent_rot = int(page.rotation or 0) % 360
            if crop and inherent_rot != 0:
                # Cropping a page that already carries a /Rotate is ambiguous
                # in mediabox space — rasterise to stay correct.
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

    img = render_page_image(resolve_path(desc['src']), int(desc['page']),
                            ops, width_px)
    reader = _pil_image_to_pdf_reader(img)
    writer.add_page(reader.pages[0])


class _RasterFallback(Exception):
    """Internal signal: this page must be rasterised, not added as vector."""


def _export_raster_pdf(pages, resolve_path, width_px) -> bytes:
    """All-raster PDF via Pillow (pypdf-free fallback)."""
    from PIL import Image  # noqa: F401

    imgs = [render_page_image(resolve_path(d['src']), int(d['page']),
                              d.get('ops') or [], width_px) for d in pages]
    buf = io.BytesIO()
    res = max(72.0, width_px / 8.27)
    imgs[0].save(buf, format='PDF', save_all=True, append_images=imgs[1:],
                 resolution=res)
    return buf.getvalue()


def _export_png_zip(pages, resolve_path, width_px) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, desc in enumerate(pages):
            img = render_page_image(resolve_path(desc['src']),
                                    int(desc['page']), desc.get('ops') or [],
                                    width_px)
            img_buf = io.BytesIO()
            img.save(img_buf, format='PNG')
            zf.writestr(f'page_{i + 1:03d}.png', img_buf.getvalue())
    return buf.getvalue()


def _export_split_pdf_zip(pages, resolve_path, width_px, split_every) -> bytes:
    """ZIP of PDFs, ``split_every`` pages each (Mode-1 per-student output)."""
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
                _add_page(writer, desc, resolve_path, width_px, src_bytes_cache)
            pdf_buf = io.BytesIO()
            writer.write(pdf_buf)
            zf.writestr(f'part_{chunk:03d}.pdf', pdf_buf.getvalue())
            idx += split_every
    return buf.getvalue()

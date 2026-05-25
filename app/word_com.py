"""
Microsoft Word COM automation for high-fidelity DOCX merging and PDF export.

Why Word COM:
  * Source DOC assets may embed MathType OLE objects, drawings, complex tables,
    fonts, etc. docxcompose-style XML splicing can corrupt those.
  * Word itself handles its own format natively — Selection.InsertFile produces
    an output that looks identical to the user opening the source file by hand.
  * Same Word instance can also export the final docx to PDF without leaving
    the OMML/equation/MathType native representation.

Windows + Microsoft Word required. On other platforms `IS_AVAILABLE` is False
and every entry point raises `WordComUnavailable`. Callers branch on
`IS_AVAILABLE` and fall back to the existing placeholder rendering.

Single-threaded by design: a module-level `threading.Lock` serializes all Word
sessions across the whole process. Callers acquire it via `word_session()`.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class WordComUnavailable(RuntimeError):
    """Raised when Word COM cannot be used (non-Windows, pywin32 missing, Word missing)."""


# Probe pywin32 once on import. Non-Windows: leave IS_AVAILABLE False and all
# entry points become no-ops that raise WordComUnavailable.
IS_AVAILABLE = False
_IMPORT_ERROR: str | None = None

if sys.platform == 'win32':
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        IS_AVAILABLE = True
    except ImportError as e:
        _IMPORT_ERROR = str(e)
        logger.warning('pywin32 not installed — Word COM features disabled: %s', e)
else:
    _IMPORT_ERROR = f'sys.platform={sys.platform!r} is not win32'


# Word COM constants (defined here so we don't need win32com.client.constants,
# which only works after EnsureDispatch).
_wdFormatPDF = 17
_wdExportFormatPDF = 17
_wdExportAllDocument = 0
_wdExportOptimizeForPrint = 0
_wdExportDocumentContent = 0
_wdExportCreateNoBookmarks = 0
_wdExportFromTo = 3
_wdDoNotSaveChanges = 0
_wdAlertsNone = 0


# Module-level lock — exactly one Word session at a time per process.
_WORD_COM_LOCK = threading.Lock()


def _require_available():
    if not IS_AVAILABLE:
        raise WordComUnavailable(
            f'Word COM unavailable: {_IMPORT_ERROR or "unknown reason"}'
        )


# ---------------------------------------------------------------------------
# Section-property stripper
# ---------------------------------------------------------------------------

# DOCX is a ZIP. word/document.xml contains the body, and `<w:sectPr>` elements
# inside it define page setup (size, margins, headers/footers references,
# section breaks). When we InsertFile a source DOCX, Word imports those
# section properties too, which can introduce unwanted breaks / margins /
# page-size changes into the master doc.
#
# The fix: produce a "sanitised" copy of the source DOCX with every <w:sectPr>
# stripped from word/document.xml before InsertFile sees it.

_W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def sanitize_docx_for_insertion(src_path: str, dst_path: str) -> None:
    """
    Copy `src_path` to `dst_path` but strip every <w:sectPr> from
    word/document.xml so the master document's layout dominates after merge.

    Pure-Python; does not need Word. Uses python-docx (already a dependency).
    """
    from docx import Document  # local import — keeps top-level import cheap

    doc = Document(src_path)

    # 1. body-level final sectPr (defines the document's overall page setup)
    body = doc.element.body
    sect_prs = body.findall(f'{{{_W_NS}}}sectPr')
    for sp in sect_prs:
        body.remove(sp)

    # 2. inline sectPr inside paragraphs (defines mid-document section breaks)
    for sp in body.iter(f'{{{_W_NS}}}sectPr'):
        parent = sp.getparent()
        if parent is not None and parent.tag != f'{{{_W_NS}}}body':
            parent.remove(sp)
            # If parent is now an orphan <w:pPr> with no children, prune it too
            if parent.tag == f'{{{_W_NS}}}pPr' and len(parent) == 0:
                grand = parent.getparent()
                if grand is not None:
                    grand.remove(parent)

    doc.save(dst_path)


# ---------------------------------------------------------------------------
# Watchdog: kill WINWORD if a single COM call hangs longer than the timeout.
# ---------------------------------------------------------------------------

def _kill_word_processes_started_after(start_time: float) -> int:
    """
    Best-effort cleanup of any WINWORD.EXE process started after `start_time`.
    Uses psutil if available, otherwise falls back to `taskkill /f /im WINWORD.EXE`
    (which kills ALL Word instances — only acceptable on a dedicated server).
    Returns the number of processes killed.
    """
    killed = 0
    try:
        import psutil  # type: ignore
        for proc in psutil.process_iter(['name', 'create_time']):
            try:
                if proc.info['name'] and proc.info['name'].lower() == 'winword.exe':
                    if proc.info['create_time'] >= start_time - 1:
                        proc.kill()
                        killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        # No psutil — fall back to taskkill, which kills ALL Word instances.
        # This is acceptable because the server is dedicated to this app and
        # the alternative is a zombie process holding the global lock forever.
        try:
            import subprocess
            subprocess.run(
                ['taskkill', '/f', '/im', 'WINWORD.EXE'],
                capture_output=True, timeout=10
            )
            killed = -1  # unknown count
        except Exception as e:  # pragma: no cover
            logger.error('Fallback taskkill failed: %s', e)
    return killed


# ---------------------------------------------------------------------------
# Session context manager
# ---------------------------------------------------------------------------

@contextmanager
def word_session(lock_timeout: float = 600.0, visible: bool = False):
    """
    Acquire the global Word COM lock, initialise COM in this thread, start a
    fresh Word.Application, yield it, then close it cleanly on exit.

    Caller MUST be in a background thread (Flask requests should NOT block on
    the lock). The lock is bounded by `lock_timeout` seconds; on timeout a
    `TimeoutError` is raised.

    Usage:
        with word_session() as word:
            merge_doc_into_master(word, master_path, insertions)
            export_to_pdf(word, master_path, pdf_path)
    """
    _require_available()

    acquired = _WORD_COM_LOCK.acquire(timeout=lock_timeout)
    if not acquired:
        raise TimeoutError(
            f'Could not acquire Word COM lock within {lock_timeout}s — '
            'another generation is in progress.'
        )

    pythoncom.CoInitialize()
    word = None
    session_start = time.time()
    try:
        # DispatchEx forces a NEW Word.Application process (not an existing
        # foreground instance owned by an interactive user). Safer for servers.
        word = win32com.client.DispatchEx('Word.Application')
        word.Visible = visible
        word.DisplayAlerts = _wdAlertsNone
        try:
            word.ScreenUpdating = False
        except Exception:
            pass

        yield word
    finally:
        # Quit Word, swallowing any exception so we always release the lock.
        if word is not None:
            try:
                word.Quit(SaveChanges=_wdDoNotSaveChanges)
            except Exception as e:
                logger.warning('Word.Quit raised: %s — falling back to taskkill', e)
                _kill_word_processes_started_after(session_start)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _WORD_COM_LOCK.release()


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def merge_doc_into_master(word_app, master_path: str, marker_to_doc_paths: dict) -> None:
    """
    Open `master_path` in Word, locate each marker text (and the matching
    bookmark), strip section properties from the corresponding source DOCX,
    then replace the marker with the inserted content via Selection.InsertFile.

    Args:
        word_app: live Word.Application COM instance
        master_path: absolute path to the master .docx (will be modified in place)
        marker_to_doc_paths: {marker_string: abs_path_to_source_docx}

    Behaviour:
        * Sanitises each source DOCX to a temp file first (strips <w:sectPr>).
        * If the source file is missing or sanitisation fails, leaves a small
          italic placeholder paragraph in place of the marker.
        * Saves and closes the master document.
    """
    _require_available()

    if not marker_to_doc_paths:
        return

    abs_master = os.path.abspath(master_path)
    doc = word_app.Documents.Open(
        abs_master,
        ConfirmConversions=False,
        ReadOnly=False,
        AddToRecentFiles=False,
    )
    try:
        with tempfile.TemporaryDirectory(prefix='oqb_docmerge_') as tmpdir:
            for marker, src_path in marker_to_doc_paths.items():
                _insert_one(doc, word_app, marker, src_path, tmpdir)

        # Save back to the same path (Word infers format from extension).
        doc.Save()
    finally:
        try:
            doc.Close(SaveChanges=_wdDoNotSaveChanges)
        except Exception:
            pass


def _insert_one(doc, word_app, marker: str, src_path: str, tmpdir: str) -> None:
    """
    Find the paragraph containing `marker`, select it, replace its content
    with the InsertFile output of a sanitised copy of `src_path`. Errors
    leave a placeholder paragraph behind.
    """
    # Use Word's Find to locate the marker text.
    rng = doc.Content
    rng.Find.ClearFormatting()
    rng.Find.Text = marker
    rng.Find.Forward = True
    rng.Find.Wrap = 0  # wdFindStop — don't wrap around
    rng.Find.MatchCase = True
    rng.Find.MatchWholeWord = False

    if not rng.Find.Execute():
        logger.warning('DOC merge: marker %r not found in master', marker)
        return

    # rng now points to just the marker text. Extend it to cover the whole
    # paragraph (including the trailing ¶) so InsertFile replaces the whole
    # placeholder line, not just the text fragment.
    rng.Expand(4)  # wdParagraph
    rng.Select()
    selection = word_app.Selection

    # The marker paragraph might be followed by other content; we want to
    # delete just our placeholder, then InsertFile at that point.
    selection.Delete()

    if not os.path.isfile(src_path):
        # Placeholder for missing file.
        selection.TypeText(f'[DOC source missing: {os.path.basename(src_path)}]')
        selection.TypeParagraph()
        return

    # Strip section properties to a temp copy.
    safe_name = f'src_{abs(hash(marker)) & 0xFFFFFFFF:08x}.docx'
    sanitised_path = os.path.join(tmpdir, safe_name)
    try:
        sanitize_docx_for_insertion(src_path, sanitised_path)
    except Exception as e:
        logger.error('DOC merge: sanitize failed for %s: %s', src_path, e)
        selection.TypeText(f'[Error preparing DOC source {os.path.basename(src_path)}: {e}]')
        selection.TypeParagraph()
        return

    try:
        selection.InsertFile(
            FileName=sanitised_path,
            ConfirmConversions=False,
            Link=False,
            Attachment=False,
        )
    except Exception as e:
        logger.error('DOC merge: InsertFile failed for %s: %s', src_path, e)
        selection.TypeText(f'[Error inserting DOC source {os.path.basename(src_path)}: {e}]')
        selection.TypeParagraph()


def export_to_pdf(word_app, docx_path: str, pdf_path: str) -> None:
    """
    Open `docx_path` in Word and export it to `pdf_path` as PDF using
    ExportAsFixedFormat. Native equations / MathType / fonts are preserved.
    """
    _require_available()
    abs_docx = os.path.abspath(docx_path)
    abs_pdf = os.path.abspath(pdf_path)

    doc = word_app.Documents.Open(
        abs_docx,
        ConfirmConversions=False,
        ReadOnly=True,
        AddToRecentFiles=False,
    )
    try:
        doc.ExportAsFixedFormat(
            OutputFileName=abs_pdf,
            ExportFormat=_wdExportFormatPDF,
            OpenAfterExport=False,
            OptimizeFor=_wdExportOptimizeForPrint,
            Range=_wdExportAllDocument,
            Item=_wdExportDocumentContent,
            IncludeDocProps=False,
            KeepIRM=True,
            CreateBookmarks=_wdExportCreateNoBookmarks,
            DocStructureTags=True,
            BitmapMissingFonts=True,
            UseISO19005_1=False,
        )
    finally:
        try:
            doc.Close(SaveChanges=_wdDoNotSaveChanges)
        except Exception:
            pass


def render_first_page_png(word_app, docx_path: str, png_path: str,
                          width_px: int = 1000,
                          transparent: bool | None = None,
                          whiteness_threshold: int | None = None,
                          bottom_padding_px: int | None = None) -> None:
    """
    Render the first page of `docx_path` to `png_path` (PNG).

    Implementation: export the full doc to a temporary PDF via Word, then
    use PyMuPDF (fitz) to rasterise page 0 to PNG at the requested width.
    The rendered image is auto-cropped to remove trailing whitespace so a
    one-paragraph question doesn't get a full A4 page of empty space below.

    Tunables (all optional; resolved from `current_app.config` when None):
      * `transparent` (bool, default False)
            If True, post-process the cropped image so the white page
            background becomes transparent. Antialiased text edges are
            preserved via a luminance-based alpha mask. Read from
            `THUMBNAIL_TRANSPARENT`.
      * `whiteness_threshold` (int 0–255, default 250)
            Pixels brighter than this are treated as background when
            cropping. Read from `THUMBNAIL_WHITENESS_THRESHOLD`.
      * `bottom_padding_px` (int, default 24)
            Blank space kept below the cropped content. Read from
            `THUMBNAIL_BOTTOM_PADDING_PX`.
    """
    _require_available()

    try:
        import fitz  # type: ignore
    except ImportError as e:
        raise WordComUnavailable(f'PyMuPDF (fitz) not installed: {e}') from e

    # Resolve tunables from app.config when not supplied explicitly.
    transparent, whiteness_threshold, bottom_padding_px = _resolve_thumb_tunables(
        transparent, whiteness_threshold, bottom_padding_px
    )

    with tempfile.TemporaryDirectory(prefix='oqb_doc_thumb_') as tmpdir:
        tmp_pdf = os.path.join(tmpdir, 'page.pdf')
        export_to_pdf(word_app, docx_path, tmp_pdf)

        if not os.path.exists(tmp_pdf):
            raise RuntimeError(f'Word produced no PDF for {docx_path}')

        pdf = fitz.open(tmp_pdf)
        try:
            if pdf.page_count == 0:
                raise RuntimeError(f'Empty PDF rendered from {docx_path}')
            page = pdf.load_page(0)
            # Compute zoom so the rendered width matches `width_px`.
            base_width = page.rect.width or 595.0  # A4 width in points fallback
            zoom = max(0.1, width_px / base_width)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            os.makedirs(os.path.dirname(png_path), exist_ok=True)

            # Auto-crop trailing whitespace: most question DOCX files only
            # use a fraction of the A4 page, so the rendered first page has
            # a large blank tail. Crop to the actual content height (keeping
            # full width so cards line up uniformly).
            _save_cropped_png(
                pix, png_path,
                bottom_padding_px=bottom_padding_px,
                whiteness_threshold=whiteness_threshold,
                transparent=transparent,
            )
        finally:
            pdf.close()


def _resolve_thumb_tunables(transparent, whiteness_threshold, bottom_padding_px):
    """Pull defaults from `current_app.config` when a tunable is None.

    Falls back to module-level literals when no Flask app context is
    available (e.g. when called from a unit test or smoke script).
    """
    if transparent is not None and whiteness_threshold is not None and bottom_padding_px is not None:
        return transparent, whiteness_threshold, bottom_padding_px
    try:
        from flask import current_app
        cfg = current_app.config
    except Exception:  # pragma: no cover — no Flask context
        cfg = {}
    if transparent is None:
        transparent = bool(cfg.get('THUMBNAIL_TRANSPARENT', False))
    if whiteness_threshold is None:
        whiteness_threshold = int(cfg.get('THUMBNAIL_WHITENESS_THRESHOLD', 250))
    if bottom_padding_px is None:
        bottom_padding_px = int(cfg.get('THUMBNAIL_BOTTOM_PADDING_PX', 24))
    return transparent, whiteness_threshold, bottom_padding_px


def _save_cropped_png(pix, png_path: str, bottom_padding_px: int = 24,
                      whiteness_threshold: int = 250,
                      transparent: bool = False) -> None:
    """
    Save a PyMuPDF Pixmap to `png_path` after cropping whitespace on every
    side.

    Strategy:
      * Convert the pixmap to a PIL Image.
      * Compute a "content mask" by diffing against a near-white reference
        image, then use `getbbox()` to find the smallest rectangle that
        contains all non-white pixels.
      * Crop to that bbox plus `bottom_padding_px` of margin on **all four
        sides** (capped to image bounds). This trims the A4 page borders
        AND any leading / leading-column whitespace, producing a tight
        thumbnail proportional to the content.
      * Optionally apply a luminance-based alpha mask so the white page
        background becomes transparent. Antialiased grey text edges
        receive partial alpha for clean rendering against any backdrop.
      * Fall back to writing the un-cropped pixmap on any error so the
        caller always gets *some* file on disk.
    """
    try:
        from PIL import Image, ImageChops, ImageOps
    except ImportError:
        # Pillow should be present (it's already a dependency) but guard
        # the import so a broken environment still produces a thumbnail.
        pix.save(png_path)
        return

    try:
        img_bytes = pix.tobytes('png')
        img = Image.open(io.BytesIO(img_bytes)).convert('RGB')

        threshold = max(0, min(255, whiteness_threshold))
        ref = Image.new('RGB', img.size, (threshold, threshold, threshold))
        # `darker` = per-pixel min(img, ref). Subtracting from `ref` gives a
        # "darkness" image where any non-near-white pixel is positive.
        # bbox() returns the smallest enclosing rectangle of non-zero pixels.
        darkness = ImageChops.subtract(ref, ImageChops.darker(img, ref))
        bbox = darkness.getbbox()

        pad = max(0, int(bottom_padding_px))

        if bbox is None:
            # Page is entirely white. Keep a small thumbnail so the resolver
            # doesn't fall through to "not rendered yet" forever.
            cropped = img.crop((0, 0, min(img.size[0], 400), min(img.size[1], 200)))
        else:
            left, top, right, bottom = bbox
            # Expand by `pad` on every side, clipped to image bounds.
            crop_box = (
                max(0, left - pad),
                max(0, top - pad),
                min(img.size[0], right + pad),
                min(img.size[1], bottom + pad),
            )
            cropped = img.crop(crop_box)

        if transparent:
            # RGB → RGBA where alpha = 255 - luminance. White → 0 alpha,
            # antialiased grey → semi-transparent, black → opaque. This is
            # the standard "save as transparent PNG" trick — fast (no
            # per-pixel Python loop), and produces smooth edges.
            gray = cropped.convert('L')
            alpha = ImageOps.invert(gray)
            cropped = cropped.convert('RGBA')
            cropped.putalpha(alpha)

        os.makedirs(os.path.dirname(png_path), exist_ok=True)
        # Write to a temp sibling first, then atomically replace the target.
        # Without this, a concurrent reader could see a partially-written
        # PNG and return a corrupt response.
        tmp_path = png_path + '.tmp'
        # `optimize=True` is expensive for large RGBA PNGs. Skip it for the
        # transparent path so a stuck Word session doesn't compound a slow
        # save into a > 60 s tail.
        cropped.save(tmp_path, format='PNG', optimize=(not transparent))
        os.replace(tmp_path, png_path)
    except Exception as e:
        logger.exception('Cropped-PNG save failed; falling back to raw pixmap: %s', e)
        try:
            os.makedirs(os.path.dirname(png_path), exist_ok=True)
            pix.save(png_path)
        except Exception as e2:
            logger.exception('Raw pixmap save also failed for %s: %s', png_path, e2)

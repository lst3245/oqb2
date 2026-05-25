"""
Batch image generation from DOC / MD source assets.

Renders each selected (question × asset_type × language × source-format)
slot to one or more PNGs using the same Word COM pipeline as DOC thumbnails,
then replaces the existing IMG assets for that slot. Supports two output
modes: stitch every source page into one tall PNG, or one PNG per page
(multi-part IMG).

Why a separate module
---------------------
* Keeps `app/generator.py` (already large) focused on the docx/pdf
  generation pipeline.
* Provides a reusable surface for future automation (e.g. CLI batch tools).

External requirements
---------------------
* `app/word_com.py` available (Windows + pywin32 + Word).
* `pandoc` on PATH (only for MD sources).
* `PyMuPDF` (`fitz`) for PDF → image rasterisation.
* Pillow (already a project-wide dep).
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from typing import Iterable

from sqlalchemy.orm import Session

from app import db, word_com
from app.models import Question, QuestionAsset

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rendering: DOC → list of cropped PIL Images
# ---------------------------------------------------------------------------

def render_doc_to_pages(word_app, src_path: str, width_px: int,
                        transparent: bool, whiteness_threshold: int,
                        bottom_padding_px: int,
                        symmetric_horizontal_crop: bool = False) -> list:
    """
    Open `src_path` in Word, export to PDF, rasterise EVERY page via PyMuPDF,
    and return a list of cropped PIL Image objects (one per page).

    Mirrors the cropping + optional transparent post-processing used by
    `app/word_com._save_cropped_png` so a stitched batch result looks
    identical to a same-page DOC thumbnail. See `_compute_crop_box` for
    `symmetric_horizontal_crop` semantics.
    """
    from PIL import Image, ImageChops, ImageOps
    import fitz  # type: ignore

    # Sanitise the source DOCX exactly like the generation pipeline does so
    # multi-page source files don't import section breaks / weird page
    # setup into the temporary export.
    with tempfile.TemporaryDirectory(prefix='oqb_batch_doc_') as tmpdir:
        sanitised = os.path.join(tmpdir, 'src.docx')
        try:
            word_com.sanitize_docx_for_insertion(src_path, sanitised)
        except Exception as e:
            logger.warning('sanitize_docx_for_insertion failed for %s (%s); falling back to original', src_path, e)
            import shutil as _shutil
            _shutil.copyfile(src_path, sanitised)

        tmp_pdf = os.path.join(tmpdir, 'src.pdf')
        word_com.export_to_pdf(word_app, sanitised, tmp_pdf)
        if not os.path.exists(tmp_pdf):
            raise RuntimeError(f'Word produced no PDF for {src_path}')

        return _pdf_to_cropped_images(
            tmp_pdf, width_px, transparent, whiteness_threshold,
            bottom_padding_px, symmetric_horizontal_crop,
        )


def render_md_to_pages(word_app, md_path: str, width_px: int,
                       transparent: bool, whiteness_threshold: int,
                       bottom_padding_px: int,
                       symmetric_horizontal_crop: bool = False) -> list:
    """
    Convert `md_path` (MD source) to DOCX via pandoc, then render via Word
    COM exactly like a DOC asset. Returns a list of cropped PIL Image
    objects (one per page).
    """
    # Imported lazily so MD support is optional when pandoc is missing.
    from app.generator import md_to_docx_via_pandoc
    import fitz  # type: ignore  # noqa: F401

    with tempfile.TemporaryDirectory(prefix='oqb_batch_md_') as tmpdir:
        intermediate = os.path.join(tmpdir, 'src.docx')
        md_to_docx_via_pandoc(md_path, intermediate)

        tmp_pdf = os.path.join(tmpdir, 'src.pdf')
        word_com.export_to_pdf(word_app, intermediate, tmp_pdf)
        if not os.path.exists(tmp_pdf):
            raise RuntimeError(f'Word produced no PDF for MD {md_path}')

        return _pdf_to_cropped_images(
            tmp_pdf, width_px, transparent, whiteness_threshold,
            bottom_padding_px, symmetric_horizontal_crop,
        )


def _pdf_to_cropped_images(pdf_path: str, width_px: int, transparent: bool,
                           whiteness_threshold: int,
                           bottom_padding_px: int,
                           symmetric_horizontal_crop: bool = False) -> list:
    """
    Rasterise every page of `pdf_path` to a cropped PIL Image. Cropping +
    optional transparency uses the same per-page rules as the DOC thumbnail
    pipeline; this guarantees visual consistency between a thumbnail and
    its batch-generated IMG counterpart.

    `symmetric_horizontal_crop` mirrors the thumbnail setting: when True,
    the left/right crops are capped to `min(left_white, right_white)` so
    short content keeps proportional whitespace. See
    `app/word_com._compute_crop_box` for the full rationale.
    """
    from PIL import Image, ImageChops, ImageOps
    from app.word_com import _compute_crop_box
    import fitz  # type: ignore

    pdf = fitz.open(pdf_path)
    out_images: list = []
    try:
        if pdf.page_count == 0:
            return out_images

        threshold = max(0, min(255, int(whiteness_threshold)))

        for page_index in range(pdf.page_count):
            page = pdf.load_page(page_index)
            base_width = page.rect.width or 595.0  # A4 width pts fallback
            zoom = max(0.1, width_px / base_width)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes('png')
            img = Image.open(io.BytesIO(img_bytes)).convert('RGB')

            # --- crop whitespace on every side --------------------------------
            ref = Image.new('RGB', img.size, (threshold, threshold, threshold))
            darkness = ImageChops.subtract(ref, ImageChops.darker(img, ref))
            bbox = darkness.getbbox()
            pad = max(0, int(bottom_padding_px))
            if bbox is None:
                cropped = img.crop((0, 0, min(img.size[0], 400), min(img.size[1], 200)))
            else:
                crop_box = _compute_crop_box(
                    img_size=img.size, bbox=bbox, pad=pad,
                    symmetric_horizontal=symmetric_horizontal_crop,
                )
                cropped = img.crop(crop_box)

            # --- optional transparency ----------------------------------------
            if transparent:
                gray = cropped.convert('L')
                alpha = ImageOps.invert(gray)
                cropped = cropped.convert('RGBA')
                cropped.putalpha(alpha)

            out_images.append(cropped)
    finally:
        pdf.close()

    return out_images


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------

def stitch_vertically(images: list, transparent: bool = False) -> 'Image.Image':
    """
    Stack `images` vertically into one tall PIL Image. Widths are normalised
    to the widest input by left-padding shorter pages with the background
    colour (white or transparent depending on `transparent`).
    """
    from PIL import Image

    if not images:
        raise ValueError('stitch_vertically called with empty list')

    max_width = max(im.width for im in images)
    total_height = sum(im.height for im in images)

    mode = 'RGBA' if transparent else 'RGB'
    background = (0, 0, 0, 0) if transparent else (255, 255, 255)
    canvas = Image.new(mode, (max_width, total_height), background)

    y = 0
    for im in images:
        # Ensure the input has the same mode as the canvas.
        if im.mode != mode:
            im = im.convert(mode)
        canvas.paste(im, (0, y))
        y += im.height
    return canvas


# ---------------------------------------------------------------------------
# DB / disk replacement
# ---------------------------------------------------------------------------

def _build_img_rel_path(question: Question, asset_type: str, language: str,
                        part_number: int) -> str:
    """Construct the canonical relative file path for an IMG asset.

    Mirrors `_build_asset_file_path` in `app/admin.py` (no _PART suffix for
    part 1, `_2`/`_3`/... for additional parts).
    """
    # Local import to avoid circular dependency with admin.py at module load.
    from app.admin import _extract_qb_detail

    part_suffix = f'_{part_number}' if part_number > 1 else ''
    filename = f"{question.qid}_{language}_{asset_type}{part_suffix}.png"

    if question.source in ('DSE', 'CE', 'AL'):
        folder = '/'.join([question.subject, 'PP', question.source,
                           str(question.year), question.paper])
    else:
        detail = _extract_qb_detail(question.qid)
        folder = '/'.join([question.subject, 'QB', detail])

    return f"{folder}/{filename}"


def replace_img_assets(question: Question, asset_type: str, language: str,
                       pages: list, stitch: bool, source_path: str) -> dict:
    """
    Atomically replace every IMG asset for `(question, asset_type, language)`
    with the freshly-rendered `pages` (a list of PIL Images).

    Strategy:
      1. Render the new files to disk FIRST (under a temp prefix in the
         canonical folder). If anything below fails we can clean those up.
      2. Delete existing IMG rows + their files on disk.
      3. Rename the new files into their canonical names.
      4. Insert new QuestionAsset rows.
      5. Commit.

    Returns a dict `{wrote: int, deleted: int, file_paths: [str]}` summary.

    Caller must commit/flush DB session; this function commits at the end.
    """
    from PIL import Image  # noqa: F401  — type only

    if not pages:
        raise ValueError('replace_img_assets called with empty pages list')

    # Normalise to either one stitched image or one per page.
    if stitch:
        stitched = stitch_vertically(pages, transparent=(pages[0].mode == 'RGBA'))
        out_images = [stitched]
    else:
        out_images = list(pages)

    # Step 1: write temp files alongside their final destinations.
    rel_paths = [
        _build_img_rel_path(question, asset_type, language, i + 1)
        for i in range(len(out_images))
    ]
    abs_paths = [os.path.join(source_path, *rp.split('/')) for rp in rel_paths]
    tmp_paths = [p + '.tmp_batchimg' for p in abs_paths]

    for img, tmp in zip(out_images, tmp_paths):
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        img.save(tmp, format='PNG', optimize=True)

    try:
        # Step 2: delete existing IMG rows + files.
        existing = QuestionAsset.query.filter_by(
            question_id=question.id,
            asset_type=asset_type,
            language=language,
            file_format='IMG',
        ).all()
        deleted_count = 0
        for old in existing:
            old_abs = os.path.join(source_path, *old.file_path.split('/'))
            try:
                if os.path.isfile(old_abs):
                    os.remove(old_abs)
            except OSError as e:
                logger.warning('Could not remove old IMG file %s: %s', old_abs, e)
            db.session.delete(old)
            deleted_count += 1
        db.session.flush()

        # Step 3: rename temp files into place.
        for tmp, final_abs in zip(tmp_paths, abs_paths):
            # If a stale file with the canonical name somehow survived,
            # remove it first so os.replace can succeed on Windows.
            if os.path.isfile(final_abs):
                try:
                    os.remove(final_abs)
                except OSError:
                    pass
            os.replace(tmp, final_abs)

        # Step 4: insert new rows.
        new_assets: list[QuestionAsset] = []
        for part_number, rel_path in enumerate(rel_paths, start=1):
            row = QuestionAsset(
                question_id=question.id,
                asset_type=asset_type,
                file_format='IMG',
                language=language,
                file_path=rel_path,
                part_number=part_number,
            )
            db.session.add(row)
            new_assets.append(row)
        db.session.commit()

        # Step 5: post-commit DOC thumbnail lifecycle. A newly-created IMG
        # eclipses any DOC thumbnail in the same slot — clean those up.
        try:
            from app import doc_thumbnails
            for a in new_assets:
                doc_thumbnails.on_img_asset_created(a)
        except Exception as e:
            logger.warning('DOC thumbnail lifecycle skipped after batch IMG gen: %s', e)

        return {
            'wrote': len(rel_paths),
            'deleted': deleted_count,
            'file_paths': rel_paths,
        }
    except Exception:
        # Roll back DB changes; clean up any straggling temp files.
        db.session.rollback()
        for tmp in tmp_paths:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Source-asset lookup
# ---------------------------------------------------------------------------

def find_best_source(question: Question, asset_type: str, language: str,
                     allow_doc: bool, allow_md: bool) -> QuestionAsset | None:
    """
    Return the preferred source asset to render for the slot
    `(question, asset_type, language)`. Preference: DOC > MD (matches the
    fidelity ranking — DOC preserves MathType, MD goes through pandoc).

    Returns None when no usable source exists.
    """
    if allow_doc:
        doc = QuestionAsset.query.filter_by(
            question_id=question.id, asset_type=asset_type,
            language=language, file_format='DOC',
        ).first()
        if doc:
            return doc
    if allow_md:
        md = QuestionAsset.query.filter_by(
            question_id=question.id, asset_type=asset_type,
            language=language, file_format='MD',
        ).first()
        if md:
            return md
    return None


def slot_has_img(question_id: int, asset_type: str, language: str) -> bool:
    """True when at least one IMG asset already exists for the slot."""
    return QuestionAsset.query.filter_by(
        question_id=question_id, asset_type=asset_type,
        language=language, file_format='IMG',
    ).first() is not None

"""
DOC asset thumbnails — server-rendered first-page PNG, cached to disk.

Lifecycle (mirrors the user's requested behaviour):
  * Generated **only** for a DOC asset whose `(question_id, asset_type,
    language)` slot has no IMG asset. The IMG resolver wins automatically
    via the existing format-priority logic; the thumbnail is only useful
    when DOC is the visible representative.
  * Stored at  `<DOC_THUMBNAIL_PATH>/<asset_id>.png`. Keyed by asset_id so
    file_path changes (rename) don't invalidate.
  * Deleted when:
      - the DOC asset is deleted,
      - an IMG asset is uploaded into the same slot,
      - the DOC source file is no longer rendered (currently a no-op; the
        ingestor doesn't track DOC mtime).
  * Rendered asynchronously: a daemon thread runs Word COM in the background
    so HTTP responses (upload, save, ingest tick) return immediately.

Word COM is required. On Linux / macOS / when pywin32 is missing, all
rendering calls log a warning and skip silently. The dashboard then falls
back to the existing "download" preview mode.
"""
from __future__ import annotations

import logging
import os
import threading

from flask import current_app

from app import db, word_com
from app.models import QuestionAsset

logger = logging.getLogger(__name__)


def thumbnail_path(asset_id: int, base_dir: str | None = None) -> str:
    """Return the absolute path where the thumbnail for `asset_id` would live."""
    base = base_dir or current_app.config['DOC_THUMBNAIL_PATH']
    return os.path.join(base, f'{int(asset_id)}.png')


def thumbnail_exists(asset_id: int, base_dir: str | None = None) -> bool:
    """True if a cached PNG exists on disk."""
    try:
        return os.path.isfile(thumbnail_path(asset_id, base_dir))
    except Exception:
        return False


def delete_thumbnail(asset_id: int, base_dir: str | None = None) -> bool:
    """Delete the cached thumbnail file if present. Returns True on success."""
    path = thumbnail_path(asset_id, base_dir)
    try:
        if os.path.isfile(path):
            os.remove(path)
            return True
    except OSError as e:
        logger.warning('Failed to delete DOC thumbnail %s: %s', path, e)
    return False


def _slot_has_img(question_id: int, asset_type: str, language: str) -> bool:
    return QuestionAsset.query.filter_by(
        question_id=question_id,
        asset_type=asset_type,
        language=language,
        file_format='IMG',
    ).first() is not None


def render_doc_thumbnail_sync(app, asset_id: int) -> bool:
    """
    Synchronously render the thumbnail for a single DOC asset.

    Looks up the asset within `app.app_context()`, checks that its slot has
    no IMG (otherwise the IMG resolver wins and we shouldn't waste a Word
    session), then runs `word_com.render_first_page_png`.

    Returns True on success, False otherwise.
    """
    if not word_com.IS_AVAILABLE:
        logger.info('DOC thumbnail skipped (Word COM unavailable): asset %s', asset_id)
        return False

    with app.app_context():
        asset = QuestionAsset.query.get(asset_id)
        if not asset or asset.file_format != 'DOC':
            return False

        if _slot_has_img(asset.question_id, asset.asset_type, asset.language):
            # IMG took the slot — delete any stale thumbnail.
            delete_thumbnail(asset_id)
            return False

        source_path = app.config['SOURCE_PATH']
        src_abs = os.path.join(source_path, *asset.file_path.split('/'))
        if not os.path.isfile(src_abs):
            logger.warning('DOC thumbnail: source missing %s', src_abs)
            return False

        target = thumbnail_path(asset_id, app.config['DOC_THUMBNAIL_PATH'])
        width = int(app.config.get('DOC_THUMBNAIL_WIDTH', 1000))
        lock_timeout = float(app.config.get('WORD_COM_LOCK_TIMEOUT', 600))

        try:
            with word_com.word_session(lock_timeout=lock_timeout) as word_app:
                word_com.render_first_page_png(word_app, src_abs, target, width_px=width)
            return True
        except Exception as e:
            logger.exception('DOC thumbnail render failed for asset %s: %s', asset_id, e)
            return False


def schedule_thumbnail(asset_id: int) -> None:
    """
    Fire-and-forget thumbnail render in a daemon thread. Safe to call from
    Flask handlers and SSE generators — it returns immediately. Skips the
    work if Word COM is unavailable.
    """
    if not word_com.IS_AVAILABLE:
        return
    app = current_app._get_current_object()

    def _worker():
        try:
            render_doc_thumbnail_sync(app, asset_id)
        except Exception:
            logger.exception('DOC thumbnail worker crashed (asset %s)', asset_id)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# In-process de-dupe set for lazy `ensure_thumbnail` calls. Without this, a
# dashboard page with 20 DOC questions would queue 20 Word sessions back to
# back (serialised through the global lock anyway, but still wasteful). Once
# a render is in flight or finished, the same `asset_id` is skipped until
# the process restarts.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: set[int] = set()


def ensure_thumbnail(asset_id: int) -> bool:
    """
    Lazy thumbnail ensurer for the preview / viewer paths.

    Returns True if a cached thumbnail is already on disk (the caller can
    serve it immediately). Returns False if no thumbnail exists yet; in that
    case a render is scheduled in the background and the caller should
    fall back to the existing download stub. The thumbnail will appear on
    the next refresh.

    Idempotent: a second call for the same `asset_id` while a render is in
    flight (or after one completed in this process) is a no-op.
    """
    if thumbnail_exists(asset_id):
        return True

    if not word_com.IS_AVAILABLE:
        return False

    # Avoid scheduling the same asset multiple times.
    with _INFLIGHT_LOCK:
        if asset_id in _INFLIGHT:
            return False
        _INFLIGHT.add(asset_id)

    app = current_app._get_current_object()

    def _worker():
        try:
            render_doc_thumbnail_sync(app, asset_id)
        except Exception:
            logger.exception('DOC thumbnail worker crashed (asset %s)', asset_id)
        # NOTE: intentionally do NOT remove from _INFLIGHT on success — leave
        # the entry as a "we tried, don't retry this session" marker. On
        # failure we also leave it so we don't hammer Word on broken files.
        # Restart the process to retry.

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return False


# ---------------------------------------------------------------------------
# Slot-level lifecycle helpers — called from admin.py upload / delete paths.
# ---------------------------------------------------------------------------

def on_doc_asset_created(asset: QuestionAsset) -> None:
    """A DOC asset was just created/saved. Render thumbnail if no IMG wins the slot."""
    if asset is None or asset.file_format != 'DOC':
        return
    if _slot_has_img(asset.question_id, asset.asset_type, asset.language):
        return
    schedule_thumbnail(asset.id)


def on_img_asset_created(asset: QuestionAsset) -> None:
    """
    An IMG asset was just created. Any DOC in the same slot is now eclipsed
    by the IMG (since the resolver picks IMG first), so its thumbnail is
    no longer reachable — delete it from disk to free space.
    """
    if asset is None or asset.file_format != 'IMG':
        return
    docs_in_slot = QuestionAsset.query.filter_by(
        question_id=asset.question_id,
        asset_type=asset.asset_type,
        language=asset.language,
        file_format='DOC',
    ).all()
    for d in docs_in_slot:
        delete_thumbnail(d.id)


def on_doc_asset_deleted(asset_id: int) -> None:
    """A DOC asset has been deleted from the DB — drop its cached PNG too."""
    delete_thumbnail(asset_id)


def on_img_asset_deleted(asset: QuestionAsset) -> None:
    """
    An IMG asset was deleted. If a DOC still exists in the slot AND no other
    IMG is left, that DOC becomes visible again — schedule its thumbnail.
    """
    if asset is None or asset.file_format != 'IMG':
        return
    if _slot_has_img(asset.question_id, asset.asset_type, asset.language):
        return
    docs_in_slot = QuestionAsset.query.filter_by(
        question_id=asset.question_id,
        asset_type=asset.asset_type,
        language=asset.language,
        file_format='DOC',
    ).all()
    for d in docs_in_slot:
        schedule_thumbnail(d.id)

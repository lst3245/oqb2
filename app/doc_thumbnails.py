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
            ok = os.path.isfile(target) and os.path.getsize(target) > 0
            if ok:
                logger.info('DOC thumbnail rendered: asset=%s -> %s (%d bytes)',
                            asset_id, target, os.path.getsize(target))
            else:
                logger.error('DOC thumbnail: render_first_page_png returned without producing %s', target)
            return ok
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


# In-process de-dupe + retry-backoff for lazy `ensure_thumbnail` calls.
#
# `_INFLIGHT` tracks asset_ids whose render thread is currently running so
# we don't queue duplicates from a single dashboard page load (20 cards
# resolving the same DOC slot would otherwise spawn 20 background threads
# all competing for the Word lock).
#
# `_LAST_ATTEMPT` records the timestamp of the most recent attempt per
# asset. A re-attempt within `_RETRY_COOLDOWN_S` is skipped — but once
# the cooldown elapses we DO retry, so a transient failure (Word hang,
# bad source, etc.) doesn't permanently mark an asset as broken until
# the process is restarted (the old behaviour, which caused 404-forever
# loops in the frontend poller).
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: set[int] = set()
_LAST_ATTEMPT: dict[int, float] = {}
_RETRY_COOLDOWN_S = 5.0


def ensure_thumbnail(asset_id: int) -> bool:
    """
    Lazy thumbnail ensurer for the preview / viewer paths.

    Returns True if a cached thumbnail is already on disk (the caller can
    serve it immediately). Returns False if no thumbnail exists yet; in
    that case a render is scheduled in the background and the caller
    should fall back to the existing download stub. The thumbnail will
    appear on the next refresh.

    Idempotent within a short window: a second call within
    `_RETRY_COOLDOWN_S` (or while the previous attempt is still in flight)
    is a no-op. Calls beyond the cooldown will retry — so a transient
    failure recovers automatically the next time someone views the asset.
    """
    import time as _time

    if thumbnail_exists(asset_id):
        return True

    if not word_com.IS_AVAILABLE:
        return False

    now = _time.time()
    with _INFLIGHT_LOCK:
        if asset_id in _INFLIGHT:
            return False
        last = _LAST_ATTEMPT.get(asset_id)
        if last is not None and (now - last) < _RETRY_COOLDOWN_S:
            return False
        _INFLIGHT.add(asset_id)
        _LAST_ATTEMPT[asset_id] = now

    app = current_app._get_current_object()

    def _worker():
        try:
            render_doc_thumbnail_sync(app, asset_id)
        except Exception:
            logger.exception('DOC thumbnail worker crashed (asset %s)', asset_id)
        finally:
            # Always clear the in-flight marker so the next ensure() call
            # past the cooldown can retry. Without this, a single failure
            # marks the asset as stuck until process restart.
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(asset_id)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return False


def force_rerender(asset_id: int) -> bool:
    """
    Delete any cached PNG for `asset_id` and schedule a fresh render
    immediately, bypassing the cooldown check. Intended for the
    per-preview "Re-render" button.

    Returns True when a render was actually scheduled (Word available);
    False otherwise (e.g. Word COM not available — caller should show
    an error toast).
    """
    delete_thumbnail(asset_id)
    if not word_com.IS_AVAILABLE:
        return False

    with _INFLIGHT_LOCK:
        if asset_id in _INFLIGHT:
            # Already rendering — caller can just start polling.
            return True
        _INFLIGHT.add(asset_id)
        # Reset cooldown so a follow-up ensure_thumbnail call works too.
        _LAST_ATTEMPT.pop(asset_id, None)

    app = current_app._get_current_object()

    def _worker():
        try:
            render_doc_thumbnail_sync(app, asset_id)
        except Exception:
            logger.exception('DOC thumbnail worker crashed (asset %s)', asset_id)
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(asset_id)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return True


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

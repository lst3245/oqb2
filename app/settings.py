"""
Runtime-tunable system settings, persisted in the `system_settings` DB table
and mirrored into `app.config` so consumers never need to touch the DB.

Architecture
------------
* `app/config.py` (the `Config` class) defines the bootstrap defaults read
  from `.env`. These remain the source of truth for secrets (DB creds,
  paths, SECRET_KEY) which intentionally do NOT live in `system_settings`.
* `app/settings.py` (this module) defines the REGISTRY of all tunables
  exposed via the admin UI: their type, default, validator, group, label.
* On app startup, `load_all(app)` reads every row of `system_settings` and
  writes the value into `app.config[key]`, overriding the `.env` bootstrap.
* When an admin saves a setting via `POST /admin/settings/save`, the new
  value is upserted into the DB AND mirrored to `app.config` immediately
  — no server restart needed.
* The reset endpoint deletes the DB row and restores `app.config[key]` to
  the `.env`/Config default (which was captured during `load_all`).

Hot-reload guarantees
---------------------
The single-process Flask dev server and a single-worker production WSGI
server see settings changes immediately. Multi-worker deployments (e.g.
gunicorn -w 4) only update the worker that processed the POST; other
workers still see the old value until they restart or until they look up
a setting that triggers a DB re-read (NOT currently implemented — see
docs for future enhancement).

Concurrency
-----------
Concurrent writes to the same key are serialised by the database's
row-level locking. The in-memory mirror is set after the commit succeeds.
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Any, Callable

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type coercion / validation helpers
# ---------------------------------------------------------------------------

def _coerce_int(raw: Any) -> int:
    if isinstance(raw, bool):  # bool is an int subclass — reject explicitly
        raise ValueError('expected integer, got bool')
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return int(raw.strip())
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    raise ValueError(f'cannot coerce {raw!r} to int')


def _coerce_float(raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueError('expected float, got bool')
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return float(raw.strip())
    raise ValueError(f'cannot coerce {raw!r} to float')


def _coerce_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ('1', 'true', 'yes', 'on'):
            return True
        if s in ('0', 'false', 'no', 'off', ''):
            return False
        raise ValueError(f'cannot parse boolean from string {raw!r}')
    raise ValueError(f'cannot coerce {raw!r} to bool')


def _coerce_string(raw: Any) -> str:
    if raw is None:
        return ''
    return str(raw)


_COERCERS: dict[str, Callable[[Any], Any]] = {
    'int': _coerce_int,
    'float': _coerce_float,
    'bool': _coerce_bool,
    'string': _coerce_string,
}


def _range_validator(low: int | float | None = None,
                     high: int | float | None = None):
    """Build a validator that enforces an inclusive numeric range."""
    def _check(v):
        if low is not None and v < low:
            raise ValueError(f'must be >= {low}')
        if high is not None and v > high:
            raise ValueError(f'must be <= {high}')
        return v
    return _check


def _choice_validator(*choices: str):
    """Build a validator that restricts a string to a fixed set (case-
    insensitive). The stored value is whatever the admin typed; consumers
    are expected to ``.strip().lower()`` it."""
    allowed = {c.lower() for c in choices}

    def _check(v):
        if str(v).strip().lower() not in allowed:
            raise ValueError('must be one of: ' + ', '.join(choices))
        return v
    return _check


# ---------------------------------------------------------------------------
# REGISTRY of tunables
# ---------------------------------------------------------------------------

class _Spec(dict):
    """Tiny dict subclass for nicer attribute-style access in templates."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def _spec(key: str, type_: str, *, group: str, label: str, help: str = '',
          validator: Callable[[Any], Any] | None = None,
          min: int | float | None = None, max: int | float | None = None,
          choices_fn: Callable[[], list] | None = None) -> _Spec:
    """Build a registry spec. `min`/`max` are convenience shortcuts for a
    numeric range validator on int/float types.

    `choices_fn` is an optional zero-argument callable that returns a list of
    ``{'value': ..., 'label': ...}`` dicts for a dropdown UI. It is called
    lazily inside ``as_dict()`` so it can query the DB safely. When present,
    the settings UI renders a ``<select>`` instead of a text input.
    """
    if validator is None and (min is not None or max is not None):
        validator = _range_validator(min, max)
    return _Spec(
        key=key, type=type_, group=group, label=label, help=help,
        validator=validator, min=min, max=max, choices_fn=choices_fn,
    )


def _llm_endpoint_choices() -> list:
    """Return ``[{value, label}, ...]`` for all enabled LLMConfig rows,
    ordered by sort_order then name. Called lazily from ``as_dict()``."""
    try:
        from app.models import LLMConfig
        rows = (LLMConfig.query
                .filter_by(enabled=True)
                .order_by(LLMConfig.sort_order, LLMConfig.name)
                .all())
        return [
            {
                'value': r.name,
                'label': f'{r.name} ({r.model_name})'
                         + (' · vision' if r.supports_vision else ''),
            }
            for r in rows
        ]
    except Exception:
        return []


REGISTRY: 'OrderedDict[str, _Spec]' = OrderedDict([
    # Dashboard
    ('QUESTIONS_PER_PAGE', _spec(
        'QUESTIONS_PER_PAGE', 'int', group='Dashboard',
        label='Questions per page',
        help='Default page size for question lists. Users can still override per session via the page-size dropdown.',
        min=5, max=200,
    )),

    # Markdown
    ('MD_MAX_SIZE_BYTES', _spec(
        'MD_MAX_SIZE_BYTES', 'int', group='Markdown',
        label='Max MD file size (bytes)',
        help='Hard cap on individual .md asset uploads. Base64-embedded images bloat this fast — default 5 MiB.',
        min=64 * 1024,
    )),

    # Word COM
    ('WORD_COM_TIMEOUT', _spec(
        'WORD_COM_TIMEOUT', 'int', group='Word COM',
        label='Per-job watchdog (seconds)',
        help='Kill the WINWORD process if a single Word call exceeds this. Not currently enforced strictly; reserved for future watchdog.',
        min=30, max=3600,
    )),
    ('WORD_COM_LOCK_TIMEOUT', _spec(
        'WORD_COM_LOCK_TIMEOUT', 'int', group='Word COM',
        label='Lock-acquire timeout (seconds)',
        help='Max wait when another generation is holding the Word COM lock. On timeout, the new job fails with a clear error.',
        min=10, max=7200,
    )),

    # Thumbnails
    ('DOC_THUMBNAIL_WIDTH', _spec(
        'DOC_THUMBNAIL_WIDTH', 'int', group='Thumbnails',
        label='Thumbnail width (px)',
        help='Render width of DOC asset first-page PNG thumbnails. ~A4 width at 96 DPI ≈ 1000 px.',
        min=200, max=4000,
    )),
    ('THUMBNAIL_TRANSPARENT', _spec(
        'THUMBNAIL_TRANSPARENT', 'bool', group='Thumbnails',
        label='Transparent background',
        help='Post-process the rendered PNG so the white page background becomes transparent. Antialiased text edges are kept smooth via a luminance-based alpha mask.',
    )),
    ('THUMBNAIL_WHITENESS_THRESHOLD', _spec(
        'THUMBNAIL_WHITENESS_THRESHOLD', 'int', group='Thumbnails',
        label='Whiteness threshold (0–255)',
        help='Pixels brighter than this are considered "white" when cropping trailing whitespace. Higher = more aggressive cropping.',
        min=0, max=255,
    )),
    ('THUMBNAIL_BOTTOM_PADDING_PX', _spec(
        'THUMBNAIL_BOTTOM_PADDING_PX', 'int', group='Thumbnails',
        label='Bottom padding (px)',
        help='Extra blank space kept below the content after auto-cropping. 0 hugs the content tightly; 24 leaves a small margin.',
        min=0, max=500,
    )),
    ('THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP', _spec(
        'THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP', 'bool', group='Thumbnails',
        label='Symmetric horizontal cropping',
        help=(
            "When ON, the left and right margins are cropped by the SAME "
            "amount — min(left_white_margin, right_white_margin). The content's "
            "relative position on the original A4 page is preserved, so "
            "asymmetric layouts (right-aligned text, indented content, "
            "left-justified equations with trailing whitespace) don't get "
            "their position collapsed by the bbox crop. "
            "Note: for content that's already centred on the page, this "
            "behaves identically to tight cropping (both margins are equal). "
            "When OFF (default), both sides are cropped tight to the content "
            "bounding box — short asymmetric questions look stretched when "
            "displayed in a card."
        ),
    )),

    # Batch image generation
    ('BATCH_IMG_DEFAULT_WIDTH', _spec(
        'BATCH_IMG_DEFAULT_WIDTH', 'int', group='Batch IMG Generation',
        label='Default render width (px)',
        help='Pre-fills the width field in the bulk "Generate IMG from DOC/MD" modal on Question Management.',
        min=200, max=4000,
    )),
    ('BATCH_IMG_DEFAULT_STITCH', _spec(
        'BATCH_IMG_DEFAULT_STITCH', 'bool', group='Batch IMG Generation',
        label='Stitch multi-page sources by default',
        help='When ON, the modal defaults to stitching all source pages vertically into one tall PNG. When OFF, it defaults to producing one PNG per source page (multi-part IMG).',
    )),

    # AI Tools
    ('AI_TOOLS_ENABLED', _spec(
        'AI_TOOLS_ENABLED', 'bool', group='AI Tools',
        label='Enable AI Tools',
        help='Master switch for the admin AI Tools (image proofreading + Markdown generation). When OFF, the AI Tools button and endpoints are disabled. Configure LLM endpoints on the dedicated LLM Endpoints page.',
    )),
    ('LLM_IMAGE_MAX_DIM', _spec(
        'LLM_IMAGE_MAX_DIM', 'int', group='AI Tools',
        label='Max image dimension sent to LLM (px)',
        help='Images are downscaled so their longest edge is at most this many pixels before being base64-encoded and sent to the model. Lower = cheaper/faster but less legible; 1600 is a good balance for exam scans.',
        min=256, max=4096,
    )),
    ('EXPLAIN_DEFAULT_LLM', _spec(
        'EXPLAIN_DEFAULT_LLM', 'string', group='AI Tools',
        label='Default LLM for Explain feature',
        help=(
            'LLM endpoint to use for the Explain tutor chat on the dashboard. '
            'Leave blank to auto-select the first enabled, vision-capable '
            'endpoint ordered by sort order then name. The chosen endpoint '
            'does not need to be vision-capable — text-only questions fall '
            'back to Markdown text automatically — but vision is required to '
            'explain image-based questions.'
        ),
        choices_fn=_llm_endpoint_choices,
    )),
    ('LLM_CHAT_TIMEOUT_SECONDS', _spec(
        'LLM_CHAT_TIMEOUT_SECONDS', 'int', group='AI Tools',
        label='Interactive chat timeout (seconds)',
        help=(
            'How long to wait for the LLM to respond to an interactive chat '
            'request — the dashboard Explain tutor and the LLM Endpoints '
            'Chat console. Reasoning models (DeepSeek-R1, Gemma reasoning, '
            'QwQ, etc.) often think for several minutes before producing '
            'visible output, so this defaults much higher than the per-'
            'endpoint timeout (which still applies to fast batch ops like '
            'proofreading). Set to 0 to fall back to the endpoint\'s own '
            'timeout.'
        ),
        min=0, max=3600,
    )),

    # PDF Batch Import
    ('PDF_IMPORT_RASTER_WIDTH', _spec(
        'PDF_IMPORT_RASTER_WIDTH', 'int', group='PDF Import',
        label='PDF page raster width (px)',
        help='Width that uploaded PDF pages are rasterised to. Per-question crops are cut from these high-res page images (the copy sent to the LLM is downscaled separately to "Max image dimension sent to LLM"). Higher = sharper crops but larger temp files; 1700 suits A4 exam papers.',
        min=600, max=4000,
    )),
    ('PDF_IMPORT_COORD_ORDER', _spec(
        'PDF_IMPORT_COORD_ORDER', 'string', group='PDF Import',
        label='Bounding-box coordinate order',
        help='Axis order the vision model uses for detected boxes. "xyxy" = [x1,y1,x2,y2] (Qwen and most models). "yxyx" = [y1,x1,y2,x2] (Gemma / Gemini / PaliGemma family — they put the vertical coordinate first). If detected boxes are shifted or rotated relative to the real questions, flip this. The number range (0–1, 0–1000, or pixels) is auto-detected; only the axis order needs setting.',
        validator=_choice_validator('xyxy', 'yxyx'),
    )),
    ('PDF_IMPORT_DESKEW_DEFAULT', _spec(
        'PDF_IMPORT_DESKEW_DEFAULT', 'bool', group='PDF Import',
        label='Auto-deskew scans by default',
        help='Whether the "Auto-deskew scans" checkbox in PDF Import Setup starts ticked. Deskew straightens skewed/rotated scanned pages during staging (projection-profile angle search, NumPy required). Users can still toggle it per run.',
    )),
    ('PDF_IMPORT_DEFAULT_METHOD', _spec(
        'PDF_IMPORT_DEFAULT_METHOD', 'string', group='PDF Import',
        label='Default detection method',
        help='Detection method pre-selected in PDF Import Setup. "llm" = the model draws boxes (works without NumPy). "refine" = the model draws boxes, then classical CV snaps each edge to the printed content. "segment" = the model only marks where each question starts and CV derives the boxes. refine/segment need NumPy; users can change the method per run and per page.',
        validator=_choice_validator('llm', 'refine', 'segment'),
    )),
])


# ---------------------------------------------------------------------------
# Bootstrap defaults captured from app.config at first load.
# Used by reset() to restore the .env/Config default after a DB row is removed.
# ---------------------------------------------------------------------------
_BOOTSTRAP_DEFAULTS: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def coerce(key: str, raw_value: Any) -> Any:
    """Parse a raw value (str / int / bool / float) into the registry type
    for `key` and run its validator. Raises ValueError on invalid input.

    Public-side helper so the admin route can use the same validation that
    `set()` would apply, without committing anything.
    """
    spec = REGISTRY.get(key)
    if not spec:
        raise KeyError(f'Unknown setting key: {key}')
    coercer = _COERCERS[spec['type']]
    parsed = coercer(raw_value)
    validator = spec.get('validator')
    if validator:
        validator(parsed)
    return parsed


def load_all(app) -> None:
    """Bootstrap pass — called once from create_app() after `db.init_app(app)`.

    1. Snapshot each REGISTRY key's current `app.config` value into
       `_BOOTSTRAP_DEFAULTS` (so reset() can restore the .env default).
    2. Read every row from `system_settings` and overwrite `app.config[key]`
       with the parsed DB value.

    Robust against the case where the table doesn't exist yet (fresh DB
    before `init_db.py` runs) — those errors are swallowed and the bootstrap
    defaults remain authoritative.
    """
    # Step 1: capture bootstrap defaults from the live app.config.
    for key in REGISTRY:
        if key in app.config:
            _BOOTSTRAP_DEFAULTS[key] = app.config[key]
        else:
            _BOOTSTRAP_DEFAULTS[key] = None

    # Step 2: load DB overrides, if the table exists.
    from app import db
    from app.models import SystemSetting

    try:
        with app.app_context():
            rows = SystemSetting.query.all()
            for row in rows:
                if row.key not in REGISTRY:
                    logger.warning('SystemSetting row %r is unknown; ignored', row.key)
                    continue
                try:
                    raw = json.loads(row.value)
                except (ValueError, TypeError):
                    logger.warning('SystemSetting %r has malformed JSON value %r; using bootstrap default', row.key, row.value)
                    continue
                try:
                    parsed = coerce(row.key, raw)
                except (ValueError, KeyError) as e:
                    logger.warning('SystemSetting %r failed validation (%s); using bootstrap default', row.key, e)
                    continue
                app.config[row.key] = parsed
                logger.info('SystemSetting loaded: %s = %r', row.key, parsed)
    except SQLAlchemyError as e:
        logger.warning('Could not load system_settings (table missing?): %s', e)
        # silently use bootstrap defaults — table will be created by init_db.py


def get(key: str) -> Any:
    """Read the live value of a setting. Equivalent to `current_app.config.get`."""
    from flask import current_app
    return current_app.config.get(key)


def set_value(key: str, raw_value: Any, user_id: int | None = None) -> Any:
    """Validate, persist (upsert into `system_settings`), and mirror to
    `app.config`. Returns the parsed value.

    Raises ValueError / KeyError on invalid input; the caller maps these
    to a 400 response.
    """
    from flask import current_app
    from app import db
    from app.models import SystemSetting

    parsed = coerce(key, raw_value)
    encoded = json.dumps(parsed)

    row = SystemSetting.query.get(key)
    if row is None:
        row = SystemSetting(key=key, value=encoded, updated_by=user_id)
        db.session.add(row)
    else:
        row.value = encoded
        row.updated_by = user_id
    db.session.commit()

    # Mirror to app.config so subsequent reads see the new value immediately.
    current_app.config[key] = parsed
    logger.info('SystemSetting saved: %s = %r (by user %s)', key, parsed, user_id)
    return parsed


def reset(key: str, user_id: int | None = None) -> Any:
    """Delete the DB row for `key` (if any) and restore `app.config[key]`
    to the bootstrap default captured at startup. Returns the restored
    default value."""
    from flask import current_app
    from app import db
    from app.models import SystemSetting

    if key not in REGISTRY:
        raise KeyError(f'Unknown setting key: {key}')

    row = SystemSetting.query.get(key)
    if row is not None:
        db.session.delete(row)
        db.session.commit()

    default = _BOOTSTRAP_DEFAULTS.get(key)
    if default is None:
        # No .env default either — drop from app.config too so consumers
        # can fall back to their own hardcoded defaults.
        current_app.config.pop(key, None)
    else:
        current_app.config[key] = default
    logger.info('SystemSetting reset to default: %s = %r (by user %s)', key, default, user_id)
    return default


def as_dict() -> dict:
    """Return a JSON-friendly snapshot for the admin UI:

        {
          'groups': ['Dashboard', 'Markdown', ...],
          'registry': {
            'KEY': {key, type, group, label, help, min?, max?,
                    value, default, has_override},
            ...
          }
        }

    `value` is the live `app.config` value; `default` is the bootstrap value
    captured at startup; `has_override` is True when a DB row exists.
    """
    from flask import current_app
    from app.models import SystemSetting

    overrides = {row.key for row in SystemSetting.query.all()}
    out_registry = {}
    groups = []
    for key, spec in REGISTRY.items():
        if spec['group'] not in groups:
            groups.append(spec['group'])
        choices_fn = spec.get('choices_fn')
        choices = None
        if choices_fn is not None:
            try:
                choices = choices_fn()
            except Exception:
                choices = []
        out_registry[key] = {
            'key': key,
            'type': spec['type'],
            'group': spec['group'],
            'label': spec['label'],
            'help': spec.get('help', ''),
            'min': spec.get('min'),
            'max': spec.get('max'),
            'choices': choices,
            'value': current_app.config.get(key, _BOOTSTRAP_DEFAULTS.get(key)),
            'default': _BOOTSTRAP_DEFAULTS.get(key),
            'has_override': key in overrides,
        }
    return {'groups': groups, 'registry': out_registry}

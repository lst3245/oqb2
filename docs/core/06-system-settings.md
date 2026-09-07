# 06 — System Settings (DB-backed runtime tunables)

> `system_settings` is a key/value table whose rows override the `.env` / `Config` bootstrap defaults at runtime. The REGISTRY in `app/settings.py` is the source of truth for each tunable's type, default, label, help, and validator. Decision record: [ADR-004](../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md).

## Files

| Concern | File |
|---|---|
| Model | `app/models.py` `SystemSetting` (`key` PK, `value` JSON text, `updated_at`, `updated_by`) |
| REGISTRY + `load_all` / `get_value` / `set_value` / `reset` / `as_dict` | `app/settings.py` |
| Routes | `app/admin.py` `settings_page` (`GET /admin/settings`), `settings_data` (`GET /admin/settings/data`), `settings_save` (`POST /admin/settings/save`), `settings_reset` (`POST /admin/settings/reset/<key>`) — all `@super_admin_required` |
| UI | `templates/admin_settings.html` (auto-renders every registry row; `choices_fn` → `<select>`) |
| Bootstrap defaults | `app/config.py` `Config` — what `reset()` restores; `.env.example` documents them |

## Precedence

```
module literal  <  Config default  <  .env  <  system_settings row
```

`app.config[KEY]` always holds the winner. **Consumers must read `current_app.config[KEY]`** (or `app.config[KEY]` inside a thread's `app_context`). Never re-read `os.getenv` or `Config.KEY` at call time.

## Lifecycle

1. **Startup** — `create_app()` calls `settings.load_all(app)`: snapshot every registry key's current `app.config` value into `_BOOTSTRAP_DEFAULTS`, then overwrite `app.config[key]` from each valid row. Missing table / bad JSON / failed validation → log and keep bootstrap default; never raises.
2. **Save** — `POST /admin/settings/save` body `{key: value, ...}`; each key validated, upserted, mirrored to `app.config`; response carries per-key `errors` so partial saves apply the good rows.
3. **Reset** — `POST /admin/settings/reset/<key>` deletes the row and restores `_BOOTSTRAP_DEFAULTS[key]`.

Hot reload is immediate for a single process. Multi-worker deployments would see the change only in the worker that handled the POST — the app is single-process by assumption.

## REGISTRY (keep in sync with `app/settings.py`)

Entry: `_spec(key, type, group, label, help, min?, max?, validator?, choices_fn?)`; types `int | float | bool | string`. `_choice_validator(...)` restricts strings; `_llm_endpoint_choices` lists enabled LLM endpoints for `<select>` rendering.

| Group | Key | Type | Default | Meaning |
|---|---|---|---|---|
| Dashboard | `QUESTIONS_PER_PAGE` | int | 20 | page size cap |
| Markdown | `MD_MAX_SIZE_BYTES` | int | 5 MiB | max `.md` asset size |
| Word COM | `WORD_COM_TIMEOUT` | int s | 300 | intended per-job limit — **registered but not read by `app/word_com.py`** (no watchdog exists yet) |
| Word COM | `WORD_COM_LOCK_TIMEOUT` | int s | 600 | max wait for the global Word lock |
| Thumbnails | `DOC_THUMBNAIL_WIDTH` | int px | 1000 | DOC first-page render width |
| Thumbnails | `THUMBNAIL_TRANSPARENT` | bool | False | white → alpha |
| Thumbnails | `THUMBNAIL_WHITENESS_THRESHOLD` | int 0–255 | 250 | background cutoff |
| Thumbnails | `THUMBNAIL_BOTTOM_PADDING_PX` | int | 24 | padding kept on every side after auto-crop |
| Thumbnails | `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP` | bool | False | cap L/R crop at `min(left, right)` to keep A4-relative position |
| Batch IMG Generation | `BATCH_IMG_DEFAULT_WIDTH` | int px | 1500 | pre-fill for bulk DOC/MD → IMG |
| Batch IMG Generation | `BATCH_IMG_DEFAULT_STITCH` | bool | True | stitch multi-page sources |
| AI Tools | `AI_TOOLS_ENABLED` | bool | True | master switch (also gates Explain) |
| AI Tools | `LLM_IMAGE_MAX_DIM` | int 256–4096 | 1600 | long-edge downscale before sending images |
| AI Tools | `EXPLAIN_DEFAULT_LLM` | string (select) | `''` | dashboard Explain tutor endpoint; blank = first enabled vision endpoint |
| AI Tools | `AUTOTAG_DEFAULT_LLM` | string (select) | `''` | Auto-tag (single + batch) |
| AI Tools | `MD_DEFAULT_LLM` | string (select) | `''` | Generate Markdown (+ figure-bbox pass) |
| AI Tools | `CHECK_DEFAULT_LLM` | string (select) | `''` | Proofread / Quick check |
| AI Tools | `SOLVEGEN_DEFAULT_LLM` | string (select) | `''` | Solve → generate ANS / SOL / Answer Text |
| AI Tools | `SOLVECHECK_DEFAULT_LLM` | string (select) | `''` | Solve → check ANS / SOL / Answer Text |
| AI Tools | `PDF_IMPORT_DEFAULT_LLM` | string (select) | `''` | PDF Import bbox detect + paper-name guess (falls back to `EXPLAIN_DEFAULT_LLM`) |
| AI Tools | `SMART_IMPORT_DEFAULT_LLM` | string (select) | `''` | Smart Import "Analyze with AI" — **text-only**, vision not required |
| AI Tools | `LLM_CHAT_TIMEOUT_SECONDS` | int 0–3600 | 600 | interactive chat timeout (Explain, admin chat console); 0 = endpoint's own |
| AI Tools | `LLM_REASONING_EFFORT_DEFAULT` | `off|low|medium|high` | `off` | inherited by endpoints with blank effort |
| AI Tools | `LLM_REASONING_SUMMARY_DEFAULT` | `auto|none` | `auto` | inherited by Responses-API endpoints |
| PDF Import | `PDF_IMPORT_RASTER_WIDTH` | int 600–4000 | 1700 | page raster width for crops |
| PDF Import | `PDF_IMPORT_RASTER_WORKERS` | int 1–32 | 4 | parallel page rasterisation (capped by CPU count) |
| PDF Import | `PDF_IMPORT_COORD_ORDER` | `xyxy|yxyx` | `xyxy` | bbox axis order from the vision model; also used by MD figure cropping |
| PDF Import | `PDF_IMPORT_DESKEW_DEFAULT` | bool | True | "Auto-deskew scans" starts ticked |
| PDF Import | `PDF_IMPORT_DEFAULT_METHOD` | `llm|refine|segment` | `llm` | detection method pre-selected |
| PDF Import | `PDF_IMPORT_CROP_PAD_PCT` | float 0–10 | 0.6 | safety margin (% of page) around every box |
| PDF Import | `PDF_IMPORT_REFINE_GROW_PCT` | float 0–20 | 3.5 | refine: search-window growth |
| PDF Import | `PDF_IMPORT_ASSIST_PAD_PCT` | float 0–10 | 0.6 | refine/segment: content padding |
| PDF Import | `PDF_IMPORT_TRIM_WHITE_DEFAULT` | bool | False | "Trim whitespace" starts ticked |
| PDF Import | `PDF_IMPORT_UNIFORM_WIDTH_DEFAULT` | bool | True | "Uniform width per side" starts ticked |
| Toolbox | `TOOLBOX_DEFAULT_DPI` | int 72–600 | 200 | PDF Tool process/export DPI |
| Toolbox | `TOOLBOX_OCR_DPI` | int 72–600 | 300 | Find & Mark OCR raster DPI |
| Toolbox | `TOOLBOX_OCR_WORKERS` | int 1–32 | 4 | parallel OCR pages (capped by CPU count) |
| Toolbox | `TOOLBOX_OCR_AUTO_ORIENT` | bool | True | retry OCR at 90/180/270° on sparse pages |
| Toolbox | `TOOLBOX_SESSION_RETENTION_HOURS` | int 1–720 | 48 | how long PDF Tool staging sessions stay restorable before purge (no `Config` attribute; registry default only) |
| Markup | `MARKUP_NORMALIZED_MAX_DIM` | int 400–8000 | 2400 | longest-edge world units for imported images |

Not in the registry but stored in `system_settings`: `FILE_BROWSER_EXTRA_ROOTS` (JSON list of absolute paths) — managed by the File Browser's own roots routes; `load_all` logs and ignores it as unknown.

LLM **endpoints** are not settings; they live in `llm_configs` (Admin → LLM Endpoints). See [../modules/ai-tools.md](../modules/ai-tools.md).

## What stays in `.env` only

Secrets and infrastructure: `SECRET_KEY`, `DB_*`, all path roots ([05-storage-and-paths.md](05-storage-and-paths.md)), `PANDOC_PATH`, `TESSERACT_CMD`, `LLM_API_KEY`, `LLM_KEY_SECRET`, `TOOLBOX_RASTER_WIDTH`, `TOOLBOX_EXPORT_WIDTH`, `TOOLBOX_SAVE_SUBDIR`, and anything needing a restart (engine options).

## Adding a tunable

1. Add `('KEY', _spec('KEY', type, group=..., label=..., help=..., min/max or validator, choices_fn?))` to `REGISTRY`.
2. Add `KEY = os.getenv('KEY', default)` to `Config` in `app/config.py` (with the same coercion as neighbours).
3. Add a commented line to `.env.example` under the right group with `[tunable]`.
4. Read it via `current_app.config['KEY']` in the consumer.
5. Add the row to the table above. The UI needs no changes.

## Gotchas

- Do not hardcode tunable values in modules; accept a `None` argument and resolve from config.
- Capture `app = current_app._get_current_object()` before starting a thread; read config inside `with app.app_context()`.
- `load_all` must never raise; a corrupt row falls back to the bootstrap default.
- Validate at write time (`set_value`) so admins get a clean per-key error.
- Settings routes are super-admin only; `@admin_required` is not enough for system-wide changes.

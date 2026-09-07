# DOC source format (Word COM)

> `QuestionAsset.file_format='DOC'` is the high-fidelity, single-slot source format for content authored in Microsoft Word (MathType OLE, embedded images, native tables). Everything Word-related runs through `app/word_com.py`: one global lock, a fresh headless WINWORD.EXE per session, `Selection.InsertFile` native merge into generated documents, `ExportAsFixedFormat` PDF output, and PyMuPDF-rasterised first-page PNG thumbnails cached per asset id.

## Files

| File | Role |
|---|---|
| `app/word_com.py` | Word COM engine: `IS_AVAILABLE`, `WordComUnavailable`, `_WORD_COM_LOCK`, `word_session`, `merge_doc_into_master`/`_insert_one`, `export_to_pdf`, `render_first_page_png`, `sanitize_docx_for_insertion`, `_compute_crop_box`, `_save_cropped_png`, `_kill_word_processes_started_after`. |
| `app/doc_thumbnails.py` | Thumbnail lifecycle: `thumbnail_path`/`thumbnail_exists`/`delete_thumbnail`, `render_doc_thumbnail_sync`, `schedule_thumbnail`, `ensure_thumbnail` (lazy + cooldown), `force_rerender`, slot hooks `on_doc_asset_created`/`on_img_asset_created`/`on_doc_asset_deleted`/`on_img_asset_deleted`. |
| `app/generator.py` | DOC branch of `add_question_content_to_doc` (marker paragraphs + `doc_insertions`), `_run_word_postprocess_single`/`_run_word_postprocess_split`, lazy PDF: `_pdf_sibling_filename`, `_build_pdf_from_docx`, `_build_pdf_zip_from_docx_zip`, route `download_pdf`. `get_viewer_asset` attaches `thumbnail_url` for DOC. |
| `app/batch_image_gen.py` | Batch "Generate IMG from DOC/MD": `render_doc_to_pages`, `render_md_to_pages`, `stitch_vertically`, `replace_img_assets`; reuses the thumbnail crop/transparency primitives. |
| `app/admin.py` | `upload_question_asset`/`delete_question_asset` lifecycle hooks, `rerender_doc_thumbnail`, `doc_thumbnail_backfill` (SSE), `doc_thumbnail_clear`. |
| `app/dashboard.py` | `doc_thumbnail` route; preview resolver `mode: 'thumbnail'`; `filter_questions`/`get_question_preview` call `ensure_thumbnail`. |
| `app/ingestor.py` | `determine_file_format` (`doc`/`docx` → `DOC`), `upsert_asset`, `scan_directory_stream` schedules thumbnails best-effort, `sync_database` drops orphan PNGs. |
| `app/config.py`, `app/settings.py` | Bootstrap defaults + System Settings registry (groups "Word COM", "Thumbnails"). |
| `templates/base.html` | `oqbLoadMarkdownPreviewCards`, `oqbPollDocThumbnails`, `oqbRerenderThumb`, `_oqbBuildThumbHtml`, `window.OQB_IS_ADMIN`. |
| `templates/dashboard.html`, `templates/partials/question_list.html` | `previewAsset` modal + card list wiring for the poller. |
| `templates/viewer.html` | `loadAsset`/`loadAnswerAsset` show DOC thumbnails (no rerender button; does not extend `base.html`). |
| `templates/admin_questions.html` | `#batchImgModal` (Generate IMG) and DOC single-slot upload cards. |
| `templates/admin_health.html` | "DOC Asset Thumbnails" card: Backfill Missing / Force Re-render All / Delete All. |

## Tables

`QuestionAsset` (`app/models.py`): `file_format` enum includes `DOC`; slot = `(question_id, asset_type, version)`; `part_number` is always 1 for DOC; `file_path` is relative to `SOURCE_PATH`. `GeneratedFile.filename` drives the PDF sibling mapping. No DOC-specific tables; thumbnails are files, keyed by `QuestionAsset.id`. See `../core/03-data-model-and-migrations.md`.

## Routes

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/dashboard/api/doc_thumbnail/<int:asset_id>.png` | `@login_required` + subject access (`has_subject_access`, super admin bypass) | Serve cached PNG. 404 when not on disk (frontend falls back to the download stub / starts polling). `send_file(conditional=True)` + `Cache-Control: private, no-cache, must-revalidate`. |
| POST | `/admin/questions/<int:question_id>/assets/<int:asset_id>/rerender-thumb` | `@admin_required` (subject admin) | Delete cached PNG and `force_rerender(asset_id)` (bypasses cooldown). Drives the per-preview Re-render button. |
| GET | `/admin/health/doc-thumbnails/backfill?force=0|1` | `@super_admin_required` | **SSE**: render missing PNGs (`force=0`) or every DOC (`force=1`). |
| POST | `/admin/health/doc-thumbnails/clear` | `@super_admin_required` | Delete every cached PNG under `DOC_THUMBNAIL_PATH`; lazy resolver recreates on demand. |
| GET | `/generate/pdf/<int:file_id>` | `@login_required` + `_user_can_view_file` (owner / super admin / shared-with) | Lazy PDF for a completed `GeneratedFile`: serve the sibling if present, else build it via Word (`_build_pdf_from_docx` or `_build_pdf_zip_from_docx_zip`), cache next to the source, then send. Errors are JSON: 403 access, 409 not completed, 400 not convertible, 404 source missing, 503 Word unavailable, 500 build failed. |
| GET | `/admin/questions/batch-generate-images` | `@admin_required` | **SSE** batch DOC/MD → IMG (params in `admin-panel.md`). |
| POST | `/admin/questions/<int:question_id>/assets/upload` | `@admin_required` | Upload `.docx` into a slot; rejects a second DOC in the same slot; fires `on_doc_asset_created`. |
| POST/DELETE | `/admin/questions/<int:question_id>/assets/<int:asset_id>/delete` | `@admin_required` | Delete; fires `on_doc_asset_deleted` / `on_img_asset_deleted`. |

Generation routes (`POST /generate/create`, `/generate/status/<id>`, `/generate/download/<id>`) are documented in `generator.md`.

## Business rules / invariants

### Locked decisions

1. **Single-slot**: one DOC per `(question, asset_type, version)`. No `_2.docx` multi-part; upload rejects a second DOC into the same slot. Filename `<QID>_<VERSION>_<ATYPE>.docx` (no `_PART` suffix). Ingest accepts `.doc` and `.docx` extensions.
2. **Merge engine is Word COM via `pywin32`, NOT docxcompose.** XML-splicing libraries can corrupt MathType OLE references. `Selection.InsertFile` is what Word itself does for Insert → Text from File, so output matches opening the source by hand.
3. **Section properties are stripped** from each source before insertion (`sanitize_docx_for_insertion` removes `<w:sectPr>` from `word/document.xml`). The master's A4 + margins + page numbers always win; a source's own page setup never leaks in.
4. **Global lock**: `_WORD_COM_LOCK = threading.Lock()` — exactly one Word session per process across generation jobs, lazy PDF builds, thumbnail renders, and batch IMG. `word_session(lock_timeout=WORD_COM_LOCK_TIMEOUT)` raises `TimeoutError` ("another generation is in progress") when the wait exceeds the bound. The lock is non-reentrant.
5. **Fresh Word per session**: `win32com.client.DispatchEx('Word.Application')` starts a NEW WINWORD.EXE (never attaches to an interactive user's instance). `Visible=False`, `DisplayAlerts=wdAlertsNone`, `ScreenUpdating=False`. `pythoncom.CoInitialize()`/`CoUninitialize()` bracket the session in the calling thread. Callers must be in a background thread.
6. **Cleanup**: on exit `word.Quit(SaveChanges=wdDoNotSaveChanges)`; if Quit raises, `_kill_word_processes_started_after(session_start)` kills WINWORD.EXE processes created since the session began (psutil), or falls back to `taskkill /f /im WINWORD.EXE` (kills ALL Word instances — acceptable only on a dedicated server). The lock is always released in `finally`.
7. **Non-Windows / no pywin32**: `word_com.IS_AVAILABLE` is False (probed once at import, gated on `sys.platform == 'win32'`). Entry points raise `WordComUnavailable`. The DOC branch of generation falls back to an italic placeholder line, thumbnails are silently skipped, and `GET /generate/pdf/<id>` returns 503.
8. **Output format at generation time is always DOCX.** `create_document` pins `output_format = 'DOCX'` and there is no create-time Output Format radio. PDF is produced lazily from My Files / the Generate success banner via `GET /generate/pdf/<id>`.

### Generation merge (DOC-specific part only)

1. `add_question_content_to_doc` DOC branch: when `doc_insertions is not None and word_com.IS_AVAILABLE`, emit a unique marker paragraph `__OQB_DOC_INSERT_<uuid>__` and record `doc_insertions[marker] = abs_path`. Otherwise emit the legacy italic placeholder.
2. `create_word_document(...)` returns `(doc, doc_insertions)`.
3. `_generate_in_background`: if `doc_insertions` is empty, save the docx directly (no Word). Otherwise save to a temp `.docx` and call `_run_word_postprocess_single(app, intermediate_docx, doc_insertions, 'DOCX', final_path)` → `word_session` → `merge_doc_into_master(word, tmp, doc_insertions)`.
4. `merge_doc_into_master` opens the master (`ReadOnly=False`), and for each marker `_insert_one`: `Find` the marker text (MatchCase, no wrap) → `Expand(wdParagraph)` → `Select` → `Delete` → sanitise the source to a temp copy → `Selection.InsertFile(FileName=..., ConfirmConversions=False, Link=False, Attachment=False)`. Missing source / sanitise failure / InsertFile failure each leave a bracketed placeholder line (`[DOC source missing: …]`, `[Error preparing …]`, `[Error inserting …]`) and continue. Finally `doc.Save()` and `Close(SaveChanges=0)`.
5. Split jobs: `_run_word_postprocess_split(app, merge_jobs, tmpdir, output_format)` performs N merges inside ONE `word_session` (much faster than one Word per group).
6. Lazy PDF: `_pdf_sibling_filename` maps `*.docx` → `*.pdf`, `*.zip` → `*.pdf.zip`, anything else → `None` (UI hides the PDF button). `_build_pdf_zip_from_docx_zip` extracts, converts each `.docx` inside a single `word_session`, copies non-docx entries through, and re-zips. Deleting a My Files row removes both the source and the cached sibling.
7. `export_to_pdf` opens `ReadOnly=True` and calls `ExportAsFixedFormat(ExportFormat=wdExportFormatPDF(17), OptimizeFor=Print, Range=AllDocument, Item=DocumentContent, IncludeDocProps=False, KeepIRM=True, CreateBookmarks=None, DocStructureTags=True, BitmapMissingFonts=True, UseISO19005_1=False)`.

For answer modes, spacing, split ZIP grouping, styles and the generation options JSON see `generator.md`.

### Thumbnail lifecycle (slot-scoped)

```
DOC created  → IMG in slot? yes → no-op (IMG resolver wins anyway)
                             no  → schedule async Word render → <asset_id>.png
IMG created  → delete any DOC thumbnails in the same slot
DOC deleted  → delete <asset_id>.png
IMG deleted  → IMG still in slot? yes → no-op
                                  no  → re-schedule DOC thumbnails for the slot
Preview path → ensure_thumbnail(asset_id):
                 PNG on disk → True (resolver uses mode 'thumbnail')
                 else        → schedule render, return False; resolver returns 'download' now;
                               frontend poller swaps in the PNG when it appears
```

- Files: `<DOC_THUMBNAIL_PATH>/<asset_id>.png`. Keyed by **asset id**, so question renames (which change `file_path`) do not invalidate. Width `DOC_THUMBNAIL_WIDTH` (default 1000 px ≈ A4 at 96 DPI).
- Slot check `_slot_has_img(question_id, asset_type, version)` mirrors the preview resolver's winner logic: EN-DOC and CH-IMG coexist; only an IMG in the SAME slot suppresses the DOC thumbnail. `render_doc_thumbnail_sync` re-checks and deletes a stale PNG if an IMG now wins.
- Trigger points: `admin.upload_question_asset` / `delete_question_asset` post-commit, `ingestor.scan_directory_stream` per file (best effort), `ingestor.sync_database` orphan cleanup, and the lazy `ensure_thumbnail` calls in `dashboard.filter_questions`, `dashboard.get_question_preview`, `generator.get_viewer_asset`.
- Render path: `render_first_page_png` → `export_to_pdf` to a temp PDF → PyMuPDF page 0 at `zoom = width_px / page.rect.width` → `_save_cropped_png`.

### Scheduler retry-backoff (`doc_thumbnails.py`)

- `_INFLIGHT` set (guarded by `_INFLIGHT_LOCK`) dedupes concurrent renders; a 20-card page does not queue 20 threads for one asset.
- `_INFLIGHT` is cleared in `finally` after every attempt — a transient failure never marks an asset broken until restart (the old behaviour caused frontend 404 loops).
- `_LAST_ATTEMPT[id]` + `_RETRY_COOLDOWN_S = 5.0`: `ensure_thumbnail` within the cooldown is a no-op; beyond it, retry.
- `force_rerender(id)` deletes the PNG, resets the cooldown, schedules immediately (returns True if already in flight), and returns False when Word is unavailable (caller shows an error toast).
- All renders run in daemon threads created from `current_app._get_current_object()`; each acquires the global Word lock.

### Whitespace cropping + transparency (`_save_cropped_png`)

1. Pixmap → PIL RGB.
2. Content mask: `ImageChops.subtract(ref, ImageChops.darker(img, ref))` against a flat reference at `THUMBNAIL_WHITENESS_THRESHOLD` (default 250); antialiased grey still counts as content. `getbbox()` = tight content rectangle.
3. Crop via `_compute_crop_box(img_size, bbox, pad, symmetric_horizontal)`: vertical always tight + `pad` (`THUMBNAIL_BOTTOM_PADDING_PX`, default 24, applied on all four sides); horizontal tight + pad, OR when `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP` is on, both sides cropped by `min(left_white, right_white) - pad` so content keeps its proportional position on the A4 page (short centred equations do not blow up ~2.7× at uniform card width). Shared with `batch_image_gen._pdf_to_cropped_images`.
4. Entirely white page → 400×200 px blank crop so the resolver never loops waiting for "real" content.
5. `THUMBNAIL_TRANSPARENT` on → RGBA with `alpha = 255 − luminance` (`ImageOps.invert` of the L channel): white → transparent, grey → partial, black → opaque. `optimize=` is skipped for RGBA (slow on large PNGs).
6. Atomic write: `<png>.tmp` then `os.replace`; readers never see a partial file.
7. Any exception → fall back to the raw un-cropped pixmap (`logger.exception`), so a thumbnail always lands.

`render_first_page_png(..., transparent=, whiteness_threshold=, bottom_padding_px=, symmetric_horizontal_crop=)` accepts overrides; `None` values resolve from `current_app.config` via `_resolve_thumb_tunables` (module literals when no app context, e.g. unit tests).

### Frontend polling / rerender (`base.html`)

- `window.oqbPollDocThumbnails(root)` scans for `[data-doc-pending-id]` elements and probes the thumbnail URL every `_OQB_THUMB_INTERVAL_MS = 3000` up to `_OQB_THUMB_MAX_ATTEMPTS = 60` (~3 min — generous because a render may wait on the Word lock). Same cache-busted URL (`?t=<Date.now()>`) for the `Image()` probe AND the swapped `<img src>`, so the fresh probe result is reused. Per-element dedup via `data-doc-poller-active`. Auto-runs on `DOMContentLoaded` and `htmx:afterSwap`; also invoked by `oqbLoadMarkdownPreviewCards` (when the resolver returns `mode='download'` for a DOC), `dashboard.html previewAsset`, and `viewer.html loadAsset`/`loadAnswerAsset`.
- `window.oqbRerenderThumb(questionId, assetId, triggerEl)` (admins only, gated on `window.OQB_IS_ADMIN`): POST rerender → replace container with a "Re-rendering preview..." placeholder carrying `data-doc-pending-id` / `-question-id` / `-filename` / `-downloadurl` → drop `data-doc-poller-active` → `oqbPollDocThumbnails(container.parentNode || container)`.
- `_oqbBuildThumbHtml(thumbUrl, filename, downloadUrl, assetId, questionId)` renders image + download + rerender button; the button is hidden unless BOTH ids are present.
- The viewer intentionally has no rerender button (it does not extend `base.html`).

### Backfill / clear (Database Health page)

Use **Backfill Missing** (`force=0`) after upgrading to a build with thumbnails or after wiping the folder; **Force Re-render All** (`force=1`) after changing `DOC_THUMBNAIL_WIDTH` or cropping settings (renders are NOT invalidated by config or code changes); **Delete All** to reclaim disk (lazy resolver recreates on demand).

### Batch IMG generation (DOC/MD → IMG)

```
DOC → sanitize_docx_for_insertion → Word.ExportAsFixedFormat → PDF → PyMuPDF N pages → crop + transparency → stitch (or N parts) → replace IMG asset(s)
MD  → md_to_docx_via_pandoc (app/generator.py) → same Word path
```

Replacement is atomic: temp files → delete old IMG rows + files → rename temps → insert rows + commit → `on_img_asset_created` clears the now-eclipsed DOC thumbnail. See `admin-panel.md` for the SSE parameters.

### Security model

DOC content is admin-authored. The server only runs Word headlessly on `<SOURCE_PATH>/...` files (`ReadOnly=True` for exports), inserts into a master it just created, and strips `<w:sectPr>` via python-docx/XML before handing the file to Word. No untrusted content reaches Word; the threat model matches the MD pipeline (trusted authors, compromised-admin concern).

## Settings & config keys

See `../core/06-system-settings.md`. Paths stay `.env`-only.

| Key | Where | Default | Meaning |
|---|---|---|---|
| `WORD_COM_TIMEOUT` | System Settings (Word COM), `.env` | 300 (30–3600) | Per-job watchdog seconds. **Defined but not enforced** anywhere in `app/` today (registry help text says "reserved for future watchdog"). |
| `WORD_COM_LOCK_TIMEOUT` | System Settings (Word COM), `.env` | 600 (10–7200) | Max wait for `_WORD_COM_LOCK`; on timeout the job fails with a clear `GeneratedFile.error_message`. |
| `DOC_THUMBNAIL_PATH` | `.env` only | see `app/config.py` | Directory for `<asset_id>.png`. |
| `DOC_THUMBNAIL_WIDTH` | System Settings (Thumbnails) | 1000 (200–4000) | Render width in px. |
| `THUMBNAIL_TRANSPARENT` | System Settings (Thumbnails) | off | Luminance alpha mask. |
| `THUMBNAIL_WHITENESS_THRESHOLD` | System Settings (Thumbnails) | 250 (0–255) | Pixels brighter than this are background. |
| `THUMBNAIL_BOTTOM_PADDING_PX` | System Settings (Thumbnails) | 24 (0–500) | Padding kept around content (all sides). |
| `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP` | System Settings (Thumbnails) | off | Crop both sides by the smaller white margin. |
| `BATCH_IMG_DEFAULT_WIDTH`, `BATCH_IMG_DEFAULT_STITCH` | System Settings (Batch IMG) | — | Defaults for the Generate IMG modal. |

External dependencies (see `../core/01-runtime-and-ops.md`): Microsoft Word (with MathType compatibility), `pywin32` (Windows-only marker in `requirements.txt`), PyMuPDF, optional `psutil` for precise WINWORD.EXE cleanup.

## Permissions

- Thumbnail PNG: any logged-in user with `has_subject_access(question.subject)` (super admin bypass).
- Rerender single thumbnail, upload/delete DOC, batch IMG: `@admin_required` (subject admin for that question).
- Backfill / clear all thumbnails: `@super_admin_required`.
- Lazy PDF: anyone who can view the `GeneratedFile` (`_user_can_view_file`).
- Word COM tunables: super admin via System Settings.

## Background work / SSE / threads

- Generation runs in a daemon thread spawned from `create_document`; Word merge happens inside it.
- Thumbnail renders: daemon threads from `schedule_thumbnail` / `ensure_thumbnail` / `force_rerender`, serialised by the global Word lock.
- `GET /generate/pdf/<id>` is **synchronous**: the request holds until Word finishes (and waits on the lock up to `WORD_COM_LOCK_TIMEOUT`).
- SSE: `/admin/health/doc-thumbnails/backfill`, `/admin/questions/batch-generate-images`.

## Gotchas

1. **Never** call a Word COM API outside `word_session` — you leak the global lock and orphan WINWORD.EXE.
2. **Never** call `merge_doc_into_master` (or open a second `word_session`) from inside another job's session — the lock is non-reentrant and you deadlock.
3. **Never** import `pywin32` at module top level outside `app/word_com.py`. `from app import word_com` is always safe; entry points raise `WordComUnavailable` on non-Windows.
4. Key thumbnails by `asset_id`, never `file_path` — renames keep the id.
5. Slot check is `(question_id, asset_type, version)`; do not add cross-version suppression.
6. Do not add a long `Cache-Control: max-age` to the thumbnail endpoint — the rerender button depends on `no-cache, must-revalidate` + ETag revalidation.
7. Commit (`db.session.commit()`) BEFORE calling any `on_*_asset_*` hook; they query the DB.
8. `word_com.IS_AVAILABLE` is the single source of truth; the DOC generation branch, the scheduler, `_run_word_postprocess_*`, and `/generate/pdf` all gate on it.
9. Import `word_com` at module scope in files that use it (e.g. `app/admin.py`) — endpoint-level `IS_AVAILABLE` guards run before SSE generator closures.
10. Carry `question_id` AND `asset_id` through the rerender flow (`data-doc-pending-question-id`); the button hides if either is missing.
11. Sources may carry their own page setup — `sanitize_docx_for_insertion` exists precisely so the master layout wins.
12. Thumbnail renders are not invalidated by config/code changes; use Force Re-render All after changing width or crop settings.
13. `WORD_COM_TIMEOUT` is advisory only; a hung Word call is bounded only by other callers' `WORD_COM_LOCK_TIMEOUT` waits and the Quit/taskkill fallback on session exit.
14. Without psutil the fallback `taskkill` kills every Word instance on the machine.
15. Generation never produces PDF; anything that reads `output_format == 'PDF'` at create time is dead code kept for compatibility with saved presets (ignored on load).

## Related

- `generator.md` — full generation pipeline (answer modes, split ZIP, styles, options JSON, My Files PDF button).
- `md-format.md` — the independent pandoc/docxcompose MD pipeline that coexists in `add_question_content_to_doc`.
- `admin-panel.md` — upload/delete/batch IMG routes; `markup.md` — DOC thumbnails openable in Markup.
- `../decisions/ADR-003-word-com-for-doc-merge-and-pdf.md`
- `../core/01-runtime-and-ops.md` — Windows / Word / pywin32 / PyMuPDF prerequisites.
- `../core/05-storage-and-paths.md` — `SOURCE_PATH`, `DOC_THUMBNAIL_PATH`, generated-file dirs.
- `../core/06-system-settings.md` — Word COM / Thumbnails registry groups.

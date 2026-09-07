# Ingestion and Smart Import

> How question files on disk become `Question` + `QuestionAsset` rows: the strict filename scanner (`app/ingestor.py`, CLI `ingest`/`sync`, Library scan), and the heuristic **Smart Import** folder importer (`app/smart_import.py`, `/admin/import`).

One admin page, `/admin/import` (`templates/admin_smart_import.html`), exposes both modes:

- **Library scan** — strict scan of `SOURCE_PATH/<subject>`; files must already be named canonically and sit in the canonical folder. Backed by `ingestor.preview_source_directory` + `scan_directory_stream` via `GET /admin/ingestion/preview` and `GET /admin/ingestion/start`.
- **Folder import** — heuristic matching of one or more arbitrary folders (server-side via the file selector, or uploaded from the browser with `webkitdirectory`) onto existing questions/slots, with old-vs-new review, then a format-aware apply that copies files into canonical `SOURCE_PATH` locations.

`GET /admin/ingestion` 302-redirects to `/admin/import` (query string forwarded). The old `admin_ingestion.html` template is gone.

## Files

| File | Role |
|---|---|
| `app/ingestor.py` | `PP_PATTERN` / `QB_PATTERN` embed `QNO_TOKEN_PATTERN`; `parse_filename`, `construct_qid`, `parse_qno` (integer start via `parse_qno_token`), `extract_folder_metadata`, `determine_file_format`, `determine_question_type`, `upsert_question` (`ensure_question`), `upsert_asset`, `scan_directory`, `scan_directory_stream`, `sync_database`, `sync_database_stream`, `sync_command`, `ingest_command`, `preview_source_directory`, `get_database_stats`, `find_untracked_files`. |
| `app/hierarchy.py` | QNO grammar + `ensure_question`. Spec: [question-hierarchy.md](question-hierarchy.md). |
| `app/smart_import.py` | Folder-import engine: `normalize_profile`, `_apply_rule_to_profile`, `_scan_tokens` (qno via `QNO_TOKEN_RE` on the unsplit stem), `_resolve_file`, `resolve_folder`, `_summarize`, `_canonical_rel`, plan stash (`build_plan`, `save_plan`, `load_plan`, `discard_plan`), upload staging (`create_upload`, `upload_dir`, `discard_upload`), apply (`iter_apply`, `_apply_img`, `_apply_doc`, `_apply_md`, `_ensure_question` → `ensure_question`, `_backup_files`), AI assist (`list_sample_paths`, `infer_structure_rule`). |
| `app/admin.py` | Sections `Ingestion (Admin)` and `Smart Import`: routes below plus helpers `_smart_import_resolve_root`, `_smart_import_sources`, `_smart_import_resolve_source_set`, `_smart_import_build_plan_for_sources`, `_smart_import_filter_subjects`. |
| `cli.py` | Click group: `ingest`, `sync`, `migrate-storage`. |
| `app/batch_image_gen.py` | `replace_img_assets` used by the IMG apply path. |
| `app/doc_thumbnails.py`, `app/md_render.py` | Lifecycle hooks / cache invalidation called on ingest and apply. |
| `app/files_service.py` | `RootRegistry(current_user)` (user scope) resolves server-folder sources. |
| `templates/admin_smart_import.html` | Page: mode toggle, Library scan UI (subject picker, preview tree, SSE log), Folder import UI (source picker, profile form, proposals grid, Compare modal, apply log). |
| `templates/partials/file_selector.html` | `OQBFileSelector` used in multi-folder mode. |

## Tables

Schema reference: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Model | Written |
|---|---|
| `Question` | Created by `upsert_question` / `_ensure_question` via `ensure_question` (find-or-creates ancestors; may insert an empty stem so `..._Q3a` has a parent). Fields: `qid, subject, source, year, paper (PP only), qno, qno_end, parent_id, part, part_sort, q_type` (`determine_question_type`), `level = NULL`, `section = NULL`. Existing rows are fetched, never modified by ingest. Deleted by sync when they have no assets, are > 24 h old, **and** have no children. |
| `QuestionAsset` | Upserted on `(question_id, asset_type, version, file_format, part_number)`; ingest updates `file_path` on an existing row. Deleted by sync when the file is missing. Smart Import replaces/creates rows per format. |
| `Subject` | Read (health stats, admin scoping). Ingest does **not** create subjects; the subject folder name must match an existing `Subject.id`. |

## Routes

Paths relative to `/admin`; all `@login_required` + `@admin_required` (`A`) and scoped to `get_user_admin_subjects()`.

### Library scan

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/ingestion` | A | 302 to `/admin/import`. |
| GET | `/ingestion/preview?subject_id=MATC` | A | 400 without subject, 403 if not an admin subject. JSON `{folders:[{path,total,parseable,skipped}], total_files, parseable_files, skipped_files, subject_path}`; if the folder is missing returns `{error, folders:[], ...}` with 200. |
| GET | `/ingestion/start?subject_id=MATC` | A | **SSE.** Errors (no subject / denied) are streamed as a single `error` event. Otherwise `scan_directory_stream(SOURCE_PATH/<subject>, base_path=SOURCE_PATH)`; events `info|success|skip|error|progress|done`; `done.stats = {processed, skipped, errors, new_questions, new_assets, updated_assets}`. |

### Smart Import (Folder import)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/import` | A | Page. `?mode=library|folder` (default `library`), `?qids=Q1,Q2` restricts matching to those QIDs and disables create-missing. Context: `subjects`, `source_path`, `ai_tools_enabled`, `initial_mode`, `initial_qids`. |
| POST | `/import/analyze` | A | JSON `{sources:[{root_id, rel_path}], profile, qids?}`. Legacy `{root_id, rel_path}` and `{upload_token}` also accepted (`_smart_import_sources`). 403 on inaccessible root, 400 on unreadable folder. Returns `{proposals[], stats, folder (labels joined by '; '), folders[{root_id,root_label,rel_path,label}], profile}`. Proposals for subjects the caller cannot admin are forced to `status:'skip'`. |
| POST | `/import/analyze-llm` | A | Same body plus `endpoint_id?`. 400 if `AI_TOOLS_ENABLED` is false or no endpoint. Runs `infer_structure_rule` on the **first** source, merges the returned rule (minus `notes`) into `profile.rule`, re-resolves every source. Adds `rule`, `rule_raw`, `endpoint` (name) to the analyze response. |
| POST | `/import/prepare` | A | JSON `{<source fields>, items[], profile}`. Items outside admin subjects are dropped; 400 if none remain or no jobs. Builds one plan (single source: `build_plan`; multiple server sources: grouped by each item's `source_root_id`, items whose root was not selected are skipped with reason `source folder not selected`). Returns `{token, count, skipped[{src_rel, reason}]}`. |
| GET | `/import/apply?token=` | A | **SSE.** Loads the plan (streams `error` + `done` if missing/expired), re-filters jobs to admin subjects, runs `iter_apply`, always `discard_plan` in `finally`. |
| POST | `/import/upload` | A | Multipart `files[]` + parallel `paths[]` (`webkitRelativePath`). Stages under `System/.smart_import_uploads/<token>/`, path-escape attempts skipped via `storage.safe_join`. Returns `{upload_token, count, folder_name}`; 400 if nothing saved (staging dir discarded). |
| GET | `/import/uploaded-file?token=&path=` | A | Serves one staged file for the review-grid preview. |

## Business rules / invariants

### Filename and folder grammar (strict scan)

Full grammar: [../reference/filename-convention.md](../reference/filename-convention.md). Summary of what `parse_filename` enforces:

- PP: `SUBJ_(DSE|CE|AL)_YEAR_PAPER_<QNO>_VERSION_TYPE[_PART].EXT` (e.g. `MATC_DSE_2025_P2_Q5_EN_QUE_2.png`, `ECON_DSE_2023_P1_Q23-24_ENO_QUE.png`). QB: `SUBJ_QB_DETAIL_<QNO>_VERSION_TYPE[_PART].EXT` (`DETAIL` has no `_`). `<QNO>` is `Q5` / `Q5a` / `Q3ci` / `Q23-24` (`QNO_TOKEN_PATTERN`). The optional `_PART` after TYPE is IMG page N, not a sub-question letter.
- `VERSION` in `ENO|CHO|EN|CH|BI` — the regex lists the longer official tokens first so `ENO` is not shadowed by `EN`. Parsed key is `parsed['version']`.
- `TYPE` in `QUE|ANS|SOL`; `PART` optional int (default 1); `EXT` maps via `determine_file_format`: `png|jpg|jpeg|gif|bmp` -> IMG, `doc|docx` -> DOC, `md|markdown` -> MD, anything else -> `None` (skipped).
- Expected folders under `SOURCE_PATH`: `<SUBJ>/PP/<SOURCE>/<YEAR>/<PAPER>/` and `<SUBJ>/QB/<DETAIL>/`. `extract_folder_metadata` reads these but the QID is built from the **filename** (`construct_qid`), so a misplaced file still ingests under its filename QID with the actual relative path stored.
- `file_path` is stored relative to the scan **base path** with forward slashes. When scanning a subject subfolder pass `source_path=SOURCE_PATH/<SUBJ>` and `base_path=SOURCE_PATH` so the prefix `MATC/PP/...` is kept; passing the subject folder as base produces wrong paths.
- **MD is single-part**: `upsert_asset` skips any `.md` with `part_number != 1` (logged warning).
- Auto `q_type` (`determine_question_type`): `MATC DSE P1` -> `CQ`, `MATC DSE P2` -> `MC`, `MAT1`/`MAT2` DSE -> `CQ`, everything else (other subjects, QB, CE, AL) -> `NULL`. Only applied when the question is created.
- `scan_directory` (CLI) processes files in `natsorted` order, commits per file, writes unparseable/errored paths to `ingest_errors.log` in the CWD. `scan_directory_stream` yields per-file events instead. Both fire `doc_thumbnails.on_doc_asset_created` / `on_img_asset_created` for newly created rows (best effort).
- **Sync** (`sync_database[_stream]`): deletes `QuestionAsset` rows whose `SOURCE_PATH/<file_path>` is missing (dropping DOC thumbnails), then `Question` rows with zero assets — except those created within the last **24 hours** (grace period for questions mid-creation in the Add wizard) and except rows that still have **children** (empty stems created so a `..._Q3a` file has a parent). `dry_run=True` reports only.
- `find_untracked_files`: parseable files on disk whose relative path is not in any `QuestionAsset.file_path` ("reverse orphans").
- `get_database_stats`: DB-only counts and anomaly lists (cap 500), see [admin-panel.md](admin-panel.md#database-health-admin_healthhtml). `questions_no_assets*` / untagged / no-type / no-level exclude stems. Extra keys: `stems_with_tags`, `parts_missing_que`, `empty_range_stems`.

### CLI (`cli.py`)

```
python cli.py ingest [--source-path P]              # scan_directory(P or SOURCE_PATH); creates app via create_app()
python cli.py sync   [--source-path P] [--dry-run|--no-dry-run] [--force]
```

- `sync` defaults to **dry run**. `--no-dry-run` prompts `Do you want to continue?` unless `--force`; then deletes orphan rows.
- `migrate-storage` is documented in [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md).

### Smart Import engine

- **Profile** (`normalize_profile`): `{subject, source (default DSE), detail, version (default EN), asset_type (default QUE), overwrite (default true), backup (default false), create_missing (default false), rule{}}`. Invalid enum values fall back to defaults. `_apply_rule_to_profile` lets `rule.{subject,source,version,asset_type,detail}` override the defaults.
- **Resolve order per file** (`_resolve_file`): (1) strict `parse_filename` -> `method:'strict'`, `confidence:'high'`; (2) heuristic `_scan_tokens` over folder segments + filename stem: `qno` from the **unsplit** stem first via `QNO_TOKEN_RE` (`Q5` / `Q5a` / `Q23-24` — hyphens in a range token are not split), then a digits-only fallback, then folder segments; `year` (`(19|20)\d{2}`) and `paper` (`P\d{1,3}[A-Za-z]?`) from folders first, `version`/`asset_type` from any token; gaps filled from the profile. Heuristic confidence: PP with year+paper = `high` if version or type was found in the data else `medium`; only one of year/paper = `low`; QB = `medium` with detail else `low`; no qno = `none`.
- **Status**: `skip` (unsupported ext, no QID, no subject-admin access, outside `qid_scope`), `ambiguous` (no subject in profile, or low confidence), `unmatched` (QID not in DB; `accept` = `create_missing`), `overwrite` (slot occupied; `accept` = profile `overwrite`), `add` (empty slot; accepted).
- **Proposal shape**: `{id, src_rel, filename, ext, format, subject, source, year, paper, detail, qno, qno_token, version, asset_type, part, method, confidence, status, qid, note, existing[], existing_count, accept}` plus `source_root_id`, `source_root_label`, `source_rel_path` for server sources. `existing[]` entries: `{asset_id, file_path, part_number, file_format, check_state, issue_text}` (`issue_text` rendered from the existing asset's proofread `check_result` by `_issue_text`).
- **Plan** (`build_plan`): re-validates each edited item (source file exists under the root via `safe_join`, enum values valid, PP has year+paper, QID + subject present) into jobs `{src_abs, src_rel, filename, ext, format, qid, subject, source, year, paper, detail, qno, version, asset_type}`; invalid items go to `skipped[{src_rel, reason}]`. Persisted as JSON at `System/.smart_import/<32-hex token>.json`.
- **Apply** (`iter_apply`), per job, after `_ensure_question` (creates the question only when `create_missing`):
  - IMG -> `batch_image_gen.replace_img_assets(question, atype, version, [PIL.Image], stitch=False, source_path)` — whole-slot replace (all existing IMG parts removed).
  - DOC -> delete old DOC rows + files, copy to `_canonical_rel(...)` (ext `doc`/`docx` else `docx`), insert row, `on_doc_asset_deleted` for old ids, `on_doc_asset_created` for the new row.
  - MD -> size check against `MD_MAX_SIZE_BYTES`, copy to canonical `.md`, update existing row's `file_path` (removing the old file if the name differed) or insert, `md_render.invalidate`.
  - `overwrite=false` skips occupied slots; `backup=true` copies replaced files (rel layout preserved) into `System/ImportBackups/<YYYYmmdd_HHMMSS>/` first.
  - Events `info|success|skip|error|done`; `done.stats = {ok, created_questions, skipped, errors}`.
- `_canonical_rel` mirrors `admin._build_asset_file_path` / `upload_question_asset`; keep the three in sync.
- Sources: **server** entries resolve through `RootRegistry(current_user)` (user scope, so only My Files + accessible `Shared/<subject>` roots); **upload** entries use `base_dir = System/.smart_import_uploads/<token>`, `rel_path = ''`. Both produce absolute `src_abs` in the plan so apply is source-agnostic.
- **AI assist** (`infer_structure_rule`): text-only. Sends up to 80 sample relative paths (`list_sample_paths`) using prompts `SMART_IMPORT_SYSTEM` / `SMART_IMPORT_USER` (vars `subject`, `versions`, `tree`; editable on Admin -> AI Prompts, resolved per endpoint via `ai_prompts.system_prompt` / `render_prompt`). Endpoint = explicit `endpoint_id` (must be enabled) else `llm_client.resolve_default_endpoint('SMART_IMPORT_DEFAULT_LLM', vision_only=False)`. Reply must contain a JSON object; keys `subject, source, version, asset_type, detail, notes` are kept. Raises `RuntimeError` on transport/parse failure (surfaced as 400).

### Frontend (`admin_smart_import.html`)

- `sourceMode` is `'server'` or `'upload'`; `buildSourceBody()` emits `{sources:[...]}` or `{upload_token}` for analyze / analyze-llm / prepare.
- Server folders are picked with `OQBFileSelector.open({mode:'folder', multiple:true, locationKey:'smart-import', ...})`; upload uses `<input webkitdirectory directory multiple>` -> `uploadFolder()` -> `POST /import/upload`.
- Proposals grid: old preview via `/dashboard/files/<path>`; new preview via `GET /files/api/download?root=<source_root_id>&path=` (server) or `/admin/import/uploaded-file` (upload). Per-row QID/version/type edits, bulk accept, status filter, `filteredProposals()`. Version/type selects come from `window.OQB_VERSIONS`. AI endpoint dropdown reuses `GET /admin/questions/ai/endpoints`.
- **Compare modal** (`#compareModal`, fullscreen): old vs new side by side plus the existing asset's `issue_text`; Prev/Next buttons, Left/Right arrow keys, Accept checkbox (`a` toggles) synced back to the grid on close; navigates `filteredProposals()`.
- Question Management's **Import Files** button opens `/admin/import?mode=folder&qids=<selected data-qid list>`.

## Settings & config keys

Reference: [../core/06-system-settings.md](../core/06-system-settings.md).

| Key | Where |
|---|---|
| `SOURCE_PATH` (`.env`) | Scan root; apply destination. |
| `STORAGE_PATH` / `storage.system_path()` | `System/.smart_import/` (plans), `System/.smart_import_uploads/` (uploads), `System/ImportBackups/` (backups). |
| `MD_MAX_SIZE_BYTES` | MD apply size cap. |
| `AI_TOOLS_ENABLED` | Gates `/import/analyze-llm` and the AI UI. |
| `SMART_IMPORT_DEFAULT_LLM` | System Setting: default endpoint for "Analyze with AI" (blank = first enabled endpoint by sort order then name). Text-only; vision not required. |
| Prompts `SMART_IMPORT_SYSTEM`, `SMART_IMPORT_USER` | `app/ai_prompts.PROMPTS_REGISTRY`, group "Smart Import — Structure Inference". |

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md). All routes `@admin_required`. Library scan requires the target subject in `get_user_admin_subjects()`. Folder import filters proposals (analyze), items (prepare) and jobs (apply — re-checked when the token is consumed) to admin subjects; server-folder access is additionally limited by the user-scope `RootRegistry`. The CLI has no auth and operates on everything under `SOURCE_PATH`.

## Background work / SSE / threads

- SSE: `GET /ingestion/start`, `GET /import/apply` (and `/health/sync` in [admin-panel.md](admin-panel.md)). Generators run inside `app.app_context()` and emit `data: {json}\n\n`; headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Unexpected exceptions are converted into an `error` event followed by `done`.
- Ingest/apply commit per file, so a browser disconnect mid-stream leaves earlier files imported.
- DOC thumbnail rendering triggered by ingest/apply happens asynchronously in `app/doc_thumbnails.py`.
- Plan and upload staging files are not garbage-collected automatically except `discard_plan` after apply and `discard_upload` when an upload saves zero files.

## Gotchas

- `base_path` vs `source_path`: the Library scan walks `SOURCE_PATH/<SUBJ>` but must store paths relative to `SOURCE_PATH`. A path without the subject prefix will be reported as a path mismatch and fail to resolve.
- The QID comes from the filename, not the folder. A canonically named file in the wrong folder ingests fine but shows up in `path_mismatches`; Smart Import (or rename with file move) fixes it.
- Ingest never deletes; `sync` never creates. Run `ingest` after adding files, `sync --no-dry-run` after removing files — in that order if both happened.
- `sync` grace period is 24 h from `Question.created_at`; a question created via the Add wizard and left without assets survives sync for a day, then is deleted — unless it still has children (do not "clean up" empty stems).
- `scan_directory` (CLI) writes `ingest_errors.log` to the current working directory, not to `STORAGE_PATH`.
- Ingest does not create `Subject` rows; a new subject folder under `SOURCE_PATH` must be added via Manage Subjects first or `Question.subject` will reference a non-existent id.
- Heuristic mode takes `qno` from the filename stem first (`QNO_TOKEN_RE` before `_SPLIT_RE`); a folder named `Q3` containing `1.png` still resolves to Q1, not Q3. `Q23-24` in the stem is one token, not `Q23` + `24`.
- `analyze-llm` runs inference on the **first** selected source only, then applies the rule to all sources.
- Proposals edited client-side are re-validated by `build_plan`; edits that produce an invalid slot are dropped into `skipped`, not applied.
- Server-source previews and multi-folder apply depend on `source_root_id` — a proposal without it (legacy single-source shape) can only be applied when exactly one source is selected.
- IMG apply is a whole-slot replace: importing one `Q5.png` into a slot that had three parts leaves one part.
- `overwrite` defaults to **true** in `normalize_profile`; `backup` defaults to false. Enable backup before bulk overwrites you may need to undo (`System/ImportBackups/<ts>/`).
- Apply re-checks subject access per job but not root access; the plan already holds absolute `src_abs` paths validated at prepare time.
- Uploaded folders are staged on the server drive under `System/`; large uploads consume storage until the token dir is removed (no auto-cleanup after apply).

## Related

- [question-hierarchy.md](question-hierarchy.md) — `ensure_question`, QNO token, sync stem skip.
- [admin-panel.md](admin-panel.md) — Database Health (stats, untracked, orphan sync) which reuses this module.
- [admin-questions.md](admin-questions.md) — canonical path helper, upload route, `replace_img_assets`.
- [file-browser.md](file-browser.md) — `RootRegistry`, `OQBFileSelector`, `/files/api/download`.
- [pdf-import.md](pdf-import.md) — the other bulk import path (PDF -> IMG assets).
- [ai-tools.md](ai-tools.md), [ai-prompts.md](ai-prompts.md) — endpoint resolution and prompt variants.
- [../reference/filename-convention.md](../reference/filename-convention.md).
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md), [../core/06-system-settings.md](../core/06-system-settings.md).
- [../decisions/ADR-005-unified-storage-tree.md](../decisions/ADR-005-unified-storage-tree.md).

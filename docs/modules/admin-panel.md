# Admin Panel

> Blueprint `admin_bp` (`app/admin.py`, prefix `/admin`): admin index, Subjects, Topics/Chapters, Users, Export/Import CSV, Database Health, plus thin HTTP shells for System Settings, AI Prompts and LLM Endpoints. Question Management is documented separately in [admin-questions.md](admin-questions.md).

`app/admin.py` is ~7300 lines. It is organised into labelled sections with `# ==================== <Section> ====================` comment banners; grep for those banners (or `@admin_bp.route(`) to navigate. Every route below is stacked `@login_required` + one authz decorator.

## Files

| File | Role |
|---|---|
| `app/admin.py` | All `admin_bp` routes. Sections (in file order): Subject Management, Topic Management, Chapter Management, Question Tagging, Question Deletion, Batch Question Update, Question Management, Markdown asset endpoints, User Management, Export / Import, Ingestion, Smart Import, PDF Batch Import, Database Health, DOC Thumbnail Backfill, Batch IMG Generation, AI Tools, Auto Question Tagging, Verification, System Settings, AI Prompts, LLM Endpoints, File Browser. |
| `app/utils.py` | `admin_required`, `super_admin_required`, `get_user_admin_subjects()`, `validate_username()` / `USERNAME_RE`, `VERSIONS` / `TYPED_VERSIONS`, `utc_iso`. |
| `app/ingestor.py` | `get_database_stats`, `find_untracked_files`, `sync_database_stream` used by Database Health. See [ingestion.md](ingestion.md). |
| `app/doc_thumbnails.py` | `thumbnail_exists`, `render_doc_thumbnail_sync`, `force_rerender`, lifecycle hooks used by the thumbnail backfill / clear routes. |
| `app/storage.py` | `safe_username`, `user_path` used when renaming a user moves their storage home. |
| `templates/admin_index.html` | Admin hub cards. |
| `templates/admin_subjects.html` | Manage Subjects (super admin). |
| `templates/admin_topics.html`, `templates/admin_chapters.html` | Topic/Subtopic and Chapter/Subchapter CRUD + drag reorder. |
| `templates/admin_users.html` | Users + per-subject permission selects. |
| `templates/admin_export_import.html` | CSV export/import forms. |
| `templates/admin_health.html` | Database Health: stats, anomaly modal, orphan sync, untracked files, DOC thumbnail backfill/clear. |
| `templates/admin_settings.html` | System Settings UI (see [../core/06-system-settings.md](../core/06-system-settings.md)). |
| `templates/admin_prompts.html`, `templates/admin_llm_endpoints.html` | AI Prompts and LLM Endpoints UIs (see [ai-prompts.md](ai-prompts.md), [ai-tools.md](ai-tools.md)). |
| `templates/admin_files.html` | Super-admin File Browser shell (see [file-browser.md](file-browser.md)). |
| `templates/admin_smart_import.html`, `templates/admin_pdf_import.html` | Smart Import ([ingestion.md](ingestion.md)) and PDF Batch Import ([pdf-import.md](pdf-import.md)). |
| `templates/base.html` | Admin navbar dropdown; Super Admin section links Manage Users, Database Health, File Browser, System Settings. |

## Tables

Schema reference: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Model | Touched by |
|---|---|
| `Subject` (`id` string PK, `name`, `split_parts_default`) | Subjects CRUD. `id` is immutable (embedded in QIDs and `SOURCE_PATH/<subject>/`). `split_parts_default` is on the add/edit form and seeds the PDF-import **Split questions into parts** checkbox. |
| `Topic` / `Subtopic` (`subject_id`, `sort_order`; Subtopic `hidden`) | Topics page, Topics CSV. Cascade-deleted with their Subject. |
| `Chapter` / `Subchapter` (same shape as Topic/Subtopic) | Chapters page, Chapters CSV. |
| `Question` | Counted for subject delete-block; Question Tags CSV writes `major_topic_id`, `major_subtopic_id`, `minor_topics`, `subtopics`, `chapter_id`, `subchapter_id`, `section`, `level`, `q_type`, `correct_percentage`, `description`, `answer`, `comment`. |
| `QuestionAsset` | Health stats (duplicates, path mismatches), orphan sync deletes rows. |
| `User` (`username`, `password_hash`, `is_super_admin`, legacy `is_admin`) | Users CRUD. `is_admin` is kept in sync with `is_super_admin` for backwards compatibility. |
| `UserSubjectPermission` (`user_id`, `subject_id`, `role`) | Users permissions; deleted explicitly on subject delete (FK without cascade). |
| `SavedQuestionSet` (`subject`) | Deleted explicitly on subject delete (FK without cascade). |
| `SavedFilter` (`filter_data` JSON) | Rows whose JSON has `subject == <id>` are deleted on subject delete (JSON scan, not an FK). |
| `SystemSetting` | Settings routes (`app/settings.py` REGISTRY). |
| `LLMConfig`, `PromptVariant`, `PromptEndpointAssignment` | LLM Endpoints / AI Prompts routes. |

## Routes

All paths are relative to `/admin`. Authz column: `A` = `@admin_required` (any subject admin or super admin), `S` = `@super_admin_required`.

### Index

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/` | A | `admin_index.html` hub cards. |

### Subject Management (`admin_subjects.html`)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/subjects` | S | Page; table with per-subject question/topic/chapter counts. |
| GET | `/subjects/<subject_id>/usage` | S | JSON `{id, name, questions, topics, chapters, saved_filters, question_sets, permissions}` for the delete-confirm modal. `saved_filters` comes from `_saved_filter_subject_count()` parsing every `SavedFilter.filter_data`. |
| POST | `/subjects/add` | S | Form `{id, name, split_parts_default?}`. `id` is stripped, uppercased, must match `^[A-Z0-9]{1,10}$` and be unique. Returns `{id, name, split_parts_default}`. |
| POST | `/subjects/<subject_id>/edit` | S | Form `{name, split_parts_default?}`. Name and the split-default flag; the ID cannot change. |
| POST/DELETE | `/subjects/<subject_id>/delete` | S | 400 if any `Question.subject == id`. Otherwise deletes `SavedQuestionSet`, matching `SavedFilter` rows, `UserSubjectPermission`, then the `Subject` (topics/subtopics/chapters/subchapters cascade). The `SOURCE_PATH/<subject>/` folder is left untouched. |

### Topic Management (`admin_topics.html`)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/topics` | A | Page; subjects limited to `get_user_admin_subjects()`; topics ordered by `sort_order`. |
| POST | `/topics/add` | A | Form `{subject_id, name}`; `sort_order = max + 1`. |
| POST | `/topics/<int:topic_id>/edit` | A | Form `{name}`. |
| POST/DELETE | `/topics/<int:topic_id>/delete` | A | Delete topic (subtopics cascade). |
| POST | `/subtopics/add` | A | Form `{topic_id, name, hidden('1'/'0')}`. |
| POST | `/subtopics/<int:subtopic_id>/edit` | A | Form `{name, hidden?}`; `hidden` only changes when present. |
| POST | `/subtopics/<int:subtopic_id>/toggle-hidden` | A | Flip `hidden`; returns `{id, name, hidden}`. |
| POST/DELETE | `/subtopics/<int:subtopic_id>/delete` | A | Delete subtopic. |
| POST | `/topics/reorder` | A | JSON `{topic_ids: [...]}`; index becomes `sort_order`. |
| POST | `/subtopics/reorder` | A | JSON `{subtopic_ids: [...]}`. |

### Chapter Management (`admin_chapters.html`)

Identical shape to Topics with `chapter`/`subchapter` names:

| Method | Path | Authz |
|---|---|---|
| GET | `/chapters` | A |
| POST | `/chapters/add`, `/chapters/<int:chapter_id>/edit`, `/chapters/reorder` | A |
| POST/DELETE | `/chapters/<int:chapter_id>/delete` | A |
| POST | `/subchapters/add`, `/subchapters/<int:subchapter_id>/edit`, `/subchapters/<int:subchapter_id>/toggle-hidden`, `/subchapters/reorder` | A |
| POST/DELETE | `/subchapters/<int:subchapter_id>/delete` | A |

Reorder bodies are `{chapter_ids: [...]}` / `{subchapter_ids: [...]}`.

### User Management (`admin_users.html`)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/users` | S | Page; every user with `{subject_id: role}` map + all subjects. |
| POST | `/users/add` | S | Form `{username, password, is_super_admin('1')}`. Runs `validate_username`; rejects duplicates. Returns `{success, id, username}`. |
| POST | `/users/<int:user_id>/edit` | S | Form `{username, password?, is_super_admin}`. 400 when editing your own account. If `username` changed, `_rename_user_storage_folder(old, new)` moves `User/<old>` to `User/<new>` under `STORAGE_PATH` (best effort) before the DB update. Blank password keeps the old one. |
| POST/DELETE | `/users/<int:user_id>/delete` | S | 400 when deleting yourself. |
| POST | `/users/<int:user_id>/permissions` | S | JSON `{permissions: {subject_id: role}}`; role is `'viewer'`, `'user'`, `'admin'`, or `''`/`null` to remove. Only the submitted subject keys are touched (upsert/delete per key, not a full replace). The page sends one subject per change. |
| GET | `/users/<int:user_id>/permissions/get` | S | `{user_id, username, is_super_admin, permissions}`. |

### Export / Import (`admin_export_import.html`)

All exports are CSV (`text/csv; charset=utf-8`, attachment). Imports read `utf-8-sig`, are idempotent (upsert), flash a summary plus up to 50 warnings, and redirect back to `/admin/export-import`.

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/export-import` | A | Page; subject picker limited to admin subjects. |
| GET | `/export/question-tags` | A | Either `?question_ids=1,2,3` (DB ids, filtered to the caller's admin subjects unless super admin; filename `question_tags_selected_{N}.csv`, used by the "Dashboard selections only" toggle) **or** `?subject_id=MATC` (whole subject; `question_tags_{subject_id}.csv`). `question_ids` wins when both present. Rows sorted by `hierarchy.sort_key`. Columns: `qid, subject, major_topic, major_subtopic, minor_topics, subtopics, chapter, subchapter, section, level, q_type, correct_percentage, description, answer, comment`. Multi-valued columns are `; `-joined names. |
| POST | `/import/question-tags` | A | Multipart `file` (`.csv`) + repeated `import_fields` checkboxes (subset of the 13 non-key columns; none submitted = import all). Only fields both selected and present in the CSV are applied. Matches by `qid`; rows for unknown QIDs or non-admin subjects are skipped. Names resolve within the question's subject; unknown names null the field and add a warning. `major_subtopic` requires a resolved `major_topic`; `subchapter` requires a resolved `chapter`. `correct_percentage` outside 0-100 becomes NULL. |
| GET | `/export/topics` | A | `?subject_id=`; columns `subject_id, topic_name, subtopic_name, subtopic_hidden`; row order = `sort_order`. |
| POST | `/import/topics` | A | Requires `subject_id` + `topic_name` columns. Row position defines `sort_order` (topics and subtopics renumbered from 1). Creates missing topics/subtopics; `subtopic_hidden` `0`/`1` updates the flag when present. |
| GET | `/export/chapters` | A | `?subject_id=`; columns `subject_id, chapter_name, subchapter_name, subchapter_hidden`. |
| POST | `/import/chapters` | A | Same rules as topics import. |

### Ingestion (legacy) and Smart Import

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/ingestion` | A | **302 redirect** to `/admin/import` (query string forwarded). Still routed for legacy links. |
| GET | `/ingestion/preview`, `/ingestion/start` | A | Live; used by Smart Import's Library scan mode. |
| GET/POST | `/import`, `/import/analyze`, `/import/analyze-llm`, `/import/prepare`, `/import/apply`, `/import/upload`, `/import/uploaded-file` | A | Smart Import. Documented in [ingestion.md](ingestion.md). |
| * | `/pdf-import/*` | A | PDF Batch Import. Documented in [pdf-import.md](pdf-import.md). |

### Database Health (`admin_health.html`)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/health` | S | Page (`source_path` passed for display). |
| GET | `/health/stats` | S | `get_database_stats()` JSON: `total_questions`, `total_assets`, `total_subjects`, `subjects[]`, `untagged_questions(_list)` (**excluding stems**), `questions_no_subtopic(_list)` (excluding stems), `questions_no_assets(_list)` (older than 24 h, **excluding** stems that still have children), `questions_no_assets_recent(_list)`, `assets_missing_files` (always `null`, see Gotchas), `duplicate_qids[]`, `duplicate_assets(_list)`, `path_mismatches(_list)`, `questions_no_type(_list)` (excluding stems), `questions_no_level(_list)` (excluding stems), `stems_with_tags(_list)`, `parts_missing_que(_list)` (child has no QUE and neither does its root), `empty_range_stems(_list)`. Lists are capped at 500 entries. DB-only; no filesystem access. |
| GET | `/health/untracked` | S | `{count, files[:500]}` of parseable files on disk with no `QuestionAsset.file_path`. Each entry `{file_path, qid, filename}`. |
| GET | `/health/sync` | S | **SSE.** `?mode=dry_run` (default) or `?mode=delete`. Streams `sync_database_stream()` events `info|warning|progress|success|error|done`; `done.stats = {orphaned_assets, orphaned_questions, deleted_assets, deleted_questions, skipped_grace, dry_run}`. Delete mode removes asset rows whose file is missing, then questions with zero assets (skipping those created < 24 h ago), and drops cached DOC thumbnails for deleted DOC rows. |
| GET | `/health/doc-thumbnails/backfill` | S | **SSE.** `?force=0|1`. 400 (JSON) when Word COM is unavailable. Walks every DOC asset; skips slots where an IMG exists (`IMG wins slot`), skips cached PNGs unless `force=1`, else `render_doc_thumbnail_sync`. `done.stats = {rendered, skipped_img, skipped_existing, failed}`. |
| POST | `/health/doc-thumbnails/clear` | S | Deletes every `*.png` in `DOC_THUMBNAIL_PATH`; `{success, deleted, errors[], message}`. Lazy resolver re-renders on demand. |

Anomaly modal (in `admin_health.html`): two parallel maps, `anomalyData[key]` (display strings) and `anomalyDataQids[key]` (raw QIDs for the "View in Dashboard" / "View in Question Management" footer buttons). For `duplicate_assets_list` and `path_mismatches_list` the QID is recovered from each formatted entry via `extractQidPrefix()`. Buttons navigate with `?qids=Q1,Q2,...`; when the joined list exceeds ~6 KB they store it in `localStorage` under a generated token and pass `?qids_token=<token>` instead (the receiving page reads and removes the key).

### System Settings (`admin_settings.html`)

Full reference: [../core/06-system-settings.md](../core/06-system-settings.md).

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/settings` | S | Page (`settings_page`). |
| GET | `/settings/data` | S | `settings.as_dict()` JSON (`{groups, registry}`) (`settings_data`). |
| POST | `/settings/save` | S | JSON `{KEY: value, ...}`; per-key validation; always 200 with `{saved[], errors{}, values{}}` so partial saves succeed (`settings_save`). Unknown keys are reported in `errors`. |
| POST | `/settings/reset/<key>` | S | Drop the DB override; 404 for unknown key; returns `{key, value, has_override: false}` (`settings_reset`). |

### AI Prompts (`admin_prompts.html`)

Super-admin only; see [ai-prompts.md](ai-prompts.md). Routes: `GET /prompts`, `GET /prompts/data`, `POST /prompts/variant/create`, `POST /prompts/variant/<int:variant_id>/save|delete|activate|assign`, `POST /prompts/<key>/reset-builtin`, `POST /prompts/<key>/unassign`.

### LLM Endpoints (`admin_llm_endpoints.html`)

Super-admin only; see [ai-tools.md](ai-tools.md). Routes: `GET /llm-endpoints` (page), `GET /llm-endpoints/data` (keys masked), `POST /llm-endpoints/save` (encrypts a newly entered key; blank keeps the existing one; `clear_key` removes it), `POST /llm-endpoints/<int:cid>/delete`, `POST /llm-endpoints/<int:cid>/duplicate` (clones all fields incl. `api_key_enc`, auto-unique name), `POST /llm-endpoints/<int:cid>/test` (connectivity ping), `POST /llm-endpoints/<int:cid>/chat` (raw chat; passes `turns` straight to `llm_client.chat_messages`, no system prompt). Linked from System Settings and the Admin navbar.

### File Browser shell

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/files` | S | Renders `admin_files.html` with `RootRegistry(current_user, scope='admin').list_dicts()`, `allowed_drive`, `fb_can_manage_roots=True`, `fb_scope='admin'`. All data calls go to `/files/api/*` (`files_bp`). See [file-browser.md](file-browser.md). |

## Business rules / invariants

- `Subject.id` is immutable: it is the PK, embedded in every QID and in the on-disk `SOURCE_PATH/<subject>/` layout. Only `name` is editable.
- Subject delete is blocked (400) while any `Question` references it. When allowed, clean-up order is: `SavedQuestionSet` -> matching `SavedFilter` (JSON scan) -> `UserSubjectPermission` -> `Subject` (ORM cascade handles topics/subtopics/chapters/subchapters). `SavedGenerationProfile` is options-only and untouched. Disk folders are never deleted.
- Subtopics/Subchapters have `hidden`; hidden ones are excluded from the dashboard but shown in admin (dashboard uses `include_hidden=1` for admin views).
- `sort_order` for topics/subtopics/chapters/subchapters is written by the reorder routes (list index) and by CSV import (row position).
- Username policy (`app/utils.validate_username`): must match `USERNAME_RE = ^[A-Za-z0-9._-]{1,80}$`, must not start or end with `.`, and must not be one of the reserved names `generated, con, prn, aux, nul, com1..com4, lpt1..lpt3` (case-insensitive). The same rule keeps the username usable as the `User/<username>` storage folder name. Applied on add and edit.
- Renaming a user moves `User/<safe_username(old)>` to `User/<safe_username(new)>` via `shutil.move`, only when the source exists and the destination does not; `OSError` is logged and swallowed so a locked folder never blocks the rename.
- You cannot edit or delete your own account from the Users page.
- CSV imports never create questions; unknown `qid` rows are skipped. Topic/Chapter imports do create hierarchy rows.
- Health orphan sync uses a **24-hour grace period**: questions created less than 24 h ago with no assets are reported separately and never deleted (they may be mid-upload from the Add Question wizard). Empty stems that still have children are also excluded from the no-assets counts (they are not orphans).
- `/health/stats` intentionally does no file-existence checks (network drives are slow); `assets_missing_files` is always `null`. Use the orphan sync for that.
- Path-mismatch check compares `asset.file_path` with `_build_asset_file_path(question, asset)` for at most 5000 assets.

## Settings & config keys

Reference: [../core/06-system-settings.md](../core/06-system-settings.md).

- `SOURCE_PATH` (`.env`) — root scanned by health/untracked/sync; passed to the health page.
- `STORAGE_PATH` / `storage.user_path()` — where `User/<name>` homes live (user rename).
- `DOC_THUMBNAIL_PATH`, `DOC_THUMBNAIL_WIDTH` — thumbnail cache dir cleared by `/health/doc-thumbnails/clear`; renders are cached by `asset_id` only, so changing the width requires clear + backfill.
- `word_com.IS_AVAILABLE` — gate for the thumbnail backfill.
- Settings routes operate on `app/settings.py` `REGISTRY` only; `FILE_BROWSER_EXTRA_ROOTS` is a non-registry `system_settings` row managed by `files_bp` (see [file-browser.md](file-browser.md)).

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

- `@admin_required`: caller is super admin or has `role='admin'` on at least one subject. Subject-scoped pages (Topics, Chapters, Export/Import) then filter by `get_user_admin_subjects()`; per-row routes (e.g. topic edit) do **not** re-check the topic's subject.
- `@super_admin_required`: Subjects, Users, Health, Settings, Prompts, LLM Endpoints, `/admin/files`.
- Export by `question_ids` filters to admin subjects unless the caller is super admin; export by `subject_id` 403s (flash + redirect) for non-admin subjects.

## Background work / SSE / threads

- SSE endpoints in this module: `GET /health/sync`, `GET /health/doc-thumbnails/backfill` (and the ingestion/import streams documented in [ingestion.md](ingestion.md)). All use `Response(generate(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})`, wrap the generator in `app.app_context()`, and emit `data: {json}\n\n` frames with `type` in `info|success|skip|warning|progress|error|done` plus optional `message`, `current`, `total`, `stats`. Clients close the `EventSource` on `done`.
- Thumbnail backfill renders synchronously inside the stream (`render_doc_thumbnail_sync`), serialised through the global Word COM lock shared with docx generation and batch IMG.
- `rerender-thumb` (documented in [admin-questions.md](admin-questions.md)) schedules an async render and returns immediately.
- nginx must set `proxy_buffering off` for SSE.

## Gotchas

- `GET /admin/ingestion` is a redirect, not a page; `templates/admin_ingestion.html` no longer exists. Link to `/admin/import` instead.
- `/health/sync` selects mode with `?mode=delete`; there is no `dry_run` query parameter. Anything else (or absent) is a dry run.
- User permissions payload is wrapped: `{"permissions": {"MATC": "admin"}}`. Sending `{"MATC": "admin"}` at top level silently does nothing.
- Permission updates are per-key merges. To remove access send `''` or `null` for that subject.
- Renaming a user does not rename `GeneratedFile.filename` rows; the storage move keeps the files reachable because `storage.user_home()` is derived from the current username.
- Subject `id` validation happens only on add. Existing lowercase/long IDs seeded by `init_db.py` keep working.
- Question Tags CSV import clears M2M `minor_topics`/`subtopics` when those columns are selected, even if the cell is empty — deselect the column to preserve existing values.
- Health anomaly QID hand-off: the receiving pages (`/dashboard`, `/admin/questions`) accept `?qids=` or `?qids_token=`; the token value is a `localStorage` key that the receiving page deletes after reading, so a refresh of the target page loses the pinned list.
- Thumbnail cache is keyed by `asset_id` only. After changing `DOC_THUMBNAIL_WIDTH` run clear then backfill (`force=1` also works).
- `_build_asset_file_path(question, asset)` is the single source of the canonical relative path (`<SUBJ>/PP/<SOURCE>/<YEAR>/<PAPER>/<QID>_<VER>_<TYPE>[_<part>].<ext>` or `<SUBJ>/QB/<DETAIL>/...`). Rename, reorder, health path-mismatch and Smart Import all rely on it; keep them in sync if you change the layout.

## Related

- [question-hierarchy.md](question-hierarchy.md) — `split_parts_default` (Subjects form + PDF wizard checkbox).
- [admin-questions.md](admin-questions.md) — Question Management, Edit modal, batch operations.
- [ingestion.md](ingestion.md) — Library scan, Smart Import, CLI ingest/sync.
- [file-browser.md](file-browser.md) — `/admin/files` and `files_bp`.
- [pdf-import.md](pdf-import.md), [ai-tools.md](ai-tools.md), [ai-prompts.md](ai-prompts.md).
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md), [../core/06-system-settings.md](../core/06-system-settings.md).
- [../decisions/ADR-005-unified-storage-tree.md](../decisions/ADR-005-unified-storage-tree.md).

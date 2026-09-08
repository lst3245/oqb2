# Admin Question Management

> `/admin/questions`: the admin question table, the Add Question wizard, the shared Edit Question modal (Tags / Assets / Details), per-asset endpoints, and every bulk operation (delete, delete assets, Combine parts, Split into parts, batch update, Copy/Move Assets, Set MCQ ANS, Generate IMG, verify, check-state).

All backend routes live in `app/admin.py` (blueprint `admin_bp`, prefix `/admin`) in the sections `Question Tagging`, `Question Deletion`, `Batch Question Update`, `Question Management`, `Markdown asset edit/create endpoints`, `Batch IMG Generation from DOC/MD`, `Whole-question verification + batch QA state`, plus the `batch-mcq-ans` route. AI-driven operations (proofread, Gen MD, auto-tag, solve) share this page but are documented in [ai-tools.md](ai-tools.md).

## Files

| File | Role |
|---|---|
| `app/admin.py` | Routes listed below. Helpers: `validate_qid_format` (`parse_qid` from `app.hierarchy`), `_extract_qb_detail`, `_build_asset_file_path`, `_admin_questions_query_from_args`, `_admin_check_scope_from_args`, `_admin_check_scope_clauses`, `_normalize_asset_ops`, `_require_md_admin`. |
| `app/hierarchy.py` | QNO grammar, `ensure_question`, rename rewrite, subtree delete order, `breadcrumb_parts`. Spec: [question-hierarchy.md](question-hierarchy.md). |
| `app/question_split.py` | Stage/commit IMG crops for `/questions/<id>/split`. Root commit writes `WHOLE` from the staged PNG when that slot is empty. |
| `app/question_combine.py` | Restore/reconstruct combine; `preview_combine`, `combine_parts`, `combine_many`. |
| `app/batch_image_gen.py` | `render_doc_to_pages`, `render_md_to_pages`, `_pdf_to_cropped_images`, `stitch_vertically`, `_build_img_rel_path`, `replace_img_assets`, `find_best_source` (DOC > MD), `slot_has_img`. Used by Generate IMG, Smart Import IMG apply, and the per-slot `generate-img` route. |
| `app/doc_thumbnails.py` | Lifecycle hooks `on_doc_asset_created`, `on_doc_asset_deleted`, `on_img_asset_created`, `on_img_asset_deleted`, `force_rerender`. Every asset mutation here must call the right hook. |
| `app/md_render.py` | `invalidate(asset_id)` after MD delete/save/replace. |
| `app/word_com.py` | `IS_AVAILABLE`, `word_session(lock_timeout=)` global lock used by Generate IMG. |
| `resources/mcq_answer_img/{A,B,C,D}.png` | Source PNGs for Set MCQ ANS (repo root, resolved as `dirname(app.root_path)/resources/mcq_answer_img`). |
| `templates/admin_questions.html` | Page: toolbar (incl. Tree filter), table (Part column), select-all banner, Add Question wizard (`#addQuestionModal`), `#bulkDeleteModal`, `#bulkCombineModal`, `#batchImgModal`, `#batchMcqAnsModal`, `#batchAssetOpsModal`, `#batchCheckStateModal`, `#bulkVerifiedWrap`, `#aiToolsModal`; JS `loadQuestions`, `openSmartImport`, `bulkDeleteSelected`, `openBulkCombineModal` / `executeBulkCombine`, `openBulkSplit`, `selectAllMatchingQuestions`, `borrowTagFormForAdd` / `returnTagFormToEditModal`. |
| `templates/admin_question_split.html` | Dedicated IMG crop page (stem / `a` / `ci` boxes + Auto-detect parts). |
| `templates/partials/edit_question_modal.html` | Shared `#editQuestionModal` markup + `#renameConfirmModal` + `#mdEditorModal` + `#combinePartsModal` + `#toastContainer` + scoped CSS. Mounted by `admin_questions.html` and `dashboard.html`. Includes `partials/md_editor.html` (idempotent). |
| `templates/partials/edit_question_modal_js.html` | All Edit-modal JS (`openEditModal`, save, rename, upload, delete, reorder, MD editor, paste handler, Prev/Next nav, thumb-compact toggle, `openCombineConfirm` / `confirmCombineParts`). Pulls in `partials/tag_editor_js.html` — never include that separately on a host. |
| `templates/partials/tag_editor_form.html` | `<form id="editForm">` used by the Tags tab and borrowed by Add Question step 3. |
| `templates/partials/tag_editor_js.html` | `populateTagForm`, topic/subtopic/chapter cascading selects. Loaded transitively only. |
| `templates/partials/md_editor.html` | EasyMDE + marked + KaTeX editor used inline (`#mdEditorModal`) and by `templates/admin_md_editor.html` (fullscreen). |
| `templates/dashboard.html` | Also hosts the Edit modal and the **Bulk Edit** modal that POSTs `/admin/questions/batch-update`; links to this page with `?from=dashboard`. |

## Tables

Schema reference: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Model | Columns touched here |
|---|---|
| `Question` | `qid, subject, source, year, paper, qno, qno_end, parent_id, part, part_sort` (create / rename), `level, q_type, section, description, answer, comment, correct_percentage`, `major_topic_id, major_subtopic_id, chapter_id, subchapter_id`, M2M `minor_topics`, `subtopics`, `verified, verified_at, verified_by`, `created_at` (read-only). |
| `QuestionAsset` | `asset_type` (QUE/ANS/SOL/WHOLE), `file_format` (IMG/MD/DOC), `version` (from `app/utils.VERSIONS`: EN/CH/BI/ENO/CHO), `file_path` (forward-slash, relative to `SOURCE_PATH`), `part_number`, `check_state` (None/`checking`/`ok`/`issues`/`error`), `check_result` (JSON), `check_raw`, `checked_at`. Unique on `(question_id, asset_type, version, file_format, part_number)`. `WHOLE` is IMG-only on a root. |
| `Topic`, `Subtopic`, `Chapter`, `Subchapter` | Read for tag validation (`major_subtopic.topic_id == major_topic_id`, `subchapter.chapter_id == chapter_id`). |
| `Subject` | Existence + admin-access checks on create/rename. |

## Routes

Paths are relative to `/admin`. Authz `A` = `@admin_required`; "subject-scoped" means the route additionally checks the question's subject is in `get_user_admin_subjects()` (super admins bypass).

### Page and list

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/questions` | A | Page. Context: `subjects` (admin subjects), `md_max_size_bytes`, `ai_tools_enabled`. |
| GET | `/questions/api/list` | A (scoped by query) | Paginated table data. Query: `page`, `page_size` (10/20/50/100/200, else 50), `sort` (`qid` = subject/source/year/paper/qno then roots-before-children/`part_sort`, or `subject|source|year|paper|qno|q_type|created_at|selection_order`), `dir` (`asc|desc`), `tree_scope` (`all` default / `roots` / `leaves`), plus the filters below. Response `{items[], total, page, page_size, total_pages}`; each item `{id, qid, subject, source, year, paper, section, qno, qno_end, parent_id, part, depth, is_stem, q_type, level, created_at (ISO UTC), asset_count (true total, all versions), verified, check_summary}` where `check_summary = {status: none|issues|unchecked|ok, total, ok, issues, unchecked}` computed over the active Status scope. Part column shows `(a)` indented by `depth`, or `stem` / `—`. |
| GET | `/questions/<int:question_id>/details` | A, scoped | Edit-modal payload: all tag ids (`minor_topic_ids[]`, `subtopic_ids[]`), metadata including `qno_end`, `parent_id`, `part`, `child_count`, `is_stem`, `has_whole`, `breadcrumb`, `children`, `tag_union`, `created_at`, `verified`, `verified_at`, `verified_by` (username). |
| GET | `/questions/api/ids` | A | Same filter/sort helper; returns every match `{items:[{id,qid}], ids[], total}`. Backs the "select all N matching" banner. |
| GET | `/questions/<int:question_id>/assets` | A | `{qid, assets: {VERSION: {ATYPE: [{id, part_number, file_format, file_path, preview_url, check_state, check_result (parsed), check_raw, checked_at}]}}}`. |

**Filters** in `_admin_questions_query_from_args` (always restricted to admin subjects). Mutually exclusive, first match wins:

1. `qids=Q1,Q2` — exact QID list (DB-Health anomaly hand-off). Supplied order is preserved when `sort` is `selection_order` or `created_at`.
2. `selected_ids=1,2,3` — internal ids ("Show dashboard selections", read from `localStorage['oqb_selectedQuestions']`). Same order-preservation rule.
3. `qid_search=` — `*`/`%` wildcards become `ILIKE` pattern; otherwise substring `ILIKE %term%`.

Non-exclusive: `verified=1|0`, `tree_scope=all|roots|leaves`, `check_status=issues|ok|unchecked` (correlated `EXISTS` over assets in scope; `ok` = has assets in scope and none non-ok). Scope defaults to `TYPED_VERSIONS` (EN/CH/BI), all types, all formats; override with `check_versions=EN,CH`, `check_atypes=QUE,ANS,SOL`, `check_formats=IMG,MD,DOC` (advanced Status dropdown, `#checkAdvancedBtn`).

### Create / tag / rename

| Method | Path | Authz | Purpose |
|---|---|---|---|
| POST | `/questions/create` | A, scoped | JSON `{subject, source, year?, paper?, qno, detail?}`. `qno` is a **string token** (`5`, `5a`, `3ci`, `23-24`, optional leading `Q`). PP needs `year` + `paper`; QB needs `detail` without `_`. Builds QID via `parse_qno_token` + `validate_qid_format`. `ensure_question` find-or-creates missing ancestors. 409 if the target QID already exists. Sets `q_type` via `determine_question_type`. Returns `{success, question:{id,qid}}`. Step 1 of the Add wizard. |
| POST | `/questions/<int:question_id>/update` | A | Form (Save Tags). Fields applied only when present: `level, q_type, section, description, answer, comment, correct_percentage (0-100 else NULL), major_topic_id, major_subtopic_id, chapter_id, subchapter_id`; `minor_topic_ids[]`, `subtopic_ids[]` replace the M2M sets (absent = cleared). **Stems ignore** level/q_type/topic/chapter writes; answer/comment still apply. Changing `major_topic_id` clears `major_subtopic_id`; a subtopic not under the major topic is nulled. Same for chapter/subchapter. |
| POST | `/questions/<int:question_id>/rename` | A, scoped to the **new** subject | JSON `{new_qid, confirm_rename_files}`. Validates format, subject exists. Cascades to descendants: rewrites each QID relative to the renamed node, then `relink_parent`. 400 if the rename would change range-vs-part shape while children exist (`rename_shape_ok`). 409 on QID collision in or outside the subtree. When `confirm_rename_files` is true, every asset in the subtree is moved to `_build_asset_file_path(...)` (`shutil.move`, dirs created) and `file_path` updated; missing files just get the new path. Returns `{old_qid, new_qid, renamed_files[], errors[]}`. |
| POST | `/questions/<int:question_id>/children` | A, scoped | JSON `{part}` letters (`a`, `b`, `ci`). `ensure_question` under this token. 400 on range stems or invalid label. |
| POST | `/questions/<int:question_id>/parent` | A, scoped | JSON `{parent_id}` (null detaches). Same subject; `token_fits_under`; no cycles. 400 if detaching a labelled part. |
| GET | `/questions/<int:question_id>/split` | A, scoped | Stages IMG QUE; renders `admin_question_split.html`. MD/DOC QUE flash-redirects to the list. Hidden in the Edit modal when the row is already a stem. |
| GET | `/questions/<int:question_id>/split/image/<version>` | A, scoped | Staged PNG (`?token=`). |
| POST | `/questions/<int:question_id>/split/detect` | A, scoped | JSON `{token, version, endpoint_id}`. Pass-2 vision detect on the staged image → `{success, boxes, raw}`. |
| POST | `/questions/<int:question_id>/split/commit` | A, scoped | JSON `{token, boxes:[{label, box:[x1,y1,x2,y2]}], copy_tags}`. Requires `stem` + ≥1 part. Crops every staged version; optional copy tags then clear the stem. Root: writes WHOLE from the staged PNG if that slot is empty. |
| GET | `/questions/<int:question_id>/combine/preview` | A, scoped | Dry payload for the Combine confirm dialog (`preview_combine`). 400/409 when the row is a leaf or range stem. |
| POST | `/questions/<int:question_id>/combine` | A, scoped | JSON `{stitch?, save_whole?}`. Restore WHOLE→QUE or reconstruct, then delete descendants. |
| POST | `/questions/combine` | A | JSON `{ids, stitch?, save_whole?}`. Bulk; overlapping stems collapse to the ancestor. `{ok, results[]}`. |

### Assets (per question)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| POST | `/questions/<int:question_id>/assets/upload` | A, scoped | Multipart `files[]`, `version` (legacy `language` accepted, default EN), `asset_type` (default QUE; `WHOLE` allowed on roots, IMG only). Extension decides format: png/jpg/jpeg/gif/bmp -> IMG (multi-part; next part = max IMG part + 1), doc/docx -> DOC (single-slot; rejected if one exists), md/markdown -> MD (single-slot; rejected if one exists; `MD_MAX_SIZE_BYTES` enforced; extension normalised to `.md`). Files land at the canonical path. Post-commit: IMG -> `on_img_asset_created`, DOC -> `on_doc_asset_created`. Returns `{uploaded_count, errors[]}`. |
| POST/DELETE | `/questions/<int:question_id>/assets/<int:asset_id>/delete` | A | Optional JSON `{delete_from_disk}` (default true). MD -> `md_render.invalidate`; DOC -> `on_doc_asset_deleted`; IMG -> `on_img_asset_deleted` (a DOC left alone in the slot gets its thumbnail scheduled). |
| POST | `/questions/<int:question_id>/assets/reorder` | A | JSON `{version (legacy language), asset_type, asset_ids[]}` in new order. IMG only — MD/DOC ids are skipped (400 if nothing IMG remains). Two-phase DB update (negative temp part numbers) then two-phase file rename via `.tmp_reorder` names to avoid unique-constraint and swap collisions. Returns `{success, files_renamed, warnings?}`. |
| POST | `/questions/<int:question_id>/assets/check-state` | A, scoped | JSON `{version, asset_type, state, note?, severity?}`; `state` in `ok|issues|error|clear` (`clear` -> unchecked). Writes `check_state/check_result/checked_at` to every row in the slot, `check_raw` cleared. `check_result = {status, issues[], checked_by:'manual', editor, note?}`; `issues` gets one `{severity (minor|major|critical), location:'', description}` entry for `issues`. |
| POST | `/questions/<int:question_id>/assets/<int:asset_id>/rerender-thumb` | A, scoped | Force DOC thumbnail re-render (`doc_thumbnails.force_rerender`); 503 when Word COM unavailable. Returns immediately; the client polls the thumbnail URL (`window.oqbPollDocThumbnails`). |

### Markdown editor

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/questions/<int:question_id>/assets/<int:asset_id>/md/content` | A, scoped (`_require_md_admin`) | `{asset_id, qid, version, asset_type, file_path, mtime_ns, content, max_size}`. Stats before reading so `mtime_ns` matches `content`. 400 on non-UTF-8. |
| POST | `/questions/<int:question_id>/assets/<int:asset_id>/md/save` | A, scoped | JSON `{content, expected_mtime_ns?, force?}`. 409 on optimistic mtime conflict unless `force`. Invalidates render cache. |
| POST | `/questions/<int:question_id>/assets/md/create` | A, scoped | JSON `{version (legacy language, default EN), asset_type (default QUE), content}`. 409 if an MD already occupies the slot. |
| GET | `/questions/<int:question_id>/assets/<int:asset_id>/md/edit` | A | Fullscreen editor page (`admin_md_editor.html`). |
| GET | `/questions/<int:question_id>/assets/md/new?version=&asset_type=` | A | Fullscreen editor in create mode (legacy `language=` accepted). |

Full MD pipeline: [md-format.md](md-format.md).

### Bulk operations (form POST)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| POST | `/questions/delete` | A | Form `question_ids[]`, `delete_files` (`'true'`), `delete_children` (`'true'`). Selection intersected with admin subjects. If any selected row has descendants not also selected, **409** unless `delete_children=true` (then those descendants in admin subjects are included). Deletes deepest-first (`ON DELETE RESTRICT`). Assets cascade; optionally deletes files. After commit: DOC thumbnail + MD invalidate for dropped assets. Returns `{deleted_count, deleted_qids[], files_deleted}` or `{error, blocking_qids[]}`. |
| POST | `/questions/batch_delete_assets` | A | Form `question_ids[]`, `formats[]` (IMG/MD/DOC), `versions[]` (legacy `langs[]`), `atypes[]` (QUE/ANS/SOL), `delete_files` (default `'true'`). Each axis must be non-empty; the three `IN(...)` clauses are ANDed. Scoped to admin subjects. Fires the same thumbnail lifecycle hooks and MD invalidation as single delete. |
| POST | `/questions/batch-update` | A | Form `question_ids[]` + flag/value pairs: `update_level`/`level`, `update_q_type`/`q_type`, `update_section`/`section`, `update_correct_pct`/`correct_percentage`, `update_topics` (bundles `major_topic_id`, `major_subtopic_id`, `minor_topic_ids[]`, `subtopic_ids[]`), `update_chapters` (`chapter_id`, `subchapter_id`). Flags are `'1'`. Empty value with the flag set clears the field. UI is the **Bulk Edit** modal on `dashboard.html`. |
| POST | `/questions/batch-set-verified` | A, scoped | JSON `{question_ids[], verified}`. Sets `verified_at`/`verified_by` (or nulls them). Returns `{updated, verified}`. |
| POST | `/questions/batch-set-check-state` | A, scoped | JSON `{question_ids[], versions[], atypes[], state, note?, severity?, overwrite?}`. Same write shape as the single check-state route across every (question x version x atype) slot with assets. With `overwrite` false (default) slots that already have any `check_state` are skipped; `clear` always applies. Returns `{slots_updated, assets_updated}`. |
| POST | `/questions/<int:question_id>/verify` | A, scoped | JSON `{verified}`. Response `{verified, verified_at, unchecked_assets (count with check_state != 'ok'), total_assets}` for the modal soft-warn. Always allowed. |

### Bulk operations (SSE GET)

All return `text/event-stream` with `data: {type, message, current?, total?, stats?}` frames; `type` in `info|success|skip|error|done`. Question ids outside the caller's admin subjects are silently dropped; 403 JSON if none remain.

| Path | Query | Purpose |
|---|---|---|
| `/questions/batch-generate-images` | `question_ids` (csv, required), `types` (default `QUE`), `versions` (default all; legacy `langs`), `sources` (default `DOC,MD`), `stitch` (default 1), `overwrite` (default 0), `width`, `transparent`, `symmetric_horizontal` | 400 JSON if `word_com.IS_AVAILABLE` is false. For each (question x type x version) slot: `find_best_source` (DOC before MD); skip if no source, skip if IMG exists and not `overwrite`; render via `render_doc_to_pages` / `render_md_to_pages` (pandoc -> docx -> Word -> PDF -> PyMuPDF -> Pillow crop), then `replace_img_assets` (stitch or one IMG per page). Defaults resolved inside the app context: `BATCH_IMG_DEFAULT_WIDTH` (fallback `DOC_THUMBNAIL_WIDTH`), `THUMBNAIL_TRANSPARENT`, `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP`, `THUMBNAIL_WHITENESS_THRESHOLD`, `THUMBNAIL_BOTTOM_PADDING_PX`, `WORD_COM_LOCK_TIMEOUT`. One `word_session` for the whole run. UI: **Generate IMG** (`#batchImgModal`). |
| `/questions/batch-mcq-ans` | `question_ids`, `versions` (default `EN`; legacy `langs`), `overwrite` (default 0) | For each question x version: skip unless `q_type == 'MC'` and `answer` in A-D; copy `resources/mcq_answer_img/<answer>.png` into the canonical ANS IMG slot and upsert the `QuestionAsset` row (existing IMG ANS skipped unless `overwrite`). UI: **Set MCQ ANS** (`#batchMcqAnsModal`). |
| `/questions/batch-asset-ops` | `question_ids`, `ops` (JSON list, max 25) | **Copy/Move Assets** (`#batchAssetOpsModal`). Each op: `{action: copy|move, source_versions[], target_versions[], source_atypes[], target_atypes[], source_formats[], target_formats[], overwrite}`. Ops run sequentially per question. Mapping per axis: one source version/atype fans out to every selected target; multiple sources lock that axis and map by name. Formats are same-format only (intersection of source/target formats). IMG keeps multi-part numbering; MD/DOC stay single-slot. Overwrite deletes target rows/files and fires MD invalidate + DOC thumbnail hooks. Move removes the source slot only after every requested target for that source succeeded. `stats = {copied, moved, skipped, errors}`. |

AI SSE streams on the same page (`/questions/ai/check`, `/ai/generate-md`, `/ai/solve-generate`, `/ai/solve-check`, `/ai/auto-tag`, `POST /ai/cancel`, `GET /ai/endpoints`) and per-slot AI POSTs (`/assets/ai/generate-md`, `/assets/ai/check`, `/assets/ai/solve-generate`, `/assets/ai/solve-check`, `/ai/answer-text`, `/ai/suggest-tags`, `/assets/generate-img`) are documented in [ai-tools.md](ai-tools.md).

## Business rules / invariants

- **Canonical asset path** is always `_build_asset_file_path(question, asset)`: `<SUBJ>/PP/<SOURCE>/<YEAR>/<PAPER>/<QID>_<VERSION>_<TYPE>[_<part>].<ext>` for DSE/CE/AL, `<SUBJ>/QB/<DETAIL>/<QID>_<VERSION>_<TYPE>[_<part>].<ext>` for QB. Part suffix only when `part_number > 1`. Stored with forward slashes.
- **IMG is multi-part; MD and DOC are single-slot** (`part_number` always 1). Upload rejects a second MD/DOC in the same `(question, asset_type, version)` slot; reorder ignores MD/DOC ids; MD/DOC never consume an IMG part index.
- **QID grammar**: `parse_qid` in `app/hierarchy.py` (not a local regex). `QNO` is `Q<n>`, `Q<n><part>`, or `Q<n>-<n>`. QB `detail` cannot contain `_`. Creating `3a` also creates an empty `Q3` parent when missing. Creating `23-24` adopts parentless same-paper `Q23`/`Q24` with `part IS NULL`.
- **Rename cascade / delete guard**: see [question-hierarchy.md](question-hierarchy.md). Cannot rename `Q3` → `Q3a` while this row would still be its own parent.
- Tag validation: `major_subtopic` must belong to `major_topic`; `subchapter` must belong to `chapter`; violations are silently nulled, not rejected. Changing the parent clears the child.
- **DOC thumbnail lifecycle**: any code path that creates/deletes IMG or DOC rows must call the matching `doc_thumbnails.on_*` hook; an IMG in a slot eclipses the DOC thumbnail for that slot.
- **MD render cache**: call `md_render.invalidate(asset_id)` after any MD content or row change.
- **Check state is per format within a slot**: manual check-state writes hit every row in the `(version, asset_type)` slot; AI proofreading writes per format (see [ai-tools.md](ai-tools.md)). Status rollup in the table defaults to `TYPED_VERSIONS` only.
- **Verified flag** is independent of check states; verifying is always allowed but the modal soft-warns using `unchecked_assets`.
- Bulk destructive routes intersect the submitted ids with the caller's admin subjects; per-question routes 403 on a foreign subject.
- Generate IMG and Smart Import both funnel IMG writes through `replace_img_assets` (render new files under temp names first, delete old rows/files, rename into canonical names, insert rows, commit).
- Word COM work is serialised through the single global lock shared with docx generation and DOC thumbnails (`word_com.word_session`).

### Frontend contracts

#### Page (`admin_questions.html`)

- Toolbar filter state (QID search, Show dashboard selections, Status + advanced scope, Verified, page size, sort, page) persists in `localStorage['oqb_adminQuestionsFilters']` (`ADMIN_Q_FILTER_KEY`).
- `?from=dashboard` (the dashboard **Manage** link) auto-enables Show dashboard selections once; dashboard selections come from `localStorage['oqb_selectedQuestions']`.
- `?qids=Q1,Q2` or `?qids_token=<key>` pins an exact QID list and shows a dismissible banner; the token is read from and removed from `localStorage`; the URL params are stripped after load. Typing in search or toggling dashboard selections drops the pinned list.
- Select-all: ticking the header checkbox selects visible rows and shows a banner; `#selectAllMatchingBtn` calls `/questions/api/ids` and selects every filtered question for bulk operations.
- Bulk buttons appear once something is selected: Delete Selected, **Combine parts**, **Split into parts** (exactly one id → navigates to `/questions/<id>/split`), Generate IMG, Set MCQ ANS, Copy/Move Assets, Import Files (always visible; `openSmartImport()` opens `/admin/import?mode=folder&qids=` from selected rows' `data-qid`), AI Tools, Auto Tag, Set Check State, Set Verified dropdown.
- **Delete Selected** opens one two-tab modal: *Whole questions* (POST `/questions/delete`) and *Specific assets* (POST `/questions/batch_delete_assets`). Confirm gate: an "I understand" checkbox for <= `BULK_DELETE_TYPED_THRESHOLD` (10) questions, typing `DELETE` above that. Switching tabs resets the confirm. The assets tab starts with all format/version/type checkboxes unticked. Whole-questions tab has `#bulkDeleteChildren` (`delete_children`) — required when a selected row has parts.
- Add Question is a 3-step wizard (`#addQuestionModal`): 1 Details (POST `/questions/create` must succeed first; `#addQno` is text — `1`, `3a`, `3ci`, `23-24`), 2 Assets (upload into the new question), 3 Tags (`borrowTagFormForAdd()` moves `#editForm` from the Edit modal into `#addStep3`; `returnTagFormToEditModal()` puts it back). After a successful create the wizard tries `parseInt(qno)+1` for the next add — that is integer-only (fine for `5`, not for `5a`). Only the Edit modal is shared with the dashboard; the Add wizard is admin-only.

#### Shared Edit modal (`partials/edit_question_modal*.html`)

- Tabs, fixed order: **Tags** (default; `tag_editor_form.html`), **Assets** (one version pill per entry in `OQB_VERSIONS` x QUE/ANS/SOL; roots also show a **Whole question archive** IMG strip (`WHOLE`); upload / delete / reorder; IMG multi-part, MD inline editor via `#mdEditorModal` plus a fullscreen link, DOC upload + download; requests send `version`), **Details** (QID rename via `#renameConfirmModal`; if `child_count > 0` the confirm shows `#renameDescendantNote`; Hierarchy: create child, set parent, Split link, **Combine parts** confirm `#combinePartsModal`; read-only `created_at`, Verified toggle).
- Entry point `openEditModal(questionId, opts?)`: fetches `/questions/<id>/details` and `/questions/<id>/assets`, calls `populateTagForm`, shows the modal. `opts.preserveView` keeps the active main tab + version pill; `opts.preserveAssetScroll` restores asset scroll (used by Prev/Next).
- **Host refresh contract**: after save / rename / asset mutations the modal calls `window.oqbAfterQuestionEdit?.()`. Hosts assign it right after the include: `window.oqbAfterQuestionEdit = loadQuestions` (admin) / `= refreshCurrentPage` (dashboard). Asset mutations mark the list stale (`markEditModalListStale()`) and flush on `hidden.bs.modal`; Save Tags / verify / rename refresh immediately.
- **Navigation contract**: hosts define `window.oqbEditNavSource()` returning the ordered id array, read fresh on every open (admin: `#questionTableBody tr.q-row`; dashboard: `#allQuestionIds`). `#editNavControls` Prev/Next and Left/Right arrow keys step through it preserving tab, pill and scroll; Ctrl+Left/Right switches version pills. Shortcuts are ignored while typing or when a stacked child modal is open.
- Per-slot AI controls in the Assets tab are gated on `window.OQB_AI_TOOLS_ENABLED`: editable proofread bar (`renderCheckStatusBar`), Quick Check (`openQuickCheckModal`), **Gen IMG** on MD/DOC cards (`generateImgFromSlot`).
- `localStorage['oqb_assetThumbsCompact']` (`'1'|'0'`) toggles compact vs full-width asset previews (`<body>.asset-thumbs-compact`, `oqbInitThumbCompact()`).
- Global helpers relied on from `base.html`: `window.OQB_VERSIONS`, `OQB_VERSION_LABELS`, `oqbFormatLocalTime`, `oqbPollDocThumbnails`, `oqbRerenderThumb`, `oqbLoadMarkdownPreviewCards`.

## Settings & config keys

Reference: [../core/06-system-settings.md](../core/06-system-settings.md).

| Key | Used by |
|---|---|
| `SOURCE_PATH` (`.env`) | Every file write/move/delete. |
| `MD_MAX_SIZE_BYTES` | Upload, MD create/save, Smart Import MD apply; passed to the page as `md_max_size_bytes`. |
| `BATCH_IMG_DEFAULT_WIDTH`, `BATCH_IMG_DEFAULT_STITCH` | Generate IMG modal defaults (`width` falls back to `DOC_THUMBNAIL_WIDTH`). |
| `THUMBNAIL_TRANSPARENT`, `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP`, `THUMBNAIL_WHITENESS_THRESHOLD`, `THUMBNAIL_BOTTOM_PADDING_PX` | Crop parameters for Generate IMG. |
| `WORD_COM_LOCK_TIMEOUT` | `word_session(lock_timeout=)` for Generate IMG. |
| `AI_TOOLS_ENABLED` | Gates AI controls (`ai_tools_enabled` template var). |
| `DOC_THUMBNAIL_PATH` | Where lifecycle hooks read/write PNGs. |

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md). Everything here is `@admin_required`; list/bulk routes are additionally filtered to `get_user_admin_subjects()`, and single-question routes marked "scoped" 403 when the question's (or, for rename, the new QID's) subject is not one the caller administers. Super admins bypass the scope checks. The Edit modal on the dashboard is only mounted for users with `window.OQB_IS_ADMIN`.

## Background work / SSE / threads

- SSE generators: `batch-generate-images`, `batch-mcq-ans`, `batch-asset-ops` (plus the AI streams). Standard frame shape and headers as in [admin-panel.md](admin-panel.md#background-work--sse--threads); the client closes on `done`. Generate IMG holds the Word COM lock for the entire stream.
- `rerender-thumb` and `on_doc_asset_created` schedule background renders in `app/doc_thumbnails.py`; the UI polls `data-doc-pending-id` elements every 3 s (`oqbPollDocThumbnails`, up to ~3 min).
- Batch form POSTs are synchronous single transactions; a failure rolls back the whole batch.

## Gotchas

- The single-asset delete URL is `/questions/<qid>/assets/<aid>/delete` (POST or DELETE), not a bare `DELETE /assets/<aid>`.
- `update_question` clears `minor_topics`/`subtopics` whenever the arrays are absent from the form — always submit the full current lists from the Tags form.
- Batch update lives in the **dashboard** Bulk Edit modal, not in `admin_questions.html`; it does not intersect with admin subjects beyond `@admin_required`.
- Legacy `language`/`langs` parameters are accepted everywhere `version`/`versions` are; always send the new names.
- `check_versions`/`check_atypes`/`check_formats` change both the Status filter **and** the `check_summary` badge per row; `asset_count` is never scoped.
- `qids`, `selected_ids` and `qid_search` are exclusive; when `qids` is set the search box is ignored server-side.
- Reorder renames files through `.tmp_reorder` intermediates and commits the new `part_number`s even when some renames fail (failures come back as `warnings`, and the affected rows keep their old `file_path`); a crash mid-way can leave `*.tmp_reorder` files in the question folder. Check Database Health path mismatches afterwards.
- Rename with `confirm_rename_files=false` updates `qid`/subject fields but leaves files at their old paths, which then show as `path_mismatches` in Database Health. A stem rename with that flag false does the same for every descendant.
- Generate IMG default `types` is `QUE` only; pass `types=QUE,ANS,SOL` to cover all.
- Set MCQ ANS defaults to `versions=EN`; other versions must be requested explicitly. The PNG pool lives outside `SOURCE_PATH` in `resources/`.
- Copy/Move Assets never converts formats; IMG->MD etc. is not possible. A move whose target write fails keeps the source.
- `batch-set-check-state` without `overwrite` leaves already-checked slots alone — use `overwrite: true` to force, or `state: 'clear'` first.
- Do not include `tag_editor_js.html` or `md_editor.html` directly on a host page; both are pulled in by the Edit-modal partials (duplicate init breaks the tag form and the EasyMDE instance).
- `openEditModal` resets to the Tags tab and EN pill unless `preserveView` is set; the Add wizard borrows `#editForm`, so opening the Edit modal while the Add wizard is on step 3 would show an empty Tags tab.
- The Status rollup counts only `TYPED_VERSIONS` (EN/CH/BI) by default; official scans (ENO/CHO) are excluded unless the advanced scope includes them.
- Split commit calls `replace_img_assets` per version (each call commits). A later version failing can leave earlier crops already written. Root split also writes WHOLE first when that slot is empty.
- Combine (`POST /questions/<id>/combine` and bulk) also commits per `replace_img_assets` call, then deletes descendants. Range stems 409. Nested Combine never touches WHOLE. Bulk overlapping stems collapse to the ancestor.
- `tree_scope` is persisted in `oqb_adminQuestionsFilters.treeScope`.

## Related

- [question-hierarchy.md](question-hierarchy.md) — stem/parts tree, QNO grammar, create/rename/delete invariants.
- [admin-panel.md](admin-panel.md) — the rest of `admin_bp`.
- [ai-tools.md](ai-tools.md) — proofreading, Gen MD, solve, auto-tag, cancel, endpoints.
- [ingestion.md](ingestion.md) — Smart Import (Import Files button), `_canonical_rel` mirror of `_build_asset_file_path`.
- [md-format.md](md-format.md), [doc-format.md](doc-format.md) — MD pipeline and DOC thumbnails.
- [dashboard.md](dashboard.md) — the other host of the Edit modal and the Bulk Edit modal.
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md), [../core/06-system-settings.md](../core/06-system-settings.md).
- [../reference/filename-convention.md](../reference/filename-convention.md).

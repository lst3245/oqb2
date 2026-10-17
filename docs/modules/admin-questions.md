# Admin Question Management

> `/admin/questions`: a single-subject question tree (one row per whole question, parts nested), the Add Question wizard, the shared Edit Question modal (Tags / Assets / Details), per-asset endpoints, and every bulk operation (delete, delete assets, Combine parts, Split into parts, batch update, Copy/Move Assets, Set MCQ ANS, Generate IMG, verify, check-state) run through one bulk panel.

**One subject at a time (ADR-016).** The page has a subject picker with no "All" option. List, search, "select all" and every bulk call are limited to that subject; bulk routes also receive `subject_id` and refuse ids outside it (`_bulk_subject_guard`). See [../decisions/ADR-016-single-subject-question-management.md](../decisions/ADR-016-single-subject-question-management.md).

All backend routes live in `app/admin.py` (blueprint `admin_bp`, prefix `/admin`) in the sections `Question Tagging`, `Question Deletion`, `Batch Question Update`, `Question Management`, `Markdown asset edit/create endpoints`, `Batch IMG Generation from DOC/MD`, `Whole-question verification + batch QA state`, plus the `batch-mcq-ans` route. AI-driven operations (proofread, Gen MD, auto-tag, solve) share this page but are documented in [ai-tools.md](ai-tools.md).

## Files

| File | Role |
|---|---|
| `app/admin.py` | Routes listed below. Helpers: `validate_qid_format` (`parse_qid` from `app.hierarchy`), `_extract_qb_detail`, `_build_asset_file_path`, `_admin_list_subject` (required `subject`), `_admin_questions_filter` / `_admin_questions_query_from_args` (subject-scoped filter + print-order sort), `_admin_list_args` (query + POST JSON/form merged), `_admin_asset_rollup`, `_admin_row_payload`, `_admin_check_scope_from_args`, `_admin_check_scope_clauses`, `_request_bulk_subject` / `_bulk_subject_guard` (ADR-016), `_normalize_asset_ops`, `_require_md_admin`, `_load_batch_update`. |
| `app/question_scope.py` | Pure (no Flask/DB): `ids_subject_violation`, `subjects_of`, `parse_list_args`, `qid_search_pattern`, `root_sort_key`, `page_slice`, the allowed `TREE_SCOPES` / `ROOT_SORTS` / page sizes. Tests: `tests/test_admin_questions_scope.py`. |
| `app/batch_edit.py` | Shared Bulk Edit plan: parse the form, resolve topic/chapter ids (same parent checks as the write), build the before/after preview, apply the plan. Preview and `batch_update_questions` both use it. |
| `app/hierarchy.py` | QNO grammar, `ensure_question`, rename rewrite, subtree delete order, `breadcrumb_parts`, and for this page `group_admin_tree` / `root_id_of` / `root_ids_for` (tests: `AdminTreeTests` in `tests/test_hierarchy.py`). Spec: [question-hierarchy.md](question-hierarchy.md). |
| `app/question_split.py` | Stage/commit IMG crops for `/questions/<id>/split`. Root commit writes `WHOLE` from the staged PNG when that slot is empty. |
| `app/question_combine.py` | Restore/reconstruct combine; `preview_combine`, `combine_parts`, `combine_many`. |
| `app/batch_image_gen.py` | `render_doc_to_pages`, `render_md_to_pages`, `_pdf_to_cropped_images`, `stitch_vertically`, `_build_img_rel_path`, `replace_img_assets`, `find_best_source` (DOC > MD), `slot_has_img`. Used by Generate IMG, Smart Import IMG apply, and the per-slot `generate-img` route. |
| `app/doc_thumbnails.py` | Lifecycle hooks `on_doc_asset_created`, `on_doc_asset_deleted`, `on_img_asset_created`, `on_img_asset_deleted`, `force_rerender`. Every asset mutation here must call the right hook. |
| `app/md_render.py` | `invalidate(asset_id)` after MD delete/save/replace. |
| `app/word_com.py` | `IS_AVAILABLE`, `word_session(lock_timeout=)` global lock used by Generate IMG. |
| `resources/mcq_answer_img/{A,B,C,D}.png` | Source PNGs for Set MCQ ANS (repo root, resolved as `dirname(app.root_path)/resources/mcq_answer_img`). |
| `templates/admin_questions.html` | Thin page: includes the partials below, the shared Edit modal, and `window.AQ_CONFIG` (`subjects`, `aiEnabled`, `isSuperAdmin`, `urls{}` built with `url_for`). |
| `templates/admin_questions/_header.html`, `_filters.html`, `_tree.html`, `_action_bar.html` | Subject picker `#aqSubject` + hand-off chooser `#aqHandoff`; filter bar + chips; tree table `#aqTbody` + select-all banner + pagination; sticky action bar `#aqActionBar` (Status / Assets / AI / Structure menus, Delete). |
| `templates/admin_questions/_bulk_panel.html`, `ops/*.html` | Offcanvas `#aqBulkPanel` (Configure → Review → Run) and one `<div class="bp-op" data-op="…">` form per operation. |
| `templates/admin_questions/_add_modal.html` | Add Question wizard `#addQuestionModal`. |
| `templates/admin_questions/_js_state.html` | `AQ` state, `oqb_adminQuestionsState` persistence, subject switch, facets, filters / chips, hand-off (`aqHandoff`, `aqUseDashboardSelection`). |
| `templates/admin_questions/_js_tree.html` | `aqReload` (POST `/questions/api/tree`), row render, expand/collapse, tri-state selection, select-all-matching, `loadQuestions` alias + `window.oqbAfterQuestionEdit` / `oqbEditNavSource`. |
| `templates/admin_questions/_js_ops.html` | `AQ_OPS` registry (collect / validate / summary / preview / danger / request / result per op), asset-op cards, AI endpoint picker, IMG defaults. |
| `templates/admin_questions/_js_bulk.html` | `BP` runner: preflight `subjects-of`, confirm gate, JSON / form / chunked SSE transport, stop / cancel, result + "Select failed only" + "Copy log". |
| `templates/admin_questions/_js_add.html` | Add wizard JS (`showAddModal`, `createQuestionStep1`, `borrowTagFormForAdd` / `returnTagFormToEditModal`, …). |
| `templates/admin_question_split.html` | Dedicated IMG crop page: shared `OQBBboxEditor` on the stitched image (move/resize/magnifier/preview, Add-box draw mode, Delete-key) + Auto-detect parts. Commit payload `{token, copy_tags, boxes:[{label, box:[x1,y1,x2,y2]}]}` unchanged. |
| `static/js/bbox_editor.js` / `static/css/bbox_editor.css` | Shared overlay editor. See [../frontend/conventions.md](../frontend/conventions.md#bounding-box-editor-oqbbboxeditor). |
| `templates/partials/edit_question_modal.html` | Shared `#editQuestionModal` markup + `#renameConfirmModal` + `#mdEditorModal` + `#combinePartsModal` + `#toastContainer` + scoped CSS. Mounted by `admin_questions.html` and `dashboard.html`. Includes `partials/md_editor.html` (idempotent). |
| `templates/partials/edit_question_modal_js.html` | All Edit-modal JS (`openEditModal`, save, rename, upload, delete, reorder, MD editor, paste handler, Prev/Next nav, thumb-compact toggle, `openCombineConfirm` / `confirmCombineParts`). Pulls in `partials/tag_editor_js.html` — never include that separately on a host. |
| `templates/partials/tag_editor_form.html` | `<form id="editForm">` used by the Tags tab and borrowed by Add Question step 3. |
| `templates/partials/tag_editor_js.html` | `populateTagForm`, topic/subtopic/chapter cascading selects. Loaded transitively only. |
| `templates/partials/md_editor.html` | EasyMDE + marked + KaTeX editor used inline (`#mdEditorModal`) and by `templates/admin_md_editor.html` (fullscreen). |
| `templates/dashboard.html` | Also hosts the Edit modal and the **Bulk Edit** modal. **Preview changes** POSTs `/admin/questions/batch-update/preview`; **Apply changes** POSTs `/admin/questions/batch-update`. Links to this page with `?from=dashboard`. |

## Tables

Schema reference: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Model | Columns touched here |
|---|---|
| `Question` | `qid, subject, source, year, paper, qno, qno_end, parent_id, part, part_sort` (create / rename), `level, q_type, section, description, answer, comment, correct_percentage, marks` (leaves only), `major_topic_id, major_subtopic_id, chapter_id, subchapter_id`, M2M `minor_topics`, `subtopics`, `verified, verified_at, verified_by`, `created_at` (read-only). |
| `QuestionAsset` | `asset_type` (QUE/ANS/SOL/WHOLE), `file_format` (IMG/MD/DOC), `version` (from `app/utils.VERSIONS`: EN/CH/BI/ENO/CHO), `file_path` (forward-slash, relative to `SOURCE_PATH`), `part_number`, `check_state` (None/`checking`/`ok`/`issues`/`error`), `check_result` (JSON), `check_raw`, `checked_at`. Unique on `(question_id, asset_type, version, file_format, part_number)`. `WHOLE` is IMG-only on a root. |
| `Topic`, `Subtopic`, `Chapter`, `Subchapter` | Read for tag validation (`major_subtopic.topic_id == major_topic_id`, `subchapter.chapter_id == chapter_id`). |
| `Subject` | Existence + admin-access checks on create/rename. |

## Routes

Paths are relative to `/admin`. Authz `A` = `@admin_required`; "subject-scoped" means the route additionally checks the question's subject is in `get_user_admin_subjects()` (super admins bypass).

### Page and list

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/questions` | A | Page. Context: `subjects` (admin subjects), `md_max_size_bytes`, `ai_tools_enabled`. |
| GET, POST | `/questions/api/tree` | A + `subject` | One page of whole questions. Body/query: `subject` (**required**; 400 `{error_code:'subject_required'}` when missing, 403 `subject_denied` when not an admin subject), the filters below, `sort` (`qid` = source/year/paper/qno, or `qno|year|q_type|created_at`), `dir` (`asc|desc`), `page`, `page_size` (10/20/50/100/200, else 50). Paging is by **root**: every matching row is mapped to its root, roots are sorted and sliced, and each page root comes back with its **whole** subtree. Response `{subject, items[], total_roots, total_rows, total_matched, subject_rows, page, page_size, total_pages}`; each node = row payload + `children[]` (print order: qno, then `part_sort_key`, `~c` before `c`), `depth`, `matched` (row hit the filters), `matched_count` (hits in the subtree), `subtree_ids[]` (self + descendants, pre-order). Row payload: `{id, qid, subject, source, year, paper, section, qno, qno_end, parent_id, part, label ("Q7", "Q23-24", "(a)", block label), is_block, block_end, is_stem, child_count, q_type, level, created_at (ISO UTC), asset_count (all versions), has_whole, whole_asset_ids[], que_versions[], verified, check_summary}`; `check_summary = {status: none|issues|unchecked|ok, total, ok, issues, unchecked}` over the active Status scope. |
| GET | `/questions/api/facets` | A + `subject` | Filter values for one subject: `{subject, sources[], years[] (desc), papers[], q_types[], total_rows, total_roots}`. |
| GET, POST | `/questions/api/subjects-of` | A | Hand-off resolver: `ids=1,2` or `qids=Q1,Q2` → `{subjects: {SUBJ: n} (admin subjects only, largest first), total, missing, inaccessible}`. Also the bulk panel's preflight. |
| GET, POST | `/questions/api/ids` | A + `subject` | Same filter/sort as the tree; every **matching row** (not expanded to subtrees): `{subject, items:[{id, qid, is_root}], ids[], total}`. Backs "Select all N matching rows". |
| GET, POST | `/questions/api/list` | A + `subject` | Legacy flat page of rows (`{items[], total, page, page_size, total_pages}`); the page no longer calls it. |
| GET | `/questions/<int:question_id>/details` | A, scoped | Edit-modal payload: all tag ids (`minor_topic_ids[]`, `subtopic_ids[]`), metadata including `qno_end`, `parent_id`, `part`, `child_count`, `is_stem`, `has_whole`, `breadcrumb`, `children`, `tag_union`, `created_at`, `verified`, `verified_at`, `verified_by` (username). |
| GET | `/questions/<int:question_id>/assets` | A, scoped | `{qid, assets: {VERSION: {ATYPE: [{id, part_number, file_format, file_path, preview_url, check_state, check_result (parsed), check_raw, checked_at}]}}}`. |

**Filters** (`_admin_questions_filter`, always `Question.subject == subject`). Body or query string; POST JSON lists are joined to CSV by `_admin_list_args`. Mutually exclusive, first match wins:

1. `qids=Q1,Q2` — exact QID list (Health / Subject AI hand-off).
2. `selected_ids=1,2,3` — internal ids (dashboard selection, "Review" of the current selection).
3. `qid_search=` — `*`/`%` wildcards become an `ILIKE` pattern; otherwise substring `ILIKE %term%`.

Combined with any of the above: `source`, `years=2025,2012`, `paper`, `q_type=MC|CQ`, `verified=1|0`, `tree_scope=all|with_parts|standalone|leaves|roots` (with_parts = any row inside a tree; standalone = a root with no parts), `has_whole=1` (`EXISTS` a `WHOLE` asset), `check_status=issues|ok|unchecked` (`EXISTS` over assets in scope; `ok` = has assets in scope and none non-ok). Scope defaults to `TYPED_VERSIONS` (EN/CH/BI), all types, all formats; override with `check_versions`, `check_atypes`, `check_formats` (Proofread **Scope** dropdown). Bad values fall back to "no filter" (`parse_list_args`).

### Create / tag / rename

| Method | Path | Authz | Purpose |
|---|---|---|---|
| POST | `/questions/create` | A, scoped | JSON `{subject, source, year?, paper?, qno, detail?}`. `qno` is a **string token** (`5`, `5a`, `3ci`, `23-24`, optional leading `Q`). PP needs `year` + `paper`; QB needs `detail` without `_`. Builds QID via `parse_qno_token` + `validate_qid_format`. `ensure_question` find-or-creates missing ancestors. 409 if the target QID already exists. Sets `q_type` via `determine_question_type`. Returns `{success, question:{id,qid}}`. Step 1 of the Add wizard. |
| POST | `/questions/<int:question_id>/update` | A, scoped | Form (Save Tags). Fields applied only when present: `level, q_type, section, description, answer, comment, correct_percentage (0-100 else NULL), marks (blank = NULL; whole number 0-999, else 400 `{success:false, error}` and nothing saved), major_topic_id, major_subtopic_id, chapter_id, subchapter_id`; `minor_topic_ids[]`, `subtopic_ids[]` replace the M2M sets (absent = cleared). **Stems ignore** level/q_type/topic/chapter/marks writes; answer/comment still apply. The response includes `marks`. Changing `major_topic_id` clears `major_subtopic_id`; a subtopic not under the major topic is nulled. Same for chapter/subchapter. |
| POST | `/questions/<int:question_id>/rename` | A, scoped to the current **and** the new subject | JSON `{new_qid, confirm_rename_files}`. Validates format, subject exists. Cascades to descendants: rewrites each QID relative to the renamed node, then `relink_parent`. 400 if the rename would change range-vs-part shape while children exist (`rename_shape_ok`). 409 on QID collision in or outside the subtree. When `confirm_rename_files` is true, every asset in the subtree is moved to `_build_asset_file_path(...)` (`shutil.move`, dirs created) and `file_path` updated; missing files just get the new path. Returns `{old_qid, new_qid, renamed_files[], errors[]}`. |
| POST | `/questions/<int:question_id>/children` | A, scoped | JSON `{part}` letters (`a`, `b`, `ci`). `ensure_question` under this token. 400 on range stems or invalid label. |
| POST | `/questions/<int:question_id>/parent` | A, scoped | JSON `{parent_id}` (null detaches). Same subject; `token_fits_under`; no cycles. 400 if detaching a labelled part. |
| GET | `/questions/<int:question_id>/split` | A, scoped | Stages IMG QUE; renders `admin_question_split.html`. MD/DOC QUE flash-redirects to the list. Hidden in the Edit modal when the row is already a stem. |
| GET | `/questions/<int:question_id>/split/image/<version>` | A, scoped | Staged PNG (`?token=`). |
| GET | `/questions/<int:question_id>/split/detect` | A, scoped | SSE. Query `token`, `version`, `endpoint_id`, `method?`, `find_parent?`. Stitch multi-image QUE, then pass-2 once → `done` `{boxes, raw}` on the stitch. |
| POST | `/questions/<int:question_id>/split/commit` | A, scoped | JSON `{token, boxes:[{label, box:[x1,y1,x2,y2]}], copy_tags}`. Requires `stem` + ≥1 part. Crops every staged version; optional copy tags then clear the stem. Root: writes WHOLE from the staged PNG if that slot is empty. |
| GET | `/questions/<int:question_id>/combine/preview` | A, scoped | Dry payload for the Combine confirm dialog (`preview_combine`). 400/409 when the row is a leaf or range stem. |
| POST | `/questions/<int:question_id>/combine` | A, scoped | JSON `{stitch?, save_whole?}`. Restore WHOLE→QUE or reconstruct, then delete descendants. |
| POST | `/questions/combine` | A + subject guard | JSON `{ids, stitch?, save_whole?, subject_id?}`. Bulk; overlapping stems collapse to the ancestor. `{ok, results:[{ok, id, qid, error?, deleted_ids?}]}`. |

### Assets (per question)

| Method | Path | Authz | Purpose |
|---|---|---|---|
| POST | `/questions/<int:question_id>/assets/upload` | A, scoped | Multipart `files[]`, `version` (legacy `language` accepted, default EN), `asset_type` (default QUE; `WHOLE` allowed on roots, IMG only). Extension decides format: png/jpg/jpeg/gif/bmp -> IMG (multi-part; next part = max IMG part + 1), doc/docx -> DOC (single-slot; rejected if one exists), md/markdown -> MD (single-slot; rejected if one exists; `MD_MAX_SIZE_BYTES` enforced; extension normalised to `.md`). Files land at the canonical path. Post-commit: IMG -> `on_img_asset_created`, DOC -> `on_doc_asset_created`. Returns `{uploaded_count, errors[]}`. |
| POST/DELETE | `/questions/<int:question_id>/assets/<int:asset_id>/delete` | A, scoped | Optional JSON `{delete_from_disk}` (default true). MD -> `md_render.invalidate`; DOC -> `on_doc_asset_deleted`; IMG -> `on_img_asset_deleted` (a DOC left alone in the slot gets its thumbnail scheduled). |
| POST | `/questions/<int:question_id>/assets/reorder` | A, scoped | JSON `{version (legacy language), asset_type, asset_ids[]}` in new order. IMG only — MD/DOC ids are skipped (400 if nothing IMG remains). Two-phase DB update (negative temp part numbers) then two-phase file rename via `.tmp_reorder` names to avoid unique-constraint and swap collisions. Returns `{success, files_renamed, warnings?}`. |
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

**Subject guard (ADR-016).** Every bulk route on this page (`delete`, `batch_delete_assets`, `combine`, `batch-set-verified`, `batch-set-check-state`, and the SSE `batch-generate-images`, `batch-mcq-ans`, `batch-asset-ops`, AI streams) calls `_bulk_subject_guard(ids)` before doing anything. It reads `subject_id` from the query string, form or JSON body: with it, every id must be in that subject; without it, the ids must not span subjects. Otherwise **400** `{success:false, ok:false, error, error_code:'outside_subject'|'mixed_subjects', …}`. The page always sends `subject_id`. `batch-update` (dashboard Bulk Edit) is not guarded.

| Method | Path | Authz | Purpose |
|---|---|---|---|
| POST | `/questions/delete` | A + subject guard | Form `question_ids[]`, `delete_files` (`'true'`), `delete_children` (`'true'`). Selection intersected with admin subjects. If any selected row has descendants not also selected, **409** unless `delete_children=true` (then those descendants in admin subjects are included). Deletes deepest-first (`ON DELETE RESTRICT`). A selected shared block (`Q7~c`) without `delete_children` is dissolved: its parts move up to the lettered parent instead of blocking. `delete_empty_parents=true` also deletes every ancestor that would be left with no children (cascading: `Q7~c`, then `Q7`; `Q1c`, range stems) via `hierarchy.ancestors_left_empty`, **except** the ancestors of a block being dissolved. A root holding a WHOLE archive is included like any other empty parent (its archive image goes with it; changed 2026-10-17 — it used to be kept). `dry_run=true` returns `{success, dry_run, count, empty_parent_qids[], archive_qids[]}` (`archive_qids` ⊆ `empty_parent_qids`: the ones holding a WHOLE image) before anything is written (the 409 check still applies). Assets cascade; optionally deletes files. After commit: DOC thumbnail + MD invalidate for dropped assets. Returns `{deleted_count, deleted_qids[], deleted_ids[], left_empty_qids[] (empty parents kept because the flag was off), files_deleted}` or `{error, blocking_qids[]}`. |
| POST | `/questions/batch_delete_assets` | A + subject guard | Form `question_ids[]`, `formats[]` (IMG/MD/DOC), `versions[]` (legacy `langs[]`), `atypes[]` (QUE/ANS/SOL), `delete_files` (default `'true'`). Each axis must be non-empty; the three `IN(...)` clauses are ANDed. Scoped to admin subjects. `dry_run=true` returns `{success, dry_run, count, questions_touched}` before anything is deleted (the bulk panel's Review step). Fires the same thumbnail lifecycle hooks and MD invalidation as single delete. Returns `{deleted_count, files_deleted, questions_touched, message}`. |
| POST | `/questions/batch-update/preview` | A | Same form as batch-update. Does **not** capture or commit. `{success, selected, found, missing, changed_count, unchanged_count, updating[], warnings[], questions:[{id, qid, changes:[{field, label, before, after, before_empty, after_empty, removed?, added?}]}], unchanged:[{id, qid}]}`. `questions` is only rows with at least one changed field, in submitted id order. A minor-topic / M2M-subtopic change is by set of ids (order alone is not a change). |
| POST | `/questions/batch-update` | A | Form `question_ids[]` + flag/value pairs: `update_level`/`level`, `update_q_type`/`q_type`, `update_section`/`section`, `update_correct_pct`/`correct_percentage`, `update_marks`/`marks` (whole number 0-999; stems skipped), `update_topics` (bundles `major_topic_id`, `major_subtopic_id`, `minor_topic_ids[]`, `subtopic_ids[]`), `update_chapters` (`chapter_id`, `subchapter_id`). Flags are `'1'`. Empty value with the flag set clears the field. Writes the same plan the preview showed. UI is the **Bulk Edit** modal on `dashboard.html` (**Apply changes**, only after preview). |
| POST | `/questions/batch-set-verified` | A, scoped + subject guard | JSON `{question_ids[], verified, subject_id?}`. Sets `verified_at`/`verified_by` (or nulls them). Returns `{updated, verified}`. |
| POST | `/questions/batch-set-check-state` | A, scoped + subject guard | JSON `{question_ids[], versions[], atypes[], state, note?, severity?, overwrite?, subject_id?}`. Same write shape as the single check-state route across every (question x version x atype) slot with assets. With `overwrite` false (default) slots that already have any `check_state` are skipped; `clear` always applies. Returns `{slots_updated, assets_updated}`. |
| POST | `/questions/<int:question_id>/verify` | A, scoped | JSON `{verified}`. Response `{verified, verified_at, unchecked_assets (count with check_state != 'ok'), total_assets}` for the modal soft-warn. Always allowed. |

### Bulk operations (SSE GET)

All return `text/event-stream` with `data: {type, message, current?, total?, stats?}` frames; `type` in `info|success|skip|error|done`. All take `subject_id` and run the subject guard first (400 JSON before any streaming — an `EventSource` only sees `onerror`, so the page preflights with `/questions/api/subjects-of`). Question ids outside the caller's admin subjects are silently dropped; 403 JSON if none remain. The page sends at most 800 ids per stream and runs several streams in sequence for bigger selections (the request line has a length limit).

| Path | Query | Purpose |
|---|---|---|
| `/questions/batch-generate-images` | `question_ids` (csv, required), `types` (default `QUE`), `versions` (default all; legacy `langs`), `sources` (default `DOC,MD`), `stitch` (default 1), `overwrite` (default 0), `width`, `transparent`, `symmetric_horizontal` | 400 JSON if `word_com.IS_AVAILABLE` is false. For each (question x type x version) slot: `find_best_source` (DOC before MD); skip if no source, skip if IMG exists and not `overwrite`; render via `render_doc_to_pages` / `render_md_to_pages` (pandoc -> docx -> Word -> PDF -> PyMuPDF -> Pillow crop), then `replace_img_assets` (stitch or one IMG per page). Defaults resolved inside the app context: `BATCH_IMG_DEFAULT_WIDTH` (fallback `DOC_THUMBNAIL_WIDTH`), `THUMBNAIL_TRANSPARENT`, `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP`, `THUMBNAIL_WHITENESS_THRESHOLD`, `THUMBNAIL_BOTTOM_PADDING_PX`, `WORD_COM_LOCK_TIMEOUT`. One `word_session` for the whole run. UI: action bar **Assets → Generate IMG** (bulk panel op `img`). |
| `/questions/batch-mcq-ans` | `question_ids`, `versions` (default `EN`; legacy `langs`), `overwrite` (default 0) | For each question x version: skip unless `q_type == 'MC'` and `answer` in A-D; copy `resources/mcq_answer_img/<answer>.png` into the canonical ANS IMG slot and upsert the `QuestionAsset` row (existing IMG ANS skipped unless `overwrite`). UI: **Assets → Set MCQ ANS images** (op `mcq`). `done` carries top-level `success` / `skipped` / `errors`. |
| `/questions/batch-asset-ops` | `question_ids`, `ops` (JSON list, max 25) | **Assets → Copy / move assets** (op `assetOps`). Each op: `{action: copy|move, source_versions[], target_versions[], source_atypes[], target_atypes[], source_formats[], target_formats[], overwrite}`. Ops run sequentially per question. Mapping per axis: one source version/atype fans out to every selected target; multiple sources lock that axis and map by name. Formats are same-format only (intersection of source/target formats). IMG keeps multi-part numbering; MD/DOC stay single-slot. Overwrite deletes target rows/files and fires MD invalidate + DOC thumbnail hooks. Move removes the source slot only after every requested target for that source succeeded. `stats = {copied, moved, skipped, errors}`. |

AI SSE streams on the same page (`/questions/ai/check`, `/ai/generate-md`, `/ai/solve-generate`, `/ai/solve-check`, `/ai/auto-tag`, `POST /ai/cancel`, `GET /ai/endpoints`) and per-slot AI POSTs (`/assets/ai/generate-md`, `/assets/ai/check`, `/assets/ai/solve-generate`, `/assets/ai/solve-check`, `/ai/answer-text`, `/ai/suggest-tags`, `/assets/generate-img`) are documented in [ai-tools.md](ai-tools.md). After **Suggest tags**, a successful **Save Tags** on the same question posts the suggested-vs-saved pairs to `POST /admin/questions/<id>/ai/tag-corrections` (`_logTagCorrections`, fire-and-forget) — see [subject-ai.md](subject-ai.md).

## Business rules / invariants

- **Canonical asset path** is always `_build_asset_file_path(question, asset)`: `<SUBJ>/PP/<SOURCE>/<YEAR>/<PAPER>/<QID>_<VERSION>_<TYPE>[_<part>].<ext>` for DSE/CE/AL, `<SUBJ>/QB/<DETAIL>/<QID>_<VERSION>_<TYPE>[_<part>].<ext>` for QB. Part suffix only when `part_number > 1`. Stored with forward slashes.
- **IMG is multi-part; MD and DOC are single-slot** (`part_number` always 1). Upload rejects a second MD/DOC in the same `(question, asset_type, version)` slot; reorder ignores MD/DOC ids; MD/DOC never consume an IMG part index.
- **QID grammar**: `parse_qid` in `app/hierarchy.py` (not a local regex). `QNO` is `Q<n>`, `Q<n><part>`, or `Q<n>-<n>`. QB `detail` cannot contain `_`. Creating `3a` also creates an empty `Q3` parent when missing. Creating `23-24` adopts parentless same-paper `Q23`/`Q24` with `part IS NULL`.
- **Rename cascade / delete guard**: see [question-hierarchy.md](question-hierarchy.md). Cannot rename `Q3` → `Q3a` while this row would still be its own parent.
- Tag validation: `major_subtopic` must belong to `major_topic`; `subchapter` must belong to `chapter`; violations are silently nulled, not rejected. Changing the parent clears the child.
- **Restore points:** `update_question` reads the subject state (`subject_snapshot.read_state`) before touching the question and stores it as a `question-tags` point only when `question_tags_differ` (answer / comment / description-only saves add none). `batch-update` captures one `batch-update` point per subject before the loop when any tag flag is set. `POST .../batch-update/preview` does not capture. A new tag writer here must capture before mutating — see [subject-snapshots.md](subject-snapshots.md). Split / Combine / create child / set parent deliberately do not capture.
- **DOC thumbnail lifecycle**: any code path that creates/deletes IMG or DOC rows must call the matching `doc_thumbnails.on_*` hook; an IMG in a slot eclipses the DOC thumbnail for that slot.
- **MD render cache**: call `md_render.invalidate(asset_id)` after any MD content or row change.
- **Check state is per format within a slot**: manual check-state writes hit every row in the `(version, asset_type)` slot; AI proofreading writes per format (see [ai-tools.md](ai-tools.md)). Status rollup in the table defaults to `TYPED_VERSIONS` only.
- **Verified flag** is independent of check states; verifying is always allowed but the modal soft-warns using `unchecked_assets`.
- Bulk destructive routes intersect the submitted ids with the caller's admin subjects **and** refuse ids outside `subject_id` (or spanning subjects) via `_bulk_subject_guard`; per-question routes 403 on a foreign subject (`_require_md_admin`).
- **One subject per page state (ADR-016).** There is no cross-subject list, search, select-all or bulk action. A hand-off that spans subjects (dashboard selection, Health QID list) shows a chooser instead of loading everything.
- **Selection model:** ticking a row selects its whole subtree (stem + every part); a partly selected subtree shows indeterminate and becomes fully selected on the next click. The header box does the same for every question on the page. "Select all N matching rows" selects exactly the rows the server matched (`/questions/api/ids`), not their subtrees — with *Printable parts only* that is just the leaves. The selection survives paging and filter changes inside the subject and is cleared (after a confirm) when the subject changes.
- Generate IMG and Smart Import both funnel IMG writes through `replace_img_assets` (render new files under temp names first, delete old rows/files, rename into canonical names, insert rows, commit).
- Word COM work is serialised through the single global lock shared with docx generation and DOC thumbnails (`word_com.word_session`).

### Frontend contracts

#### Page (`admin_questions.html` + `admin_questions/`)

- **Subject picker** `#aqSubject`: admin subjects only, placeholder "Choose a subject…", no "All" option. Nothing loads until a subject is picked (a user with one subject gets it preselected). Changing subject with rows selected asks "Clear the N selected row(s) in ICT?"; cancelling keeps the old subject.
- **Persistence:** `localStorage['oqb_adminQuestionsState'] = {subject, filtersBySubject: {SUBJ: {q, source, years[], paper, q_type, tree_scope, verified, check_status, has_whole, scope:{versions, atypes, formats}}}, pageSize, sort:{field, dir}}`. The old `oqb_adminQuestionsFilters` key is removed on load. The selection is never persisted.
- **Filters** (`_filters.html`): QID search (debounced 300 ms), Source, Years (multi), Paper, Type, Structure (`tree_scope`), Verified, Proofread status + Scope dropdown, Has archive, **Dashboard selection** button, Reset. Active filters show as removable chips; a pinned id/QID list shows as a yellow chip. Typing in search drops a pinned list.
- **Tree** (`_tree.html` / `_js_tree.html`): one row per root with a caret; parts render underneath, collapsed by default. A root (or block) that did not match but has a matching part opens automatically; a collapsed subtree with hidden hits shows a blue dot. When any filter is active, non-matching rows are dimmed (they are shown for context). Columns: Question (label + QID), Paper, Q#, Type, QUE versions, Assets (+ archive icon → `aqPreviewWhole` → `showFullSize` of `/dashboard/api/asset_preview/<id>`), Proofread, Verified, Created, Actions (Edit + ⋮ menu: Open in dashboard (`/dashboard/?qids=`), Import files, Split into parts (rows without parts), Combine parts (rows with parts), Delete). Row ⋮ actions run on that row only and leave the selection alone.
- **Selection** (`AQ.selected: Map<id, {qid, root}>`): see the selection model under Business rules. When the whole page is selected and there are more pages, the banner offers **Select all N matching rows in SUBJ** (`/questions/api/ids`).
- **Action bar** `#aqActionBar` (sticky, shown while something is selected): count + "N question(s) · M part / stem row(s) · K not on this page", **Review** (pins the selection as the list), Clear, menus **Status** (Set verified, Set proofread state), **Assets** (Generate IMG, Set MCQ ANS images, Copy / move assets, Import files for selection, Delete assets), **AI** (when `ai_tools_enabled`: Proofread, Generate Markdown, Generate ANS / SOL, Check ANS / SOL, Auto-tag), **Structure** (Combine parts, Split into parts — exactly one row), **Delete questions**.
- **Bulk panel** `#aqBulkPanel` (offcanvas, static backdrop; cannot be closed while running). Stages: **Configure** (op form; `validate` errors block), **Review** (summary lines, affected count + first 50 QIDs, preflight `POST /questions/api/subjects-of` — refuses if any id is missing, inaccessible or outside the subject — then the op's server dry run: delete questions → `dry_run` of `/questions/delete` lists the rows that go, empty parents and archive-protected roots, and a 409 (unselected parts) blocks Run; delete assets → `dry_run` of `batch_delete_assets` shows the asset count, 0 blocks Run), **Run** (progress, colour log, Stop, result counts, **Select failed only** — QIDs parsed from `error` events — and **Copy log**).
- **Confirm gate:** typing the subject code (case-insensitive) for *Delete questions*, *Delete assets*, *Combine parts*, *Copy / move* with any Move step or Overwrite, and *Auto-tag* with Overwrite; otherwise a tick when the action covers more than 100 rows (`BP_TICK_THRESHOLD`).
- **Stop:** AI ops POST `/questions/ai/cancel {job_id}` and keep the stream open until the server's `done` (the current item finishes); other streams are closed client-side and the log says the server may finish the current item. Further 800-id batches are not started after Stop.
- After a run the list reloads; deleted / combined-away ids leave the selection; deletes also drop the ids from `localStorage.oqb_selectedQuestions` (`aqForgetDashboardSelection`).
- **Entry points** (one-shot URL params, stripped after load): `?qids=Q1,Q2` / `?qids_token=<localStorage key>` (Health, Subject AI), `?ids=1,2` (Subject AI rows without a QID), `?from=dashboard` (dashboard **Manage**: uses `localStorage.oqb_selectedQuestions`), `?subject=SUBJ`. Linked ids go through `/questions/api/subjects-of`: one subject → switch to it and pin the list; several → `#aqHandoff` asks which subject; none accessible → toast. Without a hand-off the last subject from `oqb_adminQuestionsState` loads.
- **Add Question** (`#addQuestionModal`, `_js_add.html`): 3-step wizard; the subject defaults to the page's subject and warns when changed. 1 Details (POST `/questions/create` must succeed first; `#addQno` is text — `1`, `3a`, `3ci`, `23-24`), 2 Assets (upload into the new question), 3 Tags (`borrowTagFormForAdd()` moves `#editForm` from the Edit modal into `#addTagFormArea`; `returnTagFormToEditModal()` puts it back, also on modal close). "Save and add another" tries `parseInt(qno)+1` (integer-only). Only the Edit modal is shared with the dashboard.

#### Shared Edit modal (`partials/edit_question_modal*.html`)

- Tabs, fixed order: **Tags** (default; `tag_editor_form.html`), **Assets** (one version pill per entry in `OQB_VERSIONS` x QUE/ANS/SOL; roots also show a **Whole question archive** IMG strip (`WHOLE`); upload / delete / reorder; IMG multi-part, MD inline editor via `#mdEditorModal` plus a fullscreen link, DOC upload + download; requests send `version`; on a root / part stem that is not a block, QUE image cards from page 2 on carry a layers button → `moveQuePageToBlock` (asks "printed before which part?", then the last part that uses it (blank = all later parts), dry-runs `POST /admin/questions/<id>/assets/move-to-block`, confirms, then moves the page into a new shared block — see [question-hierarchy.md](question-hierarchy.md#shared-blocks-text-between-parts)), **Details** (QID rename via `#renameConfirmModal`; if `child_count > 0` the confirm shows `#renameDescendantNote`; Hierarchy: create child, set parent (409 when a shared block owns / excludes the part), Split link, **Combine parts** confirm `#combinePartsModal`, on a block **Ends after part** (`#editBlockEnd` + Apply → `setBlockEnd`: dry-run `POST /admin/questions/<id>/block-end`, confirm the parts leaving / joining, apply); read-only `created_at`, Verified toggle).
- Entry point `openEditModal(questionId, opts?)`: fetches `/questions/<id>/details` and `/questions/<id>/assets`, calls `populateTagForm`, shows the modal. `opts.preserveView` keeps the active main tab + version pill; `opts.preserveAssetScroll` restores asset scroll (used by Prev/Next).
- **Host refresh contract**: after save / rename / asset mutations the modal calls `window.oqbAfterQuestionEdit?.()`. Hosts assign it after the include: `window.oqbAfterQuestionEdit = loadQuestions` (admin; `loadQuestions` is an alias of `aqReload` in `_js_tree.html`, also called directly by the partial) / `= refreshCurrentPage` (dashboard). Asset mutations mark the list stale (`markEditModalListStale()`) and flush on `hidden.bs.modal`; Save Tags / verify / rename refresh immediately.
- **Navigation contract**: hosts define `window.oqbEditNavSource()` returning the ordered id array, read fresh on every open (admin: visible rows `#aqTbody tr.aq-row:not(.d-none)`, i.e. expanded parts included; dashboard: `#allQuestionIds`). `#editNavControls` Prev/Next and Left/Right arrow keys step through it preserving tab, pill and scroll; Ctrl+Left/Right switches version pills. Shortcuts are ignored while typing or when a stacked child modal is open.
- Per-slot AI controls in the Assets tab are gated on `window.OQB_AI_TOOLS_ENABLED`: editable proofread bar (`renderCheckStatusBar`), Quick Check (`openQuickCheckModal`), **Gen IMG** on MD/DOC cards (`generateImgFromSlot`).
- `localStorage['oqb_assetThumbsCompact']` (`'1'|'0'`) toggles compact vs full-width asset previews (`<body>.asset-thumbs-compact`, `oqbInitThumbCompact()`).
- Global helpers relied on from `base.html`: `window.OQB_VERSIONS`, `OQB_VERSION_LABELS`, `oqbFormatLocalTime`, `oqbPollDocThumbnails`, `oqbRerenderThumb`, `oqbLoadMarkdownPreviewCards`.

## Settings & config keys

Reference: [../core/06-system-settings.md](../core/06-system-settings.md).

| Key | Used by |
|---|---|
| `SOURCE_PATH` (`.env`) | Every file write/move/delete. |
| `MD_MAX_SIZE_BYTES` | Upload, MD create/save, Smart Import MD apply; passed to the page as `md_max_size_bytes`. |
| `BATCH_IMG_DEFAULT_WIDTH`, `BATCH_IMG_DEFAULT_STITCH` | Generate IMG form defaults (`width` falls back to `DOC_THUMBNAIL_WIDTH`); prefilled from `/admin/settings/data` for super admins only, otherwise the form leaves width blank and the server applies the defaults. |
| `THUMBNAIL_TRANSPARENT`, `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP`, `THUMBNAIL_WHITENESS_THRESHOLD`, `THUMBNAIL_BOTTOM_PADDING_PX` | Crop parameters for Generate IMG. |
| `WORD_COM_LOCK_TIMEOUT` | `word_session(lock_timeout=)` for Generate IMG. |
| `AI_TOOLS_ENABLED` | Gates AI controls (`ai_tools_enabled` template var). |
| `DOC_THUMBNAIL_PATH` | Where lifecycle hooks read/write PNGs. |

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md). Everything here is `@admin_required`. List routes require a `subject` the caller administers (403 `subject_denied` otherwise); bulk routes are filtered to `get_user_admin_subjects()` and run the subject guard; single-question routes marked "scoped" 403 when the question's (or, for rename, the current or new QID's) subject is not one the caller administers. Super admins bypass the admin-subject checks but **not** the subject guard (a super admin's bulk call still has to stay inside one subject). The Edit modal on the dashboard is only mounted for users with `window.OQB_IS_ADMIN`.

## Background work / SSE / threads

- SSE generators: `batch-generate-images`, `batch-mcq-ans`, `batch-asset-ops` (plus the AI streams). Standard frame shape and headers as in [admin-panel.md](admin-panel.md#background-work--sse--threads); the client closes on `done`. Generate IMG holds the Word COM lock for the entire stream.
- `rerender-thumb` and `on_doc_asset_created` schedule background renders in `app/doc_thumbnails.py`; the UI polls `data-doc-pending-id` elements every 3 s (`oqbPollDocThumbnails`, up to ~3 min).
- Batch form POSTs are synchronous single transactions; a failure rolls back the whole batch.

## Gotchas

- `edit_question_modal_js.html` state (`editQuestionData`, etc.) is declared with top-level `let`, so it is **not** a `window.*` property. Reference it bare inside that file; `window.editQuestionData` is silently `undefined` (this hid the WHOLE archive strip until it was fixed). Only values the host page must read are exposed on `window` explicitly.
- The single-asset delete URL is `/questions/<qid>/assets/<aid>/delete` (POST or DELETE), not a bare `DELETE /assets/<aid>`.
- `update_question` clears `minor_topics`/`subtopics` whenever the arrays are absent from the form — always submit the full current lists from the Tags form.
- **Do not add an "All subjects" option or a cross-subject bulk path** (ADR-016). Before this page was scoped, "select all" plus Delete could hit every question in the library.
- New bulk op: add a form under `admin_questions/ops/`, an `AQ_OPS` entry whose request carries `subject_id` (`opIdsForm` does it for forms; the SSE runner adds it), and `_bulk_subject_guard` in the route. `tests/test_admin_questions_ux.py` checks the builders.
- The `_js_*.html` scripts share the global scope with `partials/edit_question_modal_js.html` (top-level `let`/`const`/`function`), so a duplicate name is a `SyntaxError` that kills the whole page. Keep the `aq` / `bp` / `op` prefixes; the Add wizard names (`showAddModal`, `addCreatedQuestionId`, …) are the exception because the partial looks them up.
- `/questions/api/tree` loads every row id + parent id of the subject to map matches to roots; that is ~0.2 s for ICT (3.6k rows). It does not scale to a cross-subject list — another reason there is none.
- Batch update lives in the **dashboard** Bulk Edit modal, not in `admin_questions.html`; it does not intersect with admin subjects beyond `@admin_required`. The modal previews (`POST /questions/batch-update/preview`) and only then writes. Both routes call `_load_batch_update` → `app/batch_edit.py`; do not give the write its own field loop or the preview will drift. Preview does not call `subject_snapshot.capture*`.
- Legacy `language`/`langs` parameters are accepted everywhere `version`/`versions` are; always send the new names.
- `check_versions`/`check_atypes`/`check_formats` change both the Status filter **and** the `check_summary` badge per row; `asset_count` is never scoped.
- `qids`, `selected_ids` and `qid_search` are exclusive; when `qids` is set the search box is ignored server-side. All other filters still apply on top of a pinned list.
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
- `tree_scope` and the Has-archive checkbox are persisted per subject in `oqb_adminQuestionsState.filtersBySubject[SUBJ]` (`tree_scope`, `has_whole`).
- The archive icon in the list is a viewer only. Upload / reorder / delete of WHOLE stays in the edit modal's Assets tab on the root; batch asset ops never target WHOLE.

## Related

- [ADR-016](../decisions/ADR-016-single-subject-question-management.md) — why the page works on one subject at a time.
- [question-hierarchy.md](question-hierarchy.md) — stem/parts tree, QNO grammar, create/rename/delete invariants.
- [admin-panel.md](admin-panel.md) — the rest of `admin_bp`.
- [ai-tools.md](ai-tools.md) — proofreading, Gen MD, solve, auto-tag, cancel, endpoints.
- [ingestion.md](ingestion.md) — Smart Import (Import Files button), `_canonical_rel` mirror of `_build_asset_file_path`.
- [md-format.md](md-format.md), [doc-format.md](doc-format.md) — MD pipeline and DOC thumbnails.
- [dashboard.md](dashboard.md) — the other host of the Edit modal and the Bulk Edit modal.
- [subject-snapshots.md](subject-snapshots.md) — restore points captured by Save Tags and Bulk Edit; undo a bad bulk edit there.
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md), [../core/06-system-settings.md](../core/06-system-settings.md).
- [../reference/filename-convention.md](../reference/filename-convention.md).

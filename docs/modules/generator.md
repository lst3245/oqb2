# Generator

> Word document generation from a dashboard selection (python-docx + pandoc + Word COM), background threading, lazy on-demand PDF conversion, and the slide-style Viewer / Present mode.

## Files

| File | Role |
|---|---|
| `app/generator.py` | `generator_bp` (`/generate`). Options page, `create_document()` → background `_generate_in_background()`, `create_word_document()`, `add_question_content_to_doc()` (IMG / MD / DOC branches), compact MC key helpers, `_run_word_postprocess_single/_split`, `_split_questions_into_groups`, lazy PDF (`download_pdf`, `_pdf_sibling_filename`, `_build_pdf_from_docx`, `_build_pdf_zip_from_docx_zip`), `generated_file_dir()`, viewer routes, `md_to_docx_via_pandoc` / `_append_md_via_pandoc`, `_parse_format_priority`. |
| `app/hierarchy.py` | `resolve_render_plan`, `eager_load_tree`, seq-owner helpers. Called from create, background generate, and viewer. Spec: [question-hierarchy.md](question-hierarchy.md). |
| `app/word_com.py` | Word COM engine: `IS_AVAILABLE`, `word_session(lock_timeout)`, `merge_doc_into_master`, `export_to_pdf`, `sanitize_docx_for_insertion`, `render_first_page_png`, `_WORD_COM_LOCK`, `WordComUnavailable`. |
| `templates/generate.html` | Options form (progressive disclosure), Presets bar, Version Priority + Format Priority widgets, Reorder-blocks modal, `submitGeneration()` → status polling → success banner with Download / Get PDF / My Files. |
| `templates/viewer.html` | Standalone Present mode page (does **not** extend `base.html`); `loadAsset`, `loadAnswerAsset`, zoom/layout/theatre controls, Version Priority widget include. |
| `templates/partials/_version_priority_widget_js.html` | Drag-to-reorder version widget shared by dashboard, generate page and viewer. |
| `app/utils.py` | `apply_multi_sort`, `SORT_FIELDS`, `GROUPING_FIELDS`, `enumerate_sort_groups`, `parse_version_priority`, `DEFAULT_VERSION_PRIORITY`. |
| `app/storage.py` | `ensure_user_generated_dir(user)` / `user_generated_dir(user)` — where output files are written. |
| `app/user.py` | `_get_or_create_default_section` (new files land in the owner's default My Files section), `_user_can_view_file` (PDF route permission). |
| `app/ai_prompts.py` | `normalize_inline_math` used by `_preprocess_md_for_pandoc`. |

## Tables

- `GeneratedFile` — one row per job: `display_name`, `filename` (on disk), `status` (`pending` → `generating` → `completed` | `failed`), `error_message`, `filter_data` (JSON from dashboard), `generation_options` (JSON, table below), `question_count`, `section_id`, `manual_position`, `created_at`, `completed_at`.
- Reads `Question`, `QuestionAsset`, `SavedGenerationProfile` (via `/user/gen-profiles`, see [my-files.md](my-files.md)).

Schema: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

## Routes

All routes are `@login_required`. `_require_generate_permission()` aborts 403 when `current_user.can_generate()` is false (viewers).

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET, POST | `/generate/` | login + can_generate | Options page. POST carries `question_ids[]`, `filter_data`, `sort_config`, `sort_group_order` from the dashboard (`submitQuestionIds()`); stored in `session['generator_question_ids']`, `session['generator_filter_data']`, `session['sort_config']`, `session['sort_group_order']` so refresh works. GET falls back to the session. `?regen_file_id=<id>` (owner or super admin) pre-fills options, question IDs (unless explicit IDs were POSTed), filter data, sort config and block order from a `GeneratedFile`; the trailing `_YYYYMMDD_HHMMSS` is stripped from the display name. Redirects to the dashboard with a flash when no IDs. Form includes `hierarchy_mode` (`selected` default / `whole`). |
| GET, POST | `/generate/viewer` | login | Present mode. Accepts `question_ids[]`, `sort_config`, `sort_group_order`, `hierarchy_mode` (form or query; session fallback `viewer_question_ids`, `viewer_sort_config`). Applies `apply_multi_sort` then `resolve_render_plan`; `viewer.html` slides/drawer are **leaves** with `part`, `stem_id`, `breadcrumb`. Stem QUE loads in `#stemPanel` via `viewer_asset`. Not gated on `can_generate` server-side (the dashboard button is). |
| POST | `/generate/create` | login + can_generate | Starts a job. Form fields = generation options (below) + `question_ids[]`, `display_name`, `filter_data`. Filters out questions from subjects where the user's role is not `user`/`admin` (403 if none remain). Returns `{id, status:'pending', filename}`. |
| GET | `/generate/status/<int:file_id>` | login; owner or super admin (403 JSON) | `{id, status, error_message, display_name, filename}`. Polled every 2 s by `generate.html`. |
| GET | `/generate/download/<int:file_id>` | login + can_generate; owner or super admin | Sends the `.docx` / `.zip` with mimetype from the extension. Non-completed or missing file → flash + redirect to `/user/files`. |
| GET | `/generate/pdf/<int:file_id>` | login; `_user_can_view_file` (owner, super admin, or shared-with) | **Lazy PDF.** Serves the cached sibling if present, else builds it synchronously with Word COM, caches, then sends. JSON errors: 403 access, 409 not completed, 400 not convertible (filename not `.docx`/`.zip`), 404 source missing on disk, 503 Word COM unavailable, 500 build failed. Download name is `<display_name>.pdf` or `<display_name>.pdf.zip`. |
| POST | `/generate/api/sort-groups` | login + can_generate | Body `question_ids[]` + `group_fields` (JSON array or csv). Scopes to accessible subjects. Returns `{group_fields, blocks:[{key, labels, count}]}` for the Reorder-blocks modal. |
| GET | `/generate/api/viewer_asset/<int:question_id>/<asset_type>?version_priority=EN,CH,BI,ENO,CHO` | login | Best `(format, version)` group for the viewer. Falls back ANS ↔ SOL when the requested type is missing (`asset_type` in the response reports the type actually used). Returns `{parts:[{id,type,format,version,part_number,url}], id, question_id, type, format, version, url, asset_type, html?, thumbnail_url?}`; `html` for MD, `thumbnail_url` for DOC when a cached PNG exists (else one is scheduled). Legacy `?lang=EN` accepted. |

## Business rules / invariants

### Flow

1. Dashboard `submitQuestionIds()` POSTs the selection to `GET|POST /generate/`.
2. User configures options; `submitGeneration()` POSTs to `/generate/create`.
3. `create_document()` writes a `GeneratedFile` (status `pending`, `section_id` = owner's default section) and spawns a **daemon thread** running `_generate_in_background(app, ...)` with the real app object (`current_app._get_current_object()`).
4. The thread sorts, splits, expands with `hierarchy.resolve_render_plan(mode=hierarchy_mode)`, builds each docx with python-docx (`create_word_document` → `(doc, doc_insertions)`), and writes to `storage.ensure_user_generated_dir(gen_file.user)` (`User/<name>/generated/`). Stems emit QUE only (no seq/info). Leaves get seq via `seq_owner_id`. A leaf without ANS/SOL uses the nearest ancestor once per stem run.
5. If any DOC markers were emitted, the docx is saved to a temp dir, a single `word_com.word_session` replaces each marker via `merge_doc_into_master`, and the result is moved to the final path. Pure IMG/MD jobs never open Word.
6. Frontend polls `/generate/status/<id>` (2 s) until `completed`/`failed`, then shows Download, **Get PDF** (only when filename ends `.docx`/`.zip`) and My Files.
7. PDF is produced **lazily on demand** by `GET /generate/pdf/<id>` from the success banner (`buildPdfFromBanner`) or the My Files row button (`buildAndDownloadPdf`).

### Output is always DOCX at create time

`create_document()` pins `output_format = 'DOCX'`; there is no Output Format control in `generate.html` (`onOutputFormatChange()` survives only as a no-op so old presets carrying `output_format` do not throw, and `restoreGenerationOptions()` ignores that key). The final artefact is `<name>_<timestamp>.docx` for a single doc or `<name>_<timestamp>.zip` (one `.docx` per group) for a split job. The `PDF` branches still present inside `_generate_in_background` / `_run_word_postprocess_*` are dead at runtime.

### Lazy PDF sibling

`_pdf_sibling_filename()` maps `x.docx → x.pdf`, `x.zip → x.pdf.zip`, anything else → `None` (UI hides the button; `pdf_supported=false`). The sibling lives beside the source in `generated_file_dir(gen_file)`. `_build_pdf_zip_from_docx_zip` extracts the zip, converts every `.docx` inside one Word session, copies non-docx entries through unchanged, and rezips with matching names. Deleting a My Files row removes both files (`_remove_file_and_pdf_sibling`). `_serialise_file_row` exposes `pdf_supported` / `pdf_available` (disk check) for the row button. Conversion is synchronous and holds the request open for the duration of the Word run.

### Output directory resolution

`generated_file_dir(gen_file)`: prefer `User/<owner>/generated` if the source exists there, else legacy `OUTPUT_PATH` if it exists there, else the per-user dir. Use this helper everywhere you touch a generated file on disk; never hard-code `OUTPUT_PATH`.

### Answer modes

| Mode | Behaviour |
|---|---|
| `QUE_ONLY` | Questions only |
| `QUE_ANS` | Each question immediately followed by its answer |
| `QUE_SOL` | Each question immediately followed by its solution |
| `QUE_THEN_ANS` | All questions, then new page + "ANSWERS" heading + all answers |
| `QUE_THEN_SOL` | All questions, then new page + "SOLUTIONS" heading + all solutions |

### Compact MC keys (`QUE_THEN_ANS` only)

`compact_mc_answers` replaces contiguous eligible MC answer runs with Answer Text blocks. Eligibility (`_compact_mc_answer_text`): `q_type == 'MC'` and non-empty, single-line, trimmed `Question.answer` of at most 80 chars (`_MC_KEY_MAX_ANSWER_LENGTH`). CQ and ineligible MC answers keep full ANS rendering and split the run; MC QID headings are suppressed while compact mode is active.

- `_partition_mc_answer_runs(questions, seq_start, seq_nos=)` preserves document order. When `seq_nos` is passed (generator), those are the render-plan leaf numbers; otherwise `seq_start + index`.
- `_split_mc_answer_run(entries, columns, max_rows)` applies the optional `columns * max_rows` block capacity.
- `_add_mc_answer_key_block` emits native `Table Grid` tables (`mc_key_layout='table'`) or tab-stop paragraphs (`'tabs'`).
- Per-answer numbers and `Qx–Qy` range titles use runtime sequential numbers and are gated by `show_seq_no` (`mc_key_include_seq`, `mc_key_range_title` are forced false without it). They never use the stored real-paper `Question.qno`.
- Compact blocks always use Answer Text regardless of `answer_preference` and bypass per-question MC spacing.

### Spacing config

```python
spacing_config = {
    'mc': {'before_mode': 'lines'|'page', 'before_lines': int, 'after_mode': 'lines'|'page', 'after_lines': int},
    'cq': { ... same ... }
}
```

Form defaults: MC `lines/0` before, `lines/1` after; CQ `page/0` before, `page/0` after. If the previous question already added a page break, a `before_mode='page'` is skipped to avoid a blank page. `apply_spacing_to_ans=False` (default) uses minimal spacing for ANS/SOL in THEN modes.

### Generation options (JSON in `GeneratedFile.generation_options`)

| Option | Type | Default / notes |
|---|---|---|
| `sort_mode` | `custom` / `selection` | `custom` = Auto Sort via `apply_multi_sort`; `selection` = raw selected order |
| `sort_config` | JSON string | `[{field, direction}]` (Auto Sort only) |
| `sort_group_order` | JSON string | `{fields:[...], order:[[id,...],...]}` (Auto Sort only); ignored unless `fields` match the grouping fields in `sort_config` |
| `answer_mode` | string | `QUE_ONLY` |
| `mc_before_mode`, `mc_before_lines`, `mc_after_mode`, `mc_after_lines`, `cq_*` | see spacing | stored flat |
| `compact_mc_answers` | bool | False; effective in `QUE_THEN_ANS` only |
| `mc_key_layout` | `table` / `tabs` | `table` |
| `mc_key_columns` | int 1–20 | 5 |
| `mc_key_max_rows` | int 1–100 or null | null = automatic |
| `mc_key_include_seq`, `mc_key_range_title` | bool | effective only with `show_seq_no` |
| `version_priority` | csv | `EN,CH,BI,ENO,CHO`; legacy `preferred_language` accepted on input and converted by `parse_version_priority()` |
| `answer_preference` | `image_first` / `text_first` | `image_first` |
| `format_priority` | csv | `IMG,MD,DOC`; parsed by `_parse_format_priority()` |
| `output_format` | string | always `DOCX` (see above) |
| `show_qid`, `show_qid_answer`, `show_correct_pct` (`QID [75%]`) | bool | |
| `show_seq_no` | bool | checked on a new form; regen/presets restore the saved value |
| `seq_start` | int ≥ 1 | 1 |
| `show_page_no` | bool | footer page numbers (`_add_page_numbers`) |
| `keep_together` | bool | `keep_with_next` on headings |
| `apply_spacing_to_ans` | bool | False |
| `denote_cross_topic` | bool | appends `[Cross Topic: X, Y]` to the info line |
| `hierarchy_mode` | `selected` / `whole` | default `selected`. `selected` = stem + chosen leaves; `whole` = expand every selected node's root. Selecting a stem always expands to all descendants. |
| `info_fields`, `section_fields`, `split_fields` | dict of `topic/subtopic/chapter/subchapter` bools | per-question info line / section heading on change / split into separate docx |
| `question_ids` | list[str] | stored for regeneration; stripped from presets |

### Asset selection in generation (`add_question_content_to_doc`)

Assets are sorted by `(format_rank, version_rank, part_number)` where `format_rank` comes from `format_priority` (default `IMG > MD > DOC`) and `version_rank = version_priority.index(asset.version)`. **Format outranks version** here and in the viewer (`get_viewer_asset`), unlike the dashboard preview resolver where version outranks format. All parts of the winning `(format, version)` group are included in `part_number` order. Missing file → italic `[File not found: ...]` placeholder.

For `ANS`: `text_first` uses `question.answer` when present else the asset; `image_first` uses the asset when present else the text.

### Rendering by format

- `IMG` → `doc.add_picture()`; width = min(6 in, `px / _DPI`) with `_DPI = 96`.
- `MD` → `_append_md_via_pandoc(doc, path)`: `_preprocess_md_for_pandoc` (collapse blank lines inside `$$` blocks, `normalize_inline_math`) → `pandoc --from=markdown+tex_math_dollars+tex_math_double_backslash-implicit_figures --to=docx` → `docxcompose.Composer(doc).append(fragment)`. Binary from `PANDOC_PATH` (default `pandoc`), with Windows install-path fallbacks in `_resolve_pandoc_binary`. Errors fall through to an italic placeholder; generation does not fail.
- `DOC` → if `doc_insertions is not None and word_com.IS_AVAILABLE`, emit a marker paragraph `__OQB_DOC_INSERT_<uuid>__` and record `doc_insertions[marker] = abs_path`; otherwise italic `[Word document: ...]`. The post-save Word step (`merge_doc_into_master`) finds each marker, deletes it and `Selection.InsertFile`s a section-property-stripped copy (`sanitize_docx_for_insertion`) so the master's A4 + margins + page numbers win. Format details: [doc-format.md](doc-format.md); decision: [ADR-003](../decisions/ADR-003-word-com-for-doc-merge-and-pdf.md).

### Word COM usage in this module

- `_run_word_postprocess_single(app, tmp_docx, doc_insertions, output_format, final_path)` and `_run_word_postprocess_split(app, merge_jobs, tmpdir, output_format)` open **one** `word_session(lock_timeout=WORD_COM_LOCK_TIMEOUT)` per job and do N merges inside it. Without Word (`IS_AVAILABLE=False`) they just move the intermediate docx (placeholders were already emitted).
- `_build_pdf_from_docx` / `_build_pdf_zip_from_docx_zip` open their own session and call `export_to_pdf` per file.
- `word_session` uses `DispatchEx` (fresh WINWORD.EXE), `Visible=False`, `DisplayAlerts=None`, CoInitialize per thread, and serialises on the process-wide `_WORD_COM_LOCK`; a second job blocks up to `WORD_COM_LOCK_TIMEOUT` (default 600 s) then fails with a clear `error_message`. Orphan WINWORD processes are killed via `_kill_word_processes_started_after` when `Quit` raises.

### Split to ZIP

If any `split_fields` are enabled, `_split_questions_into_groups()` groups the **already-sorted** list by first appearance of the section key, so manual block order flows into split order automatically. Labels come from `_build_split_label` (`Topic - Subtopic _ Chapter - Subchapter`, `Unknown` for nulls, `Uncategorized` when empty) and are sanitised by `_sanitize_filename`; duplicates get ` (2)`, ` (3)`.

### Custom Word styles (`_define_oqb_styles`)

`OQB Section Heading` (centred bold 14 pt), `OQB Question ID` (bold 12 pt), `OQB Question Info` (italic 10 pt grey), `OQB Body Text` (11 pt). All `quick_style=True`, zero space before/after; reused if already present.

### Generate-page UX (`generate.html`)

- Progressive disclosure: file name → content/answer mode → numbering/headings → contextual compact-MC panel → question order; Bootstrap collapses for Asset selection, Document structure, Spacing/pagination. Collapsed controls still submit.
- `syncAdvancedOptionSummaries({openCustomized})` flags non-default sections and opens them after regen/preset restore.
- `getCurrentGenerationOptions()` serialises every control (incl. `sort_config`, `sort_group_order`, compact-MC options, `hierarchy_mode`); `restoreGenerationOptions(opts)` is the single restore path for regen and presets.
- Compact-key numbering controls are hidden (not disabled/cleared) while `show_seq_no` is off. `updateAnswerQidVisibility()` must not overwrite the user's `show_qid_answer`; answer/solution spacing is shown only for THEN modes. `updateFileExtLabel()` shows `.zip` vs `.docx`.
- Reorder blocks (Auto Sort only, hidden in Manual Sort): `openReorderBlocksModal()` POSTs `question_ids` + `group_fields` to `/generate/api/sort-groups`, stores order in `#sortGroupOrderInput`, persisted in `generation_options` and presets.
- The selected-questions list reads `localStorage['oqb_selectedQuestions']` to stay in sync with the dashboard.
- Presets bar: load dropdown (starred first under a Starred optgroup, shared under "Shared by admins"), Save-as-preset modal → `POST /user/gen-profiles/save` (upsert by name; `question_ids` stripped client- and server-side), Manage link → `/user/gen-profiles`. Loading a preset never touches `question_ids` or `filter_data`. See [my-files.md](my-files.md).

### Viewer / Present mode

`viewer.html` is a self-contained page (own copies of `oqbTypesetMath` / `oqbRenderMarkdownInto`; no rerender-thumbnail button). Slides and the drawer list **leaves** from `resolve_render_plan`. `loadStemQue` fetches the root QUE into `#stemPanel` when `stem_has_que`. `loadAsset(type, qid)` and `loadAnswerAsset(qid)` fetch `/generate/api/viewer_asset/...?version_priority=` and render image parts, MD `html`, or the DOC `thumbnail_url` (polling until ready). Supports answer preference (ANS/SOL primary with fallback), zoom per panel, layouts, theatre/fullscreen, question drawer, keyboard shortcuts, and a Markup hand-off (`markupUrlFromAsset`).

## Settings & config keys

| Key | Read in | Purpose |
|---|---|---|
| `SOURCE_PATH` | `create_word_document`, `get_viewer_asset` | Asset root. |
| `OUTPUT_PATH` | `generated_file_dir` | Legacy output dir fallback for un-migrated rows. |
| `WORD_COM_LOCK_TIMEOUT` (default 600) | `_run_word_postprocess_*`, `_build_pdf_*` | Max wait for the global Word lock. |
| `PANDOC_PATH` (default `pandoc`) | `_resolve_pandoc_binary` | pandoc binary for MD → docx. |
| `WORD_COM_TIMEOUT` | `app/word_com.py` | Per-operation timeout. |

Storage root (`STORAGE_PATH`) drives `storage.user_generated_dir`. Runtime-tunable keys: [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

- `can_generate()` (role `user`/`admin` or super admin) is required for `/generate/`, `/create`, `/download/<id>`, `/api/sort-groups`. Viewers get 403.
- `/create` silently drops questions from subjects where the caller is a viewer; 403 if nothing remains.
- `/status/<id>` and `/download/<id>`: owner or super admin.
- `/pdf/<id>`: `_user_can_view_file` — owner, super admin, or any user with a `FileShare` on the file or its section. Note the asymmetry: `/download/<id>` is owner/super-admin only, so a share recipient clicking Download on a "Shared with me" row is redirected with an "Access denied" flash; they can still obtain the file through `/generate/pdf/<id>` or `POST /user/files/bulk-download` (both use `_user_can_view_file`). Treat this as a known inconsistency, not a design rule.
- `/viewer` and `/api/viewer_asset`: login only.
- Details: [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

- Generation runs in a daemon `threading.Thread` inside `with app.app_context()`; always pass `current_app._get_current_object()`, never the proxy. Status is communicated only through `GeneratedFile.status` / `error_message`; the thread commits `generating` first, then `completed` or `failed`.
- Word COM calls happen on that worker thread (CoInitialize inside `word_session`). Thumbnail renders and other jobs contend for the same `_WORD_COM_LOCK`.
- Lazy PDF conversion runs **synchronously in the request thread** and can hold the request for the length of a Word export; the frontend shows a spinner and reports JSON errors via toast.
- No SSE in this module; polling only (2 s on generate page, 5 s on My Files while a row is generating).

## Gotchas

1. **PDF is lazy, not a create-time option.** Do not resurrect an Output Format radio; add PDF behaviour to `download_pdf` / `_pdf_sibling_filename` instead. Presets with `output_format: 'PDF'` are ignored on load.
2. **Never call Word COM outside `word_session`** and never nest a session inside another (the lock is non-reentrant → deadlock). One session per job; N merges/exports inside it.
3. **`word_com.IS_AVAILABLE` is the single gate** for the DOC branch, `_run_word_postprocess_*` and the PDF route (503). Import `word_com` at module scope; entry points raise `WordComUnavailable` on non-Windows.
4. **Format beats version in generation and viewer; version beats format on the dashboard.** Keep the two resolvers' semantics distinct on purpose.
5. **DOC markers must be unique per insertion** (`uuid4().hex`) and `create_word_document` must keep returning `(doc, doc_insertions)`; `_generate_in_background` decides whether Word is needed from that dict.
6. **Section properties are stripped from source DOCX before insertion**; the master layout always wins. Do not swap `Selection.InsertFile` for docxcompose for DOC assets (MathType OLE fidelity).
7. **Split inherits sort order**: `_split_questions_into_groups` groups the sorted list, so `sort_group_order` applies to zip ordering with no extra wiring.
8. **`sort_group_order` is only honoured when its `fields` match the grouping fields in `sort_config`** and only in `sort_mode='custom'`.
9. **Compact MC keys use runtime sequence numbers, never `Question.qno`**, and always Answer Text regardless of `answer_preference`.
10. **`show_seq_no` gating**: `mc_key_include_seq` / `mc_key_range_title` are forced off server-side when `show_seq_no` is off; the UI hides (does not clear) those controls.
11. **Files live under `User/<name>/generated`**; resolve paths with `generated_file_dir()` because legacy rows may still be in `OUTPUT_PATH`.
12. **pandoc absence degrades, it does not fail**: MD slots become italic placeholders. Freshly installed pandoc may be invisible to a long-running process (PATH); set `PANDOC_PATH`.
13. **Regen (`?regen_file_id`) honours explicit POSTed `question_ids` over the saved ones** so "regenerate with my current selection" works; filter data always comes from the original file.
14. **`viewer.html` does not extend `base.html`** — duplicate any shared helper you need there.
15. **Stem/part expansion is only `hierarchy.resolve_render_plan`** (ADR-009). Dashboard selection stays ADR-008. Do not expand in the browser or by walking `children` in `create_word_document`.

## Related

- [question-hierarchy.md](question-hierarchy.md) — `resolve_render_plan`, seq owner, ANS/SOL fallback.
- [dashboard.md](dashboard.md) — source of `question_ids`, `sort_config`, `sort_group_order`, `filter_data`; preview resolver semantics.
- [my-files.md](my-files.md) — My Files rows, lazy PDF button, regen / re-filter links, Saved Generation Presets.
- [doc-format.md](doc-format.md) — DOC asset format, thumbnails, `word_com` internals, security model.
- [../decisions/ADR-001-server-rendered-htmx-no-spa.md](../decisions/ADR-001-server-rendered-htmx-no-spa.md)
- [../decisions/ADR-003-word-com-for-doc-merge-and-pdf.md](../decisions/ADR-003-word-com-for-doc-merge-and-pdf.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/06-system-settings.md](../core/06-system-settings.md)

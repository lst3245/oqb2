# Changelog

All notable changes to the Online Question Bank System are documented in this file.

## [Unreleased]

### ✨ New Features

#### AI Tools — LLM image proofreading + Markdown generation (Admin)

A new **AI Tools** bulk action on the Question Management page calls an OpenAI-compatible LLM (local or cloud) over the selected questions to:

- **Check images (proofread)** — compare a *typed* version's images (e.g. EN) against an *official* scan (e.g. ENO) and record discrepancies. The result is stored per asset (`check_state` = `ok` / `issues` / `error`, plus the issue list, model, reference version, and timestamp) and surfaced as a colour-coded badge on each slot in the edit-question modal (green OK / red N-issues with the list in a tooltip / amber parse error).
- **Generate Markdown** — transcribe a source version's images (typed or official) into a self-contained Markdown asset for a target version, with math as LaTeX (`$...$` / `$$...$$`). **Figures are embedded only when the question actually has a diagram**: text-only questions produce clean Markdown with no image, while a question with a real diagram/graph/geometry gets a base64 image at the figure's position — **cropped to just that region** when the model can localise it (a lightweight second "locate figures" pass), otherwise the whole source image as a fallback. Honours the existing `MD_MAX_SIZE_BYTES` cap and invalidates the MD render cache.
- **Per-slot "Generate with AI"** — the edit-question modal's **Markdown** area now has a one-click *Generate with AI* button per version/asset-type slot (and a *regenerate* button when MD already exists). It transcribes that version's own image(s) into Markdown via a chosen endpoint without leaving the modal (synchronous `POST /admin/questions/<id>/assets/ai/generate-md`).

- **Endpoints** (super-admin): a dedicated **Admin → LLM Endpoints** page (`/admin/llm-endpoints`, also linked from System Settings and the Admin navbar) manages named OpenAI-compatible endpoints — `base_url`, model, provider, vision toggle, token/temperature/timeout, and a **Test** connectivity ping. Works with cloud (OpenAI, OpenRouter) and local servers (Ollama, LM Studio, vLLM); a vision-capable model is required for image operations.
- **API keys (hybrid)**: a per-endpoint key entered in the UI is stored **encrypted at rest** (`cryptography.Fernet`, secret derived from `LLM_KEY_SECRET` or `SECRET_KEY`); leave it blank to fall back to the `.env` `LLM_API_KEY`. Plaintext keys are never returned to the browser.
- **Live console + real Stop**: both operations stream a Server-Sent-Events log (per-slot OK / skip / issue / error) with a progress bar. **Stop** is a genuine server-side cancel (checked between questions) so you stop burning tokens, not just a client disconnect. Subject-admins may run it on their own subjects; the whole feature is gated by the `AI_TOOLS_ENABLED` setting.
- **Tunables**: `AI_TOOLS_ENABLED` (bool) and `LLM_IMAGE_MAX_DIM` (image downscale long-edge, default 1600 px) added to System Settings.

#### "Versions" replace "Languages" — EN / CH / BI / ENO / CHO with drag-to-reorder priority

The per-asset **language** concept (EN / CH / BI) has been generalised into **Versions**, and two new official-scan versions were added: **ENO** (English Official) and **CHO** (Chinese Official) — screen-captured / low-quality scans of the published public-exam paper. The canonical list is now `EN, CH, BI, ENO, CHO` (defined once in `app/utils.VERSIONS`), which is also the default priority order (ENO/CHO last). Labels: `English`, `Chinese`, `Bilingual`, `English (Official)`, `Chinese (Official)`.

- **Single source of truth** (`app/utils.py`): new `VERSIONS`, `VERSION_LABELS`, `DEFAULT_VERSION_PRIORITY`, and `parse_version_priority(raw, legacy_preferred=None)` (dedupes, keeps only known codes, appends any missing in default order). Injected into every template as `OQB_VERSIONS` / `OQB_VERSION_LABELS` / `OQB_DEFAULT_VERSION_PRIORITY` via a context processor, and exposed to JS as `window.OQB_*` globals.
- **Drag-to-reorder priority widget** replaces the old two-option *Preferred / Preview Language* dropdowns on all three surfaces — the **dashboard** View menu, **present mode** (viewer), and the **Generate** page. Reorder versions (SortableJS drag or up/down buttons); top wins. Backed by a single comma-separated `version_priority` request param. Shared helper lives in `templates/partials/_version_priority_widget_js.html`.
- **Generalised asset selection**: assets are now picked by sorting on `(format_rank, version_rank, part_number)` where `version_rank = version_priority.index(asset.version)`. This replaces the old hardcoded *preferred → BI → other* rule in both document generation (`app/generator.py`) and the dashboard/viewer preview resolvers (`app/dashboard.py`).
- **Admin parity**: the edit-question modal renders one tab per version (EN/CH/BI/ENO/CHO), and Batch Delete, Batch IMG generation, Batch MCQ ANS, and the Add-question wizard all expose every version. Ingestion parses the new `_ENO_` / `_CHO_` filename tokens (e.g. `MATC_DSE_2024_P1_Q5_ENO_QUE.png`).
- **Backward compatible**: old `preferred_language` / `preview_language` / `?lang=` request params, `localStorage.oqb_previewLanguage`, and saved generation/filter profiles are still read and converted to the new ordered list (`[preferred, 'BI'] + remaining defaults`) — no profile rewrite or data loss.

### 🗄️ Database Changes

- **`question_assets.language` → `version`**, enum widened from `ENUM('EN','CH','BI')` to `ENUM('EN','CH','BI','ENO','CHO')`. Run `python migrate_versions.py` (idempotent `CHANGE COLUMN`, auto-carries the `uq_asset_identity` unique index to the renamed column). Existing deployments also auto-upgrade on startup via an idempotent INFORMATION_SCHEMA check in `app/__init__.py`, so the manual script is a safety net rather than a hard requirement.
- **AI Tools**: three new nullable columns on `question_assets` — `check_state` `VARCHAR(20)`, `check_result` `TEXT` (JSON), `checked_at` `DATETIME` — plus a new **`llm_configs`** table (named LLM endpoints, encrypted keys). Both are created/added automatically on app startup via the same idempotent INFORMATION_SCHEMA pattern — no manual migration needed.
- New `.env` keys: `LLM_API_KEY` (global fallback API key) and `LLM_KEY_SECRET` (optional Fernet secret for encrypting UI-entered endpoint keys; falls back to `SECRET_KEY`). New dependency: `requests`.

### ✨ Enhanced Features

- **Per-slot "Generate with AI" now works on the dashboard too.** The button (and the proofread controls below) appear in the edit-question modal on both the admin Question Management page and the dashboard. AI-Tools availability is now a single global (`window.OQB_AI_TOOLS_ENABLED`, set in `base.html` for any admin when `AI_TOOLS_ENABLED` is on) instead of being set only on the admin page.
- **Editable, more visible proofread status.** Each image slot in the edit-question modal now shows a prominent colour-coded status bar (green OK / red issues with the full issue list / amber error / grey "Not proofread") instead of only a small badge. Admins can change it inline: **mark correct**, **mark issue…** (prompts for a note + severity), or **clear** (back to unchecked) — backed by a new `POST /admin/questions/<id>/assets/check-state` endpoint (subject-admin scoped). Manual edits are recorded with `checked_by: "manual"` and the editor's username.

### 🐛 Bug Fixes

- **Edit-modal Markdown preview showed the image, not the MD.** The per-slot Markdown preview card in the edit-question modal resolved its content through the unified preview API, which ranks IMG above MD — so a slot with both an image and a Markdown asset previewed the image. The preview API now accepts an optional `?format=IMG|MD|DOC` override, the `.md-preview-card` loader forwards an optional `data-preview-format`, and the edit-modal MD card sets `data-preview-format="MD"` so it always renders its Markdown. (Dashboard cards are unchanged — they still follow IMG > MD > DOC priority.)
- **Inline `$ ... $` math with spaces inside the delimiters didn't render in the Markdown editor preview.** The editor's inline-math regex required a non-space character immediately inside the `$`, so LLM-generated `$ D $` / `$ \beta \le -10 $` stayed as literal text (while `$$ ... $$` worked). The regex now allows inner whitespace and captures the trimmed formula, matching the server-side `dollarmath` pipeline, while still ignoring currency-style `$5`.

#### Manual block reordering (Topic / Subtopic / Chapter / Subchapter)

When the sort includes any grouping field (Topic, Subtopic, Chapter, or Subchapter), a new **Reorder blocks** button appears in the dashboard **Sort By** panel and on the Generate page's **Auto Sort** panel. It opens a modal listing every distinct block (e.g. `Algebra › AAAA`, with a question-count badge) that the user can drag into a **free, flat custom order** — blocks may move anywhere, even across topics. Within each block, questions keep sorting by the remaining lower-priority fields (e.g. Level).

- **Backend engine** (`app/utils.py`): `apply_multi_sort(items, sort_config, group_order=None)` gains an optional `group_order` arg shaped `{"fields": ["topic","subtopic"], "order": [[12,45],[13,0],...]}`. Block keys are tuples of **integer IDs** (`0` = untagged) so renaming a topic/subtopic never invalidates a saved order. New helper `enumerate_sort_groups(questions, group_fields)` returns the blocks in default natural-name order; new `grouping_fields_in_config()` + `GROUPING_FIELDS` are exported.
- **Stale guard**: a stored order only applies when its `fields` exactly match the grouping fields currently in the sort. Otherwise it is ignored (and the UI clears it). Blocks present in the data but missing from a saved order fall back to natural-name order at the end; saved-but-absent blocks are skipped.
- **New endpoints**: `POST /dashboard/api/sort-groups` (current filter + `group_fields` → blocks) and `POST /generate/api/sort-groups` (selected `question_ids` + `group_fields` → blocks). The dashboard filter logic was extracted into a reusable `_build_filtered_query(params)` helper so both the filter route and the sort-groups API see the identical result set.
- **Persistence + restore**: the order rides along as `sort_group_order` inside the existing JSON blobs — `SavedFilter.filter_data` (search profiles), `SavedGenerationProfile.options_data` (generation presets), and `GeneratedFile.filter_data` / `generation_options`. It is restored on saved-profile load, My Files **Re-filter**, and **Regenerate**. No DB migration required.
- **UI/UX**: SortableJS-backed drag list (mouse + touch), an `Apply` that re-runs the search/updates the form, a `Reset to default`, and a `custom` badge + hint when an order is active. On the Generate page the control only shows in **Auto Sort** mode (Manual Sort uses raw selection order). Split-to-ZIP jobs automatically inherit the manual block order because `_split_questions_into_groups()` groups the already-sorted list by first appearance.

#### Lazy on-demand PDF (My Files)

Generation no longer asks the user to pick **DOCX vs PDF** up-front. Every generated file is now a `.docx` (or a `.zip` of `.docx` for split jobs), and a PDF version is only built when the user actually wants one — via a new red **PDF button** beside the green Download button on every row in *My Files*.

- **Form simplified**: the radio block previously labelled *Output Format* has been removed from `templates/generate.html`. The file-ext label next to the display-name input now only flips between `.docx` and `.zip` (depending on whether any split-by field is selected). Old saved generation presets that still carry an `output_format` key are silently ignored on load.
- **Backend pin**: `POST /generate/create` ignores any client-supplied `output_format` and always treats it as `'DOCX'`. The background worker `_generate_in_background` is therefore never asked to build a PDF at creation time; the Word COM lock is only acquired now when DOC source assets need merging, never just for PDF export.
- **New route `GET /generate/pdf/<file_id>`**: serves the PDF version of a completed file.
  - For a `.docx` source → returns/builds a `<stem>.pdf` sibling next to the docx.
  - For a `.zip` source (split job) → returns/builds a `<stem>.pdf.zip` sibling that contains the same group structure but with each inner `.docx` converted to `.pdf`.
  - Build path uses `word_com.word_session()` for COM serialisation and `word_com.export_to_pdf()` per file (MathType / fonts preserved). Generation is synchronous — the request blocks until Word finishes — and the result is cached on disk so subsequent clicks are instant downloads.
  - Errors return JSON with appropriate status codes (403, 409, 404, 503, 500) so the fetch-driven frontend can surface them via toast.
- **My Files row UI**:
  - When `pdf_available=true` (sibling already on disk) → solid red `bi-file-earmark-pdf` link that downloads instantly.
  - When `pdf_available=false` (source is `.docx`/`.zip`, sibling not yet built) → outlined red button that, on click, shows a spinner, fetches the endpoint, saves the streamed bytes via a blob URL + `<a download>`, then reloads the section so the button flips to the cached-link variant.
  - Hidden when the source file is already a PDF or otherwise non-convertible. Disabled when the row's status is still `pending`/`generating`.
  - Double-click guard (`data-busy="1"`) prevents repeat clicks during a build.
- **Serialised on `_serialise_file_row`**: two new booleans — `pdf_supported` (source is `.docx`/`.zip`) and `pdf_available` (sibling exists on disk).
- **Cleanup on delete**: single + bulk delete now also remove the cached PDF/PDF-zip sibling so disk doesn't fill up with orphan PDFs after row removal.
- **"Get PDF" button on the Generate-page success banner**: when generation completes, the green "Document <name> is ready!" banner now includes a red **Get PDF** button alongside the existing **Download** and **My Files** links. Clicking it runs the same `/generate/pdf/<id>` flow (synchronous build via Word COM, blob download) and then mutates itself into a solid-red **Download PDF** link so subsequent clicks fetch the cached file instantly — users no longer need to bounce to My Files to grab the PDF version of what they just generated. Hidden when the produced filename isn't `.docx`/`.zip` (no current path produces other formats, but the regex guards against future ones).

#### My Files — sections, drag-to-move, sharing, ZIP download

The My Files page is now organised around **user-owned sections** (folders) instead of a flat list. Each user has at minimum a default `Latest` section (auto-created on first visit, undeleteable, where every newly generated file lands) and may create any number of named sections beside it.

- **Drag-and-drop file move**: each file row has a grip handle (SortableJS, `group: 'oqb-files'`). Drop into another section's body → the row is `POST /user/files/<id>/move`d to that section. Same-section reorder fires `POST /user/files/reorder` and switches the section's sort mode to `manual`.
- **Section vertical reorder**: drag the grip on a section header (`group: 'oqb-sections'`) → `POST /user/sections/reorder`. The default `Latest` section is always pinned to the top regardless of where it lands in the input list.
- **Per-section sort**: dropdown picks one of `created_at`, `name`, `completed_at`, `question_count`, `manual` × `asc/desc`. Stored on `FileSection.sort_field/sort_direction`, applied server-side.
- **Per-section pagination**: each section has its own `page_size` (5 / 10 / 25 / 50 / 100) and page-state. Independent navigation per section — no global "page" coupling.
- **Collapse / expand**: state persists to the server via `FileSection.collapsed` so the layout sticks across browsers.
- **Inline rename** (double-click): both section names and file display names. Backed by `PATCH /user/sections/<id>` and `POST /user/files/<id>/rename`. Default section name is locked; shared rows are read-only.
- **Super-admin per-user sharing**: any super-admin can share a single file (kebab → Share with users…) or a whole section (section actions → Share section…) to any subset of users. The recipient sees a read-only row in a virtual **Shared with me** section, badged with the sharer's username. Owner keeps full control; recipient can download but not move/rename/delete. Sharing a section transitively shares every file in it (including files added later).
- **Multi-select bulk bar** (sticky-top): once any checkbox is ticked, a top bar exposes **Download ZIP** (`POST /user/files/bulk-download` streams a `application/zip`), **Move to section** dropdown (`POST /user/files/bulk-move`), **Share to users…** (admin), and **Delete**. Selection accumulates across sections; `Ctrl/Cmd+A` selects every visible row; `Delete` key triggers bulk delete.
- **Live search (server-side)**: the top-bar input forwards its trimmed value to `GET /user/files/list?q=…`, which applies a case-insensitive `ILIKE` filter on both `display_name` and `filename`. Search therefore spans **every page of every section**, not just rows already rendered. The post-filter `total` returned by each section refreshes its file-count badge, and the empty-state message becomes `No files match "<query>" in this section.` when a section has zero hits. A 250 ms debounce on the input avoids per-keystroke roundtrips (empty queries revert instantly). Matches in `display_name` are still highlighted client-side with `<mark>`.
- **File size badge**: every completed row shows `os.path.getsize` next to its extension chip.
- **Auto-refresh polling**: only re-fetches sections that contain a `.status-generating` row, every 5 s — much lighter than the previous full-page reload.
- **"Show all users" toggle (super-admin)**: when enabled, `GET /user/sections?show_all=1` returns every user's sections (own first, then by `user_id`), and `GET /user/files/list?show_all=1` includes files owned by any user inside the resolved section. Foreign sections are rendered with a blue `@username` owner pill in the header so the admin always knows whose folder they are viewing. The Move-to dropdown automatically includes every visible section as a target, letting admins reorganise other users' files in place. The virtual "Shared with me" entry is omitted in `show_all` mode (the admin already sees the source files directly).
- **No-text-select while dragging**: SortableJS `onStart` / `onEnd` hooks toggle a `body.oqb-dragging` class that applies `user-select: none !important` globally for the duration of any drag. Cursor switches to `grabbing`. Prevents the highlight-while-dragging annoyance reported during initial rollout.
- **Cleaner page header**: the My Files top bar is now split into two rows — title + (super-admin) "Show all users" toggle + Refresh on top; a dedicated toolbar card below containing the search input (left, flex-grows) and the **New section** button (right). Replaces the prior single-row layout where everything bunched together and wrapped awkwardly on narrow widths.

#### API

| Route | Method | Description |
|---|---|---|
| `/user/sections?show_all=` | GET | List user's own sections + the virtual "Shared with me" entry if any shares exist. Super-admin with `show_all=1` returns every user's sections (own first, then by user_id) with `owner_username` populated on foreign rows |
| `/user/sections` | POST | Create a new section |
| `/user/sections/<id>` | PATCH | Update name / sort / page_size / collapsed |
| `/user/sections/<id>` | DELETE | Delete; contained files auto-move to default |
| `/user/sections/reorder` | POST | `{ids:[...]}` |
| `/user/files/list?section_id=&page=&show_all=&q=` | GET | Paginated single-section listing; `section_id=-1` for shared. Optional `q` is a case-insensitive substring filter on `display_name` / `filename` and is applied before sort+paginate |
| `/user/files/<id>/move` | POST | `{section_id}` |
| `/user/files/bulk-move` | POST | `{ids, section_id}` |
| `/user/files/<id>/rename` | POST | `{display_name}` |
| `/user/files/reorder` | POST | `{section_id, ids:[]}` (sets `manual_position` + switches sort to `manual`) |
| `/user/files/bulk-download` | POST | `{ids:[]}` → streams ZIP (`my-files-<ts>.zip`) |
| `/user/shares?file_id=` or `?section_id=` | GET | Super admin: list current target users + available picker |
| `/user/shares` | POST | Super admin: `{file_id?\|section_id?, user_ids:[]}` upsert |
| `/user/shares/<id>` | DELETE | Super admin: revoke one row |
| `/user/shares/users` | GET | Super admin: full user list for picker |

`GET /user/files/list` (the legacy "flat list" form) was repurposed to require `section_id`; the dashboard / generator UI calls it through the new section-aware flow.

### 🗄️ Database Changes

- New table `file_sections` (`id`, `user_id` FK, `name`, `sort_order`, `sort_field`, `sort_direction`, `page_size`, `collapsed`, `is_default`, `created_at`, `updated_at`; unique `(user_id, name)`).
- New table `file_shares` (`id`, `file_id` FK nullable, `section_id` FK nullable, `shared_by_user_id` FK, `shared_with_user_id` FK, `created_at`; CHECK exactly-one-of-file-or-section; unique `(file_id, shared_with_user_id)` and `(section_id, shared_with_user_id)`; ON DELETE CASCADE from both parents).
- `generated_files` adds `section_id` (FK to `file_sections`, ON DELETE SET NULL, indexed) and `manual_position` (int, default 0). New files default to the owner's `Latest` section via `_get_or_create_default_section`.
- `create_app()` performs an idempotent upgrade — creates the two new tables with `checkfirst=True` and `ALTER TABLE`s the new columns onto `generated_files` if absent, so existing deployments don't need to re-run `init_db.py`.

### ✨ Enhanced Features

#### Admin panel UI cleanup

- **System Settings added to Admin navbar dropdown** — the link was previously only reachable via the Admin Panel index card; it is now listed in the Super Admin section of the navbar dropdown for faster access.
- **Admin Panel index reorganised** — cards are now grouped under three labelled sections: *Content Management* (Question Management, Topics, Chapters), *Operations* (Ingestion, Export/Import), and *Super Admin* (Database Health, Manage Users, File Browser, System Settings). The redundant "Tag Questions" card (which merely linked to the Dashboard) has been removed. Each section uses a uniform `row g-3` grid with `h-100` cards so items align and never stick together regardless of screen width.
- **"Back to Admin" button added to all sub-pages** — previously only Question Management and System Settings had a back button. The button is now present on Manage Topics, Manage Chapters, Manage Users, File Browser, Question Ingestion, Export/Import, and Database Health.

#### Export Question Tags — Dashboard selections only

The Question Tags export section on the Export / Import page now has a **"Dashboard selections only"** toggle switch (identical in concept to the toggle in Question Management). When enabled:
- Shows a live count badge of how many questions are currently selected in the Dashboard (`localStorage['oqb_selectedQuestions']`).
- The export downloads only those selected questions instead of the full subject, producing `question_tags_selected_N.csv`.
- The subject selector is greyed out (not needed) and a warning is shown if the selection is empty.

When the toggle is off, the page behaves exactly as before (subject-scoped full export).

Backend: `GET /admin/export/question-tags` now accepts an optional `question_ids` (csv of DB IDs) parameter in addition to `subject_id`. When `question_ids` is supplied the export skips the subject filter and scopes only to questions the caller has admin access to.

#### Batch Set MCQ ANS images (admin Question Management)

A new **Set MCQ ANS** batch button appears in the Question Management toolbar whenever questions are selected. It opens a modal that:
- Reads the `answer` tag (A / B / C / D) of each selected question.
- Copies the matching letter PNG from `resources/mcq_answer_img/` (A.png, B.png, C.png, D.png) into the canonical `ANS` slot on disk, creating any missing sub-directories automatically.
- Upserts the `QuestionAsset` DB record (creates if absent, updates `file_path` if existing).
- Streams live SSE progress — colour-coded log lines, a progress bar, and a final summary (Set / Skipped / Errors).

Options in the modal: language selection (EN / CH / BI, default EN) and an overwrite toggle (replace existing IMG ANS assets, on by default). Questions with `q_type ≠ MC` or a blank / non-A-D answer are silently skipped and counted in *Skipped*.

Backend: `GET /admin/questions/batch-mcq-ans` (SSE, subject-admin permission).

#### File Browser — copy, paste, and duplicate

- **Copy to clipboard** — each row now has a clipboard button that adds the file or folder to an in-page clipboard. The selection toolbar also exposes a "Copy" button to batch-add all selected items.
- **Paste here** — a "Paste here" toolbar button appears whenever the clipboard is non-empty. It copies every clipboard item into the current directory. Name conflicts are resolved automatically (`_copy`, `_copy2`, … suffixes).
- **Duplicate** — each row has a duplicate button (bi-copy icon) that immediately creates a same-directory copy with an auto-generated `_copy` suffix.
- **Backend** — new `POST /admin/files/copy` route (`files_copy`) handles both single-file `shutil.copy2` and recursive `shutil.copytree`. Prevents copying a folder into itself or one of its own subdirectories.

#### Unified question Edit modal (dashboard + admin)

- The dashboard's per-card **Edit Tags** button is renamed to **Edit** and now opens the same 3-tab modal the admin Question Management page uses — no more two flavours of "edit" for the same question. Tabs are reordered to **Tags → Assets → Details** (Tags is the default tab; previously the admin modal opened on Details).
- The shared modal is mounted via two new partials, included once per page:
  - [`templates/partials/edit_question_modal.html`](templates/partials/edit_question_modal.html) — markup for `#editQuestionModal` (Tags / Assets / Details tabs), sibling `#renameConfirmModal` and `#mdEditorModal`, `#toastContainer`, and edit-modal-scoped CSS (asset cards, drop zones, fullsize overlay, `.modal-xxl` breakpoints).
  - [`templates/partials/edit_question_modal_js.html`](templates/partials/edit_question_modal_js.html) — `openEditModal`, `saveQuestionTags`, `renameQuestion`/`executeRename`, asset upload / delete / reorder, the inline Markdown editor flow, the global Ctrl+V paste handler, plus an idempotent `showToast`. Self-includes [`partials/tag_editor_js.html`](templates/partials/tag_editor_js.html) so the host page must NOT include it separately.
- Refresh wiring is now contract-based: each host page assigns `window.oqbAfterQuestionEdit` (admin → `loadQuestions`, dashboard → `refreshCurrentPage`) and the modal calls it after a successful save / rename / asset change.
- The per-card button onclick is now just `openEditModal({{ question.id }})` — the giant inline JSON blob that used to embed every question field per card is gone. The modal self-fetches via `GET /admin/questions/<id>/details`.
- The admin Add-question 3-step wizard (Details → Assets → Tags) is intentionally unchanged: it still borrows `#editForm` out of the Tags tab into Step 3 because step 1 must create the question record before assets/tags can attach to it.

#### Question Edit modal — UX follow-ups

- **Click-outside / Esc now closes the modal.** Dropped the `data-bs-backdrop="static"` that the admin-only modal carried — there's no unsaved-state guard at the modal level (tag saves are explicit, asset mutations are instant, rename has its own confirm). The inline `#mdEditorModal` keeps `backdrop="static"` because it still has its own dirty-state prompt.
- **Ctrl+V paste target survives the post-upload rerender.** Previously, selecting ANS or SOL as the paste target → pasting → the upload completed → the asset list re-rendered → the visual indicator silently snapped back to QUE (the HTML default). The JS variable still pointed at SOL, but the user couldn't tell. `loadAssetsForEdit` and the add-modal branch of `refreshAssetsView` now re-apply the stored `activePasteTarget` (resp. `addActivePasteTarget`) immediately after each rerender so the highlight stays on the user's chosen section.
- **Compact image-preview mode (default ON).** New form-switch in the Assets tab — "Compact previews" — toggles between a responsive thumbnail grid (`grid-template-columns: repeat(auto-fill, minmax(140px, 1fr))`, 140 px image height with `object-fit: contain`) and the previous full-width vertically-stacked layout. The preference is persisted in `localStorage['oqb_assetThumbsCompact']` and applied via a `<body>.asset-thumbs-compact` class so the admin Add-question wizard's asset area inherits the same setting automatically. Implementation: `oqbInitThumbCompact()` in [`templates/partials/edit_question_modal_js.html`](templates/partials/edit_question_modal_js.html) and the CSS rules + form-switch markup in [`templates/partials/edit_question_modal.html`](templates/partials/edit_question_modal.html).

### ✨ New Features

#### Batch delete assets (admin Question Management)

- The **Delete Selected** button in Admin → Question Management now opens a single two-tab modal:
  - **Whole questions** (default tab) — original behaviour: drop the selected Question rows and cascade-delete their assets. The existing "Also delete asset files from disk" checkbox lives here.
  - **Specific assets** (NEW) — pick any combination of **Format** (IMG / MD / DOC) × **Language** (EN / CH / BI) × **Asset type** (QUE / ANS / SOL) and only matching asset rows are removed. The questions themselves are kept. All three axes start **deselected by default** so nothing is removed by accident; each axis must have at least one box ticked before the Delete button enables. A "Also delete the underlying files from disk" switch sits at the bottom (on by default, mirroring the single-asset delete in the Edit modal).
- Shared confirm gate: ≤ 10 selected → an "I understand" checkbox; **> 10 selected → must type `DELETE`** (the user-requested double-confirm threshold). Switching tabs invalidates the confirm so the user always re-agrees to the action they're actually performing. `BULK_DELETE_TYPED_THRESHOLD` is the named constant if you ever want to tune the cutoff.
- New backend route `POST /admin/questions/batch_delete_assets` in [`app/admin.py`](app/admin.py). Form params: `question_ids` (repeated), `formats`, `langs`, `atypes` (each repeated; rejects requests with any axis empty), `delete_files` (`true`/`false`, default `true`). Implementation is an AND of three `IN(...)` filters, intersected with the caller's admin-subjects scope. Each removed asset triggers the same DOC-thumbnail lifecycle hooks as the single-asset delete route (`doc_thumbnails.on_doc_asset_deleted` / `on_img_asset_deleted`) so cached PNGs stay consistent. MD render-cache is invalidated per asset via `md_render.invalidate`.
- Frontend lives in [`templates/admin_questions.html`](templates/admin_questions.html) — see `bulkDeleteSelected`, `_execBulkDeleteAssets`, and `_execBulkDeleteQuestions` for the dispatch.

#### Admin → System Settings (DB-backed, hot-reload)

- New super-admin page **Admin → System Settings** for tweaking runtime preferences without editing `.env` or restarting the server.
- New DB table `system_settings` (`key`, `value` JSON, `updated_at`, `updated_by`); rows store **DB overrides** of the `.env` / `Config` bootstrap default. When no row exists for a key, the bootstrap value applies.
- New module [`app/settings.py`](app/settings.py) hosts the **registry** of all tunables (type / default / label / group / validator) plus `load_all`, `get`, `set_value`, `reset`, and `as_dict` helpers. `load_all` is invoked from `create_app()` so DB values mirror into `app.config` at startup. Saves and resets hot-update `app.config` immediately — no restart needed.
- Initial registry covers Dashboard (`QUESTIONS_PER_PAGE`), Markdown (`MD_MAX_SIZE_BYTES`), Word COM (`WORD_COM_TIMEOUT`, `WORD_COM_LOCK_TIMEOUT`), Thumbnails (`DOC_THUMBNAIL_WIDTH`, `THUMBNAIL_TRANSPARENT`, `THUMBNAIL_WHITENESS_THRESHOLD`, `THUMBNAIL_BOTTOM_PADDING_PX`), and Batch IMG (`BATCH_IMG_DEFAULT_WIDTH`, `BATCH_IMG_DEFAULT_STITCH`).
- Secrets and paths (`SECRET_KEY`, DB credentials, `SOURCE_PATH`, `OUTPUT_PATH`, `PANDOC_PATH`, `DOC_THUMBNAIL_PATH`) intentionally remain `.env`-only.
- API routes: `GET /admin/settings` (page), `GET /admin/settings/data` (registry + current values), `POST /admin/settings/save` (validates per-key + upserts), `POST /admin/settings/reset/<key>` (drops the DB override, restores the bootstrap default).
- New rule file [`.cursor/rules/system-settings.mdc`](.cursor/rules/system-settings.mdc) documents the registry pattern, hot-reload semantics, and `.env` vs DB precedence.

#### Transparent PNG thumbnails — `THUMBNAIL_TRANSPARENT` setting

- New post-processing pass in `_save_cropped_png` (in [`app/word_com.py`](app/word_com.py)) — when enabled, the rendered DOC thumbnail's white page background becomes transparent. Antialiased text edges are preserved via a luminance-based alpha mask (`alpha = 255 − luminance`), so the result looks clean against any backdrop without dark halos.
- Driven from the new **System Settings → Thumbnails → Transparent background** toggle. Defaults to off so existing installs keep their current look.
- The whiteness threshold and bottom padding used by the cropping pass are also surfaced as settings (`THUMBNAIL_WHITENESS_THRESHOLD`, `THUMBNAIL_BOTTOM_PADDING_PX`) and read live from `current_app.config`.
- After flipping any of these settings, run **Database Health → DOC Asset Thumbnails → Force Re-render All** to apply them to the existing cache.

#### Crop whitespace on every side (not just the bottom)

- `_save_cropped_png` now expands its bbox crop by `THUMBNAIL_BOTTOM_PADDING_PX` of margin on **all four sides** (clamped to image bounds). Previously the crop was width-preserving and only trimmed the bottom; now a short one-line question yields a tight thumbnail (~292×65 px) instead of a full-width strip with leading whitespace. The batch IMG generation pipeline applies the same logic in `_pdf_to_cropped_images`.

### 🐛 Bug Fixes (this release)

- **Batch IMG generation crash**: `GET /admin/questions/batch-generate-images` raised `NameError: name 'word_com' is not defined` before any work was done. Fixed by importing `word_com` at module scope in [`app/admin.py`](app/admin.py) (was only locally imported inside the SSE generator closure).
- **Rerender button disappears after one click**: `oqbRerenderThumb` set `data-doc-pending-id` / `-filename` / `-downloadurl` on the placeholder but not `-question-id`. The poller read all four when building the swap-in HTML; without `questionId`, `_oqbBuildThumbHtml` couldn't construct the admin rerender button. Now `oqbRerenderThumb` writes `data-doc-pending-question-id` too.
- **Rerendered thumbnail shows the old PNG until "Delete All"**: browser was caching the bare thumbnail URL with the previous `max-age=3600` policy. Fixed at two layers:
  - **Server**: `/dashboard/api/doc_thumbnail/<id>.png` now responds with `Cache-Control: private, no-cache, must-revalidate` and `send_file(..., conditional=True)` so the browser revalidates every fetch against Flask's auto-emitted `ETag` / `Last-Modified`. Re-renders are reflected immediately; unchanged thumbnails return a tiny 304.
  - **Frontend**: `oqbPollDocThumbnails` now uses the SAME cache-busted URL for both the `Image()` probe and the swap-in `<img src>`, so the swap reuses the just-loaded probe cache entry and never accidentally serves a stale bare-URL entry. Existing browsers will need a single hard-refresh to evict pre-fix `max-age=3600` cache entries; after that, future rerenders are instant.

#### `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP` setting — preserve A4-relative position for asymmetric layouts

- New System Settings toggle: when ON, the left/right horizontal crops are both capped to `min(left_white_margin, right_white_margin)`. This preserves the content's proportional position on the original A4 page. Smoke test: a right-aligned single line that's `220 px` wide under tight cropping comes out `754 px` wide under symmetric, because the 534 px of left whitespace is preserved.
- Centred content (where left and right margins are equal) is unaffected — symmetric mode degrades gracefully to tight cropping.
- Wired through `app/word_com.render_first_page_png` and `app/batch_image_gen.render_doc_to_pages / render_md_to_pages`. New shared helper `_compute_crop_box` consolidates the crop math; both pipelines now share it.
- Per-batch override exposed in the **Generate IMG** modal under "Horizontal cropping" so admins can flip it on a single batch without changing the global default.
- After flipping this setting, run **Database Health → DOC Asset Thumbnails → Force Re-render All** to apply it to the existing cache.

#### Per-preview "Re-render thumbnail" button

- Every rendered DOC thumbnail in the dashboard cards, the preview modal, and the inline preview helper now shows a small `bi-arrow-clockwise` button next to the download link. One click POSTs to the new `/admin/questions/<qid>/assets/<aid>/rerender-thumb` endpoint (subject-admin permission), which deletes the cached PNG and schedules a fresh render. The frontend swaps the thumbnail for a "rendering..." placeholder and starts the standard live-poller so the new PNG appears in place without a refresh.
- The button is hidden for non-admin users (gated on a new `window.OQB_IS_ADMIN` flag emitted in `base.html`).

#### Thumbnail scheduler retry-backoff (fixes "stuck at rendering…" loop)

- `doc_thumbnails.ensure_thumbnail` previously cached the `asset_id` in a one-shot `_INFLIGHT` set with no clear path. A failed or hung render left the asset stuck — every subsequent dashboard hit returned `mode='download'` with no fresh render, so the JS poller looped forever returning 404. Now `_INFLIGHT` is cleared in a `finally` block after every attempt, and a per-asset 5-second cooldown prevents hammering Word on a broken file. Bonus: a new `force_rerender(asset_id)` helper backs the per-preview button by bypassing the cooldown for an explicit user-driven re-render.
- The frontend poll budget bumped from 20 attempts × 3 s ≈ 60 s to 60 × 3 s ≈ 3 minutes — generous enough that a Word lock contention won't time out the placeholder before the render lands.
- Cropped PNGs are now written to a `.tmp` sibling and `os.replace`d into place, so a concurrent reader never sees a half-written file.

#### Question Management → bulk Generate IMG from DOC/MD source

- New **Generate IMG** button in the Question Management bulk-action bar (next to **Delete Selected**), visible whenever ≥ 1 question is checked. Opens a configurable modal that drives a server-side SSE batch render.
- Per-batch options: asset types (QUE / ANS / SOL), languages (EN / CH / BI), source formats (DOC preferred → MD fallback), multi-page mode (stitch into one tall PNG vs. one PNG per page), overwrite existing IMG (default on), render width (px), and apply transparency.
- Backend pipeline lives in new module [`app/batch_image_gen.py`](app/batch_image_gen.py): `render_doc_to_pages` (DOC → Word COM → PDF → PyMuPDF + Pillow crop), `render_md_to_pages` (MD → pandoc → DOCX → same Word COM path), `stitch_vertically` (paste pages onto one canvas), and `replace_img_assets` (atomic delete-existing + write-new + insert-rows + DOC-thumbnail cleanup).
- SSE endpoint `GET /admin/questions/batch-generate-images` streams `info | skip | success | error | done` events with current/total counters; permission-filters to questions the caller can admin; single Word session per batch (Word lock serialises with other Word jobs as usual).
- After completion the modal auto-refreshes the question list so freshly-generated IMG previews show up. MathType OLE objects in the source DOCX render as native rasterised content in the output PNG (the Word→PDF→PyMuPDF path preserves the visual exactly).

### 🗄️ Database Changes

- New table `system_settings` (key PK / value Text / updated_at / updated_by FK). Auto-created by `create_app()` on startup if missing — no need to re-run `init_db.py`. The migration is additive; existing rows are untouched.

### 🔧 Technical Improvements

- Refactored MD-to-DOCX conversion in [`app/generator.py`](app/generator.py) into a shared `md_to_docx_via_pandoc(md_path, out_docx_path)` helper plus `_resolve_pandoc_binary()`. `_append_md_via_pandoc` (existing docx generation path) and `batch_image_gen.render_md_to_pages` (new batch IMG path) both reuse it.
- `render_first_page_png` now takes optional `transparent / whiteness_threshold / bottom_padding_px` kwargs and falls back to `current_app.config` values when called without explicit overrides — so DOC thumbnail rendering and batch IMG generation share one set of tunables.

#### DOCX source files — native merging via Microsoft Word + first-page thumbnails

`QuestionAsset.file_format='DOC'` is now first-class for generation. Previously a `.docx` source asset rendered as a placeholder line (`[Word document: ...]`) in the output; now it is **merged natively** so MathType OLE objects, embedded images, drawings, native tables, and fonts come through unchanged.

- **Merging engine**: new module [`app/word_com.py`](app/word_com.py) drives Microsoft Word via COM (`pywin32`). The generator emits a unique marker paragraph for each DOC asset while building the master document with python-docx, saves the intermediate `.docx`, then opens it in a single background Word session that finds each marker and calls `Selection.InsertFile` with a section-property-stripped copy of the source DOCX. This is what gives MathType / OLE / drawing fidelity that XML splicing libraries can't match.
- **Section-property stripping**: `sanitize_docx_for_insertion()` removes every `<w:sectPr>` from each source DOCX before insertion so the master document's A4 / margins / page numbers always win — source files with their own page setup or section breaks won't pollute the merged result.
- **Concurrency**: a module-level `threading.Lock` (`WORD_COM_LOCK_TIMEOUT`, default 600s) serializes all Word sessions across the whole process. Multiple users can queue jobs but only one Word instance runs at a time.
- **Orphan-process cleanup**: `word_session` registers an atexit-style fallback that kills any WINWORD.EXE if `Word.Quit` fails (via psutil if installed, falling back to `taskkill /f /im WINWORD.EXE`).
- **Graceful fallback**: on non-Windows servers or when pywin32 isn't installed, `word_com.IS_AVAILABLE` is False and DOC assets keep rendering as the legacy italic placeholder (generation still completes).

#### PDF output — directly generate `.pdf` instead of `.docx`

New **Output Format** radio on the Generate page lets the user pick `.docx` (default) or `.pdf`. PDF export reuses the same Word COM session that merges DOC source files, calling `Document.ExportAsFixedFormat(wdExportFormatPDF)` — equations, MathType OLE objects, native tables, and fonts all survive intact.

- **Split + PDF**: when any `split_fields` is enabled together with PDF output, the result is a `.zip` containing one PDF per group (mirrors the existing split-DOCX behaviour).
- **Storage**: `generation_options.output_format` (`'DOCX'` | `'PDF'`) round-trips through generation presets, regenerate-from-file, and the saved-generation-profile JSON.
- **Mimetype**: `/generate/download/<id>` now serves `application/pdf` for `.pdf` outputs.
- **Hard requirement** for PDF: Microsoft Word + `pywin32` on the server. The Create button rejects PDF requests with a clear error when Word COM isn't available.

#### DOC asset thumbnails — server-rendered first-page PNG

DOC assets used to render as a download-only stub on the dashboard, viewer, and admin preview panels. They now show a real first-page PNG preview, rendered once on the server using Word + PyMuPDF and cached on disk.

- **Lifecycle** (per user request):
  - **Create**: thumbnail is rendered only when no IMG exists in the same `(question, asset_type, language)` slot — the IMG resolver would win anyway and we'd be wasting a Word session.
  - **IMG arrives later**: any stale DOC thumbnail in the same slot is deleted (the IMG resolver takes over).
  - **DOC deleted**: cached PNG is dropped.
  - **IMG deleted from a slot that still has a DOC**: thumbnail is re-scheduled.
  - **Lazy on-demand rendering**: every preview path (dashboard card, modal, viewer) calls `doc_thumbnails.ensure_thumbnail(asset_id)`. If the PNG is on disk → preview uses it. If missing → a render is scheduled in the background and the resolver falls back to the download stub for that request; the PNG appears on the next refresh. De-duplicated per-process so a 20-card page does not queue 20 redundant renders. This means **existing DOC assets that predate the feature get thumbnails on first view**, no migration required.
- **Auto-crop trailing whitespace**: thumbnails now crop the empty space below the actual content (kept full width for uniform card columns). A one-line question that used to render as a full A4 page (1000 × 1414 px) now produces a tight 1000 × ~160 px thumbnail — roughly an 8× height reduction for short questions. Implemented via Pillow `ImageChops.subtract` + `getbbox()` with a configurable whiteness threshold and a small bottom-padding margin. Falls back to the un-cropped full-page image if cropping fails for any reason.
- **Backfill + clear (super-admin)**: new SSE endpoint `GET /admin/health/doc-thumbnails/backfill?force=0|1` plus a sibling `POST /admin/health/doc-thumbnails/clear` that wipes every cached PNG from disk. The "DOC Asset Thumbnails" card on the Database Health page exposes three buttons — **Backfill Missing**, **Force Re-render All**, and **Delete All** — covering all routine maintenance flows (priming a fresh library, applying a new `DOC_THUMBNAIL_WIDTH`, reclaiming disk space).
- **Live-fetch the rendered PNG**: new global JS helper `window.oqbPollDocThumbnails(root)` in `templates/base.html`. Any element carrying `data-doc-pending-id="<asset_id>"` is polled every 3 s (20 attempts ≈ 60 s) — when the cached PNG appears on disk the placeholder is swapped in-place for the real thumbnail without a page refresh. Wired into the dashboard card list (HTMX-aware), preview modal, and viewer (present mode). Per-process Set deduplication keeps a 20-card page from queuing 20 redundant pollers.

#### My Files — file extension badge + preferred-format icon
- Each row in **My Files** (`/user/files`) now shows the file extension as a colour-coded badge next to the display name (`.docx` blue, `.pdf` red, `.zip` yellow).
- A small icon to the right of the badge indicates the user's **preferred asset format** for that generation (first entry of `format_priority`): `bi-image` for IMG, `bi-markdown` for MD, `bi-file-earmark-word` for DOC. Hover shows the full tooltip.
- The API (`GET /user/files/list`) gained two response fields — `file_ext` and `format_priority_top` (also `output_format` for completeness).
- **Storage**: `<DOC_THUMBNAIL_PATH>/<asset_id>.png` (default: `<OUTPUT_PATH>/.doc_thumbnails`). Keyed by `asset_id` so file_path renames don't invalidate.
- **Rendering**: asynchronous via daemon thread — upload / save / ingest responses return immediately and the thumbnail appears shortly after.
- **Routes**: new `GET /dashboard/api/doc_thumbnail/<asset_id>.png` serves the cached PNG with subject-access gating. The unified preview resolver gained a new `mode='thumbnail'` shape; the viewer's `/generate/api/viewer_asset/...` endpoint now also includes `thumbnail_url` for DOC results.
- **Frontend**: dashboard / admin card preview, preview modal, viewer mode, and the `oqbLoadMarkdownPreviewCards` helper in `templates/base.html` all branch on the new shape and render `<img>` + a small "Open original .docx" link.

### ✨ Enhanced Features

#### Generate page — Output Format selector
- New `output_format` radio group below File Name (`Word (.docx)` / `PDF (.pdf)`).
- The file-extension badge next to the filename input updates live: `.docx`, `.pdf`, `.zip`, or `.zip (PDFs)` depending on split + format.
- Persisted alongside other generation options in saved presets and via `?regen_file_id=`.

### 🗄️ Database Changes

None — `DOC` was already a valid `file_format` enum value. The migration introduced for the MD feature continues to apply; no new schema changes are needed.

### 🔧 Technical Improvements

- New module [`app/word_com.py`](app/word_com.py): `word_session` context manager (Word COM + global lock + cleanup), `merge_doc_into_master`, `export_to_pdf`, `render_first_page_png`, `sanitize_docx_for_insertion`.
- New module [`app/doc_thumbnails.py`](app/doc_thumbnails.py): slot-aware lifecycle helpers (`on_doc_asset_created`, `on_img_asset_created`, `on_doc_asset_deleted`, `on_img_asset_deleted`) and an async `schedule_thumbnail` daemon thread.
- New config keys in [`app/config.py`](app/config.py): `WORD_COM_TIMEOUT` (default 300s), `WORD_COM_LOCK_TIMEOUT` (default 600s), `DOC_THUMBNAIL_PATH` (default `<OUTPUT_PATH>/.doc_thumbnails`), `DOC_THUMBNAIL_WIDTH` (default 1000 px).
- New dependencies in `requirements.txt`: `pywin32==306` (Windows-only marker `sys_platform == "win32"`), `PyMuPDF==1.24.10`. Microsoft Word required on the server for DOC merge / PDF export / DOC thumbnails.
- `app/ingestor.py` `upsert_asset()` now returns `(asset, was_created)` so the ingester can trigger DOC thumbnail rendering for newly-imported DOC files and drop stale thumbnails when a slot's IMG arrives via ingestion.
- New rule file [`.cursor/rules/docx-source-format.mdc`](.cursor/rules/docx-source-format.mdc) — mirror of `markdown-assets.mdc` for the DOC pipeline. The existing `document-generation.mdc` rule was updated with the new `output_format` option and the post-save Word COM step.

### ✨ New Features

#### Markdown source format — self-contained `.md` assets with live editor
- `QuestionAsset.file_format` now accepts a third value `MD` alongside `IMG` and `DOC`. Markdown files are **self-contained**: LaTeX math via `$...$` / `$$...$$` and images embedded as `data:image/...;base64,...` URIs — no sidecar files. Multi-part (`_2.md` etc.) is **not** supported; one `.md` = one whole asset.
- **Ingestion**: `.md` and `.markdown` files matching the standard filename pattern (`MATC_DSE_2024_P1_Q5_EN_QUE.md`) are picked up by both the CLI scanner and the SSE-streaming admin Ingestion UI. A `part_number > 1` MD file is logged and skipped.
- **Admin upload**: drag-and-drop or click-to-upload now accepts `.md`/`.markdown`. Uploads are size-capped by `MD_MAX_SIZE_BYTES` (default 5 MiB) and rejected if an MD asset already exists in the (QUE/ANS/SOL × EN/CH/BI) slot — use Edit instead.
- **In-browser live editor**: new EasyMDE + KaTeX + marked editor with a math toolbar (`$x$` / `$$x$$`), an "Insert image as base64" button, clipboard-image paste handler, and a live size meter that warns at 80 % of the cap. Two entry points per asset slot:
  - **Inline modal** in the Edit Question modal — opens over the asset list, saves into place.
  - **Fullscreen page** at `/admin/questions/<id>/assets/<asset_id>/md/edit` (or `/assets/md/new?language=&asset_type=` for create) — full viewport, Ctrl/Cmd+S to save, `beforeunload` guard.
  - Concurrent edits use optimistic concurrency on `mtime_ns`; a 409 prompts the user to discard or overwrite.
- **Dashboard preview**: when a question has no IMG QUE assets the card lazily fetches the new `GET /dashboard/api/question/<id>/preview/<asset_type>` resolver and renders the sanitized HTML inline (KaTeX typesets the math client-side). DOC-only questions now show an inline download stub instead of the broken `<img>` fallback.
- **Modal preview**: `previewAsset()` on dashboard, admin, and generator pages now uses the unified preview resolver, branching on `mode: image | html | download`.
- **Viewer mode**: `format === 'MD'` renders the sanitized HTML in the existing question/answer panels (zoom controls still apply).
- **Generation**: `add_question_content_to_doc()` gained an MD branch that converts the file via `pandoc --from=markdown+tex_math_dollars --to=docx` and splices the produced fragment into the master document with `docxcompose.Composer.append()`. Format priority is now **IMG > MD > DOC** in both preview and generation.
- New module `app/md_render.py` (markdown-it-py + plugins + bleach allowlist) renders MD to sanitized HTML and caches by `(asset_id, mtime_ns)`. The cache is invalidated on save and delete.

### 🗄️ Database Changes

#### `question_assets.file_format` enum widened to include `MD`
- Run `python migrate_md_format.py` once on existing deployments (idempotent — safe to re-run; checks the current `information_schema` column type before issuing the `ALTER`).
- The migration executes:
  ```
  ALTER TABLE question_assets MODIFY COLUMN file_format ENUM('IMG','DOC','MD') NOT NULL;
  ```
- Existing rows are unaffected — only the set of allowed values grows.

### ✨ Enhanced Features

#### Format Priority — per-generation reorderable format preference
- New **Format Priority** widget on the Generate page (below Preferred Language). Three pill chips (Image / Markdown / Word) with up/down arrows let the user reorder the format preference that applies when a question has multiple available formats for the same asset slot.
- Default order is `IMG > MD > DOC` (matches the dashboard preview resolver). Reorder to e.g. `MD > IMG > DOC` to prefer rendered Markdown over images for questions that have both.
- Stored as `format_priority` (comma-separated string) in `generation_options`, so it round-trips through Generation Presets, regenerate-from-file, and the SavedGenerationProfile load/save flow.
- Files: `app/generator.py` (`_parse_format_priority`, threaded through `add_question_content_to_doc`), `templates/generate.html` (chip widget JS + getCurrent/restore plumbing).

### 🐛 Bug Fixes (Markdown follow-ups, same release)

- **Security**: `md_render.sanitize()` now properly rejects `javascript:` / `vbscript:` / `data:` URLs in `<a href>` while still allowing `data:image/...;base64,...` in `<img src>`. The previous bleach configuration delegated attribute filtering to a callable but the callable wasn't checking URL schemes, leaving the door open to stored XSS via crafted Markdown.
- **TOCTOU**: `get_md_asset_content` now `os.stat`s the file BEFORE reading it so the returned `mtime_ns` cannot belong to a newer version than the returned content. The previous order could let an optimistic-mtime `save` silently overwrite a concurrent write.
- **Reorder API**: `/admin/questions/<id>/assets/reorder` now skips non-IMG asset IDs (MD and DOC are single-slot) — a buggy client can no longer renumber MD/DOC.
- **UTF-8**: both `get_md_asset_content` and `_append_md_via_pandoc` now catch `UnicodeDecodeError` on corrupted `.md` files and return a clear 4xx / runtime error instead of a 500.
- **Multi-language MD preview cards**: the lazy dashboard / admin preview-card loader (`oqbLoadMarkdownPreviewCards`) now honours `data-preview-lang`, so the CH / BI / EN slots render the right content.
- **HTMX-loaded editor partial**: the fullscreen MutationObserver now attaches synchronously when the DOM is already ready, so the editor's fullscreen mode works on pages that include the partial via an HTMX swap (not just initial render).
- **Cache cap**: `md_render._CACHE` is now an OrderedDict with a 512-entry LRU cap so a long-running worker can't grow the cache unboundedly.

### 🔧 Technical Improvements

- New config keys `PANDOC_PATH` (defaults to `pandoc` on `PATH`) and `MD_MAX_SIZE_BYTES` (default 5 MiB) in `app/config.py`.
- New dependencies in `requirements.txt`: `markdown-it-py`, `mdit-py-plugins`, `bleach`. **Pandoc binary** is required for document generation and must be installed separately (not a pip package).
- KaTeX (CSS + JS + auto-render) added to `templates/base.html`; shared `oqbTypesetMath()` / `oqbRenderMarkdownInto()` / `oqbLoadMarkdownPreviewCards()` JS helpers live there too. Auto-runs on `DOMContentLoaded` and `htmx:afterSwap` so HTMX swaps pick up new preview cards without bespoke wiring.
- Files: `app/models.py`, `app/config.py`, `app/ingestor.py`, `app/admin.py`, `app/dashboard.py`, `app/generator.py`, `app/md_render.py` (new), `templates/base.html`, `templates/partials/md_editor.html` (new), `templates/admin_md_editor.html` (new), `templates/partials/question_list.html`, `templates/dashboard.html`, `templates/viewer.html`, `templates/admin_questions.html`, `migrate_md_format.py` (new), `.env`.

### ✨ Enhanced Features

#### Show Selected Only — paginates the FULL selection (was previously broken)
- Fixed: when `Show Selected Only` was on, the dashboard only showed the selected questions that happened to be on the current page of the active filter. With a 156-question selection and a filter that returned 5 of them on page 1, the user saw 5 and had no way to view the other 151 — the feature was effectively useless across pages.
- The toggle now drives a server-side override: it writes the current selection into the new hidden `#idsInput` and re-submits the filter form. The backend's new `ids` parameter (mirrors the existing `qids` override) returns ONLY those questions, ignores all sidebar filters, and paginates them with the user's chosen `page_size`. The user can now browse their entire selection regardless of the sidebar filter.
- The banner now reads "Showing selected questions only — N selected total, page shows M. Sidebar filters are temporarily ignored." Pagination links work normally.
- Selection-changing actions while the toggle is on (Clear button, Set Operations Apply, Select All) automatically re-sync the visible page to the new selection.
- Files: `app/dashboard.py` (new `ids` filter param), `templates/dashboard.html` (`#idsInput`, `syncShowSelectedOnlyToServer()`, refactored `applyShowSelectedOnly`).

#### Set Operations modal — new "Filter Result" chip
- Added a fourth source chip (after Selection, Result, Saved Sets) labelled **Filter Result**. It resolves to every DB ID matching the current sidebar filter (across all pages, not just the visible one).
- Common use case: build `Selection ∩ Filter Result` and click `Replace Selection` to trim the selection down to whatever the current filter returns. Or `Selection ∪ Filter Result` to add the entire current filter's results to the selection.
- Files: `templates/dashboard.html` (chip button + `getCurrentFilterResultIds()` resolver).

#### Set Operations modal — Scratch Sets (browser-only named snapshots)
- Each live source chip (Selection, Filter Result, Result) now has a small **Duplicate** button (`bi-copy`) that snapshots the current contents into a named "scratch set". Scratch sets persist in the browser (`localStorage`), are **subject-scoped**, and never touch the server.
- A new "Scratch Sets (in browser)" section in the modal's sources panel lists them with **Rename** and **Delete** actions per row, plus a **Clear all** link that wipes the current subject's scratches (other subjects' scratches survive).
- Useful for building up several frozen "buckets" of questions across separate filter passes (e.g. "Topic A picks", "hard MC subset", "needs review") and then combining them via Union / Intersection / Difference, without committing to a server-side saved Question Set yet.
- Promoting a scratch into a permanent saved Question Set: append it as the only chip in the expression, click Evaluate, then "Save Result as set…".
- Deleting a scratch (or Clear all) also removes any matching chips from the current expression so the bar never holds dangling references.
- Files: `templates/dashboard.html` (`SCRATCH_SETS` storage key, helpers `loadScratchSets / saveScratchSets / duplicateAsScratch / renameScratch / deleteScratch / clearAllScratches / renderScratchSets`, new chip kind `src='scratch'`).

### 🐛 Bug Fixes

#### Selection vs Filter — independence policy and stale-tick fix
- **Stale checkbox ticks** after a Replace Selection: `initializeSelectionState()` previously only set `cb.checked = true` for selected IDs and never reset it for the deselected branch. Switching from a 5-question selection to a 2-question result left 3 ticks stranded on screen. Now both branches go through `cb.checked = isSelected`.
- **Auto-prune of selection on every filter change**: `pruneSelectionsToFilteredResults()` ran after every HTMX swap of `#questionList` and silently deleted selected IDs not in the new filter results. This violated the "selection survives across filter changes" mental model. Function and its caller removed; selection now strictly survives any filter / sort / paging change.
- **Subject change** clears selection automatically (selections are subject-tied and cannot be generated as a mix). A `suppressSelectionClearOnSubjectChange` flag bypasses this for the `?question_set_id=` flow when its target subject differs from the current one.
- **`?question_set_id=` apply** is now pure category (1) when the set's subject matches the active dashboard subject: only the selection changes; the sidebar filter is left untouched. (Different-subject apply still resets the filter to subject-only defaults since old topic IDs are stale.)
- New rule file `.cursor/rules/dashboard-selection-vs-filter.mdc` documents the full touchpoint matrix and category (1) / (2) / (3) semantics for future agents.
- Files: `templates/dashboard.html`, `.cursor/rules/dashboard-selection-vs-filter.mdc` (new), `.cursor/rules/dashboard-search.mdc`, `.cursor/rules/question-sets.mdc`.

### 🗄️ Database Changes

#### New `saved_question_sets` table
- Added by `db.create_all()` in `init_db.py` — additive only, safe to run on existing databases.
- Columns: `id`, `user_id` (FK), `name`, `subject` (FK to subjects.id), `question_ids` (JSON list of int IDs), `is_starred`, `is_shared`, `created_at`, `updated_at`.
- Backs the new Question Sets feature (see below).

### ✨ New Features

#### Question Sets — saved per-subject question collections + set-algebra builder
- New "**Set**" button on the dashboard (between Manage and Generate) opens a **Set Operations** modal that lets you combine the live **Selection** with one or more **saved sets** using **Union (∪)**, **Intersection (∩)**, and **Difference (\\)** in a freeform formula chip expression bar. The result can replace the current selection, be appended to it, or be saved as a new named set.
- Sets are **subject-scoped** — each saved set stores a snapshot of question DB IDs for one subject and is only listed when that subject is active in the dashboard. Subject permissions still apply when applying or loading a set.
- New page **My Stuff → Question Sets** lists, stars, renames, deletes, bulk-deletes, and (super-admin only) shares saved sets with all users that have access to that subject. Admin-shared sets show a green **Shared** badge.
- Apply directly via URL: `?question_set_id=<id>` on the dashboard restores the subject, replaces the selection with the set's questions, and auto-enables **Show Selected Only**.
- Save flow upserts by `(user, subject, name)` — saving with the same name in the same subject overwrites the contents.
- Backend: new `SavedQuestionSet` model and `/user/sets/*` JSON API mirroring the existing `SavedFilter` pattern (list, save, data, delete, bulk-delete, star, share, rename).
- Files: `app/models.py`, `app/user.py`, `init_db.py`, `templates/saved_question_sets.html` (new), `templates/dashboard.html`, `templates/base.html`.

#### DB Health anomaly view — jump to questions in Dashboard / Question Management
- The anomaly detail modal (Admin → Database Health → Anomaly Detection → **View**) now has two extra buttons in the footer:
  - **View in Dashboard** — opens the dashboard in a new tab, filtered to exactly the QIDs from that anomaly
  - **View in Question Management** — same, but lands on `/admin/questions`
- Backend: both `/dashboard/filter` and `/admin/questions/api/list` now accept a `qids` parameter (comma-separated list of QIDs). When set, it overrides all other filters and returns only the matching questions. Order is preserved on the admin page.
- The destination page shows a dismissible **"Filtering by N specific QIDs"** banner; clicking *Clear list filter* returns to normal browsing. Any sidebar filter change on the dashboard automatically clears the list filter too.
- For very large QID lists (above ~6,000 URL chars) the modal falls back to a one-shot `localStorage` token (`?qids_token=…`) so the request never hits browser URL limits.
- Files: `app/dashboard.py`, `app/admin.py`, `templates/admin_health.html`, `templates/dashboard.html`, `templates/admin_questions.html`.

### ✨ Enhanced Features

#### Generate page — quick preview button on each selected question
- Each row in the **Selected Questions** panel now has a small eye (👁) button.
- Clicking it opens a modal showing the question image without leaving the page.
- The image is fetched via the existing `/generate/api/viewer_asset/<id>/QUE?lang=` endpoint, using whichever **Preferred Language** is currently selected in the form (EN or CH), with the same `BI` → other fallback logic used during document generation.
- No new backend route needed; button tap is isolated from SortableJS drag via `touch-action: manipulation`.
- File: `templates/generate.html`.

#### Generator Sort Order — clearer labels and auto-switch on manual reorder
- Renamed the two Sort Order modes on the Generate page for clarity:
  - **"Custom Sort" → "Auto Sort"** (sorts automatically by tags like Year, Level, Topic, etc.)
  - **"Selection Order" → "Manual Sort"** (uses the order shown in the Selected Questions panel)
- Updated the toggle icons (`bi-magic`, `bi-hand-index`) and added explanatory tooltips to each button.
- Dragging a row in the **Selected Questions** panel now automatically switches the Sort Order to **Manual Sort** — previously the reorder would be silently discarded if Auto Sort was active.
- The "Drag to reorder" hint on the Selected Questions panel is now always visible (with a sub-hint that dragging switches modes), and the info banner under Manual Sort got tightened wording.
- Underlying form values (`custom` / `selection`) are unchanged, so existing saved generation presets continue to work.
- File: `templates/generate.html`.

### 🐛 Bug Fixes

#### Drag-to-reorder now works on mobile/touch and gives clear visual feedback
- The dashboard's **Sort By** list, and the generator's **Sort By** list + **Selected Questions** list, previously used the HTML5 native drag-and-drop API. That API does not fire on mobile/touch devices at all, and on desktop gave only a faint opacity change with no insertion indicator.
- Replaced with [SortableJS](https://github.com/SortableJS/Sortable) (loaded from CDN in `templates/base.html`), configured with `forceFallback: true` so mouse and touch use the same code path.
- New visual cues: a blue dashed ghost shows where the item will land, the dragged row gets a subtle shadow and tilt, and the touched item gets a blue focus ring.
- Dragging is limited to the grip handle (`.drag-handle`) so vertical scrolling still works elsewhere on the row.
- Touch handles are enlarged automatically on small screens / coarse pointers, and a 100 ms hold delay (`delayOnTouchOnly`) prevents drags from being triggered by an accidental scroll-start.
- Files: `templates/base.html`, `templates/dashboard.html`, `templates/generate.html`.

#### Regenerate — search profile always copied from original file
- Previously, if the user had visited the generator from the dashboard before clicking Regenerate, the stale session `generator_filter_data` would silently override the original file's saved search profile, causing the new file to record the wrong filter.
- Fixed: the original file's `filter_data` now always wins when regenerating, regardless of session state (`app/generator.py`).

### ✨ Enhanced Features

#### "Reuse Filter" also restores the saved question selection
- Clicking the funnel (🔽) button on the My Files page now pre-selects the question IDs that were used to generate that file, in addition to restoring the dashboard filter.
- The question IDs are fetched from the file's saved generation options and added to the active selection before the filter re-run. Any IDs that no longer appear in the restored filter results are pruned (e.g. questions deleted since the file was generated).

### ✨ New Features

#### Profile Sharing (Super Admin)
- Super admins can mark any Search Profile or Generation Preset as "Shared" — visible to every user
- New share toggle (green share-icon) on `/user/profiles` and `/user/gen-profiles` pages (super admin only)
- Other users see shared profiles/presets in their dropdowns under a "Shared by admins" optgroup, and in their list pages with a green **Shared** badge and the owner's username
- Non-owners cannot delete, star, or bulk-select shared profiles they don't own
- New `is_shared` column on `saved_filters` and `saved_generation_profiles`
- New `POST /user/profiles/<id>/share` and `POST /user/gen-profiles/<id>/share` endpoints (super-admin only)
- `/data` endpoints for both now accept any logged-in user when the profile is shared

#### Dashboard "Load Profile" Dropdown
- Added a "Load profile…" dropdown at the top of the Dashboard filters card, matching the Generate page pattern
- Three optgroups: ★ Starred, My profiles, Shared by admins
- Selecting a profile restores the filters and re-runs the search via HTMX (no page reload, no URL juggling)
- Newly-saved profiles appear in the dropdown immediately after the save modal closes

#### Saved Generation Presets
- New `/user/gen-profiles` page lists reusable generation-option presets per user
- "Save as preset" button + "Load preset…" dropdown on the Generate page ([templates/generate.html](templates/generate.html)) — pick a preset to instantly restore every option (answer mode, spacing, info/section/split fields, language, sort, etc.) without changing the current question selection
- Presets store options only — they are independent of the current question selection or filter
- Saving with an existing name overwrites that preset (upsert by name)
- Manage / bulk-delete / star presets at `/user/gen-profiles`

#### Starring (Favorites) for Saved Profiles
- Both Search Profiles and Generation Presets can be starred
- Starred items always sort to the top of list pages and the Generate-page dropdown (with an `★ Starred` optgroup)
- Click the star icon in either list to toggle

### 🗄️ Database Changes
- New table `saved_generation_profiles` (id, user_id, name, options_data JSON, is_starred, is_shared, created_at, updated_at)
- New columns on `saved_filters`: `is_starred` and `is_shared` (BOOLEAN NOT NULL DEFAULT FALSE), each with its own index
- Migration: run `python migrate_starring.py` once on existing deployments (idempotent — safe to re-run)

### 🔌 API Changes
- New `POST /user/profiles/<id>/star` — toggle star on a filter profile (owner / super admin)
- New `POST /user/profiles/<id>/share` — toggle share on a filter profile (super admin only)
- New `GET /user/gen-profiles` — page
- New `GET /user/gen-profiles/list` — JSON list (starred first, then by name; includes own + shared)
- New `POST /user/gen-profiles/save` — upsert preset by name
- New `GET /user/gen-profiles/<id>/data` — fetch preset for restore (now accepts shared)
- New `DELETE /user/gen-profiles/<id>` — delete one (owner / super admin)
- New `POST /user/gen-profiles/bulk-delete` — delete many
- New `POST /user/gen-profiles/<id>/star` — toggle star on a preset
- New `POST /user/gen-profiles/<id>/share` — toggle share on a preset (super admin only)
- Updated `GET /user/profiles/list` ordering: `is_starred DESC, name ASC`; now also returns shared profiles owned by other users plus `is_own` / `is_shared` flags
- Updated `GET /user/profiles/<id>/data`: any logged-in user can fetch a shared profile

## [2.3.0] - 2026-05-23

### ✨ New Features

#### Subject-Based Permission System (Replaces Legacy Role)
- Replaced the simple `is_admin` boolean with a full subject-level RBAC system
- New `UserSubjectPermission` model with roles: `viewer`, `user`, `admin`
- `is_super_admin` flag for god-mode access (all subjects, all operations)
- `viewer` role: read-only access, cannot generate documents
- `user` role: can browse + generate documents for that subject
- `admin` role: full subject admin (tagging, ingestion, export)
- Permission decorators: `@admin_required`, `@super_admin_required`, `@subject_admin_required`

#### Background Document Generation
- Generation now runs in a **background thread** instead of blocking the HTTP request
- New `GeneratedFile` DB model tracks generation status (`pending` → `generating` → `completed` / `failed`)
- Frontend polls `GET /generate/status/<id>` for live status updates
- Stale `pending`/`generating` records auto-reset to `failed` on app restart

#### My Files & Saved Filter Profiles (user_bp)
- New `/user/files` page: list, download, re-generate, and delete previously generated documents
- New `/user/profiles` page: save and restore named dashboard filter configurations
- Bulk delete for both files and profiles
- Super admin can view all users' files/profiles with `?show_all=1`

#### Viewer / Presentation Mode
- New `GET /generate/viewer` page for slide-style question review (no Word generation)
- Supports all question types with ANS/SOL toggle, language preference
- ANS ↔ SOL automatic fallback if one type is missing
- Asset API: `GET /generate/api/viewer_asset/<question_id>/<type>?lang=EN`

#### Answer & Comment Text Fields on Questions
- `answer` (TEXT): text-based answer, alternative to ANS image asset
- `comment` (TEXT): notes/commentary, displayed in dashboard card and viewer
- In generation: `answer_preference` controls whether text or image is used first for ANS

#### Chapter / Subchapter System
- New `Chapter` and `Subchapter` models parallel to Topic/Subtopic
- Questions can be linked to a `chapter_id` / `subchapter_id` (textbook organisation)
- Chapters visible on dashboard filter and in generated documents
- Admin CRUD at `/admin/chapters` (same reorder/hidden behaviour as topics)

#### Admin File Browser
- New `/admin/files` page (super admin only) for managing files directly within `SOURCE_PATH`
- Upload, download, rename, delete files; create directories
- Useful for correcting filenames without SSH/file system access

#### Database Health Dashboard
- New `/admin/health` page (super admin only) with fast DB-only statistics
- Reports: total counts, untagged questions, questions with no assets, duplicates, file-path mismatches
- `GET /admin/health/untracked` — files on disk that have no DB record
- `GET /admin/health/sync?dry_run=1` — SSE stream to find and optionally delete orphaned DB records

#### Export / Import (CSV Round-trip)
- `/admin/export-import` page with full CSV export and import for:
  - **Question Tags**: `qid, major_topic, major_subtopic, level, q_type, section, minor_topics, subtopics, chapter, subchapter`
  - **Topics/Subtopics**: per-subject topic tree
  - **Chapters/Subchapters**: per-subject chapter tree
- Imports are idempotent (safe to re-run)

### ✨ Enhanced Generation Options
- **Split to ZIP**: enable per-topic/chapter splits — one `.docx` per group, zipped together
- **Sequential numbering**: show `1. 2. 3.` prefixes with configurable start number
- **Page numbers**: footer page numbers on generated documents
- **Keep together**: Word `keep_with_next` on headings/info lines
- **Apply spacing to answers**: in `QUE_THEN_ANS` / `QUE_THEN_SOL` modes, apply full MC/CQ spacing to the answer section (default: minimal 1-line spacing)
- **Denote cross-topic**: adds `[Cross Topic: X, Y]` to info line if question has minor topics

### 🗄️ Database Changes
- New table: `user_subject_permissions (id, user_id, subject_id, role)`
- New table: `saved_filters (id, user_id, name, filter_data)`
- New table: `generated_files (id, user_id, display_name, filename, status, ...)`
- New table: `chapters (id, subject_id, name, sort_order)`
- New table: `subchapters (id, chapter_id, name, hidden, sort_order)`
- New columns on `questions`: `answer TEXT`, `comment TEXT`, `chapter_id INT`, `subchapter_id INT`
- New columns on `users`: `is_super_admin BOOL`

---

## [2.1.0] - 2026-01-09

### ✨ New Feature: Correct Percentage Tracking

Added a new `correct_percentage` field to track public exam performance statistics for questions.

#### Features
- **Database Field**: New `correct_percentage` column (integer 0-100, nullable) in questions table
- **Dashboard Display**: Shows correct percentage in question cards when available (e.g., "75% correct")
- **Sorting Support**: Sort by correct percentage in both dashboard and document generation
  - NULL values always sort last regardless of sort direction
- **Edit Question**: New input field in edit question modal (0-100 validation)
- **Batch Edit**: New toggle to batch update correct percentage for multiple questions
- **Document Generation**: 
  - New checkbox option to show correct percentage in generated documents
  - Format: `MATC_DSE_2024_P1_Q5 [75%]` (shown on same line as question ID)

#### Database Migration Required

Run the following SQL command to add the new column:

```sql
ALTER TABLE questions ADD COLUMN correct_percentage INT NULL;
```

#### API Changes
- `POST /admin/questions/<id>/update`: Now accepts `correct_percentage` parameter
- `POST /admin/questions/batch-update`: Now accepts `update_correct_pct` and `correct_percentage` parameters
- `POST /generate/create`: Now accepts `show_correct_pct` parameter

---

## [2.0.0] - 2026-01-09

### 🎉 Major Features Added

#### Multi-level Sorting
- **Dashboard Sorting**: Sort questions by multiple fields with custom priority
  - Example: Topic → Level → Year
  - Available fields: QID, Year, Level, Topic, Subtopic, Source, Section, Type, Created Time
  - Natural sorting for text fields (Q1, Q2, Q10 instead of Q1, Q10, Q2)
  - Sort configuration persists in session

#### Batch Operations
- **Batch Update**: Update metadata for multiple questions simultaneously
  - Selectively update: level, question type, section, or topic/subtopic relationships
  - Checkbox system to choose which fields to update
- **Batch Delete**: Permanently remove multiple questions at once
  - Confirmation dialog to prevent accidental deletion
  - Cascade deletion of associated assets
  - Returns count and list of deleted QIDs

#### Database Sync
- **Sync Command**: Remove orphaned database records when source files are deleted
  - Dry-run mode to preview what would be deleted
  - Detects orphaned assets (files that no longer exist)
  - Detects orphaned questions (questions with no assets)
  - CLI: `python cli.py sync` (preview) or `python cli.py sync --no-dry-run` (delete)

#### Smart Document Spacing
- **Separate MC and CQ Settings**: Different spacing for multiple choice vs conventional questions
  - Before question: Skip N lines OR start from new page
  - After question: Skip N lines OR start from new page
  - Intelligent page break handling (avoids duplicate breaks)
- **Flexible Sort Modes**: 
  - Preserve selection order
  - Custom multi-level sort

### ✨ Enhanced Features

#### Dashboard Enhancements
- **Direct QID Search**: Search by question ID with wildcard support
  - Example: `MATC_DSE_2024*` finds all 2024 DSE MATC questions
  - Supports SQL wildcards (`%` or `*`)
- **Topic Mode**: AND/OR logic for multi-topic filtering
  - AND mode: Questions must have ALL selected topics
  - OR mode: Questions can have ANY selected topics (default)
- **Preview Language Preference**: Prioritize English or Chinese in preview
  - Order: Preferred language → Bilingual → Other language
- **Configurable Page Size**: Choose 10, 20, 50, or 100 questions per page
- **Dynamic Year Loading**: Years are loaded based on selected subject and source
- **Level Filter**: Added "Not Assigned" option to filter untagged questions
- **Session Persistence**: Filter and sort settings persist across page loads

#### Question Model Enhancements
- **Major Subtopic**: Assign a primary subtopic to each question
  - Separate from many-to-many subtopics
  - Must belong to the major topic (validation enforced)
- **Description Field**: Optional text description for questions
- **Enhanced Validation**: Ensures subtopic belongs to major topic when set

#### Document Generation Enhancements
- **Additional Answer Mode**: "All Questions, Then All Solutions"
- **Language Preference**: Prefer English or Chinese assets in generated documents
  - Automatic fallback to Bilingual if preferred not available
  - Format preference: IMG before DOC
- **Optional QID Display**: Separate toggles for showing QIDs on questions vs answers
- **Graceful Error Handling**: Missing assets show italic placeholder instead of crashing

#### Ingestor Enhancements
- **Automatic Question Type Detection**: 
  - MATC DSE P1 → CQ
  - MATC DSE P2 → MC
  - MAT1/MAT2 DSE → CQ
  - Others → NULL (to be tagged manually)
- **Natural Sorting**: Files are processed in natural order (Q1, Q2, Q10)

### 🐛 Bug Fixes
- Fixed language preference ordering in preview and generation
- Fixed page break logic to avoid duplicate breaks
- Improved asset selection with format preference (IMG over DOC)
- Enhanced error handling for missing files
- Fixed subtopic loading when topics are deselected

### 🔧 Technical Improvements
- Refactored sorting logic into reusable `apply_multi_sort()` function
- Added `SORT_FIELDS` configuration for extensible sort options
- Improved database query optimization for large datasets
- Enhanced session management for filter persistence
- Better separation of concerns in document generation
- Added comprehensive inline documentation

### 📚 Documentation Updates
- Updated README.md with all v2.0 features
- Updated SETUP.md with new CLI commands and features
- Updated PROJECT_SUMMARY.md with technical details
- Added this CHANGELOG.md to track version history
- Updated API endpoint documentation

### 🗄️ Database Changes
- Added `major_subtopic_id` column to questions table
- Added `description` column (TEXT) to questions table
- No migration needed for existing data (new columns allow NULL)

### 🔐 Security
- No changes to authentication or authorization
- Batch delete requires admin privileges
- Sync command requires file system access

---

## [1.0.0] - 2025-12-XX

### Initial Release

#### Core Features
- **Flask Application**: Complete web application structure
- **Database Layer**: SQLAlchemy ORM with MariaDB
  - 7 models: User, Subject, Topic, Subtopic, Question, QuestionAsset
  - 2 association tables: question_minor_topics, question_subtopics
- **Authentication**: Flask-Login with role-based access (Admin/Regular)
- **File Ingestor**: 
  - Recursive directory scanning
  - Regex-based filename parsing
  - Support for PP and QB formats
  - Error logging
- **Dashboard**:
  - Advanced filtering (subject, source, year, topic, level, type)
  - Cross-topic search
  - Question preview
  - Answer/solution preview modals
  - Pagination (20 per page)
- **Document Generator**:
  - Word document creation (python-docx)
  - A4 page size
  - Multiple answer modes
  - Configurable spacing
  - Natural sorting
- **Admin Panel**:
  - Topic/subtopic management (CRUD)
  - Question tagging
  - User registration
- **Multi-language Support**: EN, CH, BI
- **UI**: Bootstrap 5 + HTMX for dynamic interactions

#### Initial Documentation
- README.md - Project overview
- SETUP.md - Installation guide
- TESTING.md - Testing procedures
- PROJECT_SUMMARY.md - Technical summary

---

## Version Comparison

| Feature | v1.0.0 | v2.0.0 |
|---------|--------|--------|
| Multi-level Sorting | ❌ | ✅ |
| Batch Operations | ❌ | ✅ |
| Database Sync | ❌ | ✅ |
| Smart MC/CQ Spacing | ❌ | ✅ |
| QID Search | ❌ | ✅ |
| Topic AND/OR Mode | ❌ | ✅ |
| Language Preference | ❌ | ✅ |
| Major Subtopic | ❌ | ✅ |
| Question Description | ❌ | ✅ |
| Configurable Page Size | 20 only | 10/20/50/100 |
| Answer Modes | 4 | 5 |
| Auto Question Type | ❌ | ✅ |
| Session Persistence | ❌ | ✅ |

---

## Upgrade Guide: v1.0 → v2.0

### Database Migration

No manual migration required! The new columns (`major_subtopic_id`, `description`) allow NULL values, so existing data will work without changes.

If you want to explicitly add the columns (optional):

```sql
-- Only if not auto-created by SQLAlchemy
ALTER TABLE questions ADD COLUMN major_subtopic_id INT NULL;
ALTER TABLE questions ADD COLUMN description TEXT NULL;
ALTER TABLE questions ADD FOREIGN KEY (major_subtopic_id) REFERENCES subtopics(id);
```

### Configuration Changes

No `.env` changes required. All new features work with existing configuration.

### Feature Migration

1. **Sorting**: Old sort parameters are automatically converted to new format
2. **Filtering**: All old filters continue to work, new ones are optional
3. **Generation**: Old answer modes work as before, new modes available

### Testing After Upgrade

1. Test existing workflows (filtering, generation, tagging)
2. Try new features (batch operations, multi-level sort, QID search)
3. Run `python cli.py sync` (dry-run) to check for orphaned records
4. Verify language preferences work correctly

### Rollback

If needed, you can roll back by:
1. Restore database backup
2. Checkout v1.0.0 code
3. Restart application

---

## Roadmap

### Future Enhancements (v2.1)
- Export/Import question sets
- Question statistics and analytics
- Advanced search with full-text search
- Question versioning/history
- Collaborative tagging with change tracking
- Mobile app integration

### Future Enhancements (v3.0)
- AI-powered question tagging suggestions
- Automatic difficulty level detection
- Question similarity detection
- LaTeX/MathML support for equations
- Multi-user collaborative editing
- Real-time notifications

---

## Support

For issues or questions about any version:
- Check documentation: README.md, SETUP.md, TESTING.md
- Review error logs: Terminal output, `ingest_errors.log`
- Check database: phpMyAdmin or MySQL client

## Contributors

Developed and maintained by the OQB Team.

---

**Last Updated**: January 9, 2026

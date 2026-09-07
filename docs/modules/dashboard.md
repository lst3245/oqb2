# Dashboard

> Question browsing: sidebar filter + Python-side multi-sort + HTMX partial swaps, an independent client-side selection, the Set Operations builder, the unified asset preview resolver, and the Explain AI-tutor chat.

## Files

| File | Role |
|---|---|
| `app/dashboard.py` | `dashboard_bp` (`/dashboard`). `_build_filtered_query(params)` (shared query builder; excludes stem ids unless `qids`/`ids`), `filter_questions()` (group via `hierarchy.group_for_dashboard`), `get_sort_groups()`, taxonomy lookups, asset preview resolver (`_resolve_preview_assets`, `get_question_preview`), `serve_file`, `doc_thumbnail`, Explain routes (`explain_endpoints`, `explain_question`) + helpers (`_default_explain_endpoint`, `_can_pick_explain_endpoint`, `_explain_slot_context`). Explain prepends ancestor QUE via `ai_tools.load_ancestor_que_images`. |
| `templates/dashboard.html` | Full page. Sidebar `#filterForm`, hidden `#qidsInput` / `#idsInput` / `#sortConfigInput` / `#sortGroupOrderInput`, View menu (Version Priority widget, page size, Show Selected Only), Sort By panel + Reorder-blocks modal, `#setOpsModal`, `#explainModal`, filter-profile save/load, all selection state JS (leaf + `.stem-checkbox`). |
| `templates/partials/question_list.html` | HTMX target fragment swapped into `#questionList`. Emits `#allQuestionIds[data-ids]` (**leaves only** across all pages), stem header cards + indented part cards, pagination links (`hx-get` with `hx-include="#filterForm"`). |
| `templates/base.html` | Shared preview helpers used by the cards: `oqbLoadMarkdownPreviewCards`, `oqbPollDocThumbnails`, `oqbRerenderThumb`, `_oqbBuildThumbHtml`, Version Priority widget (`partials/_version_priority_widget_js.html`). |
| `app/utils.py` | `apply_multi_sort`, `SORT_FIELDS`, `GROUPING_FIELDS`, `enumerate_sort_groups`, `parse_version_priority`, `VERSIONS`, `get_user_accessible_subjects`. |
| `app/md_render.py` | Server-side Markdown render (`render_file`, `render_text`) for MD previews and Explain replies. |
| `app/doc_thumbnails.py` | `ensure_thumbnail(asset_id)` used by the card resolver and preview route for DOC assets. See `doc-format.md`. |
| `app/llm_client.py`, `app/ai_prompts.py` | Explain transport (`chat_messages_stream`, `prepare_image`, `prepare_image_from_data_url`, `resolve_default_endpoint`) and prompts (`EXPLAIN_SYSTEM`, `build_explain_initial_user_text`, `normalize_inline_math`). |

## Tables

Read-only against `Question`, `QuestionAsset`, `Topic`, `Subtopic`, `Chapter`, `Subchapter`, `Subject`, `LLMConfig` (Explain endpoint lookup). No writes. Schema: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

Server session keys written by `filter_questions`: `session['filter_params']`, `session['sort_config']`, `session['sort_group_order']`. The generator reads `session['sort_config']` / `session['sort_group_order']` / `session['generator_question_ids']` (see [generator.md](generator.md)).

## Routes

All routes are `@login_required`. Subject scoping is enforced inside the handler via `current_user.has_subject_access()` / `get_user_accessible_subjects()`; there is no admin-only route in this blueprint.

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/dashboard/` | login | Full page. Renders `dashboard.html` with accessible subjects, `session['sort_config']` (default `[{"field":"qid","direction":"asc"}]`), `subject_roles`. `no_access=True` when the user has no subjects. |
| GET, POST | `/dashboard/filter` | login; 403 if `subject` not accessible | Core filter. Reads params from args or form (see table below). With `HX-Request` header returns `partials/question_list.html`; otherwise the full `dashboard.html`. |
| POST | `/dashboard/api/sort-groups` | login; 403 if `subject` not accessible | Same filter fields as `/filter` plus `group_fields` (JSON array or csv of `topic|subtopic|chapter|subchapter`). Returns `{group_fields, blocks:[{key, labels, count}]}` in natural-name order for the Reorder-blocks modal. Uses `_build_filtered_query` so it sees exactly the same result set as `/filter`. |
| GET | `/dashboard/api/topics/<subject_id>` | login; `[]` if no access | `[{id, name}]` ordered by `sort_order`. |
| GET | `/dashboard/api/subtopics?topic_ids=1,2&include_hidden=0&q_type=all` | login | `[{id, name, topic_id, hidden, count}]`. `count` = **leaf** questions with the subtopic as major OR in the M2M, optionally restricted by `q_type`. Hidden subtopics excluded unless `include_hidden=1`. Stems are excluded from counts. |
| GET | `/dashboard/api/chapters/<subject_id>` | login; `[]` if no access | `[{id, name}]`. |
| GET | `/dashboard/api/subchapters?chapter_ids=1,2&include_hidden=0` | login | `[{id, name, chapter_id, hidden}]`. |
| GET | `/dashboard/api/years/<subject_id>/<source>` | login; `[]` if no access | Distinct years, descending. |
| GET | `/dashboard/api/sections/<subject_id>/<source>` | login; `[]` if no access | Distinct sections, ascending. |
| GET | `/dashboard/files/<path:filepath>` | login | Serves a raw asset file from `SOURCE_PATH`. 404 if missing. |
| GET | `/dashboard/api/asset/<int:asset_id>` | login | `{id, type, format, version, url}`. |
| GET | `/dashboard/api/asset_preview/<int:asset_id>` | login | Sends the asset file itself. |
| GET | `/dashboard/api/question/<int:id>/assets/<asset_type>` | login | Legacy image-centric: all parts of the best version (`version DESC`, then `part_number`). `{parts:[...], id, type, format, version, url}`. |
| GET | `/dashboard/api/question/<int:id>/preview/<asset_type>?version_priority=EN,CH,BI,ENO,CHO&format=IMG|MD|DOC` | login | Unified preview resolver (below). 400 for `asset_type` outside `QUE|ANS|SOL`, 404 when nothing matches. Legacy `?lang=EN` accepted as fallback preferred version. |
| GET | `/dashboard/api/question/<int:id>/explain/endpoints` | login; 403 if no subject access | `{can_pick, default_id, endpoints:[{id, name, model, supports_vision}]}`. `endpoints` is empty and `can_pick=false` for non-admins. Returns the empty shape (200) when `AI_TOOLS_ENABLED` is off. |
| POST | `/dashboard/api/question/<int:id>/explain` | login; 403 if no subject access; 400 if `AI_TOOLS_ENABLED` off | Explain tutor chat, SSE response (below). |
| GET | `/dashboard/api/doc_thumbnail/<int:asset_id>.png` | login; 403 if no subject access | Cached first-page PNG for a DOC asset. 404 if not DOC or PNG not on disk. `Cache-Control: private, no-cache, must-revalidate` + `conditional=True` (ETag/304). |

### `/dashboard/filter` parameters

| Param | Type | Notes |
|---|---|---|
| `subject` | string | Subject ID (e.g. `MATC`). 403 if the user lacks access. |
| `source_type` | string | `DSE`, `CE`, `AL` (matched against `Question.source`) or `QB`. |
| `years` | list[int] | Multi-select, past papers only. |
| `section` | string | `A`, `Section I`, ...; `all` or empty = no filter. |
| `topics` | list[int] | Topic IDs. |
| `topic_mode` | `AND` / `OR` | Default `OR`. |
| `subtopics` | list[int] | Subtopic IDs. |
| `subtopic_mode` | `AND` / `OR` | Default `OR`. |
| `is_crosstopic` | truthy | OR mode only: match major OR any minor topic. Ignored in AND mode (see business rules). |
| `is_crosssubtopic` | truthy | Same for subtopics (major subtopic OR M2M `subtopics`). |
| `chapters` / `subchapters` | list[int] | Chapter / Subchapter IDs. |
| `levels` | list | `1`, `2`, `3` and/or the literal string `null` (questions with no level). |
| `q_type` | string | `MC`, `CQ`, or `all`. |
| `qid_search` | string | Pattern search on `Question.qid`. |
| `qid_strict` | bool (`on`/`true`/`1`) | Strict: user-controlled pattern, `*` maps to SQL `%`, no wildcard = substring. Loose (default): uppercase, split on non-alphanumerics, `%TOK1%TOK2%`. |
| `qids` | csv string | Explicit QID list. **Overrides every other filter** (only intersected with accessible subjects). Set from Admin DB Health via `?qids=` or `?qids_token=<localStorage key>`; held in hidden `#qidsInput` with a dismissible banner. |
| `ids` | csv int | Explicit `Question.id` list. Same override semantics as `qids`. Powers **Show Selected Only** (hidden `#idsInput`). Non-integer tokens are dropped. |
| `page` | int | Default 1. Pagination links pass it as a query arg on a `hx-get`. |
| `page_size` | int | 10, 20, 50, 100; anything else becomes 20; missing = `QUESTIONS_PER_PAGE` (default 20). |
| `version_priority` | csv | Ordered version preference for card previews (e.g. `EN,CH,BI,ENO,CHO`). Legacy `preview_language` (`EN`/`CH`) accepted and converted via `parse_version_priority()`. |
| `sort_config` | JSON string | `[{field, direction}, ...]`. Invalid JSON falls back to `qid asc`. |
| `sort_group_order` | JSON string | `{fields:[...], order:[[id,...],...]}` manual block order. Passed to `apply_multi_sort(..., group_order=)`; ignored unless `fields` match the grouping fields in `sort_config`. |

Response context for the partial: `questions` (flat leaf dicts), `question_groups` (`[{stem, leaves}]` — `stem` is null for standalone cards), `page`, `total_pages`, `total` (leaf count), `all_question_ids` (leaves only), `sort_config`, `admin_subjects`. Each leaf may include `stem_qid` / `stem_preview` so the part card can show the shared background above its own QUE.

### Preview resolver response shapes (`/preview/<type>`)

```text
{mode:'image',     format:'IMG', version, parts:[{id, part_number, url}]}
{mode:'html',      format:'MD',  version, asset_id, html, url}
{mode:'thumbnail', format:'DOC', version, asset_id, question_id, thumbnail_url, download_url, filename}
{mode:'download',  format:'DOC', version, asset_id, question_id, url, filename}
```

`thumbnail` is returned when `doc_thumbnails.ensure_thumbnail(asset_id)` finds a cached PNG; otherwise a render is scheduled and `download` is returned for now. The `.md-preview-card` loader in `base.html` forwards `data-preview-format` as the `format` param so the edit-modal MD card shows its MD even when an IMG outranks it.

### Explain SSE contract (`POST .../explain`)

Request body: `{turns:[{role:'user'|'assistant', content, images?:[dataURL]}], version_priority?, endpoint_id?}`.

- `turns` is the conversation AFTER the server-built initial turn; the server rebuilds turn 1 (system `EXPLAIN_SYSTEM` + labelled QUE/SOL context via `_explain_slot_context`, plus ancestor QUE images labelled "Shared background" when the row is a part) on every call, so source images are never uploaded by the browser. Only the last 20 turns are kept; text is capped at 8000 chars per turn; empty turns are dropped.
- `endpoint_id` is honoured only when `_can_pick_explain_endpoint(question)` (super admin or subject admin of the question's subject); otherwise silently ignored and `_default_explain_endpoint()` is used. 400 if no endpoint resolves.
- User images: max `EXPLAIN_MAX_IMAGES_PER_TURN = 6` per turn (extra silently dropped), `EXPLAIN_MAX_IMAGES_TOTAL_BYTES = 32 MB` post-encode across the request (413). Each is run through `llm_client.prepare_image_from_data_url(du, LLM_IMAGE_MAX_DIM)`. Images on a non-vision endpoint return 400 `"The selected LLM endpoint can't see images..."`. Undecodable image returns 400.
- 400 if the question has neither a QUE image nor QUE Markdown.

Response is `text/event-stream` with headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`. First bytes are a `: stream-start` comment, then:

```text
{type:'preamble'}
{type:'delta', content?, reasoning?}      (zero or more)
{type:'done', reply, reply_html, model, endpoint, has_solution}
   | {type:'error', message}
```

`reply` is `normalize_inline_math(text)`; `reply_html` is `md_render.render_text(reply)` (bleach-sanitised; KaTeX is client-side). If the model returns no content but did return reasoning, the reasoning is used as the reply. Empty reply emits an `error` event mentioning `finish_reason`.

## Business rules / invariants

### Filter evaluation order (`_build_filtered_query`)

1. `qids` (if non-empty) — exact `Question.qid IN (...)` intersected with accessible subjects; **nulls every other filter variable** so the later blocks are no-ops.
2. else `ids` — same, keyed on `Question.id`.
3. else `qid_search` — strict or loose LIKE pattern.
4. then subject, source, years, section, topics, subtopics, chapters, subchapters, levels, q_type.

`qids` wins over `ids` wins over `qid_search`. When both `qids` and `ids` are set (DB-Health banner plus Show Selected Only), the anomaly list wins.

### Topic / subtopic AND vs OR

- **AND with 2+ IDs**: one `.filter()` per ID, each `major == id OR minor.any(id)`. A question has only one major topic, so AND is meaningful only if minors count; the `is_crosstopic` flag is **not consulted** in this branch.
- **OR (or AND with a single ID)**: `is_crosstopic` truthy → `major IN ids OR minor.any(IN ids)`; falsy → `major IN ids` only.
- Identical logic for subtopics with `major_subtopic_id` / `Question.subtopics` and `is_crosssubtopic`.

### "Include tagged in minor" is implied in AND mode (verified in `templates/dashboard.html`)

`updateCrosstopicImpliedState()` / `updateCrosssubtopicImpliedState()` run on topic/subtopic checkbox change, mode radio change, restore-from-storage and reset:

- When `mode === 'AND'` and **>= 2** topics (subtopics) are checked, the `#crosstopic` (`#crosssubtopic`) checkbox is **force-checked and disabled**, and the `(implied by AND)` note `#crosstopicImplied` is shown. Before disabling, the user's own value is stashed in `cb.dataset.userValue` (`'1'`/`'0'`).
- When the condition stops holding, the checkbox is re-enabled and restored from `dataset.userValue`.
- **Persistence stores intent, not the forced value**: `saveStateToStorage()` writes `is_crosstopic` as `dataset.userValue === '1'` when the box is disabled, else `cb.checked`, into `localStorage['oqb_filterSettings']`. `restoreFilterSettings()` sets both `checked` and `dataset.userValue` from the stored value, then re-runs the implied-state functions.
- Because a disabled checkbox is not submitted by the browser, the backend never sees `is_crosstopic` in AND mode; that is fine because the AND branch ignores it anyway.
- Exception to intent-persistence: `getCurrentFilterValues()` (used for the "filter modified" indicator and for **saving a Saved Search Profile** via `saveFilterProfile()`) reads raw `cb.checked`, so a profile saved while the box is implied stores `is_crosstopic: true`. Loading that profile in a non-implied state therefore shows the box ticked.
- `resetFilters` clears `checked`, `disabled`, `dataset.userValue='0'` and hides the note.

### Sorting and pagination are in Python

`query.all()` then `apply_multi_sort(all_questions, sort_config, group_order=sort_group_order)` then list slicing. This is intentional: multi-field sort with null handling, manual block order, and hierarchy-aware `qid`/`qno` keys (`hierarchy.sort_key` / `qno_sort_key` so `Q3ci` / `Q23-24` order correctly) cannot be expressed as a simple `ORDER BY`. Do not move sorting into SQLAlchemy without reproducing all of that. Sort fields come from `SORT_FIELDS` in `app/utils.py`: `qid`, `qno`, `year`, `level`, `topic`, `subtopic`, `source`, `section`, `q_type`, `correct_percentage`, `chapter`, `subchapter`, `created_time`. `qno` (integer start of the paper token) is a sort criterion only, never a filter or grouping field.

Unless `qids` or `ids` override, `_build_filtered_query` excludes stem ids (`~id.in_(stem_id_query())`). After pagination, `group_for_dashboard` wraps consecutive leaves that share a root under a stem header (collapsible). Standalone roots stay ungrouped. Select All uses `#allQuestionIds` (leaves). A stem header checkbox adds the stem id; generate/viewer expand it with `resolve_render_plan`. Topic filters match tagged **leaves**; the stem is only a header.

### Manual block reordering

When `sort_config` contains a grouping field (`topic`/`subtopic`/`chapter`/`subchapter`) the Sort By panel shows **Reorder blocks**. The modal POSTs the current filter form + `group_fields` to `/dashboard/api/sort-groups`, renders the blocks with SortableJS, stores the order in hidden `#sortGroupOrderInput` and `localStorage['oqb_sortGroupOrder']`, includes it in `getCurrentFilterValues()` as `sort_group_order`, and resubmits. Stale orders are cleared client-side when the grouping fields change. The dashboard forwards `sort_group_order` to the Generate page via `submitQuestionIds()`.

### Card preview asset selection (`filter_questions`)

QUE assets are ordered by a SQL `CASE` on `version_priority` then `part_number`; the first row's version wins; within that version the best format wins with rank `IMG=0, MD=1, DOC=2`; all parts of that `(version, format)` group are returned. **Version outranks format here** (an EN-DOC beats a CH-IMG when EN is first). `_resolve_preview_assets()` used by `/preview/<type>` and Explain sorts `(version_rank, format_rank, part_number)` — the same rule. The viewer and generator sort format first; see [generator.md](generator.md).

### Selection vs filter independence

The page keeps two independent pieces of state that must never be coupled as a hidden side-effect ([ADR-008](../decisions/ADR-008-selection-independent-of-filter.md)):

- **Selection** `selectedQuestions: Set<string>` (stringified DB IDs), persisted in `localStorage['oqb_selectedQuestions']`. Survives filter changes, reloads and HTMX swaps. There is **no auto-prune** of selection on filter change.
- **Filter** = `#filterForm` + hidden `#qidsInput` / `#idsInput`, persisted in `localStorage['oqb_filterSettings']`.

| Trigger | Filter | Selection | Banner |
|---|---|---|---|
| Sidebar submit / Reset | yes | no | no |
| Sidebar subject change | yes | **cleared** (the one exception) | no |
| Card / checkbox toggle, Select Page / All, Deselect All, header Clear | no | yes | no |
| Show Selected Only toggle | overridden via `ids` | no | `showSelectedOnlyNotice` |
| Set Ops Replace / Append / Save | no | yes | no |
| `?profile_id=` | yes | no | no |
| `?filter_data_id=` (My Files Re-filter) | yes | yes | no |
| `?question_set_id=` same subject | no | replaced | Show Selected Only auto-on |
| `?question_set_id=` different subject | reset to subject-only | replaced | Show Selected Only auto-on |
| `?qids=` / `?qids_token=` | one-shot `qids` override | no | dismissible |
| Initial localStorage restore | yes | yes | no |
| HTMX swap of `#questionList` | n/a | reorder only, never delete | no |

Category names used in commit messages: **(1) Selection-only**, **(2) Both**, **(3) Both + banner + special-purpose filter**, **Filter-only**.

Selections are subject-tied (Generate cannot mix subjects), so changing `#subjectSelect` clears the selection. `let suppressSelectionClearOnSubjectChange = false;` is set briefly during a different-subject `?question_set_id=` apply so the seeded selection survives the implied subject switch.

### Show Selected Only is server-side

`#showSelectedOnly` (View menu) is not a client-side hide. `syncShowSelectedOnlyToServer()` is the single entry point:

```text
toggle ON  → #idsInput.value = Array.from(selectedQuestions).join(',') → submit form
toggle OFF → #idsInput.value = ''                                       → submit form
```

It is also called whenever the selection changes while the toggle is on (Clear, Set Ops Apply, Select All). `applyShowSelectedOnly()` is cosmetic only (banner + Select-All controls). Edge case: unticking a card while the toggle is on hides that card locally in `updateSelection()` for instant feedback; the next fetch already excludes it. While the toggle is on, `#allQuestionIds` equals the selection.

### URL handlers (`DOMContentLoaded` in `dashboard.html`)

- `?profile_id=` → `GET /user/profiles/<id>/data` → `convertProfileToSettings()` → `restoreFilterSettings()` → submit.
- `?filter_data_id=` → `GET /user/files/<id>/filter` → restores filter AND selection from the generated file.
- `?question_set_id=` → `GET /user/sets/<id>/data`; same-subject = selection-only + Show Selected Only on, no auto-submit; different subject = switch subject with the suppress flag, reset filter to subject-only defaults, seed selection, auto-submit, Show Selected Only on. See [question-sets.md](question-sets.md).
- `?qids=` or `?qids_token=<key>` (the token names a `localStorage` entry holding the csv, removed after read) → hidden `#qidsInput` + banner.
- After handling, these params are stripped from the URL (`cleanUrl`).

### Set Operations modal (`#setOpsModal`)

Opened by the **Set** button in the list header (between Manage and Generate, gated on `current_user.can_generate()`). Sources panel chip kinds, resolved by `resolveSetChip(chip)` on `chip.src`:

| Chip | `chip.src` | Resolves to |
|---|---|---|
| Selection | `'selection'` | `new Set(selectedQuestions)` |
| Filter Result | `'filter'` | `getCurrentFilterResultIds()` reading `#allQuestionIds.dataset.ids` (all pages of the current filter) |
| Result | `'result'` | `setOpsLastResult` (previous Evaluate); shown after first Evaluate |
| Scratch | `'scratch'` (+`id`, `name`) | `getScratchById(id).ids` from `setOpsScratchCache` |
| Saved set | `'saved'` (+`id`, `name`, cached `ids`) | payload from `GET /user/sets/<id>/data` |

Operators `union`, `intersect`, `diff` (left minus right, binary); single precedence, left-associative, parentheses for grouping; recursive-descent parse over the chip array evaluated client-side on `Set<string>`. No complement. Empty expression = empty result. Apply actions: Replace Selection (order-preserving), Append to Selection, Save Result as set, plus "Save current Selection as set" bypassing the expression. Apply actions are category (1) and re-sync Show Selected Only if it is on. `Selection ∩ Filter Result → Replace` is the "trim selection to current filter" idiom.

**Scratch sets** are browser-only frozen snapshots in `localStorage['oqb_scratchSets']`, flat array `[{id:'scr_<base36>_<rand>', subject, name, ids:[string], createdAt}]`, subject-scoped (modal lists only `setOpsCurrentSubject`). Helpers: `loadScratchSets`/`saveScratchSets`, `refreshScratchCacheForCurrentSubject`, `getScratchById`, `duplicateAsScratch(src)` (default name `"<Source> snapshot"`), `renameScratch`, `deleteScratch`, `clearAllScratches` (current subject only), `addScratchChip`, `renderScratchSets`. Deleting a scratch also removes matching chips from the expression. Not synced or shareable; promote via "Save Result as set".

All chip IDs are **strings** (checkbox `value`); saved sets are int on the server and coerced back to string on load.

### Explain modal (`#explainModal`)

- `openExplainModal(questionId, qid, {QUE, ANS, SOL})` is called from the card with availability flags; it renders compact preview buttons that reuse `previewAsset()` (stacked z-index so it sits above the Explain modal) and fetches `/explain/endpoints` to populate the picker (`_explainApplyEndpoints`).
- First Send may be blank (means "full explanation") only before the chat starts. `sendExplainMessage()` → `_explainStreamRequest(url, body, bubble, callbacks)` reads `response.body.getReader()`, parses SSE blocks, renders reasoning in a `<details>` disclosure and content via `window.oqbRenderMarkdownLiveSplit` (throttled ~10 fps, stable prefix frozen), then on `done` swaps in the server `reply_html` and runs `oqbTypesetMath`. Raw `reply` and any user `images` stay in `explainState.turns` and are re-sent every turn.
- Attach/paste/drop staging: `_explainStageFiles`, `_explainRenderStaged`; caps `EXPLAIN_MAX_IMAGES_PER_TURN = 6`, `EXPLAIN_MAX_IMAGE_BYTES = 8 MB`, `EXPLAIN_MAX_TOTAL_IMAGE_BYTES = 32 MB`. `_explainSyncVisionFromPicker` greys out Attach when the picked endpoint lacks `supports_vision`.
- Picker change after the chat started triggers `_explainRegenerate()`: aborts the in-flight stream via `explainState.aborter` (AbortController), drops the last assistant turn and trailing assistant bubbles, re-fires `_explainSend(null, null)`. Every stream's `onDone`/`.finally` is guarded by an aborter-equality check so a stale stream cannot clobber the new one.

### localStorage keys (`STORAGE_KEYS` in `dashboard.html`)

`oqb_selectedQuestions`, `oqb_filterSettings`, `oqb_sortConfig`, `oqb_sortGroupOrder`, `oqb_viewSettings`, `oqb_previewLanguage` (legacy fallback), `oqb_versionPriority`, `oqb_pageSize`, `oqb_showSelectedOnly`, `oqb_showHiddenSubtopics`, `oqb_showHiddenSubchapters`, `oqb_currentPage`, `oqb_scratchSets`. Plus ad-hoc `qids_token` keys written by DB Health.

## Settings & config keys

| Key | Read in | Purpose |
|---|---|---|
| `SOURCE_PATH` | `serve_file`, `get_asset`, `get_asset_preview`, `get_question_preview`, `explain_question` | Asset root. |
| `QUESTIONS_PER_PAGE` (default 20) | `filter_questions` | Page size when the client sends none. |
| `AI_TOOLS_ENABLED` (default True) | `explain_endpoints`, `explain_question` | Gates Explain. |
| `LLM_IMAGE_MAX_DIM` (default 1600) | `explain_question` | Long-edge downscale for QUE/SOL and user images. |
| `LLM_CHAT_TIMEOUT_SECONDS` | `explain_question` | Passed as `timeout` to `chat_messages_stream`; 0/blank = library default. |
| `EXPLAIN_DEFAULT_LLM` | `_default_explain_endpoint` via `llm_client.resolve_default_endpoint(..., vision_only=True, named_vision_only=False)` | Named endpoint (may be text-only) else first enabled vision endpoint by `sort_order, name`. |

Runtime-tunable keys hot-reload from System Settings: [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

- Everything requires login. Subject access is checked per request (`has_subject_access`); taxonomy lookups return `[]` rather than 403 for inaccessible subjects, while `/filter` and `/api/sort-groups` return 403.
- `qids` / `ids` overrides are always intersected with `get_user_accessible_subjects()`.
- The **Set** button and Generate/Viewer submission are gated on `current_user.can_generate()` (viewers cannot).
- Edit buttons on cards render only for subjects in `current_user.get_admin_subjects()`.
- Explain is available to any user with subject access; only super admins and subject admins of that subject may pick a non-default endpoint (`_can_pick_explain_endpoint`).
- Details: [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

- `POST .../explain` streams SSE from a generator that pushes `app.app_context()` (request context is gone by then). Requires a threaded server; nginx needs `proxy_buffering off` (the route also sets `X-Accel-Buffering: no`).
- The card resolver and `/preview/<type>` call `doc_thumbnails.ensure_thumbnail()`, which may schedule a background Word COM render (deduped per process). See `doc-format.md`.
- No other threads. Sorting all matching rows in Python on every request is the main per-request cost.

## Gotchas

1. **`qids` > `ids` > `qid_search`**, and the first two null every other filter variable. Do not add a filter block before this cascade.
2. **Never prune `selectedQuestions` on an HTMX swap.** The `htmx:afterSwap` handler on `#questionList` may only run `applyViewSettings`, `initializeSelectionState`, `applyShowSelectedOnly`. `initializeSelectionState()` must set `cb.checked = isSelected` in both branches.
3. **Subject change is the only flow allowed to clear selection implicitly**; use `suppressSelectionClearOnSubjectChange` when you need to switch subject and keep a seeded selection.
4. **Show Selected Only lives in `#idsInput`, not in CSS.** Any code that mutates the selection while the toggle is on must call `syncShowSelectedOnlyToServer()`.
5. **AND mode ignores `is_crosstopic`.** The UI mirrors this by force-checking and disabling the box; persist `dataset.userValue`, not the forced state. `getCurrentFilterValues()` deliberately reads the forced value (profiles saved in that state carry `true`).
6. **Version beats format in the dashboard resolver** (`_resolve_preview_assets` and the card loop) but format beats version in the viewer/generator. Do not "fix" one to match the other without checking both call sites.
7. **Sorting/pagination is Python-side by design** (natural sort, null handling, manual block order). Avoid `ORDER BY`/`LIMIT` shortcuts.
8. **`sort_group_order` is ignored unless its `fields` equal the grouping fields in `sort_config`**; the client clears it when grouping changes.
9. **Explain is stateless server-side**: turn 1 is rebuilt each call; the client must re-send `turns` including any user `images` every time. Stale-stream races are prevented by the `aborter` identity check; keep it when touching `_explainStreamRequest`.
10. **`chat_messages_stream` sets `resp.encoding='utf-8'`** because some local LLM servers omit charset and `requests` would otherwise decode ISO-8859-1 (mojibake in CJK/curly quotes).
11. **DOC thumbnail endpoint must stay `no-cache, must-revalidate`**; the rerender button relies on it.
12. **`/api/subtopics` counts are OR-style** (major or M2M) regardless of the sidebar's mode, and are recomputed when `q_type` changes.
13. **Explain JSON error responses** (400/403/413) happen before the stream starts; once streaming, errors arrive as `{type:'error'}` events. The frontend handles both.
14. **`#allQuestionIds` must stay leaves-only.** Adding stem ids there would make Select All double-count (stem + parts). Stem header checkboxes are a separate `name="stem_checkbox"` and are not included in Select All / Select This Page.

## Related

- [question-hierarchy.md](question-hierarchy.md) — tree model, `group_for_dashboard`, `#allQuestionIds` leaves-only.
- [generator.md](generator.md) — receives `question_ids`, `sort_config`, `sort_group_order`, `filter_data` from `submitQuestionIds()`; viewer asset resolver.
- [question-sets.md](question-sets.md) — saved sets used by the Set Ops modal and `?question_set_id=`.
- [my-files.md](my-files.md) — saved search profiles (`?profile_id=`), My Files Re-filter (`?filter_data_id=`).
- `doc-format.md` — DOC thumbnails consumed by the card resolver.
- [../decisions/ADR-001-server-rendered-htmx-no-spa.md](../decisions/ADR-001-server-rendered-htmx-no-spa.md)
- [../decisions/ADR-008-selection-independent-of-filter.md](../decisions/ADR-008-selection-independent-of-filter.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/06-system-settings.md](../core/06-system-settings.md)

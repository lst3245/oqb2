# Frontend conventions

> Server-rendered Jinja2 + Bootstrap 5.3 + HTMX 1.9, with small inline JS modules and a set of shared helpers in `base.html`. No bundler, no SPA ([ADR-001](../decisions/ADR-001-server-rendered-htmx-no-spa.md)). Reuse what is listed here before writing a new control.

## Layout

- `templates/base.html` — navbar, Bootstrap 5.3 + Bootstrap Icons 1.11 + HTMX 1.9.10 + SortableJS + KaTeX from CDN, global CSS, shared JS helpers, and Jinja-emitted globals. Pages `{% extends "base.html" %}` and fill `{% block content %}`, `{% block extra_css %}`, `{% block extra_js %}`.
- `templates/viewer.html` and `templates/markup.html` **do not extend `base.html`** (fullscreen tools). The viewer carries minimal copies of `oqbTypesetMath` / `oqbRenderMarkdownInto`; if you add a shared helper the viewer needs, duplicate it there deliberately.
- Navigation source of truth: the navbar in `base.html` (`url_for` targets listed in `STATUS.md` frontend table). Add a nav entry when you add a page. Admin pages are reachable from two places and both must be updated: the Admin dropdown in `base.html` **and** a hub card in `templates/admin_index.html` (sections: Content Management, Operations, and the super-admin row).

## Jinja-emitted globals (from `base.html`)

| Global | Meaning |
|---|---|
| `window.OQB_IS_ADMIN` | any subject-admin or super-admin role; hides admin-only UI (server decorators are the real gate) |
| `window.OQB_AI_TOOLS_ENABLED` | `AI_TOOLS_ENABLED` setting; gates AI buttons |
| `window.OQB_VERSIONS`, `OQB_VERSION_LABELS`, `OQB_DEFAULT_VERSION_PRIORITY` | canonical version list from `app/utils.VERSIONS` via a context processor (also available in `viewer.html`). Build version UIs from these, never a literal list. `OQB_DEFAULT_VERSION_PRIORITY` is per request: the device's paper-language order (`i18n.paper_version_priority`), else `DEFAULT_VERSION_PRIORITY` |
| `window.OQB_I18N` | Chinese UI catalogue (`{English: 中文}`), loaded from `/i18n/zh.js?v=<mtime>` in `<head>` **only** when the UI language is Chinese; read it through `OQB.t`, never directly |

## UI language (`OQB.*`, `static/js/i18n.js`)

Loaded in the `<head>` of `base.html`, `viewer.html` and `markup.html` as `js/i18n.js?v=N`, so page scripts can call it at parse time. Bump `N` in all three templates whenever the file changes: static files have no automatic cache-busting, and a stale copy breaks new menu items. Spec: [../modules/i18n.md](../modules/i18n.md).

| Helper | Purpose |
|---|---|
| `OQB.t(text, params)` | UI text: the catalogue value when the UI is Chinese, else `text`; fills `{name}` placeholders. Pass **one plain string literal** (the catalogue coverage test only sees literals) and put variable parts in `params`: `OQB.t('Delete asset "{name}"?', {name})` |
| `OQB.uiLang()` / `OQB.taxLang()` / `OQB.paperLang()` | `en` / `zh`, `en` / `zh` / `both`, and `en` / `zh` / `bi` or `null` from the `oqb_lang` / `oqb_tax_lang` / `oqb_paper_lang` cookies |
| `OQB.setLang('ui' \| 'tax' \| 'paper', value)` | writes the cookie (per device, one year, path `/`) and reloads; used by `partials/language_menu.html` (navbar globe dropdown). `'paper'` also removes the dashboard's `oqb_versionPriority` / `oqb_previewLanguage` localStorage so the dashboard adopts the new default order |
| `OQB.taxName(node)` / `OQB.bilingual(name, nameZh, mode)` | display name of a taxonomy API node (`name_en` / `name_zh`) in the taxonomy mode; falls back to `node.name` |
| `OQB.taxSearchKey(node)` | lower-cased `name name_en name_zh` haystack — use for every taxonomy search box so both languages match |

Jinja side: `{{ t('English text', name=value) }}` for UI text, `{{ tax_name(node) }}` for a taxonomy node or subject, `OQB_HTML_LANG` for `<html lang>`. `showToast(message, …)` in the Edit modal runs `OQB.t(message)` itself, so a fixed toast string only needs a catalogue entry. Never send a displayed taxonomy name back to the server; post ids, or `name_en` where a name is required.

## Shared JS helpers (`base.html`)

| Helper | Purpose |
|---|---|
| `oqbTypesetMath(root)` | KaTeX auto-render inside `root`; call after inserting Markdown HTML |
| `oqbRenderMarkdownInto(el, html)` | set innerHTML + typeset |
| `oqbLoadMarkdownPreviewCards(root)` | lazy-load `.md-preview-card[data-preview-q]` placeholders from `/dashboard/api/question/<id>/preview/<type>`; branches on `mode` (image / html / thumbnail / download) |
| `oqbPollDocThumbnails(root)` | poll `data-doc-pending-id` elements every 3 s (60 attempts); same cache-busted URL for probe and swap; dedup via `data-doc-poller-active` |
| `oqbRerenderThumb(qid, aid, btn)` | POST `/admin/questions/<qid>/assets/<aid>/rerender-thumb`, re-arm poller; admin-only |
| `_oqbBuildThumbHtml(url, filename, dl, aid, qid)` | DOC thumbnail card markup used by both paths above |
| `oqbParseUtc` / `oqbFormatLocalTime` | parse API datetimes (`...Z` or legacy `YYYY-MM-DD HH:MM`) and format local `YYYY-MM-DD HH:mm` (24 h, year-first) — use for every `created_at` / `checked_at` / `verified_at` |
| `oqbInitVersionPriorityWidget({container, hiddenInput, initial, onChange})`, `oqbNormalizeVersionPriority(list)` | drag-to-reorder Version Priority widget backed by a hidden comma `version_priority` input; defined in `partials/_version_priority_widget_js.html`, included by `base.html` and `viewer.html` |

`oqbLoadMarkdownPreviewCards` and `oqbPollDocThumbnails` run on `DOMContentLoaded` and on every `htmx:afterSwap`, so HTMX refreshes need no extra wiring.

## HTMX pattern

```html
<form hx-post="/dashboard/filter" hx-target="#questionList" hx-indicator="#loading">
```

The server checks the `HX-Request` header and returns only `partials/question_list.html`; without the header it renders the full page. Rule: every HTMX interaction targets a specific `id` and returns the matching partial. Anything that must re-run after a swap hooks `htmx:afterSwap`.

## Shared partials

| Partial | Used by / notes |
|---|---|
| `partials/question_list.html` | dashboard HTMX target only (rendered by `filter_questions` and included by `dashboard.html`). Every question is a **question sheet**: head bar + paper \| rail rows, with one image scale per sheet and an Enlarge overlay. Spec and invariants: [../modules/dashboard.md](../modules/dashboard.md#question-sheets-card-layout). Elsewhere, crops of one question stacked together should likewise share one scale (no per-image `max-height`). |
| `partials/edit_question_modal.html` | shared 3-tab Edit modal + `#renameConfirmModal` + `#mdEditorModal` + `#toastContainer`; mounted by `dashboard.html` (admin-gated) and `admin_questions.html`. Contract: [../modules/admin-questions.md](../modules/admin-questions.md) |
| `admin_questions/*.html` | Question Management page parts (header, filters, tree, action bar, bulk panel + `ops/`, add modal, `_js_*.html`). Page-local, not reusable elsewhere; their JS shares the global scope with the Edit-modal JS, hence the `aq` / `bp` / `op` name prefixes. |
| `partials/edit_question_modal_js.html` | all Edit-modal JS; **transitively includes** `tag_editor_js.html` — do not include that separately |
| `partials/tag_editor_form.html`, `partials/tag_editor_js.html` | tag editor (Tags tab; borrowed by the admin Add-question wizard step 3) |
| `partials/md_editor.html` | EasyMDE-based Markdown editor; included by the Edit modal (idempotent) and `admin_md_editor.html` |
| `partials/file_browser_{css,body,js}.html` | both file browser pages |
| `partials/file_selector.html` | `window.OQBFileSelector.open({...})` picker modal used by PDF Import, PDF Tool, Smart Import ([../modules/file-browser.md](../modules/file-browser.md)) |
| `partials/pdf_annotate_editor.html` | PDF Tool redact/highlight editor |
| `partials/_version_priority_widget_js.html` | Version Priority widget |
| `partials/admin_subject_picker.html` | Topics / Chapters subject `<select>` (`endpoint`, `subjects`, `subject`) |
| `partials/taxonomy_admin_js.html` | `oqbTaxonomyAdmin(cfg)` for Topics / Chapters. Markup contract in its header: `.tax-name` (English, `rename`) and `.tax-name-zh[data-placeholder]` (Chinese, `rename-zh`, posts only `name_zh`; **no whitespace inside the span** — the placeholder uses `:empty`) |
| `partials/language_menu.html` | navbar globe dropdown: Interface language (English / 中文) and Topic & chapter names (English / 中文 / Both) |

## Bounding-box editor (`OQBBboxEditor`)

Shared overlay editor in `static/js/bbox_editor.js` + `static/css/bbox_editor.css` (no bundler; exposes `window.OQBBboxEditor`). Used by `templates/admin_pdf_import.html` (one editor per page card, `card._ed`) and `templates/admin_question_split.html` (one editor on the stitched image).

**Markup contract:** `<div class="pdf-page-wrap"><img class="pdf-page-img"><div class="pdf-overlay"></div></div>`. Boxes are fractional `[x1,y1,x2,y2]` of the image. The host owns the model (items) and persistence; the editor owns pointer sessions (move / 8-handle resize / rubber-band draw in "add mode"), the drag magnifier, crop thumbnails, the full-size crop preview, and an optional dashed green page-frame guide with draggable left/right rails. Host adds class `x-locked` on the wrap to hide the horizontal handles.

**Mount:** `OQBBboxEditor.mount({wrap, img, overlay, getBox, setBox, classNames, labelText, boxId, magnifierColor, minSize, constrainBox, onSelect, onChange, onCreate, onTooSmall, onAddModeChange, frameEditable, onFrameChange})`.

**Instance:** `add` / `remove` / `restyle` / `refreshLabel` / `refreshClass` / `refreshAll` / `select` / `setAddMode` / `isAdding` / `setFrame` / `getFrame` / `clear` / `destroy` / `nudge(item, dxPx, dyPx)` (moves by screen pixels through `constrainBox` with `type: 'move'`, then `onChange(item, 'move', false)`).

**Keyboard (document-wide, owned by the editor):** the arrow keys nudge the box last **clicked or drawn** (`keyItem`) by 1 screen px, Shift = 10 px, but only while it is still selected and on screen, no drag is running, focus is not in an input / select / textarea, and no `.modal.show` / crop preview is open. Hover-selection from a host list (`ed.select`) does not arm the arrows, so page scrolling keeps working. Clicking a box blurs a focused text field first (commits a typed label). Holding **H** hides the drag magnifier (during a drag the key is swallowed so it is not typed into a focused field). Hosts keep their own Delete / Backspace handlers. The box label chip sits at the box's top-right so it does not cover printed question numbers.

**Helpers:** `clamp01`, `applyBoxStyle`, `fracFromEvent`, `drawPreview`, `showCropPreview`, `selectNone`.

**Invariant:** never reimplement box overlays or canvas drawing. Mount `OQBBboxEditor`; mutate boxes through the host model then call `ed.restyle(item)` so the overlay stays in sync.

## Multi-select dropdowns (custom, not `<select multiple>`)

Topics, subtopics, chapters, years: Bootstrap dropdown with `data-bs-auto-close="outside"`, checkboxes in the panel, a search box, "All" / "None" buttons, and a hidden `<select multiple>` that carries the values on submit. Copy this pattern for any new multi-select filter.

## Drag-to-reorder (SortableJS only)

Native HTML5 drag-and-drop is **not** used (fails on touch). Standard config:

```js
Sortable.create(container, {
  handle: '.drag-handle', animation: 150,
  ghostClass: 'sortable-ghost', chosenClass: 'sortable-chosen', dragClass: 'sortable-drag',
  forceFallback: true, fallbackTolerance: 4, delay: 100, delayOnTouchOnly: true, touchStartThreshold: 4,
  onEnd(evt) { /* reorder model array with evt.oldIndex / evt.newIndex */ }
});
```

Init is idempotent: stash on `container._sortableInstance` and bail if set. `.drag-handle` has `touch-action: none` globally; a larger hit area is applied on coarse pointers.

Nested lists (parent rows each holding a child list, e.g. Admin Topics / Chapters via `partials/taxonomy_admin_js.html`) give each level its own handle class (`.drag-handle.tax-parent-handle` / `.drag-handle.tax-child-handle`) and `draggable: '.tax-item'`, so a child drag never moves the parent. Keep arrow buttons as a fallback next to the handle; both paths go through one `afterMove(container)` that refreshes arrow state and saves the order.

**Inline rename** (same partial): double-click the `.tax-name` span (or a pencil button) swaps in an `input`; Enter / blur saves, Esc cancels, and a `done` guard stops the blur after Enter from saving twice. Reuse this pattern rather than an edit modal for single-field renames.

## SSE consumption

```js
const es = new EventSource(url);
es.onmessage = e => { const ev = JSON.parse(e.data); appendLog(ev.message, ev.type); if (ev.type === 'done') es.close(); };
```

Event types: `info | success | skip | error | done`, optional `current` / `total`. Server contract: [../core/04-backend-conventions.md](../core/04-backend-conventions.md).

## Bulk actions (Question Management `BulkRunner`)

New bulk actions on `/admin/questions` do not get their own modal. They are one entry in `AQ_OPS` (`admin_questions/_js_ops.html`) plus a form `admin_questions/ops/<op>.html` (`<div class="bp-op d-none" data-op="…">`), driven by the `#aqBulkPanel` offcanvas (`_js_bulk.html`):

- **Configure** → `collect()` / `validate(p)`; **Review** → `summary(p)`, affected-row list, preflight `/admin/questions/api/subjects-of`, optional server dry run `preview(ids, p)` → `{html, block}`; **Run** → `transport` `json` / `form` (`request(ids, p)`, `result(data)`) or `sse` (`sse.url()`, `sse.params(p)`, `stats` keys; ids are sent 800 per stream).
- Every request carries `subject_id` (`opIdsForm` for forms; the SSE runner adds it). The route must call `_bulk_subject_guard` ([ADR-016](../decisions/ADR-016-single-subject-question-management.md)).
- Confirm gate: `danger(p) === 'typed'` → the user types the subject code; otherwise more than 100 rows → a tick. Destructive = deletes rows / files or overwrites data a teacher entered.
- Stop: AI ops cancel through `/admin/questions/ai/cancel` and wait for `done`; other streams close client-side.
- Results: put the QID in `error` event messages (or an event `qid`) so **Select failed only** can find the rows.

The same panel pattern (Configure → Review with a dry run → Run with log) is the model for any other page that runs a destructive batch.

## Polling conventions

- `generate.html` polls `GET /generate/status/<id>` every 2 s until `completed` / `failed`.
- `my_files.html` refreshes every 5 s only while a row has `.status-generating`; only sections containing in-progress files are re-fetched.

## localStorage keys in use

| Key | Owner |
|---|---|
| `oqb_assetThumbsCompact` (`'1'`/`'0'`) | Edit modal asset preview density (`oqbInitThumbCompact`) |
| `oqb_adminTaxonomyPreview` (`'1'`/`'0'`, absent = on) | Admin → Topics numbering & MC/CQ preview switch (`oqbTaxonomyAdmin`) |
| `oqb_adminQuestionsState` (JSON `{subject, filtersBySubject, pageSize, sort}`) | Question Management (`admin_questions/_js_state.html`); replaced `oqb_adminQuestionsFilters`, which is deleted on load. The selection is never stored. |
| dashboard selection / scratch sets / set-builder state | `dashboard.html` (see [../modules/dashboard.md](../modules/dashboard.md)) |
| Markup canvas autosave | IndexedDB, not localStorage ([../modules/markup.md](../modules/markup.md)) |

When you add a key, prefix it `oqb_` and list it here.

Cookies (not localStorage, because the server renders with them): `oqb_lang` (`en` / `zh`) and `oqb_tax_lang` (`en` / `zh` / `both`), written only by `OQB.setLang`.

## Style rules

- Bootstrap utilities and components first; page-specific CSS in `{% block extra_css %}`; no hex colours where a Bootstrap variable exists.
- Icons: Bootstrap Icons (`bi bi-...`).
- Toasts via the shared `#toastContainer` when the Edit modal partial is present; otherwise Bootstrap alerts / `flash()`.
- Modals stack: keyboard shortcuts in the Edit modal ignore input while a child modal is open — follow the same guard if you add shortcuts.
- Dates always through `oqbFormatLocalTime`.
- New teacher-facing text goes through `t()` / `OQB.t()` with a matching entry in `app/translations/zh_Hant.json` (Hong Kong terms: [../reference/glossary-zh.md](../reference/glossary-zh.md)); taxonomy names through `tax_name` / `OQB.taxName`. Logs, SSE progress lines and AI prompts stay English.
- Logic never reads displayed text. Keep status values English in JS state and translate when rendering (`statusLabel()` in `admin_pdf_import.html`); use `data-*` attributes for anything a handler needs to read back. Never name a Jinja variable `t` (it shadows `t()`).

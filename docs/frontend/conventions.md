# Frontend conventions

> Server-rendered Jinja2 + Bootstrap 5.3 + HTMX 1.9, with small inline JS modules and a set of shared helpers in `base.html`. No bundler, no SPA ([ADR-001](../decisions/ADR-001-server-rendered-htmx-no-spa.md)). Reuse what is listed here before writing a new control.

## Layout

- `templates/base.html` — navbar, Bootstrap 5.3 + Bootstrap Icons 1.11 + HTMX 1.9.10 + SortableJS + KaTeX from CDN, global CSS, shared JS helpers, and Jinja-emitted globals. Pages `{% extends "base.html" %}` and fill `{% block content %}`, `{% block extra_css %}`, `{% block extra_js %}`.
- `templates/viewer.html` and `templates/markup.html` **do not extend `base.html`** (fullscreen tools). The viewer carries minimal copies of `oqbTypesetMath` / `oqbRenderMarkdownInto`; if you add a shared helper the viewer needs, duplicate it there deliberately.
- Navigation source of truth: the navbar in `base.html` (`url_for` targets listed in `STATUS.md` frontend table). Add a nav entry when you add a page.

## Jinja-emitted globals (from `base.html`)

| Global | Meaning |
|---|---|
| `window.OQB_IS_ADMIN` | any subject-admin or super-admin role; hides admin-only UI (server decorators are the real gate) |
| `window.OQB_AI_TOOLS_ENABLED` | `AI_TOOLS_ENABLED` setting; gates AI buttons |
| `window.OQB_VERSIONS`, `OQB_VERSION_LABELS`, `OQB_DEFAULT_VERSION_PRIORITY` | canonical version list from `app/utils.VERSIONS` via a context processor (also available in `viewer.html`). Build version UIs from these, never a literal list |

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
| `partials/question_list.html` | dashboard HTMX target; admin question list |
| `partials/edit_question_modal.html` | shared 3-tab Edit modal + `#renameConfirmModal` + `#mdEditorModal` + `#toastContainer`; mounted by `dashboard.html` (admin-gated) and `admin_questions.html`. Contract: [../modules/admin-questions.md](../modules/admin-questions.md) |
| `partials/edit_question_modal_js.html` | all Edit-modal JS; **transitively includes** `tag_editor_js.html` — do not include that separately |
| `partials/tag_editor_form.html`, `partials/tag_editor_js.html` | tag editor (Tags tab; borrowed by the admin Add-question wizard step 3) |
| `partials/md_editor.html` | EasyMDE-based Markdown editor; included by the Edit modal (idempotent) and `admin_md_editor.html` |
| `partials/file_browser_{css,body,js}.html` | both file browser pages |
| `partials/file_selector.html` | `window.OQBFileSelector.open({...})` picker modal used by PDF Import, PDF Tool, Smart Import ([../modules/file-browser.md](../modules/file-browser.md)) |
| `partials/pdf_annotate_editor.html` | PDF Tool redact/highlight editor |
| `partials/_version_priority_widget_js.html` | Version Priority widget |

## Bounding-box editor (`OQBBboxEditor`)

Shared overlay editor in `static/js/bbox_editor.js` + `static/css/bbox_editor.css` (no bundler; exposes `window.OQBBboxEditor`). Used by `templates/admin_pdf_import.html` (one editor per page card, `card._ed`) and `templates/admin_question_split.html` (one editor on the stitched image).

**Markup contract:** `<div class="pdf-page-wrap"><img class="pdf-page-img"><div class="pdf-overlay"></div></div>`. Boxes are fractional `[x1,y1,x2,y2]` of the image. The host owns the model (items) and persistence; the editor owns pointer sessions (move / 8-handle resize / rubber-band draw in "add mode"), the drag magnifier, crop thumbnails, the full-size crop preview, and an optional dashed green page-frame guide with draggable left/right rails. Host adds class `x-locked` on the wrap to hide the horizontal handles.

**Mount:** `OQBBboxEditor.mount({wrap, img, overlay, getBox, setBox, classNames, labelText, boxId, magnifierColor, minSize, constrainBox, onSelect, onChange, onCreate, onTooSmall, onAddModeChange, frameEditable, onFrameChange})`.

**Instance:** `add` / `remove` / `restyle` / `refreshLabel` / `refreshClass` / `refreshAll` / `select` / `setAddMode` / `isAdding` / `setFrame` / `getFrame` / `clear` / `destroy`.

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

## SSE consumption

```js
const es = new EventSource(url);
es.onmessage = e => { const ev = JSON.parse(e.data); appendLog(ev.message, ev.type); if (ev.type === 'done') es.close(); };
```

Event types: `info | success | skip | error | done`, optional `current` / `total`. Server contract: [../core/04-backend-conventions.md](../core/04-backend-conventions.md).

## Polling conventions

- `generate.html` polls `GET /generate/status/<id>` every 2 s until `completed` / `failed`.
- `my_files.html` refreshes every 5 s only while a row has `.status-generating`; only sections containing in-progress files are re-fetched.

## localStorage keys in use

| Key | Owner |
|---|---|
| `oqb_assetThumbsCompact` (`'1'`/`'0'`) | Edit modal asset preview density (`oqbInitThumbCompact`) |
| dashboard selection / scratch sets / set-builder state | `dashboard.html` (see [../modules/dashboard.md](../modules/dashboard.md)) |
| Markup canvas autosave | IndexedDB, not localStorage ([../modules/markup.md](../modules/markup.md)) |

When you add a key, prefix it `oqb_` and list it here.

## Style rules

- Bootstrap utilities and components first; page-specific CSS in `{% block extra_css %}`; no hex colours where a Bootstrap variable exists.
- Icons: Bootstrap Icons (`bi bi-...`).
- Toasts via the shared `#toastContainer` when the Edit modal partial is present; otherwise Bootstrap alerts / `flash()`.
- Modals stack: keyboard shortcuts in the Edit modal ignore input while a child modal is open — follow the same guard if you add shortcuts.
- Dates always through `oqbFormatLocalTime`.

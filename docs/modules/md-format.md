# MD source format (Markdown assets)

> `QuestionAsset.file_format='MD'` is the self-contained, single-slot Markdown source format: LaTeX math via `$...$` / `$$...$$` and images embedded as `data:image/...;base64,...` URIs. Server renders with markdown-it-py + bleach (client KaTeX typesets), an EasyMDE editor with optimistic mtime concurrency edits files in place, and generation converts through pandoc + docxcompose.

## Files

| File | Role |
|---|---|
| `app/md_render.py` | `render_text` (markdown-it-py + `sanitize`), `render_file(asset_id, abs_path)` mtime-keyed LRU cache, `invalidate(asset_id=None)`, bleach allowlists `_ALLOWED_TAGS` / `_ALLOWED_ATTRS` / `_attr_allowed` / `_is_safe_url` / `_BLEACH_PROTOCOLS`. |
| `app/admin.py` | MD endpoints (`get_md_asset_content`, `save_md_asset_content`, `create_md_asset`, `edit_md_asset_page`, `new_md_asset_page`), helpers `_require_md_admin`, `_mtime_ns_json`, `_parse_mtime_ns`; `upload_question_asset` single-slot + size guard; `delete_question_asset` MD branch invalidates the cache. |
| `app/generator.py` | `_preprocess_md_for_pandoc`, `_resolve_pandoc_binary`, `md_to_docx_via_pandoc`, `_append_md_via_pandoc`, `_parse_format_priority`, `_DEFAULT_FORMAT_PRIORITY = ('IMG','MD','DOC')`; `get_viewer_asset` returns `{format:'MD', html}`. |
| `app/ai_prompts.py` | `normalize_inline_math` (tightens `$ x $` → `$x$`); MD generation/Explain prompts carry the `\$` literal-dollar rule. |
| `app/ai_tools.py` | `generate_md_slot` writes AI-generated MD (already tightened). |
| `app/dashboard.py` | `_resolve_preview_assets`, `get_question_preview`, `filter_questions` — return `mode:'html'` for MD. |
| `app/ingestor.py` | `determine_file_format` (`md`/`markdown` → `MD`), `upsert_asset` multi-part guard. |
| `app/batch_image_gen.py` | `render_md_to_pages` (MD → docx via pandoc → Word → PNG). |
| `migrate_md_format.py` | One-off, idempotent `ALTER TABLE question_assets` to extend `file_format` to `ENUM('IMG','DOC','MD')`. |
| `templates/partials/md_editor.html` | Editor factory `window.oqbCreateMdEditor` (EasyMDE + marked + KaTeX + FontAwesome 4.7, all CDN); idempotent include. |
| `templates/admin_md_editor.html` | Fullscreen editor page (edit + create modes), dirty-state, 409 handling, `beforeunload` guard. |
| `templates/admin_questions.html`, `templates/partials/edit_question_modal*.html` | `renderAssetSections`, `openMdEditorModal`, `#mdEditorModal` inline modal editor, paste-handler isolation. |
| `templates/base.html` | Shared client helpers `oqbTypesetMath`, `oqbRenderMarkdownInto`, `oqbLoadMarkdownPreviewCards`. |
| `templates/viewer.html` | Duplicates those helpers (does NOT extend `base.html`). |

## Tables

`QuestionAsset` (`app/models.py`): `file_format` enum `IMG|DOC|MD`; MD rows always have `part_number=1`; `file_path` relative to `SOURCE_PATH`, named `<QID>_<VERSION>_<ATYPE>.md` under `<subject>/PP/<source>/<year>/<paper>/` or `<subject>/QB/<detail>/`. Run `migrate_md_format.py` once on databases created before MD existed. See `../core/03-data-model-and-migrations.md`.

## Routes

All admin MD endpoints are `@login_required @admin_required` plus `_require_md_admin(question)` (super admin, or the question's subject is in the caller's admin subjects; otherwise 403 `{error:'Access denied'}`).

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/admin/questions/<int:question_id>/assets/<int:asset_id>/md/content` | admin + subject | Returns `{asset_id, qid, version, asset_type, file_path, mtime_ns (string), content, max_size}`. 400 if not MD or invalid UTF-8; 404 if the file is missing. mtime is stat'ed BEFORE the read. |
| POST | `/admin/questions/<int:question_id>/assets/<int:asset_id>/md/save` | admin + subject | Body `{content, expected_mtime_ns? (string or int), force?}`. 413 over `MD_MAX_SIZE_BYTES`; **409** `{error, current_mtime_ns}` when `expected_mtime_ns` differs from disk and `force` is false. Writes bytes, calls `md_render.invalidate(asset.id)`, returns `{success, asset_id, mtime_ns, size_bytes}`. |
| POST | `/admin/questions/<int:question_id>/assets/md/create` | admin + subject | Body `{version, asset_type, content}` (legacy `language` accepted). Validates `version ∈ VERSIONS`, `asset_type ∈ QUE/ANS/SOL`, size; **409** `{error, existing_asset_id}` if the slot already has an MD. Writes the file, inserts the row (`part_number=1`), returns `{success, asset_id, file_path, mtime_ns, size_bytes}`. |
| GET | `/admin/questions/<int:question_id>/assets/<int:asset_id>/md/edit` | admin + subject | Fullscreen editor page (`admin_md_editor.html`, gets `question`, `asset`, `md_max`). Non-MD → flash + redirect to Question Management. |
| GET | `/admin/questions/<int:question_id>/assets/md/new?version=&asset_type=` | admin + subject | Fullscreen editor in create mode (legacy `language=` accepted; invalid values fall back to `EN`/`QUE`). |
| GET | `/dashboard/api/question/<id>/preview/<type>?version_priority=&format=MD` | login + subject access | Unified preview resolver; MD yields `{mode:'html', format:'MD', html, ...}` (see `dashboard.md`). |
| GET | `/generate/api/viewer_asset/<int:question_id>/<asset_type>` | login | Viewer resolver; MD yields `{format:'MD', html}` (see `generator.md`). |
| POST | `/admin/questions/<int:question_id>/assets/upload` | admin | Accepts `.md`; rejects a second MD in the same `(asset_type × version)` slot; enforces `MD_MAX_SIZE_BYTES`. |
| POST/DELETE | `/admin/questions/<int:question_id>/assets/<int:asset_id>/delete` | admin | Deletes file + row; MD branch calls `md_render.invalidate`. |

AI writers (`GET /admin/questions/ai/generate-md` SSE, `POST /admin/questions/<id>/assets/ai/generate-md`) are documented in `ai-tools.md`.

## Business rules / invariants

### Locked decisions

1. **Self-contained MD**: no sidecar image files; images MUST be base64 data URIs inside the markdown.
2. **Single-slot**: one MD per `(question, asset_type, version)`; multi-part rejected at ingest (`upsert_asset`) and upload (`upload_question_asset`); no `_PART` suffix, never `part_number > 1`. The reorder endpoint and frontend `moveAssetPart` stay IMG-only.
3. **Filename** `<QID>_<VERSION>_<ATYPE>.md`, `VERSION ∈ EN/CH/BI/ENO/CHO`.
4. **Default format priority** `IMG > MD > DOC`, user-configurable per generation via the Format Priority widget; stored as `generation_options.format_priority` (comma list), parsed by `_parse_format_priority` (unknown tokens dropped, missing ones appended in default order).
5. **No server-side KaTeX**: `_render_math` re-emits `$...$` / `$$\n...\n$$` (HTML-escaped) inside dollarmath's `<span class="math inline">` / `<div class="math block">` so the client's KaTeX auto-render typesets it. dollarmath is configured `allow_space=True, double_inline=True`.
6. **Editor stack**: EasyMDE + marked + KaTeX + FontAwesome 4.7 (pinned) via CDN; no bundler.
7. markdown-it instance: `MarkdownIt('gfm-like', {html: False, linkify: False, typographer: True})` + `table`, `strikethrough`, `footnote_plugin`, `deflist_plugin`, `dollarmath_plugin`. Raw HTML in MD source is escaped, not parsed.

### Security model (`md_render.sanitize`)

Threat surface: admin-authored content rendered to other admins and to read-only users (dashboard preview / viewer). Authors are trusted but compromised admin accounts matter.

- **Tag allowlist** `_ALLOWED_TAGS` = bleach defaults + markdown block/inline tags + MathML elements + KaTeX-emitted `span/div/svg/path/g/use/defs/rect/line/polyline`. No `<script>`, `<iframe>`, `<object>`, etc. `strip=True`.
- **Attribute callable** `_attr_allowed(tag, name, value)`: per-tag attribute names (`_ALLOWED_ATTRS`, star attrs `class id style aria-* role title data-line*`) plus URL scheme validation:
  - `<a href>` and `<use href|xlink:href>`: `_is_safe_url` — only `http(s)://`, `mailto:`, `tel:`, `/`, `#`, `//`. Rejects `javascript:`, `vbscript:`, and ALL `data:`.
  - `<img src>`: `_is_safe_url` OR `data:image/<type>;base64,...` (the embedded-image case).
- `_BLEACH_PROTOCOLS = ['http','https','mailto','tel','data']` **must include `data`**: bleach pre-filters URL attributes against this list BEFORE the callable runs; without `data`, base64 images are silently stripped. The narrow gate is the callable, not the protocol list.
- `style` is allowed with no CSS sanitiser (acceptable for admin authors).

### Render cache (`render_file`)

- Key `asset_id → (mtime_ns, html)` in an `OrderedDict` guarded by `_CACHE_LOCK`; LRU eviction at `_CACHE_MAX_ENTRIES = 512`.
- `st_mtime_ns` is read **BEFORE** the file content so a concurrent writer cannot desync cached HTML from its mtime key.
- Returns `''` on missing file or `UnicodeDecodeError` and never caches errors, so a later fixed file renders fresh.
- Eager invalidation on save (`save_md_asset_content`), delete (`delete_question_asset` MD branch), Smart Import MD apply, and Copy/Move asset ops. The mtime check is the safety net; eager invalidation gives deterministic behaviour across processes.

### Optimistic concurrency

- `mtime_ns` is serialised as a **string** in JSON (`_mtime_ns_json`) because nanosecond epochs exceed JS `MAX_SAFE_INTEGER`; `_parse_mtime_ns` accepts string or int.
- `save` compares `expected_mtime_ns` to the current disk mtime; mismatch without `force` → 409 with `current_mtime_ns`. The fullscreen editor (`doSave(force)`) prompts to reload from disk or force-overwrite; create mode never gets 409 for mtime (only for occupied slots).
- The `content` endpoint stats before reading for the same reason (stat-after-read could pair a NEW mtime with OLD content and let the next save clobber newer disk state).

### Editor (`templates/partials/md_editor.html`)

`oqbCreateMdEditor(textareaEl, {initialContent, maxSize, minHeight, onChange})` returns `{easymde, getValue, setValue, getBytes, getMaxBytes, insertAtCursor, focus, refreshPreview, destroy}`.

- **Live preview**: `_oqbPreprocessMath()` runs KaTeX `renderToString()` over every `$...$` / `$$...$$` first, then marked parses the result. This matches server fidelity and survives blank-line-separated `$$` blocks (marked alone splits them into separate `<p>`). The inline regex tolerates inner whitespace (`$ D $` previews like `$D$`, captured trimmed — faithful to the docx because the server tightens before pandoc) and ignores currency-style `$5` via a "closing `$` not followed by a digit" guard.
- **Image insert / paste** produce `![](data:image/...;base64,...)` with **empty alt** (pandoc would otherwise render alt as a visible caption in the docx).
- **Paste isolation**: the CodeMirror paste handler calls `e.stopPropagation()` so the admin page's document-level paste handler does not ALSO upload the image as an IMG asset; `admin_questions.html` also skips its handler when the MD modal is showing or focus is inside `.EasyMDEContainer`.
- **Fullscreen reparenting**: a `MutationObserver` (`_fsObserver`) watches for EasyMDE's `.fullscreen` class; on entry the whole `.EasyMDEContainer` is moved to `<body>` (placeholder left behind) to escape the Bootstrap-modal stacking context that would confine the `position: fixed` editor; on exit it is restored. `destroy()` calls `toggleFullScreen()` first so the textarea is not reparented onto `<body>`.
- **Toolbar collision**: `.EasyMDEContainer .editor-toolbar button.table { width: auto !important; }` because Bootstrap's `.table { width: 100% }` stretches the Insert-Table button.
- **Idempotent include**: the script bails if `window.oqbCreateMdEditor` already exists (the partial is pulled in transitively by `edit_question_modal.html` and directly by `admin_md_editor.html`).
- Byte counter uses `fmtBytes` against `maxSize` (default `MD_MAX_SIZE_BYTES`).

Fullscreen page (`admin_md_editor.html`): tracks `dirty` (status bar `status-dirty`/`status-saved`/`status-error`), `lastMtime` from the last load/save, `beforeunload` prompt while dirty, "Discard unsaved changes and reload from disk?" confirm on reload; create mode POSTs `md/create`, edit mode POSTs `md/save` with `expected_mtime_ns: lastMtime, force`.

### Generation (`_append_md_via_pandoc`)

```
md file → _preprocess_md_for_pandoc → tmp/input.md →
   pandoc --from=markdown+tex_math_dollars+tex_math_double_backslash-implicit_figures --to=docx -o tmp/fragment.docx →
   docxcompose.Composer(master).append(Document(fragment))
```

- `_preprocess_md_for_pandoc`: (1) collapses blank lines INSIDE `$$...$$` blocks (`_MD_DISPLAY_MATH_RE`; pandoc's `tex_math_dollars` treats a blank line as a paragraph break so `$$\n\nformula\n\n$$` never renders); (2) calls `ai_prompts.normalize_inline_math` to tighten `$ x $` → `$x$` (`tex_math_dollars` only recognises inline math with no space just inside the dollars). AI-generated MD is also tightened at write time (`ai_tools.generate_md_slot`), and the MD prompt asks for tight `$x$`, literal/currency dollars escaped as `\$` (an unescaped `$` opens math mode; both `normalize_inline_math` and pandoc leave `\$` as a literal `$`), escaped question numbers (`8\.`), and one MC option per line. The Explain tutor prompt carries the same `\$` rule.
- `-implicit_figures`: a standalone `![](data:...)` is a plain inline image, NOT a captioned figure.
- `+tex_math_dollars+tex_math_double_backslash`: enable `$`/`$$` math and `\\` line breaks in math. Math becomes native Word OMML equations.
- `_resolve_pandoc_binary`: `shutil.which(PANDOC_PATH)`; when the configured value is the bare `pandoc` and not found, falls back to `C:\Program Files\Pandoc\pandoc.exe`, `C:\Program Files (x86)\Pandoc\pandoc.exe`, `%LOCALAPPDATA%\Pandoc\pandoc.exe`, `%APPDATA%\Pandoc\pandoc.exe` (installers update PATH only for NEW shells, so a long-running Flask process cannot see a fresh install). Shared with `batch_image_gen`.
- `md_to_docx_via_pandoc` runs pandoc with a 60 s timeout and raises `RuntimeError` on missing binary, timeout, non-zero rc, unreadable/non-UTF-8 source, or missing output; the caller falls back to an italic placeholder — generation does NOT fail as a whole.
- `add_question_content_to_doc(..., format_priority=)` picks assets by `(format_rank, version_rank, part_number)`; the MD branch calls `_append_md_via_pandoc(doc, file_path)`.

### DOC pipeline is separate

MD never touches Word COM. DOC and MD coexist as separate format branches in `add_question_content_to_doc`; see `doc-format.md`. Batch IMG for MD (`render_md_to_pages`) is the one place MD → pandoc docx → Word render happens.

## Settings & config keys

See `../core/06-system-settings.md`.

| Key | Where | Default | Meaning |
|---|---|---|---|
| `MD_MAX_SIZE_BYTES` | System Settings (group "Markdown"); `.env` | 5 MiB | Max size for upload / save / create (413 when exceeded). Passed to the editor as `maxSize`. |
| `PANDOC_PATH` | `.env` only | `pandoc` | Pandoc binary; resolved via `_resolve_pandoc_binary` fallbacks. |

Pandoc is NOT a pip package. Windows: GitHub releases or `winget install --id=JohnMacFarlane.Pandoc`; Linux `apt install pandoc`; macOS `brew install pandoc`. Python deps: `markdown-it-py`, `mdit_py_plugins` (dollarmath, footnote, deflist), `bleach`, `docxcompose`, `python-docx`. See `../core/01-runtime-and-ops.md`.

## Permissions

- Read/edit/create MD: `@admin_required` + `_require_md_admin` (subject admin or super admin).
- Rendered HTML (dashboard preview, viewer): any logged-in user with subject access.
- `MD_MAX_SIZE_BYTES` tunable: super admin via System Settings.

## Background work / SSE / threads

None specific to MD. Rendering is synchronous per request (cached). Pandoc runs as a subprocess inside the generation daemon thread and inside the batch IMG / AI SSE generators.

## Gotchas

1. Do NOT feed raw markdown to the live preview without the KaTeX preprocess step; marked + auto-render alone breaks on blank-line-separated `$$...$$`.
2. Do NOT drop `data` from `_BLEACH_PROTOCOLS` — base64 images silently disappear. The narrow per-tag gate lives in `_attr_allowed`.
3. Do NOT call `easymde.toTextArea()` while in fullscreen without `toggleFullScreen()` first — the container is reparented to `<body>`.
4. Do NOT use the `easymde.maxHeight` option in modal contexts — it collapses the side-by-side preview to ~77 px; bound heights with CSS.
5. Do NOT assume `data-preview-version` defaults; `oqbLoadMarkdownPreviewCards` reads `data-preview-version` (legacy `data-preview-lang` fallback) and admin cards set it per `(version × atype)` slot. `data-preview-format=MD` forces the resolver to show the MD even when a higher-priority IMG exists.
6. Invalidate `md_render` on EVERY disk write to an MD file (save, delete, import, copy/move).
7. Keep `viewer.html` copies of `oqbTypesetMath` / `oqbRenderMarkdownInto` in sync with `base.html`.
8. Keep reorder (`/assets/reorder`, `moveAssetPart`) IMG-only.
9. Always stat before read (content endpoint, `render_file`); stat-after-read reintroduces the stale-write race.
10. Treat `mtime_ns` as an opaque string on the client; never coerce to `Number`.
11. Padded inline math (`$ x $`) previews fine but pandoc only renders it because the server tightens it first — keep `normalize_inline_math` in `_preprocess_md_for_pandoc`.
12. Literal dollars must be written `\$` in MD source; an unescaped `$` opens math mode in both KaTeX and pandoc.
13. Image alt text must stay empty in inserted images or pandoc emits a caption line in the docx.
14. Without pandoc, MD assets render as an italic placeholder in generated docx; generation itself still succeeds.

## Related

- `doc-format.md` — the Word COM pipeline that coexists in `add_question_content_to_doc`; batch IMG renders MD via Word.
- `generator.md` — format priority widget, generation options, `add_question_content_to_doc` branches.
- `admin-panel.md` — Edit Question modal, asset upload/delete, Smart Import / Copy-Move hooks.
- `ai-tools.md` — AI MD generation and proofreading prompts (`\$` rule, `normalize_inline_math`).
- `dashboard.md` — unified preview resolver (`mode:'html'`).
- `../decisions/ADR-007-self-contained-markdown-assets.md`
- `../core/01-runtime-and-ops.md` — pandoc installation and PATH caveat.
- `../core/05-storage-and-paths.md` — `SOURCE_PATH` layout for MD files.
- `../core/06-system-settings.md` — `MD_MAX_SIZE_BYTES` registry entry.

# Toolbox hub and PDF Tool

> Self-service utility hub at `/admin/toolbox` (all logged-in users) plus the admin-only PDF Tool: stage PDFs/images, split A3 booklets, rotate/deskew/crop/adjust, reorder with multi-select, redact/highlight/mark up, Find & Mark (fuzzy text, OCR, AI region detect), and export as true-redaction PDF / flattened PDF / PNG ZIP to download or to a server folder. Staging sessions persist on disk and can be restored.

## Files

| File | Role |
|---|---|
| `app/toolbox/__init__.py` | `toolbox_bp` (`url_prefix='/admin/toolbox'`), landing `GET /`. Imports `pdf` and `markup` submodules after the blueprint exists. |
| `app/toolbox/common.py` | `pdf_source_root()` (legacy `PDF_SOURCE_PATH`), `safe_join` (delegates to hardened `storage.safe_join`), `safe_filename(name, fallback)` (80 chars, keeps spaces). |
| `app/toolbox/pdf.py` | All PDF Tool routes, staging sessions, annotation/op sanitisers, async export jobs, Find & Mark SSE, LLM detect SSE, session list/restore/delete. |
| `app/pdf_tools.py` | Processing primitives: op chain, split descriptors, raster/annotation rendering, hybrid export, compression. Shared with Batch PDF Import (`app/pdf_import.py`). |
| `app/pdf_text.py` | Word extraction (digital text layer / Tesseract OCR), orientation retry, words cache signature, cross-page fuzzy `find_matches`. |
| `app/parallel.py` | `run_parallel(app, cancel, items, worker_fn, max_workers)` used by Find & Mark and LLM detect. |
| `templates/admin_toolbox.html` | Hub grid. Markup card for everyone; PDF Tool card only when `current_user.is_super_admin or current_user.has_any_admin_access()`. |
| `templates/admin_toolbox_pdf.html` | PDF Tool single-page frontend (plain `fetch()` + client `pages[]` array). |
| `templates/partials/pdf_annotate_editor.html` | Fullscreen Konva annotation editor, included in `extra_js` after the main script (shares its globals). |
| `templates/partials/file_selector.html` | Unified file/folder picker (`window.OQBFileSelector`) used for server-PDF picks and save destinations. |

Add a new tool by creating `app/toolbox/<tool>.py`, importing it at the bottom of `__init__.py`, and adding a card to `admin_toolbox.html`. The hub is linked from the all-user **My Stuff** navbar dropdown; admin-only tools must hide their cards for non-admins.

## Tables

None. The PDF Tool is entirely file-based (staging dirs under `SYSTEM_PATH/.toolbox`). It reads `LLMConfig` rows for the AI engine (see `../core/03-data-model-and-migrations.md`) and per-user roots via `files_service.RootRegistry`.

## Routes

All PDF routes are `@login_required @admin_required` unless stated. Base prefix is `/admin/toolbox`.

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/admin/toolbox/` | `@login_required` | Hub landing (`admin_toolbox.html`). |
| GET | `/admin/toolbox/pdf` | admin | PDF Tool page. Template gets `raster_width`, `export_width`, `default_dpi`, `save_subdir`, `pdf_source_available`, `numpy_available`, `ocr_available`, `ai_enabled`, `ocr_dpi`, `session_retention_hours`. |
| POST | `/admin/toolbox/pdf/upload` | admin | Multipart `pdf` (PDF **or** image in `_IMAGE_EXTS`, images converted to a one-page PDF at ~150 DPI by `_image_file_to_pdf`) OR `server_path` (+ optional `server_root`) plus optional `token`. Blank token creates a new session. Returns `{token, srcid, filename, page_count, pages:[{index}]}`. |
| POST | `/admin/toolbox/pdf/add-pages` | admin | `{token, srcid, mode, mode1_pages_per_student?, pre_rotate, dpi, filters}` → expands to descriptors (each tagged with `dpi`; Mode 1 pages also carry `mode1_pages_per_student`), appends. Returns `{token, added, pages, total}`. |
| POST | `/admin/toolbox/pdf/reorder` | admin | `{token, order:[id]}`. Ids missing from `order` are dropped. |
| POST | `/admin/toolbox/pdf/page-ops` | admin | `{token, page_id, ops}` replace one page's op chain (sanitised by `_sanitize_ops`). |
| POST | `/admin/toolbox/pdf/page-delete` | admin | `{token, page_ids}`. |
| POST | `/admin/toolbox/pdf/duplicate` | admin | `{token, page_ids, after_id?}` clone pages (new ids, same src/page/ops/dpi) inserted after `after_id` or at the end. Returns `{pages:[clones], order:[id], total}`. Powers copy/paste. |
| POST | `/admin/toolbox/pdf/set-pages` | admin | `{token, pages:[{id, src, page, ops, mode, mode1_pages_per_student, dpi, annots}]}` **wholesale replace** of the working set; entries validated against the session's sources, bad ones dropped, annots re-sanitised. Powers Undo and the annotation editor. |
| GET | `/admin/toolbox/pdf/thumb/<token>/<page_id>.png` | admin | Working-set page render with ops + annots. `?w=` small grid thumb; `?dpi=` full export-resolution preview; `&annots=0` clean background for the Konva editor. |
| GET | `/admin/toolbox/pdf/src-thumb/<token>/<srcid>/<int:idx>.png` | admin | Raw source page thumbnail (`?w=`). |
| GET | `/admin/toolbox/pdf/sessions` | admin | `{sessions:[{token, created_at, last_saved, page_count, source_names, size_bytes}]}` newest first, only dirs still on disk (i.e. within retention). |
| POST | `/admin/toolbox/pdf/session-restore` | admin | `{token}` → `{ok, token, sources, pages}` (full session state). 404 if the dir is gone. |
| POST | `/admin/toolbox/pdf/sessions/delete` | admin | `{tokens:[...]}` (or a single string) → `{ok, deleted}`. |
| POST | `/admin/toolbox/pdf/sessions/clear-all` | admin | Delete every staging dir → `{ok, deleted}`. |
| POST | `/admin/toolbox/pdf/export-start` | admin | `{token, page_ids?, fmt:'pdf'|'zip', split_every?, filename?, output:'digital'|'image', compress:'none'|'light'|'medium'|'strong'|'size', target_mb?}` → `{ok, job_id}`. Builds bytes in a daemon thread. |
| GET | `/admin/toolbox/pdf/export-stream?job_id=` | admin | **SSE**: `heartbeat` every ~2 s, then `done` (download jobs) / `{type:'done', saved, subdir}` (save jobs) or `error`. |
| GET | `/admin/toolbox/pdf/export-download?job_id=` | admin | Serves the finished blob as an attachment and pops the job. 404 if unknown/not ready. |
| POST | `/admin/toolbox/pdf/export-save-start` | admin | Same body as export-start plus `filename` (**required**), `dest?`, `dest_root?`, `overwrite?`. Resolves `<root>/<dest>` (`dest` falls back to `TOOLBOX_SAVE_SUBDIR`), checks existence **before** building: 409 `{exists:true, filename, subdir, error}` unless `overwrite`. Returns `{ok, job_id}`; result arrives on `export-stream`. |
| POST | `/admin/toolbox/pdf/mkdir` | admin | `{path, name, dest_root?}` create a single sanitised sub-folder under a writable root → `{ok, rel_path, name}`. Mostly superseded by the unified selector's own mkdir. |
| GET | `/admin/toolbox/pdf/text-search` | admin | **SSE** Find & Mark text/OCR. Query: `token`, `page_ids` (csv), `engine` (`auto`/`digital`/`ocr`), `term` (repeated, each ≤ 300 chars), `fuzzy` (default 1), `threshold` (50–100, default 85), `case_sensitive`, `parallel`. Events: `job` → per-page `progress`/`skip` → one `results` `{results:{page_id:[{rect,term}]}, engines, pages_scanned}` → `done`. |
| GET | `/admin/toolbox/pdf/llm-detect` | admin | **SSE** vision-LLM box detection. Query: `token`, `page_ids`, `endpoint_id` (must be enabled + `supports_vision`; 0 = default `PDF_IMPORT_DEFAULT_LLM`), `instruction` (≤ 2000 chars, required), `parallel`. Events `{type:'page', page_id, boxes:[{label, box}]}` then `done`. Gated on `AI_TOOLS_ENABLED`. |
| POST | `/admin/toolbox/pdf/llm-detect-cancel` | admin | `{job_id}` → `{ok}`. Cancels both text-search and llm-detect jobs (both use `pdf_import.new_job`). |
| POST | `/admin/toolbox/pdf/discard` | admin | `{token}` delete the staging dir. |

Endpoint list for the AI engine reuses `GET /admin/questions/ai/endpoints` (admin blueprint).

## Business rules / invariants

### Page descriptor model

The working set is an ordered list of page descriptors, each pointing at one source-PDF page plus an ordered op chain:

```
{"id": <uuid12>, "src": <srcid>, "page": <0-based>, "ops": [ {op}, ... ],
 "mode": <split mode>, "mode1_pages_per_student": <int>, "dpi": <int>, "annots": [ {annot}, ... ]}
```

- `dpi` is chosen per batch in step 2 (default `TOOLBOX_DEFAULT_DPI`) and is page-size independent (zoom = dpi/72), so A4 and A3 export at the same physical quality. Export uses each page's `dpi` (fallback `default_dpi`) for raster pages and as the PDF `resolution`.
- Ops, applied in order by `pdf_tools.apply_ops`:
  - Vector-safe: `rotate` (deg 90/180/270), `crop` (box `[x1,y1,x2,y2]` fractional 0..1, y down).
  - Raster-only (`RASTER_ONLY_OPS`): `deskew`, `rotate_fine` (float deg), `brightness`/`contrast`/`sharpen` (factor), `grayscale`, `bw` (threshold 0..255).
- `build_op_chain(pre_rotate, frag_ops, filters)` = pre-rotate → split crop → image filters. `filters_to_ops` maps the UI dict `{deskew, rotate_fine, brightness, contrast, sharpen, grayscale, bw, bw_threshold}` to ops and skips no-op values.
- `rotate` degrees are clockwise (negated for PIL's CCW-positive angle) to match pypdf `/Rotate`.
- Client op lists are always re-validated server-side (`_sanitize_ops`).

### Annotations

`annots` are separate from `ops`; coordinates are **fractional 0..1 of the post-ops page** (y down). Sanitised by `_sanitize_annots` in `app/toolbox/pdf.py`; caps: `_ANNOT_MAX_PER_PAGE = 500`, ink `_ANNOT_MAX_INK_POINTS = 2000`, text `_ANNOT_MAX_TEXT_LEN = 300`, image data URL `_ANNOT_MAX_IMAGE_B64 = 4 MB`. Colours must match `_HEX_COLOR_RE` (`#rrggbb`).

| Kind | Shape | Semantics |
|---|---|---|
| `redact` | `{"id","kind":"redact","rect":[x1,y1,x2,y2],"color"}` (default `#000000`) | Opaque box; content truly removed on digital export. |
| `erase` | `{"kind":"erase","rect":[...]}` | "Remove": like redact but `color` forced `#ffffff`. Editor shows a dashed grey outline (`AE_ERASE_PROPS`, editor-only). |
| `highlight` | `{"kind":"highlight","rect","color","opacity"}` (defaults `#ffff00`, 0.4) | Alpha overlay. |
| `text` | `{"kind":"text","pos":[x,y],"text","size","color","font"}` | `size` = fraction of page height (0.004–0.25, default 0.025); `text` may contain newlines (drawn line-by-line at 1.2em in Konva/PIL/fitz alike); `font` ∈ `sans|serif|mono` (`pdf_tools.ANNOT_FONT_FILES` raster / `ANNOT_FONT_PDF` base-14 digital). Default colour `#d00000`. |
| `ink` | `{"kind":"ink","points":[[x,y],...],"color","width","opacity"}` | Freehand polyline; width 0.0005–0.05 (default 0.004), default colour `#0000ff`. |
| `image` | `{"kind":"image","rect":[...],"data":"data:image/(png|jpeg|webp);base64,..."}` | Picture stamp (signature/logo); validated by `_DATA_URL_RE`. Raster: PIL alpha paste; digital: `pg.insert_image(rect, rotate=pg.rotation)`. |

Optional `pending: true` marks an unreviewed search/AI result: rendered with a dashed orange outline and **excluded from export** (`_drop_pending_annots`) until accepted.

Raster rendering: `pdf_tools.apply_annotations(img, annots)` (Pillow RGBA composite) is called by `render_page_image(..., annots=)`, so thumbs/previews show marks live. The Konva editor requests its background with `?annots=0`.

### Hybrid export (`pdf_tools.export_pages`)

- Per page, `is_vector_safe(ops)` is true when there is no raster-only op AND no crop+rotate combo. Vector-safe pages are cloned via `pypdf` (`cropbox`/`mediabox` from the fractional box + `/Rotate`). A crop on a page with inherent `/Rotate` falls back to raster (handled in `_add_page`). Everything else rasterises: `render_page_image(..., dpi=)` → Pillow → one-page PDF → `add_page`.
- Preview thumbnails ALWAYS rasterise via the same `render_page_image` but with a small fixed `width_px`. `rasterize_page`/`render_page_image` accept EITHER `dpi` (export/processing) OR `width_px` (thumbnails).
- `pypdf` missing → all-raster PDF fallback (logged). `fmt='zip'` → ZIP of PNGs; `split_every=N` → ZIP of N-page PDFs.
- **Output modes** (`output=`): `digital` (default) keeps pages vector; an annotated vector-safe page is built by `_fitz_annotated_page_bytes` (PyMuPDF `add_redact_annot` + `apply_redactions` for both `redact` and `erase` (white fill) = content truly removed; highlights/text/ink drawn as vector overlays mapped into unrotated page space). `image` force-rasterises every page so redaction is pixel-level and unrecoverable.
- **Size control:** after `apply_redactions` a scan's page image is re-encoded raw, so `_fitz_annotated_page_bytes` recompresses with `rewrite_images(quality=85)` and saves `tobytes(deflate=True, garbage=3)`; without this a 1 MB scan ballooned to hundreds of MB. **Optional compression** (`compress=`, PDF outputs only, applied per part for split ZIPs): `{'preset': 'light'|'medium'|'strong'}` (one `rewrite_images` pass via `COMPRESS_PRESETS` — light recompress-only, medium ~150 DPI, strong ~100 DPI) or `{'target_bytes': n}` (`compress_pdf_bytes` walks `_COMPRESS_SIZE_LADDER` from the original bytes until the result fits; best effort, never larger than input). `_parse_compress` maps the request fields `compress`/`target_mb` to this dict.
- Export runs **asynchronously**: `export-start`/`export-save-start` spawn a daemon thread and return a `job_id`; the client opens `export-stream` (SSE heartbeats keep proxies alive) and then hits `export-download` or reads `saved` from the `done` event. Jobs live in the in-memory `_EXPORT_JOBS` dict and are purged after 600 s by `_cleanup_export_jobs`.

### A3 split / reorder (`split_descriptors(num_pages, mode)`)

Half boxes `LEFT_HALF=[0,0,.5,1]` / `RIGHT_HALF=[.5,0,1,1]`. `SPLIT_MODES = ('none','simple','mode1','mode2')`; `mode1_pages_per_student` defaults to 4 and is clamped to 1..200 (`pdf_tools.mode1_pages_per_student`).

- `simple`: each page → `[left, right]`.
- `mode1` (folded individual copies/booklets): use `ceil(pages_per_student/2)` A3 sides per student, order the folded booklet halves in reading order, then keep only `pages_per_student` outputs. Default 4 preserves the legacy order `p(i)_R, p(i+1)_L, p(i+1)_R, p(i)_L`; `7` keeps pages 1–7 and drops the 8th padding slot. Pair export `split_every` with the same page count for per-student ZIPs.
- `mode2` (destapled booklet): `total=2n`; for `i in range(n)`: even `i` → `ordered[total-i-1]=L_i, ordered[i]=R_i`; odd `i` → `ordered[i]=L_i, ordered[total-i-1]=R_i`. Assumes an even page count.

Reorder happens on the rotated page (pre-rotate is applied before the crop in the op chain).

### Staging sessions

- Root: `SYSTEM_PATH/.toolbox/<token>/` (falls back to `OUTPUT_PATH/.toolbox` when `SYSTEM_PATH` is unset). Contents: `session.json` (`{created_at, last_saved, sources:{srcid:{filename,page_count}}, pages:[descriptor]}`), `sources/<srcid>.pdf`, and `words/<page_id>.json` (Find & Mark word cache).
- Token regex `_TOKEN_RE = ^[0-9a-f]{8,40}$`, srcid `_ID_RE = ^[0-9a-f]{6,40}$`, validated before any path join.
- `_save_session` stamps `last_saved` (UTC ISO) on **every** write and writes atomically (`.tmp` + `os.replace`).
- **Retention:** `_cleanup_old()` runs on every new session (`_new_session`) and deletes staging dirs whose directory mtime is older than `TOOLBOX_SESSION_RETENTION_HOURS` (System Setting, default 48 h, min 1, max 720). There is no separate background purge; a session that nobody touches survives until the next upload creates a session.
- **Restore session** UI (`#restoreSessionBtn` → `#restoreSessionModal`): lists `GET /pdf/sessions` (source names, page count, size, `Saved <last_saved>`, token; the current token is badged "current"), **Restore** (`doRestoreSession`: confirms, POSTs `session-restore`, then resets `TOKEN`, `pages`, `selected`, `undoStack`, `clipboard`, `sources` and rebuilds the UI), per-row **delete** (`doDeleteSession`; if it was the current session the client state is cleared), and **clear-all** (`doClearAllSessions`). Sessions are not tied to a user: any admin can list/restore any session.
- Server-PDF picks + save destinations resolve through the unified file selector + per-user root registry via `_resolve_root_base(root_id, require_write)`; blank `root_id` falls back to the legacy `PDF_SOURCE_PATH`. See `../core/05-storage-and-paths.md`.

### Find & Mark (text / OCR / AI)

- Word extraction (the slow OCR step) fans across `min(TOOLBOX_OCR_WORKERS, cpu_count, npages)` workers via `app.parallel.run_parallel` when `parallel=1`; page events arrive in completion order. Cross-page `pdf_text.find_matches` runs ONCE, in original page order, AFTER all pages are scanned so phrases wrapping across lines or page boundaries still match (boxes emitted per page/line).
- Words cached under `words/<page_id>.json` keyed by a versioned engine/ops/dpi/auto-orient signature (`_words_sig`, `_WORDS_SIG_VERSION`).
- `auto` = digital when `digital_mappable(ops)` and a text layer exists, else OCR. Geometry-warping ops (`pdf_text.GEOMETRY_BREAKERS = {deskew, rotate_fine}`) block the digital engine for that page.
- OCR is **rotation-aware**: a page whose upright pass reads poorly (fewer than `_OCR_ORIENT_MIN_WORDS = 8` confident words at `_OCR_CONF_MIN = 60`, OR good/total ratio below `_OCR_GOOD_RATIO = 0.35` — a sideways scan still emits lots of low-confidence gibberish, so raw word COUNT is not the signal) is re-OCR'd at 90/180/270°; the orientation with the most confident words wins (`_good_word_count`; Tesseract OSD `_detect_osd_rotation` breaks ties). Gated by `TOOLBOX_OCR_AUTO_ORIENT`.
- `extract_words_ocr`/`get_page_words` return `(words, display_rot)`: words stay in **reading-upright** space (tight boxes; line grouping + margins act on the correct axes) and the route rotates only the final match rects back onto the displayed page via `rotate_rect_frac(rect, display_rot)`.
- `find_matches` slides a fuzzy window (`rapidfuzz`; exact fallback when missing) over the concatenated word stream. Match boxes use an asymmetric margin — horizontal ~25% of median word height (bridges inter-word gaps), vertical ~6% (hugs the line); `_group_lines` merges words into a line only when vertical centres are within 45% of line height.
- LLM detect renders each page with its ops, calls `llm_client.chat` with `ai_prompts.build_pdf_generic_system/user_text(instruction)`, parses via `parse_generic_boxes`, honours `PDF_IMPORT_COORD_ORDER` and `LLM_IMAGE_MAX_DIM`. `parallel=1` is only honoured for cloud endpoints with `max_concurrency>1` (same rule as `_ai_parallel`).
- Results become **pending** annots reviewed in grid/editor; Accept/Discard controls in the modal footer and editor.

### Frontend (`templates/admin_toolbox_pdf.html`)

Client `pages[]` entries are `{id, src, page, mode, dpi, baseOps, edit:{type→op}, nonce, annots:[]}`; `fullOps(p)` = `baseOps` + per-page `edit` ops in a fixed order (crop last); `nonce` cache-busts the thumb after edits.

- **Layout**: step 1 upload; step 2 "Process & add" (active-source page strip, Resolution (DPI), rotate/split, optional deskew only); step 3 preview/assemble + operations toolbar. Desktop (≥992px) is app-like: `.tb-left-col`/`.tb-right-col` sticky below the navbar, `.tb-right-card` is `height: calc(100vh - 76px)` with `#previewCanvas` as the internal scroll container. Rubber band tracks hits in canvas **content space** (client coords + scroll offsets) so off-screen pages stay selected mid-drag, and auto-scrolls near the top/bottom edge (48 px zone, rAF loop).
- **Selection model**: `selected` Set + `lastClickedId`. Click = single, Ctrl/Cmd = toggle, Shift = range (`selectRange`), rubber-band on `#previewCanvas` background (`initRubberBand`; Shift/Ctrl additive). **Select mode** (`setSelectMode`, `#selectModeBtn` or ~500 ms long-press via `beginLongPress`) makes every tap toggle, disables SortableJS, Esc exits.
- **Shared operations toolbar**: built once by `buildToolbars()` from `imageOpsHTML()` into BOTH `#mainImageOps` (`.tb-needs-sel` auto-disabled with no selection) and `#modalImageOps`. Ops resolve targets via `currentTargets()` = `[currentPreviewPage]` when the modal is open, else `selectedPagesInOrder()`. Rotate-L/R, **Adjust** dropdown (`[data-adjustmenu]`: Brightness/Contrast/Sharpen → `applyAdjust`, Grayscale/Deskew → `applyCheckToggle`, B&W + threshold → `applyBw`, Reset → `resetAdjusts`), **Mark up** (`openAnnotEditor`), **Find & Mark**, **Reset**. `saveMany` persists the whole working set in ONE atomic `/set-pages` call — never per-page `/page-ops` (concurrent writes raced on `session.json`). Keyboard (`initKeyboard`, ignored while typing or while `tbAnnotEditorActive`): Del, Ctrl+A/C/X/V, Ctrl+Z, Esc; arrows page prev/next when the modal is open.
- **Reorder ops** (`[data-reorder]` → `reorderOp(kind)`, `arrangeIds(ids, kind)`): `interleave`, `deinterleave`, `alt_interleave` (2nd half reversed, for reversed duplex backs), `alt_deinterleave`, `reverse`. Result placed back into the same positions, then `/reorder`.
- **Undo**: `undoStack` of `snapshotPages()`; `pushUndo()` BEFORE each mutation (add, edit, delete, paste, reorder-op, actual drag move). `doUndo()` restores `pages` (bumps every `nonce`), intersects `selected`, mirrors to the server via `/set-pages` (`pushServerPages`). `#undoBtn` enabled iff the stack is non-empty.
- **Clipboard**: `copySel`/`cutSel` set `clipboard={mode,ids}`; paste → copy uses `/duplicate`, cut moves locally + `/reorder`. Cut pages get `.tb-cut`.
- **Drag reorder**: SortableJS on `.tb-page-card` (`forceFallback`). Multi-selection collapses other selected cards (`.tb-drag-collapsing`) and renders a stacked drag image (`.tb-multi-drag` + `.tb-drag-badge`); `onEnd` moves the whole selection as a contiguous block; persists + pushes undo only if the order changed.
- **Enlarged preview** (`#previewModal`): double-click → `openPreview`. Quick/Full-res toggle (`setPreviewRes`, persisted as `localStorage.oqb_tbPreviewRes`, thumb `?w=1100`, full `/thumb?dpi=`), prev/next + tap zones (`#modalTapPrev/Next`), shared toolbar acts on `currentPreviewPage`.
- **Annotation editor** (`pdf_annotate_editor.html`, `openAnnotEditor(pageId)`): tools select/redact/erase/highlight/text/pen (V/R/E/H/T/P), palette + custom colour (hidden for erase), opacity + pen-width sliders, Transformer move/resize, **Copy to → all/selected pages**, per-page/global **Accept** + global **Discard** for pending marks, prev/next page, Quick/Full-res background (`?annots=0`), **Image** stamp (`#aeImageBtn` → `aeInsertImageFile`: downscale ≤1400 px, PNG keeps alpha else JPEG 0.85, ≤4 MB data URL, placed at viewport centre at 40% page width; `aeStampCache`). Inline text tool: click page → `#aeTextArea` overlay (Enter newline, Ctrl+Enter/blur commit, Esc cancel); `#aeFont` + `#aeFontSize` (frac = pt/842, `AE_PT`); blur skips commit when `relatedTarget` is inside `#aeTextWrap`/`#aeColors`/`#aeCustomColor`. Per-page undo/redo (`aeHistory`, `aeSnap()`, Ctrl+Z/Y) separate from the grid's global undo. Zoom/pan `aeSetZoom` 1–8× (wheel, pinch, +/−/0, % = fit; pan = right-drag/middle-drag/space-drag/two-finger); all drawing math uses `aeStage.getRelativePointerPosition()`. On close → one `pushUndo` + atomic `/set-pages` + refresh of touched cards. Grid cards show mark-count + pending-count badges (`editBadges`).
- **Find & Mark modal** (`#findMarkModal`): term rows (text + highlight/redact/remove kind + colour), scope (selected/all), engine Auto/Digital/OCR/AI (OCR disabled when Tesseract missing, AI when `AI_TOOLS_ENABLED` off), fuzzy + threshold, case toggle, `#fmParallel` (hidden for the instant `digital` engine). AI mode toggle `fmAiMode`: *Find the search terms* vs *Custom instruction* (`opts.customStyle`, never the first term's style) + endpoint picker. Text → `runFindMarkText` (EventSource on `/text-search`), AI → `runFindMarkAI` (`/llm-detect`); both stream progress with a Stop button. `fmApplyResults` = one undo step; footer **Accept all** / **Discard pending** (`fmSyncPendingButtons`).
- **Grid size slider** `#gridSizeRange` → `--tb-card-w`; persisted `localStorage.oqb_tbGridW` (80–340 px, default 120).
- **Pickers**: `pickServerBtn` → `OQBFileSelector.open({mode:'file', extensions:['.pdf']})` → `uploadSource(null, rel_path, root_id)`; `openSaveDest` → `OQBFileSelector.open({mode:'folder'})` → `saveDest`/`saveDestRoot` → `doSaveServer(false)`.
- **Export UI**: file name **required** (`updateExportButtons`; defaults to the first source's stem); partial selection triggers `resolveExportScope()` → `#exportScopeModal` ("Selected only (N)" / "All pages"; ids stick in `saveScopeIds` for overwrite retries). `#exportCompress` + `#exportTargetMb` feed `compress`/`target_mb` (hidden for ZIP-of-PNGs). 409 on save → `#overwriteModal` (Overwrite re-POSTs with `overwrite:true`; Rename focuses the name field). "Separate file every N pages" shows only when the scope has Mode-1 pages with one consistent `mode1_pages_per_student`.
- **Drag & drop import**: `initDropZone` on `#previewCanvas` — PDF or image files upload via `uploadSource` and add all pages untouched via `addPagesWithDefaults` (mode `none`, `DEFAULT_DPI`), skipping steps 1–2.
- **Crop tool**: `data-op="crop"` → `openCropModal(currentTargets())` (`#cropModal`); Apply writes `p.edit.crop = {type:'crop', box}` to every target. `fullOps` puts `crop` LAST; re-crop composes the new box into the previous one; **Remove crop** deletes `edit.crop`.

### Batch PDF Import reuse

`app/pdf_import.py` `rasterize_pdf`/`stage` call `pdf_tools` (`split_descriptors`, `build_op_chain`, `rasterize_page`, `apply_ops`) and accept `pre_rotate`/`split_mode`/`filters` (stored in `meta.json`); the legacy `deskew` flag is merged into `filters`. Splitting there renumbers staged PNGs contiguously in final reading order. See `pdf-import.md`.

## Settings & config keys

See `../core/06-system-settings.md` for the registry mechanics.

| Key | Where | Default | Meaning |
|---|---|---|---|
| `TOOLBOX_DEFAULT_DPI` | System Settings (Toolbox) | 200 (72–600) | Default processing/export DPI pre-selected in step 2. |
| `TOOLBOX_OCR_DPI` | System Settings (Toolbox) | 300 (72–600) | Raster DPI for Tesseract OCR in Find & Mark. |
| `TOOLBOX_OCR_WORKERS` | System Settings (Toolbox) | 4 (1–32) | Parallel Find & Mark workers; capped by `cpu_count` at runtime. |
| `TOOLBOX_OCR_AUTO_ORIENT` | System Settings (Toolbox) | on | Retry OCR at 90/180/270° on sparse/rotated pages. |
| `TOOLBOX_SESSION_RETENTION_HOURS` | System Settings (Toolbox) | 48 (1–720) | Staging-session purge window; also the "Restore session" horizon. |
| `TOOLBOX_RASTER_WIDTH` | `.env` only | 1700 | Preview raster width passed to the template. |
| `TOOLBOX_EXPORT_WIDTH` | `.env` only | 2200 | Legacy raster width fallback. |
| `TOOLBOX_SAVE_SUBDIR` | `.env` only | `Saved` | Default save sub-folder when `dest` is blank. |
| `TESSERACT_CMD` | `.env` only | `''` | Path to `tesseract.exe`; `pdf_text.resolve_tesseract` auto-detects common install dirs + PATH when unset. |
| `PDF_SOURCE_PATH` | `.env` only | — | Legacy fallback root when the selector sends no `root_id`. |
| `AI_TOOLS_ENABLED`, `PDF_IMPORT_DEFAULT_LLM`, `PDF_IMPORT_COORD_ORDER`, `LLM_IMAGE_MAX_DIM` | shared | — | Gate/config for the AI engine (see `ai-tools.md`). |

Dependencies: `pypdf>=4.0` (vector export; degrades to raster if absent), `rapidfuzz` (fuzzy match; exact fallback), `pytesseract` + local Tesseract (UI option disabled when missing), NumPy for deskew (control disabled when missing). Heavy libs (`fitz`/`PIL`/`pypdf`) are imported lazily so importing `pdf_tools` never fails. See `../core/01-runtime-and-ops.md`.

## Permissions

- Hub `GET /admin/toolbox/`: `@login_required` only. The template hides the PDF Tool card unless `is_super_admin or has_any_admin_access()`.
- Every `/pdf` and `/pdf/*` route: `@login_required @admin_required` (any subject admin or super admin).
- Server file access is further scoped by `RootRegistry(current_user)` (`_resolve_root_base`): unreadable roots → "You do not have access to that location."; read-only roots on save → "That location is read-only."
- Sessions are global (not per-user): any admin can list/restore/delete any token.

## Background work / SSE / threads

- **SSE**: `GET /pdf/text-search`, `GET /pdf/llm-detect`, `GET /pdf/export-stream`. All emit `data: {json}\n\n` with `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- **Threads**: export/save jobs run in daemon threads (`_EXPORT_JOBS`, 600 s TTL); Find & Mark and LLM detect fan out via `app.parallel.run_parallel` with a cancellable job from `pdf_import.new_job` (`POST /pdf/llm-detect-cancel`).
- Word COM is **not** involved anywhere in the PDF Tool.

## Gotchas

1. Vector crop on a page that already carries `/Rotate` is ambiguous in mediabox space → rasterise (handled in `_add_page`). Crop+rotate combos also fall back to raster.
2. Annot coords live in **post-ops** space: rotating/cropping a page AFTER marking it does NOT remap its annots. The digital-export mapper (`_fitz_annotated_page_bytes`) inverts rotate/crop into unrotated source space; `deskew`/`rotate_fine` force that page to raster and block the `digital` text-search engine.
3. Persist the working set with ONE `/set-pages` call, never a `Promise.all` of `/page-ops` — concurrent writers race on `session.json`.
4. `rotate` is clockwise; PIL's positive angle is CCW, so `apply_ops` negates it.
5. Pending annots are stripped before every export path; if a user forgets to Accept, marks silently vanish from the output by design.
6. Without `rewrite_images` + `deflate`, a redacted scan explodes in size — keep the recompression in `_fitz_annotated_page_bytes`.
7. Retention is enforced only when a NEW session is created; `last_saved` is written on every save but purge uses the directory mtime. The Restore list shows whatever is still on disk.
8. Session tokens/srcids are validated by regex before any filesystem join; keep `_TOKEN_RE`/`_ID_RE` checks on any new route.
9. Deleting the current session from the Restore modal wipes the client state; the next upload creates a new token.
10. Filename is required for save-to-server and there is no silent auto-suffix — the 409 → Overwrite/Rename flow is the only conflict path.
11. `_sanitize_annots` caps are 300 text chars and 2000 ink points (not larger). Client-side limits must not exceed these or marks will be silently truncated/dropped.
12. Preview thumbs use `width_px`; export uses `dpi`. Do not pass both to `rasterize_page`.

## Related

- `markup.md` — the other Toolbox tool (all-user Markup PWA).
- `pdf-import.md` — Batch PDF Import shares `pdf_tools` primitives and `pdf_import.new_job` cancellation.
- `ai-tools.md` — LLM endpoints, `AI_TOOLS_ENABLED`, `ai_prompts`.
- `admin-panel.md` — File Browser API (`files_bp`), unified file selector and `RootRegistry`.
- `../core/01-runtime-and-ops.md` — Tesseract / PyMuPDF / pypdf prerequisites.
- `../core/05-storage-and-paths.md` — `SYSTEM_PATH/.toolbox`, `safe_join`, per-user roots.
- `../core/06-system-settings.md` — registry and hot reload.

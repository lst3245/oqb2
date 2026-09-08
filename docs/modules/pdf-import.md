# PDF Batch Import

> Rasterise exam question / solution PDFs, detect per-question regions with a vision LLM (optionally CV-assisted), review and edit bounding boxes, then crop and create `QUE` / `SOL` IMG assets — or, in Generic Extraction mode, download labelled crops as a ZIP.

The tool lives under Admin Operations (after Question Ingestion). `app/pdf_import.py` owns staging, rasterisation, detection, cropping and commit; the atomic disk + DB write reuses `app/batch_image_gen.py`. One page image goes to the LLM per call, which keeps small local models (e.g. `qwen3vl4b`) inside their context window.

## Files

| File | Role |
|---|---|
| `app/pdf_import.py` | `parse_paper_prefix`, `guess_paper_name`, cancel registry, staging helpers, `rasterize_pdf`, `stage`, `DETECT_METHODS`, `detect_page`, `detect_parts`, `crop_page`, `pages_to_pdf_bytes`, `export_zip_bytes`, `coerce_plan_item`, `sanitize_plan`, `plan_item_label`, `map_crop_box_to_page`, `full_width_child_box`, `whole_source_crops`, `iter_detect`, `iter_split_detect`, `detect_single_page`, `_stitch_continuations`, `_group_plan`, `iter_commit` |
| `app/pdf_layout.py` | Classical-CV helpers (NumPy): `deskew_image`, `refine_box`, `segment_page`, `load_gray`, `numpy_available` |
| `app/pdf_tools.py` | Shared Toolbox primitives used by `rasterize_pdf`: `split_descriptors`, `build_op_chain`, `rasterize_page`, `apply_ops`, `mode1_pages_per_student` |
| `app/ai_prompts.py` | `PDF_QUE_BOX_SYSTEM`, `PDF_SOL_BOX_SYSTEM`, `PDF_BOX_JSON_CONTRACT`, `PDF_BOX_USER`, `build_pdf_box_system` / `build_pdf_box_user_text`, `pdf_box_order_vars`, `parse_question_boxes`; `PDF_PART_BOX_*` / `PDF_PART_SOL_BOX_SYSTEM`, `build_pdf_part_system` / `build_pdf_part_user_text`, `parse_part_boxes`; `PDF_GENERIC_BOX_*`, `build_pdf_generic_system` / `build_pdf_generic_user_text`, `parse_generic_boxes`; `PDF_ANCHOR_*`, `build_pdf_anchor_*`, `parse_question_anchors`; `PDF_PAPER_NAME_*`, `build_pdf_paper_name_*`, `parse_paper_name`. See [ai-prompts.md](ai-prompts.md) |
| `app/llm_client.py` | `prepare_image`, `sent_image_size`, `chat`, `resolve_default_endpoint` |
| `app/parallel.py` | `run_parallel` for cloud-endpoint parallel detection |
| `app/batch_image_gen.py` | `replace_img_assets`, `slot_has_img` (atomic IMG write + DOC-thumbnail lifecycle) |
| `app/admin.py` | Routes `pdf_import_*`; helpers `_pdf_vision_endpoints`, `_pdf_sse_error`, `_pdf_source_root`, `_pdf_default_endpoint`, `_ServerPDF`, `_resolve_server_pdf`, `_pdf_load_token_meta` |
| `app/files_service.py` | `RootRegistry` used by `_resolve_server_pdf` for root-aware server picks |
| `templates/admin_pdf_import.html` | 3-step wizard UI including `startSplitDetect`, `shouldAutoSplit`, Re-split / Unsplit, label-aware renumber |
| `templates/partials/file_selector.html` | Unified server-side PDF picker (backed by `/files/api/list`) |
| `app/settings.py` / `app/config.py` | REGISTRY group "PDF Import" + `PDF_IMPORT_DEFAULT_LLM` (group "AI Tools"); `PDF_SOURCE_PATH` |

## Tables

Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Table / model | Touched how |
|---|---|
| `questions` (`Question`) | `iter_commit` find-or-creates via `hierarchy.ensure_question` with token `5` / `5a` / `23-24`. `q_type` via `ingestor.determine_question_type`. `section` / `level` stay null (tagged later) |
| `question_assets` (`QuestionAsset`) | `replace_img_assets(question, atype, version, imgs, stitch=False, source_path)` deletes existing IMG rows for the slot and writes new `file_format='IMG'` parts (one per plan part). Split **roots** also get `asset_type=WHOLE` from pass-1 `source_box` pages (`whole_source_crops`) before the tight stem QUE. |
| `llm_configs` (`LLMConfig`) | Read-only: vision-capable enabled endpoints for the dropdowns, `kind` / `max_concurrency` for the parallel gate |
| `subjects` (`Subject`) | Existence check on stage; `split_parts_default` returned as `split_parts_default` so the wizard checkbox starts ticked for that subject |

No tables are written in Generic Extraction mode.

## Routes

All `@login_required @admin_required`, prefix `/admin`. Token-bearing routes call `_pdf_load_token_meta(token)`: loads `meta.json` (invalid / expired → error), and for exam sessions requires the parsed subject to be in `get_user_admin_subjects()` unless super-admin. Generic sessions have no subject and are accessible to any admin.

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/admin/pdf-import` | admin | Page. Passes `subjects`, `versions`, `endpoints` (vision-capable, enabled, with `kind` / `max_concurrency`), `ai_enabled`, `raster_width`, `deskew_default`, `trim_white_default`, `uniform_width_default`, `default_method`, `default_endpoint_id`, `pdf_source_path`, `pdf_source_available` |
| POST | `/admin/pdf-import/stage` | admin (+ subject admin for exam) | Multipart. `mode` (`exam`\|`generic`, default exam). Exam: `paper` (prefix `SUBJ_SOURCE_YEAR_PAPER`, validated by `parse_paper_prefix`; subject must exist; 403 if not subject admin), optional `que_version` / `sol_version` (legacy `version` applies to both; invalid → ENO / EN). Files: `que_pdf` / `sol_pdf` uploads, or `que_server_path` (+ `que_server_root`) / `sol_server_path` (+ `sol_server_root`); at least one side required; an upload wins over a server pick for the same side. Pre-processing: `deskew` (default on), `pre_rotate` (90/180/270), `split_mode` (`none`\|`simple`\|`mode1`\|`mode2`), `mode1_pages_per_student`, `f_deskew`, `f_brightness`, `f_contrast`, `f_sharpen`, `f_grayscale`, `f_bw`, `f_bw_threshold` (default 160). Returns `{token, mode, custom_prompt, instruction, subject, source, year, paper, que_version, sol_version, version, deskew, split_parts_default, que: {filename, pages: [{index, width, height}]}, sol: {...}}`. Does not require a vision endpoint or `AI_TOOLS_ENABLED` |
| GET | `/admin/pdf-import/source-list?path=` | admin | Legacy directory listing under `PDF_SOURCE_PATH` (`{configured, root, current_path, dirs, files}`). Still routed but the page now uses the unified file selector |
| POST | `/admin/pdf-import/guess-paper` | admin | Multipart `pdf` upload or `server_path` (+ `server_root`). 400 when AI Tools are off or no vision endpoint. Rasterises page 1, calls `guess_paper_name` with the default endpoint and the caller's admin subject codes → `{paper, filename}` (`paper` null when the reply fails `PREFIX_PATTERN`). The UI calls it on file pick only when Paper name is empty |
| GET | `/admin/pdf-import/page/<token>/<kind>/<int:page>.png` | admin (token-scoped) | Serve a staged page PNG (`kind` ∈ `que`\|`sol`); `Cache-Control: private, max-age=600` |
| GET | `/admin/pdf-import/detect` | admin (token-scoped) | SSE. Params `token`, `endpoint_id` (≤ 0 → `_pdf_default_endpoint`), `method` (`llm`\|`refine`\|`segment`), `parallel` (1/0), `debug` (1/0), `instruction` (≤ 2000 chars), `custom_prompt` (1/0). Generic mode requires `instruction` and stores it in `meta`; exam + `custom_prompt=1` requires `instruction` and sets `meta.custom_prompt = True`; otherwise clears both. Streams `iter_detect` and accumulates `plan.json`. Errors before streaming are returned via `_pdf_sse_error` (one `error` + `done` event) |
| GET | `/admin/pdf-import/split-detect` | admin (token-scoped) | SSE pass-2. Params `token`, `kind` (`que`\|`sol`\|`both`), `endpoint_id`, `debug`, `parallel` (cloud only), optional `labels` csv to re-split those parents only (a nested stem such as `4d` can be re-split by its own label). Exam sessions only. Streams `iter_split_detect`; `done` carries the replaced `plan`. Generic → SSE error |
| GET | `/admin/pdf-import/agent` | admin (token-scoped) | Spectator SSE over a background agent job (`restart`, `attach` query flags). See [pdf-agent.md](pdf-agent.md) |
| GET | `/admin/pdf-import/attention` | admin (token-scoped) | JSON attention list + outline summary of the last agent run. See [pdf-agent.md](pdf-agent.md) |
| POST | `/admin/pdf-import/redo-page` | admin (token-scoped) | `{token, kind, index, endpoint_id?, method?, debug?, instruction?, custom_prompt?}`. Overrides are persisted to `meta` for the re-run; blank `instruction` falls back to `meta.instruction`. → `{boxes, raw?}` (`raw` clipped to 6000 chars) |
| POST | `/admin/pdf-import/plan` | admin (token-scoped) | `{token, plan: {que: [...], sol: [...]}}`. `sanitize_plan` keeps `{page, qno, label, box, source_label?, source_page?, source_box?, depends_prev?}` and **recomputes `role`** from the label set (`apply_derived_roles`; a posted `role` is ignored). Exam labels are QNO tokens (`5`, `5a`, `23-24`); generic keeps free-text labels. Overwrites `plan.json` → `{success, counts: {que, sol}}` |
| GET | `/admin/pdf-import/commit` | admin (token-scoped) | SSE. Params `token`, `paper?` (re-parsed; subject must match staging), `que_version?`, `sol_version?` (fall back to meta), `overwrite` (1/0), `trim_white` (1/0; omitted → `PDF_IMPORT_TRIM_WHITE_DEFAULT`). Generic sessions → SSE error. Persists the latest paper / version meta, then streams `iter_commit` over `plan.json` |
| POST | `/admin/pdf-import/cancel` | admin | `{job_id}` → `{success, known}` (detect and commit share the `_PDF_CANCEL` registry) |
| POST | `/admin/pdf-import/discard` | admin (token-scoped) | Delete the staging dir → `{success, removed}` (idempotent; missing token → `removed: false`) |
| GET | `/admin/pdf-import/processed/<token>/<kind>.pdf` | admin (token-scoped) | Re-assemble staged (deskewed / processed) PNGs into a PDF (`pages_to_pdf_bytes`). Name `<SUBJ>_<SRC>_<YEAR>_<PAPER>_<kind>_deskewed|_processed.pdf` or `extraction_*.pdf` for generic |
| POST | `/admin/pdf-import/export-zip` | admin (token-scoped) | `{token, kind (default que), items: [{page, label, box}]}` → ZIP of PNG crops (`export_zip_bytes`, `trim_white=False`, `pad_frac = PDF_IMPORT_CROP_PAD_PCT/100`). Generic Extraction's "Download all as ZIP"; no DB writes |

### SSE contract

`data: {json}\n\n`; `type` ∈ `job` (first; carries `job_id`) / `info` / `success` / `skip` / `error` / `done`, each with `message` and usually `current` / `total`.

- `detect`: each page emits `success` (or `error`) with `page: {kind, index, boxes, raw?}`; `done` carries `stats: {pages, questions}` and the full `plan`. A pre-flight failure (no pages, method needs NumPy) yields `error` then `done`.
- `split-detect`: per parent crop `success` / `skip` / `error`; `done` carries the rewritten `plan`.
- `commit`: per group `success` / `skip` / `error`; `done` carries `stats: {questions_created, assets_written, skipped, errors}`.
- Cancellation: per-job `threading.Event` in `_PDF_CANCEL` (guarded by `_PDF_LOCK`), checked between pages / questions; a cancelled run still emits `done` with partial stats and plan.

## Business rules / invariants

### Flow (3-step UI)

```
Step 1 stage (upload/pick + rasterise) -> Step 2 [optional detect SSE] + bbox editor -> Step 3 commit (SSE) or ZIP
```

- **Step 1 — Setup**: Task toggle (`exam` / `generic`), Paper name, QUE / SOL PDFs (upload or server pick via the unified selector), "Auto-deskew scans" + collapsible "Pre-process scans" (`pre_rotate`, `split_mode`, `mode1_pages_per_student`, `f_*`), **Load PDF** (`POST /stage`). Exam staging defaults versions to ENO / EN in `meta`; the real import versions come from Step 3.
- **Step 2 — Bounding boxes**: endpoint, method, custom prompt + instruction, **Split questions into parts** (exam; default from `split_parts_default`), uniform width, debug. Detection is optional — after Load PDF the page skeleton appears immediately for manual Add box / drag / resize. When detection runs, page cards render up-front with a "detecting…" badge and fill in as each `page` event arrives (`fillPageCard`). If Split is ticked, pass 1 finishing (or ticking the box later) auto-runs `startSplitDetect`. Per-page: drag move, handle resize, Add box, edit qno / label (`3` / `3a` / `23-24`), delete, Re-run with per-page method / endpoint (`/redo-page`), per-question **Re-split** / **Unsplit**, click crop to enlarge (`showCropPreview`), Download processed PDF per side. Grey = stem, amber = part.
- **Step 3 — Import**: exam → version selects, overwrite, trim whitespace, **Import to database** (`startCommit()` POSTs the edited plan to `/plan`, then opens the commit SSE with the live `paper`, `que_version`, `sol_version`, `overwrite`, `trim_white`). Generic → **Download all as ZIP**. Because commit re-reads the Step 1 paper and Step 3 controls, one reviewed plan can be imported repeatedly (e.g. import as ENO, switch to EN, import again). Changing the subject after staging is rejected.

### Tasks: exam vs generic

`task=exam|generic` is persisted as `meta['mode']`.

- **Exam paper** (default): QUE / SOL detection → `QUE` / `SOL` IMG assets. QUE and SOL may target different versions (`iter_commit(..., versions={'que':..., 'sol':...})`). **Custom detection prompt** (Step 2 checkbox + instruction, persisted at detect time): borrows the context-free generic prompt while still importing as exam assets — `detect_page(..., generic_prompt=True)` returns exam-shaped boxes with `qno=None`, and `iter_detect._finalize_detect` assigns 1..N per side in reading order; the UI re-sequences with `renumberAllSequential` (also after a per-page re-run).
- **Generic extraction**: no exam context, no paper / subject / DB. A free-text `instruction` drives `build_pdf_generic_system` / `build_pdf_generic_user_text` + `parse_generic_boxes` → `{label, box}`. Review uses a label textbox per region (no qno cascade). Only `llm` / `refine` methods (`segment` collapses to `llm`). Output is `POST /export-zip`.

### Two-pass part split

Pass 1 is unchanged: one box per numbered question (range labels `23-24` allowed; do **not** split `(a)(b)(c)` here). Pass 2 is opt-in (`#splitPartsChk`, seeded by `Subject.split_parts_default`):

1. UI POSTs the current plan to `/plan`, then opens `/split-detect` SSE.
2. For each whole-question group, the server crops that region (`pad_frac=0`, `trim_white=False`), runs `detect_parts` (crop-relative 0–1000), maps boxes onto the page with `full_width_child_box` (vertical extent from the model, **left/right edges inherited from the parent box** so stem and parts stay aligned and respect the side's uniform width), and replaces the group with stem + lettered children (keeping `source_*` so Unsplit can restore).
3. Empty / no-parts replies keep the original whole-question box. A pass-2 `LLMError` (typically a cloud gateway read timeout at the endpoint's `timeout_seconds`) is retried once before the group is reported as `error` and kept whole.
3a. `parallel=1` (same "Parallel" tick as pass 1; honoured only for cloud endpoints with `max_concurrency > 1`) fans one side's crops across `run_parallel`. QUE completes and is merged before SOL starts so SOL `expected_labels` see the split QUE plan; plan mutation stays on the consumer thread.
4. SOL uses QUE labels as `expected_labels`; QUE receives them too when the agent supplies `expected_by_parent` (outline part tree). `detect_parts(extra_note=...)` prepends a repair note (agent).
5. `labels` csv re-splits one parent (Re-split button). Unsplit merges children back using `source_box` / `source_page`. `_replace_group` removes the parent, its descendants and anything with `source_label == parent`, so re-splitting `4d` leaves `4`, `4a`… intact; a range parent replaces only itself.
6. **Nested parts**: the pass-2 contract asks for a part's own introduction as the letter alone (`d`) and its sub-parts as `di`, `dii`; `compose_part_label` yields `4d`, `4di`. No special "nested stem" object exists.
7. **Roles are derived** from the label set on every save path (`apply_derived_roles` → `hierarchy.derive_roles`): `5` is a `stem` because `5a` exists, `5d` is a `stem` because `5di` exists, a labelled leaf is a `part`, a bare number/range with no descendants is a `question`. The browser mirrors this in `deriveRoles(kind)` so typing `4d` + `4di` in the Q inputs turns `4d` grey (stem) live.
8. **Manual editing** (review step): every exam row has a `+` "Add part below" button (`next_part_label`: `4a → 4b`, `4di → 4dii`, `4 → 5`; new box directly under the source with the same x-span), the Delete / Backspace key removes the highlighted box, and the role chip is recomputed rather than typed. `depends_prev` on an item is a toggle in the row and becomes `Question.needs_prev_parts` at commit (parts with a parent only).

### Multi-page questions

The model sees one page at a time. A question tail spilling onto the next page is reported as a page-top region flagged `continues_prev` (the page above flagged `continues_next`). `iter_detect` keeps these transiently as `_cp` / `_cn` on plan items; `_finalize_detect` runs once after ALL pages are collected: for custom-prompt runs it numbers 1..N per side in reading order `(page, box top-Y)`; for standard runs it calls `_stitch_continuations(plan)`, which walks each side in reading order and lets a page-top continuation inherit the qno of the box before it (only across a page boundary; chains resolve because processing is ordered). The continuation also inherits the head box's **x1/x2**: a continuation page has no margin question number, so the model boxes only the indented body and the left edge drifts right; pages of one paper share a layout, so the head's horizontal span is authoritative. This is independent of the order parallel detection returns pages. The flags are stripped before `plan.json` is written. The review UI mirrors it with `stitchContinuations()` (in `finishDetect` and after per-page re-run). At commit, same-`qno` parts become one multi-part IMG asset (`stitch=False`).

### Detection methods

`detect_page(config, png_path, atype, image_max_dim, method='llm', mode='exam', instruction='', generic_prompt=False, expected_labels=None)` → `(boxes, raw_text)`. `expected_labels` (agent) fills `{{expected_note}}` in the exam prompts with the question numbers the outline expects on the page; `iter_detect(expected_by_page=..., page_filter=...)` and `detect_single_page(expected_labels=...)` pass it through. Plain runs leave it empty. Exam boxes: `{qno: int|None, label: str, box: [x1,y1,x2,y2] fractions 0..1, continues_prev, continues_next}`; generic boxes: `{label: str|None, box}`. `DETECT_METHODS = ('llm', 'refine', 'segment')`. `_check_method_available` fails fast with one SSE error when `refine` / `segment` are requested without NumPy. `shrink_sides = (atype == 'QUE')` — SOL keeps full width so right-hand marking notes survive. CV runs on the high-res PNG (`load_gray`); the LLM sees the downscaled copy (`prepare_image`, `LLM_IMAGE_MAX_DIM`); `_sent_image_size` reports the dims the model saw so pixel-range replies can be normalised.

- `llm` (default; no NumPy): `build_pdf_box_system` / `parse_question_boxes`. QUE prompt = tight box excluding answer space and margins but keeping the marks annotation (`(4 marks)`); SOL prompt = full solution including right-hand notes.
- `refine`: LLM boxes, then `pdf_layout.refine_box(gray, box, shrink_sides, grow_frac=PDF_IMPORT_REFINE_GROW_PCT/100, pad_frac=PDF_IMPORT_ASSIST_PAD_PCT/100)` expands into a search window then snaps to printed content via projection profiles (recovers chopped text / marks / figures, drops blank margins). A refine failure keeps the LLM box.
- `segment`: the model returns only `{qno, y-start}` anchors (`build_pdf_anchor_system` / `parse_question_anchors`), and `pdf_layout.segment_page(gray, anchors, shrink_sides, pad_frac)` derives boxes from the whitespace gap to the next anchor. Assumes a single column.

Every prompt builder receives `endpoint_id=config.id` so per-endpoint prompt variants apply.

### Coordinate convention

Coordinate handling is the primary failure mode. The prompt pins an explicit 0-1000 integer grid with top-left origin and a worked example. The axis order is not hardcoded: `build_pdf_box_system(atype, coord_order)` / `build_pdf_box_user_text(atype, coord_order)` fill `{{box_array}}` / `{{box_corner}}` / `{{box_example}}` (contract) and `{{box_pairs}}` (user turn) from `pdf_box_order_vars(coord_order)`, so what the model reads matches `PDF_IMPORT_COORD_ORDER` and the parser. `detect_page` reads the setting and passes it to both the builders and `parse_question_boxes(text, img_w, img_h, coord_order)` (via `_normalize_box`):

- Axis order: `xyxy` (default; Qwen and most models) or `yxyx` (Gemma / Gemini / PaliGemma — vertical first). The setting drives BOTH prompt wording and parsing.
- Range (auto): max ≤ 1 → fractional; max ≤ 1024 → /1000; else raw pixels divided by the downscaled dims the model saw; else /max.

Debug: `detect?debug=1` (or `redo-page` body `debug: true`) logs each page's verbatim reply and attaches `raw` to the page event; the UI's Debug checkbox shows it in the console and a per-page collapsible. Use it to confirm the model's number range and axis order before flipping `PDF_IMPORT_COORD_ORDER`.

### Staging and rasterisation

- Staging dir: `<SYSTEM_PATH or OUTPUT_PATH>/.pdf_import/<token>/` holding `meta.json` (`mode`, `subject` / `source` / `year` / `paper`, `que_version` / `sol_version` / `version`, `custom_prompt`, `instruction`, `deskew`, `pre_rotate`, `split_mode`, `mode1_pages_per_student`, `filters`, `created_at`, per-kind `{filename, pages: [{index, filename, width, height}]}`), `plan.json`, and `que/` / `sol/` (`source.pdf` + `page_NNNN.png`, 1-based). `token = uuid4().hex`, validated by `_TOKEN_RE = ^[0-9a-f]{8,40}$` before any filesystem join. `cleanup_old()` (called on every `stage`) purges dirs older than 6 h; Discard removes one immediately. `save_meta` writes atomically via `.tmp` + `os.replace`.
- `rasterize_pdf(pdf_path, out_dir, width_px, deskew, pre_rotate, split_mode, filters, workers, mode1_pages_per_student)` delegates to `app/pdf_tools.py`: `split_descriptors` → per-fragment op chain (`build_op_chain(pre_rotate, frag_ops, filters)`) → `rasterize_page` + `apply_ops`. The legacy `deskew` flag is merged into `filters['deskew']`. Splitting yields more staged PNGs, renumbered contiguously; downstream per-page detection is unaffected (Mode 1 drops padding slots such as page 8 of a 7-page booklet).
- Parallel rasterisation: `workers` from `PDF_IMPORT_RASTER_WORKERS` (default 4) capped by `os.cpu_count()`; `workers <= 1` or a single page keeps the sequential loop. Each worker opens its OWN `fitz.open(pdf_path)` — never share a `Document` across threads. Output is written by index so `pages` stays in reading order. A render failure re-raises and aborts staging.
- Deskew (`pdf_layout.deskew_image`): projection-variance angle sweep ±6°; silently skipped with a warning when NumPy is missing. Stored as `meta['deskew']`.
- Server picks: `_resolve_server_pdf(rel, root_id)` — root-aware via `RootRegistry(current_user).resolve(root_id)` (Shared / personal / Storage), or legacy relative to `PDF_SOURCE_PATH` when `root_id` is blank; must end with `.pdf` and pass `storage.safe_join`. Returns a duck-typed `_ServerPDF` (`.filename`, `.save(dest)` copies the file) so `stage` treats it like an upload.

### Paper-name guess

`guess_paper_name(config, pdf_path, filename, subjects, image_max_dim)` rasterises page 1 at width 1700, downscales via `prepare_image`, sends `build_pdf_paper_name_system` + `build_pdf_paper_name_user_text(filename, subjects)`, parses `parse_paper_name` → `(paper, confidence)`, and re-validates through `parse_paper_prefix` (invalid → `None`). The endpoint is `_pdf_default_endpoint()`: `PDF_IMPORT_DEFAULT_LLM` (vision-only) → `EXPLAIN_DEFAULT_LLM` (vision-only) → first enabled vision endpoint.

### Commit

`_group_plan(plan)` groups by `(kind, label)` ordered by integer prefix then depth (stems before parts). Items with no label or a malformed box are skipped. For each group: `ensure_question` (QID `{subj}_{source}_{year}_{paper}_{token}`, committed immediately so a later crop failure does not lose it); if `slot_has_img(question.id, atype, version)` and not `overwrite` → `skip`; else, for a **root** QUE that pass-2 split, `whole_source_crops` collects unique pass-1 `source_box` pages and `replace_img_assets(..., 'WHOLE', ...)` writes the unsplit original (nested stems and range stems return no crops); then `crop_page` every part and `replace_img_assets(question, atype, version, imgs, stitch=False, source_path)` (atomic; deletes existing IMG rows first — that IS the overwrite path; fires the DOC-thumbnail lifecycle). Exceptions roll back and emit `error`. SOL: exact QUE label match; unmatched lettered SOL skipped; leftover whole-question SOL attaches to the stem. There is no WHOLE-SOL.

`crop_page(png_path, box, pad_frac, trim_white, whiteness_threshold, min_px=8)`: pads by `pad_frac` (= `PDF_IMPORT_CROP_PAD_PCT / 100`, the "safety margin"), crops, raises `ValueError('degenerate crop box')` below `min_px`; when `trim_white` is on, tightens to non-white content (threshold `THUMBNAIL_WHITENESS_THRESHOLD`, keeps an 8 px border; content is never removed, so SOL side-notes survive). `trim_white` is a per-run toggle: checkbox default = `PDF_IMPORT_TRIM_WHITE_DEFAULT` (OFF) → `&trim_white=` on the commit URL → `iter_commit(..., trim_white=)`. OFF = the crop respects the selected box exactly (plus safety margin); ON = tighten to content. `export_zip_bytes` always uses `trim_white=False`.

### Review UI rules (client-side, `templates/admin_pdf_import.html`)

- **Auto-renumber** (exam): reading order within a side = `(page, box top-Y)`. Add (`finishDraw` → `renumberAfterInsert`): integer prefix + 1 for later **unsplit** boxes; labelled parts (`3a`) are left alone. Delete (`renumberAfterDelete`): later integer prefixes −1 (clamped ≥ 1). `refreshNumbersUI` rewrites labels + inputs. Re-detecting a page replaces its boxes with the model's numbers and does not cascade; manual qno edits and move / resize never cascade.
- **Out-of-order warning** (`updateOrderWarning`, exam only): red banner when a side's **integer prefixes** are not ascending in reading order (a repeat = spanning question is fine; a drop is flagged with Q# / pages).
- **Part split**: `#splitPartsChk` (default from `/stage` `split_parts_default`). Auto-runs pass 2 after pass 1 and when the checkbox is ticked with existing boxes. **Detect parts** re-runs pass 2 on remaining whole-question boxes. Per-question **Re-split** / **Unsplit**. Overlay: grey stem, amber part.
- **Uniform width per side** (`uniformWidthChk`, exam + generic): lock every box on a side to the widest box's width. Default = `PDF_IMPORT_UNIFORM_WIDTH_DEFAULT` (ON). Width grows only (monotonic) and is recomputed non-lossily after each streamed page (`enableUniformWidth(kind)` — boxes are pushed at natural width, never snapped down first) and again on re-run / finish. `conformWidth` is left-anchored (only the right edge moves). Resizing one box's width (`onDragMove` while `uniformWidth[kind] != null`) re-broadcasts via `applyUniformWidthToAll`; moving does not. Untick to fine-tune (`uniformWidth` reset to `null`).
- **Live magnifier**: `updateDragMagnifier` draws a fixed floating canvas (`.pdf-magnifier`) from the original page pixels with crosshairs and the active rectangle; `magnifierSourceWindow` is handle-aware (horizontal handles show full selected width near the dragged edge, vertical handles full height, corners a broader window). Created lazily by `ensureDragMagnifier`, hidden on pointer-up.
- "Run in parallel" (`updateParallelUI`) is shown only for cloud endpoints and appends `&parallel=1`.

## Settings & config keys

DB-backed tunables in `app/settings.py` (group "PDF Import" unless noted); see [../core/06-system-settings.md](../core/06-system-settings.md) and [../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md](../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md).

| Key | Type | Semantics |
|---|---|---|
| `PDF_IMPORT_RASTER_WIDTH` | int 600-4000 | Width pages are rasterised to (default 1700, suits A4). Crops are cut from these high-res PNGs; the LLM copy is downscaled separately to `LLM_IMAGE_MAX_DIM` |
| `PDF_IMPORT_RASTER_WORKERS` | int 1-32 | Pages rasterised concurrently on Load PDF (default 4), capped by CPU count at runtime; 1 = sequential. Separate from detection parallelism |
| `PDF_IMPORT_COORD_ORDER` | `xyxy` \| `yxyx` | Axis order the vision model uses for boxes. `xyxy` = `[x1,y1,x2,y2]` (Qwen, most); `yxyx` = `[y1,x1,y2,x2]` (Gemma / Gemini / PaliGemma). Number range is auto-detected; only the axis order is a setting. Also used by the figure-bbox pass in MD generation |
| `PDF_IMPORT_DESKEW_DEFAULT` | bool | Whether "Auto-deskew scans" starts ticked (default on). NumPy required |
| `PDF_IMPORT_DEFAULT_METHOD` | `llm` \| `refine` \| `segment` | Pre-selected detection method (invalid → `llm`) |
| `PDF_IMPORT_CROP_PAD_PCT` | float 0-10 | Safety margin added around every box before the final crop, % of page (default 0.6). Larger recovers edge content; white-trim (when on) removes the excess afterwards. Also applied to ZIP export |
| `PDF_IMPORT_REFINE_GROW_PCT` | float 0-20 | `refine`: how far each LLM box grows into a search window before CV snaps back (default 3.5). Too large can swallow the next question |
| `PDF_IMPORT_ASSIST_PAD_PCT` | float 0-10 | `refine` / `segment`: padding kept around detected content edges after tightening (default 0.6) |
| `PDF_IMPORT_TRIM_WHITE_DEFAULT` | bool | Initial state of "Trim whitespace on import" (default OFF). ON tightens each crop to non-white content; OFF respects the box exactly plus the safety margin |
| `PDF_IMPORT_UNIFORM_WIDTH_DEFAULT` | bool | Initial state of "Uniform width per side" (default ON) |
| `PDF_IMPORT_DEFAULT_LLM` (group "AI Tools") | string endpoint name | Pre-selected endpoint for detection, per-page re-runs and the paper-name guess; blank → `EXPLAIN_DEFAULT_LLM` → first enabled vision endpoint |
| `AI_TOOLS_ENABLED`, `LLM_IMAGE_MAX_DIM` (group "AI Tools") | | Gate + downscale; see [ai-tools.md](ai-tools.md) |
| `THUMBNAIL_WHITENESS_THRESHOLD` | int | White-trim threshold reused by `crop_page` |

`.env` / `app/config.py`: `PDF_SOURCE_PATH` (legacy server-PDF root for `source-list` and blank-`root_id` picks; defaults to `SHARED_PATH`), `SYSTEM_PATH` / `OUTPUT_PATH` (staging root), `SOURCE_PATH` (asset writes).

## Permissions

- Every route is `@admin_required`. Exam sessions are subject-scoped: `stage` rejects a paper whose subject is not in `get_user_admin_subjects()` (super-admins bypass), and every token route re-checks through `_pdf_load_token_meta`. Generic sessions bypass the subject check because they never touch the question DB.
- `guess-paper` passes only the caller's admin subject codes to the model as allowed `subjects`.
- Server picks are sandboxed through `RootRegistry` (per-user roots) or `storage.safe_join` under `PDF_SOURCE_PATH`.
- See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

- `detect`, `split-detect` and `commit` are GET SSE streams (EventSource) run inside a pushed `app.app_context()` with `Cache-Control: no-cache`, `X-Accel-Buffering: no`. The dev server must run threaded.
- Detection re-fetches the endpoint inside the generator (`live_cfg`) and sets `live_cfg._batch = True` (opts into `service_tier_batch`).
- Parallel detection: the route inlines the same gate as `_ai_parallel` — on only when `parallel=1` AND `kind == 'cloud'` AND `max_concurrency > 1`; then `iter_detect` fans `detect_page` across `app.parallel.run_parallel`. Results are ingested on the consumer thread; `CANCELLED` results are skipped. Numbering / stitching is deferred to `_finalize_detect` so parallel completion order cannot change the outcome.
- Rasterisation uses its own `ThreadPoolExecutor` (`PDF_IMPORT_RASTER_WORKERS`), one `fitz` document per worker.
- Cancel registry `_PDF_CANCEL` is in-process: the cancel POST must reach the worker running the stream (same caveat as AI Tools and settings hot-reload).

## Gotchas

1. Detection, redo-page, split-detect, commit and guess-paper are gated on `AI_TOOLS_ENABLED` and need a vision-capable `LLMConfig` (SSE routes surface this as a single `error` + `done`). Staging, page images, plan save, processed PDF, ZIP export and discard work without AI.
2. Subject admins can only stage / import papers for their subjects; generic sessions are open to any admin.
3. Past-paper sources only (`DSE` / `CE` / `AL` via `PREFIX_PATTERN`); QB is out of scope. `section` / `level` remain null.
4. The LLM sees the downscaled page (`LLM_IMAGE_MAX_DIM`); crops come from the high-res page (`PDF_IMPORT_RASTER_WIDTH`). Boxes are fractional, so they are resolution-independent — but pixel-range replies are normalised against the downscaled size, not the raster size.
5. `PDF_IMPORT_COORD_ORDER` changes both the prompt wording and the parse. If boxes look shifted or transposed, check the raw reply with Debug before flipping it. Figure-bbox detection in MD generation shares the setting.
6. `replace_img_assets` deletes the slot's existing IMG rows before writing — that is the overwrite path. The only guard is the `slot_has_img` skip when `overwrite` is off. That skip also prevents writing a missing `WHOLE` archive onto a root that already has QUE; upload the archive in the Edit modal or re-import with overwrite. Nested stems and range stems never get WHOLE.
7. NumPy is optional: `refine` / `segment` / deskew need it. Detect fails fast with one SSE error; deskew silently warns and skips. `segment` assumes a single column — dense MC or note-heavy SOL pages can over- or under-merge; per-page re-run with `llm` is the escape hatch.
8. Never share a `fitz.Document` across threads; each raster worker opens its own.
9. `_finalize_detect` runs after all pages — custom-prompt numbering and continuation stitching are deterministic regardless of completion order; the old per-page numbering was order-sensitive. Re-detecting a single page (`/redo-page`) replaces that page's boxes and does not re-stitch server-side; the UI's `stitchContinuations()` handles it.
10. Continuation flags `_cp` / `_cn` are transient; exam `plan.json` and `/plan` payloads are `{page, qno, label, box, role?, source_*, depends_prev?}` with `role` always recomputed server-side. Generic stays `{page, label, box}`.
11. `trim_white` OFF is the default; the crop honours the box exactly plus `PDF_IMPORT_CROP_PAD_PCT`. ZIP export never trims but does apply the pad.
12. Commit uses the live Step 1 paper and Step 3 versions, not the staged ones (persisted back into `meta`); changing the subject is blocked. The same plan can be imported repeatedly under different versions.
13. Stage defaults `deskew` to on when the field is absent; the toolbox filter `f_deskew` also sets it. Both merge into `filters['deskew']`.
14. Staging dirs auto-purge after ~6 h on the next `stage` call; a stale token yields "Staging session not found or expired".
15. Uploads win over server picks for the same side; `root_id` blank means the legacy `PDF_SOURCE_PATH` root.
16. Exam labels are QNO tokens via `normalize_plan_label` / `parse_qno_token` — `3a` and `23-24` are kept, not first-digit-only. Pass-2 crop mapping must use unpadded crops (`pad_frac=0`) or part boxes drift. `split_parts_default` only seeds the checkbox; the live tick is what auto-runs pass 2.
17. `pdf_import_stage` already uses the module-level `Subject`. A late `from app.models import Subject` inside the function makes `Subject` local and crashes Load PDF with `UnboundLocalError` before rasterisation.
18. Never store or trust a model-supplied `role`; `derive_roles` is the only source. If a chip looks wrong, the label set is wrong.
19. The AI agent run (`/agent`) starts from `iter_detect` and therefore **replaces** the plan; manual edits made beforehand are lost (the wizard confirms first).

## Related

- [pdf-agent.md](pdf-agent.md) — the AI agent layer that drives these tools with outline hints and an attention list (ADR-010).
- [question-hierarchy.md](question-hierarchy.md) — tree model; this wizard commits `ensure_question` tokens including parts.
- [ai-tools.md](ai-tools.md) — transport, `LLMConfig`, per-feature default endpoints, parallel executor, cancel pattern
- [ai-prompts.md](ai-prompts.md) — `PDF_*` prompt keys, `pdf_box_order_vars`, box parsers, JSON contracts
- [admin-questions.md](admin-questions.md) — the Question / QuestionAsset admin surfaces the imported assets land in
- [md-format.md](md-format.md) — figure-bbox pass that shares `PDF_IMPORT_COORD_ORDER`
- [../core/06-system-settings.md](../core/06-system-settings.md), [../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md](../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md)

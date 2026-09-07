# AI Tools

> Admin batch + per-slot LLM operations (proofread, generate Markdown, solve-generate / solve-check, auto-tag, verify) over an OpenAI-compatible transport, plus the super-admin LLM Endpoints page. The dashboard Explain tutor chat is documented in [dashboard.md](dashboard.md).

AI Tools call an `LLMConfig` endpoint over selected questions to proofread typed assets against official scans, transcribe images into Markdown, auto-tag questions, or solve a QUE to generate / check ANS, SOL, or the version-independent Answer Text. Batch runs stream a live SSE log with a real server-side Stop; per-slot variants are synchronous JSON routes used by the edit-question modal.

## Files

| File | Role |
|---|---|
| `app/llm_client.py` | Single transport adapter: Chat Completions + Responses API, streaming, image prep, Fernet key storage, `resolve_default_endpoint`, `test_endpoint` |
| `app/ai_tools.py` | SSE generators (`iter_check`, `iter_generate_md`, `iter_solve_generate`, `iter_solve_check`, `iter_auto_tag`), per-slot workers, cancel registry `_AI_CANCEL`, `_LazyWord`, tag mapping |
| `app/parallel.py` | `run_parallel(app, cancel, items, worker_fn, max_workers)` thread-pool executor + `CANCELLED` sentinel |
| `app/ai_prompts.py` | Prompt registry, resolver, parsers (`parse_check_result`, `parse_tag_result`, `parse_figure_boxes`, `FIGURE_RE`). See [ai-prompts.md](ai-prompts.md) |
| `app/admin.py` | Routes: `ai_*`, `generate_img_slot`, `set_asset_check_state`, `set_question_verified`, `batch_set_verified`, `batch_set_check_state`, `llm_endpoints_*`; helpers `_ai_tools_guard`, `_ai_parse_qs`, `_ai_parallel`, `_ai_load_endpoint`, `_ai_load_endpoint_from_body`, `_ai_stream`, `_serialize_llm_config` |
| `app/models.py` | `LLMConfig`, `QuestionAsset.check_*` columns, `Question.verified*` |
| `app/settings.py` | REGISTRY group "AI Tools" (see Settings below) |
| `app/config.py` | `.env` secrets `LLM_API_KEY`, `LLM_KEY_SECRET` |
| `app/__init__.py` | Idempotent start-up migration for `llm_configs.kind` / `max_concurrency` / `service_tier*` columns + cloud back-fill |
| `templates/admin_llm_endpoints.html` | Endpoint CRUD UI (`#epKind`, `#epMaxConcurrency`, `#epServiceTier`, `#epServiceTierBatch`, `onKindChange`), raw Chat console (`#chatModal`, `openChatModal`, `sendChatMessage`, `_chatStageFiles`, `_chatRenderStaged`) |
| `templates/admin_questions.html` | `#aiToolsModal` (ops: check / MD / solvegen / solvecheck / tag), console, `aiToolsToggleOp` → `_aiApplyDefaultEndpoint`, `aiUpdateParallelUI` |
| `templates/partials/edit_question_modal.html` | `#autoTagModal`, `#editNavControls` |
| `templates/partials/edit_question_modal_js.html` | `renderCheckBadge`, `renderCheckStatusBar`, `setSlotCheckState`, `showCheckRawModal`, `openAiSlotMdModal`, `generateImgFromSlot`, `openQuickCheckModal`, `runQuickCheck`, `openAiSolveSlotModal`, `runAiSolveSlot`, `openAnswerTextAiModal`, `runAnswerTextAi`, `openAutoTagModal`, `runAutoTag`, `applySuggestionsToForm`, `_aiPickDefaultEndpointId`, `oqbEditNavGo` |
| `templates/base.html` | `window.OQB_AI_TOOLS_ENABLED` global (admin + dashboard) |

## Tables

Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Table / model | Columns this module owns or writes |
|---|---|
| `llm_configs` (`LLMConfig`) | `name` (unique), `base_url`, `model_name`, `provider`, `api_key_enc` (Fernet ciphertext), `api_key_env`, `supports_vision`, `kind` (`local`/`cloud`), `max_concurrency` (1-32), `service_tier`, `service_tier_batch`, `api_protocol` (`chat`/`responses`), `reasoning_effort` (`''`/`off`/`low`/`medium`/`high`), `reasoning_summary` (`''`/`auto`/`none`), `reasoning_max_tokens`, `request_extra_json`, `max_output_tokens`, `temperature`, `timeout_seconds`, `enabled`, `sort_order` |
| `question_assets` (`QuestionAsset`) | `check_state` (`None`/`checking`/`ok`/`issues`/`error`), `check_result` (JSON: `{status, issues[], model, ref_version, checked_by, file_format?, mode?, editor?, note?}`), `check_raw` (verbatim reply, clipped to 32 000 chars), `checked_at` |
| `questions` (`Question`) | `verified`, `verified_at`, `verified_by`; `answer` (written by solve-generate `ANS_TEXT`) |
| `prompt_endpoint_assignments` | Cleaned up / cloned by endpoint delete / duplicate (see [ai-prompts.md](ai-prompts.md)) |

`LLMConfig._batch` is a transient Python attribute (not a column) set by batch routes to opt into `service_tier_batch`.

## Routes

All under the `admin_bp` prefix `/admin`. "Subject-admin" = `@login_required @admin_required` plus a per-question / per-selection check against `get_user_admin_subjects()` (super-admins bypass). "Super-admin" = `@super_admin_required`.

### Batch SSE (GET, `text/event-stream`, subject-admin)

All batch routes: run `_ai_tools_guard()` (400 when `AI_TOOLS_ENABLED` is off), `_ai_parse_qs()` (`question_ids` csv, intersected with admin subjects; 403 if none remain), then `_ai_load_endpoint(<default setting>)`. `endpoint_id` missing / blank / `0` falls back to the per-feature default; a named endpoint must be enabled and `supports_vision`. `parallel=1` is honoured only when `_ai_parallel` says the endpoint is `cloud` with `max_concurrency > 1`.

| Method | Path | Params | Purpose |
|---|---|---|---|
| GET | `/admin/questions/ai/check` | `question_ids`, `endpoint_id?`, `typed_version`, `ref_version` (must differ), `atypes` (csv QUE/ANS/SOL, default QUE), `formats` (csv IMG/MD/DOC; `DOCX`→`DOC`; default all), `recheck` (1/0), `parallel` | Proofread typed assets against official reference (`iter_check`). Default LLM: `CHECK_DEFAULT_LLM` |
| GET | `/admin/questions/ai/generate-md` | `question_ids`, `endpoint_id?`, `source_version`, `target_version`, `atypes`, `overwrite` (1/0), `embed_image` (1/0, default 1), `parallel` | Transcribe IMG → MD assets (`iter_generate_md`). Default LLM: `MD_DEFAULT_LLM` |
| GET | `/admin/questions/ai/solve-generate` | `question_ids`, `endpoint_id?`, `versions` (csv), `targets` (csv of `ANS`,`SOL`,`ANS_TEXT`), `include_official_sol` (1/0), `overwrite` (1/0), `parallel` | Solve QUE → write ANS/SOL MD assets and/or `Question.answer` (`iter_solve_generate`). Default LLM: `SOLVEGEN_DEFAULT_LLM` |
| GET | `/admin/questions/ai/solve-check` | `question_ids`, `endpoint_id?`, `versions`, `targets`, `formats` (asset targets only), `include_official_sol`, `parallel` | Solve QUE → check existing ANS/SOL/Answer Text (`iter_solve_check`). Default LLM: `SOLVECHECK_DEFAULT_LLM` |
| GET | `/admin/questions/ai/auto-tag` | `question_ids`, `endpoint_id?`, `versions`, `fields` (csv of `ai_prompts.TAG_FIELDS`), `overwrite` (1/0), `parallel` | Suggest + apply + commit tags per question (`iter_auto_tag`). Default LLM: `AUTOTAG_DEFAULT_LLM` |
| POST | `/admin/questions/ai/cancel` | body `{job_id}` | Set the cancel flag; returns `{success, known}` |
| GET | `/admin/questions/ai/endpoints` | — | `{endpoints: [{id, name, model_name, supports_vision, kind, max_concurrency}], defaults: {tag, md, check, solvegen, solvecheck}}` (enabled endpoints; defaults are resolved ids or `null`) |

SSE event dicts: `{type, message, current?, total?, ...}`; `type` ∈ `job` (first, carries `job_id`) / `info` / `skip` / `success` / `error` / `done`. `done` carries `stats`: `{ok, issues, skipped, errors}` for check / solve-check, `{created, skipped, errors}` for MD, `{created, updated, skipped, errors}` for solve-generate. Check events also include `raw`. `_ai_stream` wraps every generator: emits `job`, streams inside `app.app_context()`, converts an uncaught exception to `error` + `done`, then `finish_job`. Response headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

### Per-slot synchronous JSON (POST, subject-admin)

Each checks `AI_TOOLS_ENABLED` (400) and the question's subject (403), then `_ai_load_endpoint_from_body(data, <default setting>)`. Unless noted, HTTP status is 200 on success, 409 on `skip`, 502 on `error`.

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/admin/questions/<id>/assets/ai/generate-md` | `{version, asset_type, endpoint_id?, embed_image? (default true), overwrite? (false), source_version? (defaults to version)}` | `generate_md_slot` → `{success, status: created|updated|skip|error, message, asset_id}` |
| POST | `/admin/questions/<id>/assets/ai/check` | `{version, asset_type, ref_version (≠ version), endpoint_id?, recheck? (default true), formats? (list IMG/MD/DOC)}` | `check_slot` → `{success, status: ok|issues|skip|error, state, message, raw?}` |
| POST | `/admin/questions/<id>/assets/ai/solve-generate` | `{version, asset_type: ANS|SOL, endpoint_id?, include_official_sol?, overwrite?}` | `solve_generate_slot` → `{success, status, message, asset_id}` |
| POST | `/admin/questions/<id>/assets/ai/solve-check` | `{version, asset_type: ANS|SOL, endpoint_id?, include_official_sol?, formats?}` | one `solve_check_slot_format` per format → aggregated `{success, status, state, message, raw?}` |
| POST | `/admin/questions/<id>/ai/answer-text` | `{mode: generate|check, source_versions[] (or versions[]; default all), endpoint_id?, include_official_sol?, overwrite?}` | `generate_answer_text` / `check_answer_text` → `{success, status, state, message, answer, issues[]}`. Default LLM: `SOLVEGEN_DEFAULT_LLM` for generate, `SOLVECHECK_DEFAULT_LLM` for check |
| POST | `/admin/questions/<id>/ai/suggest-tags` | `{versions[], fields[], endpoint_id?}` | `suggest_tags` (NO DB write) → `{success, suggestions, display, unmatched[], fields, model}`; on parse failure `{success: false, error, raw}` with HTTP 200; `LLMError` → 502 |
| POST | `/admin/questions/<id>/assets/generate-img` | `{version, asset_type, source_format? (DOC|MD|'' auto: prefer DOC), stitch? (default true)}` | Render one MD/DOC slot to IMG parts via Word COM (`batch_image_gen.render_doc_to_pages` / `render_md_to_pages` + `replace_img_assets`) → `{success, message, wrote, deleted, source_format}`. 400 when `word_com.IS_AVAILABLE` is false. Not gated on `AI_TOOLS_ENABLED` server-side (no LLM involved); the button is hidden client-side when AI Tools are off |
| POST | `/admin/questions/<id>/assets/check-state` | `{version, asset_type, state: ok|issues|error|clear, note?, severity? (minor|major|critical)}` | Manual set / clear for ALL rows in the slot (404 when the slot is empty). Writes `checked_by: "manual"`, `editor` username; `clear` nulls the fields. Always nulls `check_raw`. → `{success, version, asset_type, check_state, checked_at}` |
| POST | `/admin/questions/<id>/verify` | `{verified}` | Set whole-question flag (+ `verified_at`, `verified_by`). → `{success, verified, verified_at, unchecked_assets, total_assets}`; `unchecked_assets` = rows whose `check_state != 'ok'`, for a soft warning only |
| POST | `/admin/questions/batch-set-verified` | `{question_ids[], verified}` | Subject-scoped batch flag → `{success, updated, verified}` |
| POST | `/admin/questions/batch-set-check-state` | `{question_ids[], versions[], atypes[], state, note?, severity?, overwrite? (false)}` | Per-slot manual write across the selection. Skips empty slots; with `overwrite=false` skips slots already carrying any `check_state` (except `clear`, which always applies). → `{success, slots_updated, assets_updated, ...}` |

### LLM Endpoints (super-admin)

| Method | Path | Body / Response |
|---|---|---|
| GET | `/admin/llm-endpoints` | Page |
| GET | `/admin/llm-endpoints/data` | `{endpoints: [_serialize_llm_config...]}` — includes `has_stored_key`, `key_resolves`, `has_request_extra`; never the plaintext key |
| POST | `/admin/llm-endpoints/save` | `{id?, name, base_url, model_name, provider?, api_key?, clear_key?, api_key_env?, supports_vision?, kind?, max_concurrency?, service_tier?, service_tier_batch?, api_protocol?, reasoning_effort?, reasoning_summary?, reasoning_max_tokens?, request_extra_json?, max_output_tokens?, temperature?, timeout_seconds?, enabled?, sort_order?}`. Blank `api_key` keeps the stored key; `clear_key: true` removes it (fall back to `.env`). Duplicate name → 409. Invalid `request_extra_json` → 400. Values are clamped: `max_concurrency` 1-32, `timeout_seconds` ≥ 5, tiers ∈ `flex|priority|auto|default|''` |
| POST | `/admin/llm-endpoints/<cid>/delete` | Deletes `PromptEndpointAssignment` rows explicitly (plus FK CASCADE), then `ai_prompts.invalidate_cache()` |
| POST | `/admin/llm-endpoints/<cid>/duplicate` | Creates `Copy of <name>` / `Copy N of <name>` with every field including `api_key_enc`, then clones the source's prompt assignments |
| POST | `/admin/llm-endpoints/<cid>/test` | `llm_client.test_endpoint(cfg)` → `{success, message}` |
| POST | `/admin/llm-endpoints/<cid>/chat` | Raw chat console; body `{turns: [{role, content, images?}]}`; SSE response (see Background work) |

## Business rules / invariants

### Transport (`app/llm_client.py`)

- One adapter, two protocols selected by `LLMConfig.api_protocol`: Chat Completions (`POST {base_url}/chat/completions`) or Responses API (`POST {base_url}/responses`). `base_url` is the API root (`https://api.openai.com/v1`, `https://openrouter.ai/api/v1`, `https://api.poe.com/v1`, `http://localhost:11434/v1`).
- Images: base64 `image_url` content blocks (Chat) or `input_image` items inside a `type: message` wrapper (Responses). Text-only Responses turns use plain-string `content` (or `input` as a lone string for a single user turn); assistant replay never uses `input_text` blocks.
- `chat(config, system, user_text, images)` → `(text, info)`; `info = {usage, finish_reason, reasoning, reasoning_details, raw}`; raises `LLMError`. `chat_messages(config, messages, max_tokens?, temperature?, timeout?)` is the multi-turn form. `chat_messages_stream(...)` is a generator yielding `{type:'delta', content, reasoning}` then `{type:'done', text, reasoning, finish_reason, usage}`.
- `_extract_message_parts` / `_extract_responses_parts` split answer text from reasoning. Saved assets use the final answer text only; `info['reasoning']` is diagnostics / UI and is never written to ANS / SOL / MD.
- Image helpers: `prepare_image(abs_path, max_dim)` → downscaled JPEG `(b64, mime)`; `prepare_image_from_pil(im, max_dim)`; `prepare_image_from_data_url(data_url, max_dim)` (JPEG q=88); `read_image_data_uri(abs_path)` → ORIGINAL bytes; `crop_image_data_uri(abs_path, box, pad, max_dim)` → PNG data-URI of a region where `box` is `[x1,y1,x2,y2]` fractions 0..1, top-left origin, alpha flattened to white, `ValueError` on a degenerate box; `sent_image_size(abs_path, max_dim)` replicates the downscale to report the dims the model saw.
- Reasoning: `_resolve_reasoning(config)` — endpoint `reasoning_effort` blank ⇒ inherit `LLM_REASONING_EFFORT_DEFAULT` (default `off`); `off` ⇒ omit all reasoning params. `reasoning_summary` blank ⇒ inherit `LLM_REASONING_SUMMARY_DEFAULT` (default `auto`). `reasoning_max_tokens > 0` adds `max_tokens`. `_apply_reasoning_params` writes a top-level `reasoning` object, EXCEPT for Claude models (`claude` in `model_name` or provider `anthropic`/`claude`): those get `thinking: {type: 'adaptive', display?: 'summarized'}` + `output_config: {effort}`.
- `_merge_request_extra` shallow-merges validated `request_extra_json` (must be a JSON object, ≤ 8192 bytes via `parse_request_extra_json`) over the request body — provider-specific keys such as Poe Chat `extra_body` fields.
- Service tier: `_resolve_service_tier` sends `service_tier_batch` when `config._batch` is truthy, else `service_tier`; blank ⇒ param omitted.
- Local servers stay on Chat Completions with reasoning off; OpenRouter GPT-5.5 can use Chat + `reasoning`; Poe reasoning models should use Responses.

### API-key storage (hybrid)

- UI-entered keys are stored Fernet-encrypted in `LLMConfig.api_key_enc`. `resolve_api_key(config)`: decrypt → else `.env` var named by `api_key_env` (default `LLM_API_KEY`, read from `app.config` then `os.getenv`).
- The Fernet key = SHA-256(`LLM_KEY_SECRET` or `SECRET_KEY` or `'oqb-llm'`) urlsafe-b64. `decrypt_key` returns `''` on failure, so a rotated secret silently degrades to the `.env` fallback.
- Plaintext keys are never serialised to the browser: `_serialize_llm_config` reports only `has_stored_key` / `key_resolves`.

### Per-feature default endpoints

`llm_client.resolve_default_endpoint(setting_key, vision_only=True, named_vision_only=None)`:

1. If `current_app.config[setting_key]` names an enabled endpoint (and it is vision-capable when `named_vision_only`), use it.
2. Else the first enabled endpoint by `sort_order, name` (filtered to `supports_vision` when `vision_only`).
3. Else `None`.

| Setting | Resolver | Used by |
|---|---|---|
| `CHECK_DEFAULT_LLM` | `_default_ai_endpoint` | `ai_check`, `ai_check_slot` |
| `MD_DEFAULT_LLM` | `_default_ai_endpoint` | `ai_generate_md`, `ai_generate_md_slot` (also the figure-bbox second pass) |
| `SOLVEGEN_DEFAULT_LLM` | `_default_ai_endpoint` | `ai_solve_generate`, `ai_solve_generate_slot`, `ai_answer_text` (generate) |
| `SOLVECHECK_DEFAULT_LLM` | `_default_ai_endpoint` | `ai_solve_check`, `ai_solve_check_slot`, `ai_answer_text` (check) |
| `AUTOTAG_DEFAULT_LLM` | `_default_ai_endpoint` | `ai_auto_tag`, `ai_suggest_tags` |
| `PDF_IMPORT_DEFAULT_LLM` | `_pdf_default_endpoint` (falls through to `EXPLAIN_DEFAULT_LLM`) | [pdf-import.md](pdf-import.md) |
| `EXPLAIN_DEFAULT_LLM` | `_default_explain_endpoint` (`named_vision_only=False`) | [dashboard.md](dashboard.md) |
| `SMART_IMPORT_DEFAULT_LLM` | text-only task, `vision_only=False` | Smart Import "Analyze with AI" |

Client side: `_aiPickDefaultEndpointId(eps, defaults, opKey)` in the edit modal; the AI Tools modal re-applies the default on every op-radio change (`aiToolsToggleOp` → `_aiApplyDefaultEndpoint`).

### Cancellation

`app/ai_tools._AI_CANCEL: {job_id: threading.Event}` guarded by `_AI_LOCK`; `new_job()` / `cancel_job(job_id)` / `finish_job(job_id)`. Each generator checks the flag between items and still emits a final `done`. The frontend posts `job_id` (from the `job` event) to `/ai/cancel` and keeps the EventSource open so `done` arrives. Single-process assumption: the cancel POST must reach the worker running the stream.

### Parallel batch ops

- `_ai_parallel(cfg, want_parallel)` → `(on, workers)`; `on` only when `want_parallel and cfg.kind == 'cloud' and max_concurrency > 1`. Local endpoints are always sequential.
- `run_parallel` yields `{item, result, error}` in completion order; each worker runs in `app.app_context()` (thread-local scoped session). `max_workers <= 1` ⇒ sequential fallback. A worker that sees the cancel flag before starting returns `CANCELLED` (consumer skips it: no event, no counter). In-flight requests finish naturally.
- Per-item units are pure functions returning a result dict or raising: `generate_md_slot`, `check_slot`, `solve_generate_slot`, `generate_answer_text`, `solve_check_slot_format`, `check_answer_text`, `_auto_tag_one`, `pdf_import.detect_page`. All counter updates and event shaping happen on the consumer thread.
- Word COM: in parallel `iter_check` each worker owns a short-lived `_LazyWord` (opened + closed inside the worker), so MD/DOC pre-rendering serialises on `word_com._WORD_COM_LOCK` while pure-IMG checks run concurrently. The sequential path reuses one session for the whole run.
- Migration: `app/__init__.py` adds `kind` / `max_concurrency` / `service_tier*` columns idempotently and back-fills `kind='cloud', max_concurrency=4` for known hosted hosts (OpenAI / OpenRouter / Google / Anthropic / Groq / Together / DeepSeek / Mistral / xAI).
- UI: "Run in parallel" checkbox (shown for cloud endpoints, default checked) in `admin_questions.html` (`aiUpdateParallelUI`) and `admin_pdf_import.html` (`updateParallelUI`); both append `&parallel=1`. `onKindChange` pins `max_concurrency` to 1 for local.

### Check-state semantics (proofread)

- `check_slot_format(question, atype, typed_version, ref_version, file_format, ...)` is the atomic worker (one IMG / MD / DOC check). `check_slot` runs every present format in `formats` (default `CHECK_FORMATS = IMG, MD, DOC`) and aggregates via `_aggregate_check_results`. `iter_check` loops `(question, atype, format)` items. `ai_check_slot` calls `check_slot` once.
- Typed side images: `_resolve_format_images` (IMG parts as-is; MD/DOC rendered via `_render_source_to_pages`). Reference side: `_resolve_slot_images` (IMG preferred, else rendered DOC/MD).
- Skip (with event) when: the typed format is absent, the reference slot is empty, no usable source renders, or `recheck` is off and any row of that format is already `ok`/`issues`.
- Prompt order: reference images first, then typed; user text = `build_check_user_text(...)` + typed-format note + image-order note; system = `system_prompt('CHECK_SYSTEM', config.id)`.
- `_write_format_check_state` writes `check_state` / `check_result` / `checked_at` / `check_raw` to every row of that format only in the typed slot; `check_result` includes `file_format`, `model`, `ref_version`, `checked_by: 'ai'`.
- States: `ok`, `issues` (list), `error` (unparseable reply or empty reply). The verbatim reply is always stored in `check_raw` (clipped to `_RAW_CHECK_REPLY_MAX = 32_000`); parsed output goes to `check_result`. Manual / batch status edits null `check_raw`.
- Status rollup is typed-only: `questions_api_list`'s per-question Status indicator and `check_status` filter consider only `TYPED_VERSIONS` (EN/CH/BI); `OFFICIAL_VERSIONS` (ENO/CHO) are excluded. `asset_count` still counts every version.
- Edit-modal UI: compact badge (`renderCheckBadge`, reads all slot parts) + editable status bar (`renderCheckStatusBar`, shown whenever the slot has ANY asset) with AI proofread (`openQuickCheckModal` → `runQuickCheck`), mark correct / mark issue / clear (`setSlotCheckState`), a braces button (`showCheckRawModal`) when `check_raw` exists, and Gen IMG on MD/DOC cards (`generateImgFromSlot`). All gated on `window.OQB_AI_TOOLS_ENABLED`.

### MD generation

- `generate_md_slot(question, atype, source_version, target_version, *, embed_image, overwrite, config, image_max_dim, md_max_bytes, source_path)` returns `{status: created|updated|skip|error, message, asset_id?}`.
- Pipeline: source IMG parts → `MD_SYSTEM` call → `strip_md_fences` → `normalize_inline_math` (tightens `$ x $` → `$x$`; pandoc's `tex_math_dollars` rejects spaced delimiters).
- Figures are embedded only when the transcription contains a `[FIGURE: ...]` placeholder (`ai_prompts.FIGURE_RE` / `figure_captions`). With `embed_image` on and a single-part source, `_embed_figures` runs a second "locate figures" pass (`build_figure_box_system()` → `parse_figure_boxes`, 0-1000 grid, honours `PDF_IMPORT_COORD_ORDER`) and replaces each placeholder with a cropped base64 image (`crop_image_data_uri`). Falls back to the whole source image when the box is missing / degenerate / near-full-page (`_box_is_useful`) or the slot has multiple parts.
- Skips + logs when the result exceeds `MD_MAX_SIZE_BYTES`. Writes to the canonical MD path (`_md_rel_path`, mirrors `admin.create_md_asset`), upserts the `QuestionAsset(file_format='MD')`, and calls `md_render.invalidate(asset.id)`.
- The UI shows the per-slot button when any version has IMG for that atype and offers a source-version picker (`source_version` defaults to `version`).

### Solve generation / checking

- `_solve_gather_inputs` takes QUE content from the target version preferring IMG parts, then MD text, then rendered DOC. `include_official_sol` appends ENO/CHO SOL content when available. Answer Text is version-independent and uses the first selected version with usable QUE content (`_solve_gather_first_question`).
- `solve_generate_slot(question, kind, version, ...)` writes ANS/SOL MD assets through the same `_md_rel_path` / `QuestionAsset(file_format='MD')` path (`_solve_write_md_asset`) and invalidates `md_render`. `generate_answer_text` writes plaintext to `Question.answer`.
- `solve_check_slot_format` checks each existing ANS/SOL format separately and writes `check_state/check_result/checked_at` with `mode: "solve"` in `check_result`. `check_answer_text` returns `ok/issues/skip/error` only and never persists state (`Question.answer` has no check columns); batch `ANS_TEXT` checks are log-only.
- Prompts `SOLVE_GEN_*` / `SOLVE_CHECK_*`; solve-check reuses `parse_check_result`'s JSON shape so the existing badge / bar renders it.

### Auto-tagging

- Inputs (`_resolve_tag_inputs`): for QUE then SOL, across requested versions in order, the FIRST available content per type — IMG parts (`prepare_image`) → MD text (data-URIs stripped via `_strip_data_uris`) → DOC rendered via `doc_thumbnails.ensure_thumbnail` (polled briefly). QUE required; SOL optional.
- Prompt: `TAG_SYSTEM` + `TAG_USER` (vars `subject_name`, `fields`, `taxonomy`); `build_tag_taxonomy(subject_id, fields)` renders only the requested fields' allowed values.
- `parse_tag_result` → `_map_tag_names`: names matched case-insensitively within the subject; subtopics validated as children of their resolved topic; `major_subtopic` must belong to `major_topic`; unmatched names are reported, never invented.
- `suggest_tags` returns suggestions + display names + unmatched (no write). `apply_tags` writes only requested fields and, with `overwrite` off, skips any field already holding a value (scalar and M2M). `iter_auto_tag` = suggest + apply + commit per question.
- Field keys (`TAG_FIELDS`): `q_type`, `level`, `section`, `major_topic`, `major_subtopic`, `minor_topics[]`, `subtopics[]`, `chapter`, `subchapter`. UI pre-selects `q_type, major_topic, major_subtopic, chapter`.

### Edit-modal Prev/Next navigation

`#editNavControls` (Prev/Next + "n / total"). `openEditModal(questionId, {preserveView})` keeps the active tab + version pill; `oqbEditNavGo(±1)` steps through `window.oqbEditNav.ids`, re-read on each open from host-defined `window.oqbEditNavSource()` — dashboard returns the full filtered set (`#allQuestionIds`), admin returns the current page's `#questionTableBody tr.q-row` ids.

## Settings & config keys

DB-backed tunables live in `app/settings.py` REGISTRY (group "AI Tools") and hot-reload into `app.config`; see [../core/06-system-settings.md](../core/06-system-settings.md) and [../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md](../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md).

| Key | Type | Meaning |
|---|---|---|
| `AI_TOOLS_ENABLED` | bool | Master switch. Off ⇒ every AI route returns 400 and the UI hides AI buttons (`window.OQB_AI_TOOLS_ENABLED`) |
| `LLM_IMAGE_MAX_DIM` | int (256-4096) | Long-edge downscale before base64 encoding (default 1600) |
| `CHECK_DEFAULT_LLM`, `MD_DEFAULT_LLM`, `SOLVEGEN_DEFAULT_LLM`, `SOLVECHECK_DEFAULT_LLM`, `AUTOTAG_DEFAULT_LLM`, `PDF_IMPORT_DEFAULT_LLM`, `EXPLAIN_DEFAULT_LLM`, `SMART_IMPORT_DEFAULT_LLM` | string (endpoint name, `choices_fn=_llm_endpoint_choices`) | Per-feature default endpoint; blank = auto-pick (see resolver above) |
| `LLM_CHAT_TIMEOUT_SECONDS` | int | Timeout for interactive streams (Explain + Chat console), default 600; the per-endpoint `timeout_seconds` still applies to batch ops |
| `LLM_REASONING_EFFORT_DEFAULT` | string (`off`/`low`/`medium`/`high`) | Inherited by endpoints with blank `reasoning_effort` |
| `LLM_REASONING_SUMMARY_DEFAULT` | string (`auto`/`none`) | Inherited by Responses endpoints with blank `reasoning_summary`; Chat endpoints ignore it |
| `MD_MAX_SIZE_BYTES` | int | Generated MD larger than this is skipped (shared with the MD module) |
| `WORD_COM_LOCK_TIMEOUT` | float | `_LazyWord` lock timeout for per-slot renders |

`.env`-only (in `app/config.py`, never in the DB): `LLM_API_KEY` (global fallback key), `LLM_KEY_SECRET` (Fernet secret; falls back to `SECRET_KEY`).

## Permissions

- Endpoint CRUD, test, duplicate, and the raw Chat console: `@super_admin_required`.
- Every other route here: `@admin_required` plus subject scoping — batch routes intersect `question_ids` with `get_user_admin_subjects()` (403 when nothing remains), per-question routes 403 unless super-admin or subject admin of `question.subject`.
- The whole feature is disabled by `AI_TOOLS_ENABLED=false` (400), and image ops require `supports_vision` on the chosen endpoint.
- See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

- Batch SSE routes are GET + `text/event-stream` (EventSource). Work is gathered in request context; streaming happens inside a pushed `app.app_context()`; the endpoint is re-fetched as `live_cfg` inside the generator and `live_cfg._batch = True` is set.
- The dev server must run threaded for the long streams.
- Thread pool: `app/parallel.run_parallel` (cloud endpoints only). Word COM is a global lock; per-worker `_LazyWord` sessions serialise on it.
- Raw Chat console (`POST /admin/llm-endpoints/<cid>/chat`): passes `turns` straight to `chat_messages_stream` with no system prompt, no injected context, no guardrails. Disabled endpoint → 400. Caps: last 40 turns, 16 000 chars per message, 6 images per user turn, 32 MB total image bytes per request (413), images rejected for non-vision endpoints (400). Each image runs through `prepare_image_from_data_url`. SSE events: `preamble` → `delta` (`content`, `reasoning`) → `done` (reply Markdown-rendered server-side after `normalize_inline_math`, so KaTeX renders like the dashboard) or `error`. Uses `LLM_CHAT_TIMEOUT_SECONDS`.
- Streaming details: `chat_messages_stream` sets `resp.encoding = 'utf-8'` before iterating (LM Studio / some Ollama builds omit `charset`, and `requests` would fall back to ISO-8859-1 → mojibake). Responses streaming accumulates `response.output_text.delta` / `response.text.delta` and reasoning deltas only.

## Gotchas

1. `AI_TOOLS_ENABLED` gates everything except `generate-img` (Word COM render, no LLM). Image ops require `supports_vision`; `_ai_load_endpoint*` rejects text-only endpoints even when explicitly named.
2. Subject-admins operate on their own subjects only; endpoint CRUD is super-admin only.
3. LLM JSON is unreliable: `parse_check_result` returns `None` on total failure and the caller stores the raw reply with `check_state='error'`. Empty replies are also `error` (with `_empty_reply_hint` on `finish_reason`).
4. Images are downscaled to `LLM_IMAGE_MAX_DIM` before sending; embedded MD figures use the original bytes.
5. Rotating `LLM_KEY_SECRET` (or `SECRET_KEY` when the former is blank) invalidates every stored `api_key_enc`; `key_resolves` drops to the `.env` fallback and admins must re-enter keys.
6. Claude via Poe: never send top-level `reasoning` — Poe translates it to the deprecated `thinking.type.enabled` which Claude 4.6+ rejects. `_is_claude_model` switches to `thinking: {type: adaptive}` + `output_config.effort`; summary `auto` adds `thinking.display: summarized`.
7. Responses streaming: harvest text only from `*.delta` events. Do not also read `response.output_item.done` — providers (Poe, OpenAI) already streamed that text, and reading both duplicates output.
8. `service_tier` / `service_tier_batch` are sent only when non-empty; batch wins over single only when `config._batch` is set (transient attribute; never persisted).
9. Reasoning precedence: endpoint `off` wins over everything (no params sent); endpoint blank inherits the system default; system default `off` ⇒ omitted.
10. Manual / batch check-state edits always null `check_raw`; only AI checks populate it. `batch-set-check-state` with `overwrite=false` never touches a slot that already has any status, but `clear` always applies.
11. Recheck default differs: the batch route defaults `recheck=0` (skip already-checked), the per-slot Quick Check defaults `recheck=true`.
12. Status rollup ignores ENO/CHO; do not expect official-scan checks to change a question's Status indicator.
13. `[FIGURE: ...]` is the only trigger for figure embedding; pure-text transcriptions produce Markdown with no image. The default `MD_FORMAT` also demands escaped question numbers (`8\.`) and one MC option per line.
14. `check_answer_text` and batch `ANS_TEXT` checks never persist state — `Question.answer` has no check columns.
15. Cancel registries (`_AI_CANCEL`, prompt cache, settings cache) are per-process; multi-worker deployments must route the cancel POST to the streaming worker.
16. Parallel workers must not touch counters or yield events; only the consumer thread does. Each parallel `iter_check` worker opens and closes its own `_LazyWord`.
17. `ai_endpoints` lists ALL enabled endpoints including text-only ones; the batch routes will still reject a text-only choice, so pick vision endpoints in the UI.

## Related

- [dashboard.md](dashboard.md) — Explain tutor chat (`/dashboard/api/question/<id>/explain`, `EXPLAIN_DEFAULT_LLM`, `_can_pick_explain_endpoint`)
- [ai-prompts.md](ai-prompts.md) — prompt registry, variants, per-endpoint pins, parsers
- [pdf-import.md](pdf-import.md) — vision detection using the same transport, cancel pattern, and parallel gate
- [md-format.md](md-format.md) — MD asset path, `md_render`, `normalize_inline_math`, `MD_MAX_SIZE_BYTES`
- [admin-questions.md](admin-questions.md) — edit-question modal, `questions_api_list`, batch operations that host the AI Tools modal
- [../core/06-system-settings.md](../core/06-system-settings.md), [../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md](../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md)

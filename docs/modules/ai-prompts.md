# AI Prompts

> Super-admin editable storage for every prompt that powers an LLM feature: a code registry of 39 keys, named variants per key, per-endpoint variant pins, separate format-rule blocks, and a cached resolver that degrades to the bootstrap default when the DB is unavailable.

The prompts cover proofreading, MD generation, solve-based ANS/SOL generation and checking, auto question tagging, the dashboard Explain tutor, the figure-bbox detector used during MD generation, PDF Batch Import (whole-question boxes, pass-2 stem/parts, anchor detection, generic extraction, paper-name guess) and Smart Import structure inference. Editing prompts shapes global model behaviour, so the permission bar is the same as System Settings: super-admin only.

Three pillars:

1. **Variants** — every key has exactly one built-in row (`is_builtin=True`; `content` NULL means "use the registry default"; reset nulls the content, never deletes the row) plus any number of admin-created custom variants. Exactly one variant per key is active — the default for unpinned endpoints.
2. **Per-endpoint assignment** — an `LLMConfig` can be pinned to a specific variant per key (`prompt_endpoint_assignments`). Unpinned endpoints use the active variant.
3. **Format-rule extraction** — output-formatting rules live in separate `*_FORMAT` / `*_JSON_CONTRACT` items (role `format`), referenced by the owning spec's `format_key`. They are injected into the system prompt (`system_prompt()`) AND re-appended at the end of the user turn (`append_format()`, under the header `OUTPUT FORMAT (mandatory):`) because several GPT builds under-weight system-role formatting rules.

## Files

| File | Role |
|---|---|
| `app/ai_prompts.py` | `PROMPTS_REGISTRY` (OrderedDict of `_PromptSpec`), resolver (`get_prompt`, `render_prompt`, `format_block`, `system_prompt`, `append_format`), cache (`_PROMPT_CACHE` + `_CACHE_LOCK`, `invalidate_cache`), seeding (`ensure_seeded`, `_ensure_builtin`), variant CRUD (`list_variants`, `create_variant`, `update_variant`, `delete_variant`, `set_active`, `reset_builtin`, `set_variant_endpoints`, `unassign_endpoint`), `as_dict`, all `build_*` helpers and all `parse_*` parsers |
| `app/models.py` | `PromptVariant`, `PromptEndpointAssignment`, legacy `PromptOverride` |
| `app/admin.py` | `prompts_page`, `prompts_data`, `prompts_variant_create`, `prompts_variant_save`, `prompts_variant_delete`, `prompts_variant_activate`, `prompts_reset_builtin`, `prompts_variant_assign`, `prompts_unassign`; endpoint lifecycle hooks in `llm_endpoints_delete` / `llm_endpoints_duplicate` |
| `app/__init__.py` | Calls `ai_prompts.ensure_seeded()` at boot after table creation (errors swallowed) |
| `templates/admin_prompts.html` | Admin UI (cards per key, variant pills, editor, endpoint chips) |
| Call sites | `app/ai_tools.py`, `app/dashboard.py` (Explain), `app/pdf_import.py`, `app/toolbox/pdf.py`, `app/smart_import.py` |

## Tables

Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Table | Columns | Notes |
|---|---|---|
| `prompt_variants` (`PromptVariant`) | `id` PK, `prompt_key` String(80) idx, `name` String(120), `content` Text NULL, `is_builtin`, `is_active`, `sort_order`, `updated_at`, `updated_by` FK `users.id` | Unique `(prompt_key, name)`. Exactly one built-in and one active row per key — enforced by `ensure_seeded` + the CRUD helpers, not by DB constraints. Built-in row name is `BUILTIN_VARIANT_NAME = 'Built-in default'` |
| `prompt_endpoint_assignments` (`PromptEndpointAssignment`) | `id` PK, `prompt_key` idx, `endpoint_id` FK `llm_configs.id` ON DELETE CASCADE, `variant_id` FK `prompt_variants.id` ON DELETE CASCADE | Unique `(prompt_key, endpoint_id)` |
| `prompt_overrides` (`PromptOverride`) | `key` PK, `content`, `updated_at`, `updated_by` | LEGACY. Read once by `ensure_seeded` to populate a newly created built-in row's content. Never write new rows |

## Routes

All `@login_required @super_admin_required`, prefix `/admin`.

| Method | Path | Authz | Purpose / shapes |
|---|---|---|---|
| GET | `/admin/prompts` | super-admin | Page |
| GET | `/admin/prompts/data` | super-admin | Calls `ensure_seeded()` then returns `as_dict()`: `{groups: [...], registry: {key: {key, group, label, description, variables, role, format_key, default, variants: [{id, name, is_builtin, is_active, content, has_custom_content, assigned_endpoint_ids, assigned_count, updated_at, updated_by_username}]}}, endpoints: [{id, name, enabled}]}`. A built-in variant with NULL content reports `content = default`, `has_custom_content = false`. If the DB has no rows for a key, a synthetic built-in with `id: null` is returned so the UI still works |
| POST | `/admin/prompts/variant/create` | super-admin | `{key, name, content}` → `{id, key, name}`; 404 unknown key, 400 validation (empty / > 32 000 chars / duplicate name / name > 120) |
| POST | `/admin/prompts/variant/<id>/save` | super-admin | `{name?, content?}`; the built-in accepts content only (name ignored) → `{id, key, name}` |
| POST | `/admin/prompts/variant/<id>/delete` | super-admin | Custom only (built-in → 400). Removes its assignments; if it was active the built-in becomes active → `{ok}` |
| POST | `/admin/prompts/variant/<id>/activate` | super-admin | Makes it the active default for its key → `{ok, key, active_id}` |
| POST | `/admin/prompts/<key>/reset-builtin` | super-admin | Nulls the built-in's content → `{key, value: <bootstrap default>}` |
| POST | `/admin/prompts/variant/<id>/assign` | super-admin | `{endpoint_ids: [...]}` declarative pin set: endpoints in the list are pinned (re-pointed from sibling variants if needed); endpoints previously pinned to this variant but absent are unpinned → `{ok}` |
| POST | `/admin/prompts/<key>/unassign` | super-admin | `{endpoint_id}` drops one pin → `{ok}` |

Every write goes through the `ai_prompts` helpers, which commit and call `invalidate_cache()`.

## Business rules / invariants

### Registered prompts

Declaration order = UI order, grouped by `group`. "→F" marks the `format_key` link.

| # | Keys | Notes |
|---|---|---|
| 1 | `CHECK_SYSTEM` (system →`CHECK_FORMAT`), `CHECK_USER` (user →`CHECK_FORMAT`; vars `asset_type`, `ref_version`, `typed_version`), `CHECK_FORMAT` (format) | STRICT JSON; parser `parse_check_result` |
| 2 | `MD_SYSTEM` (system →`MD_FORMAT`), `MD_USER` (user →`MD_FORMAT`; vars `asset_type`, `source_version`), `MD_FORMAT` (format) | Math delimiters, `8\.` numbering, one MC option per line, `[FIGURE: ...]` sentinel |
| 3 | `SOLVE_GEN_SYSTEM` (system →`SOLVE_GEN_FORMAT`), `SOLVE_GEN_USER` (user →`SOLVE_GEN_FORMAT`; vars `kind`, `target_version`, `asset_type`), `SOLVE_GEN_FORMAT` (format) | ANS / SOL / ANS_TEXT output modes + language + math |
| 4 | `SOLVE_CHECK_SYSTEM` (system →`SOLVE_CHECK_FORMAT`), `SOLVE_CHECK_USER` (user →`SOLVE_CHECK_FORMAT`; vars `kind`, `target_version`, `asset_type`), `SOLVE_CHECK_FORMAT` (format) | STRICT JSON; reuses `parse_check_result` |
| 5 | `TAG_SYSTEM` (system →`TAG_FORMAT`), `TAG_USER` (user →`TAG_FORMAT`; vars `subject_name`, `fields`, `taxonomy`), `TAG_FORMAT` (format) | STRICT JSON; parser `parse_tag_result` |
| 6 | `EXPLAIN_SYSTEM` (system →`EXPLAIN_FORMAT`), `EXPLAIN_INITIAL_USER` (user →`EXPLAIN_FORMAT`), `EXPLAIN_FORMAT` (format) | Math-delimiter rules; follow-up turns are free text with no prompt |
| 7 | `FIGURE_BOX_JSON_CONTRACT` (format; vars `box_array`, `box_corner`, `box_example`), `FIGURE_BOX_SYSTEM` (system; var `json_contract`), `FIGURE_BOX_USER` (user →`FIGURE_BOX_JSON_CONTRACT`; var `box_pairs`) | Parser `parse_figure_boxes` |
| 8 | `PDF_BOX_JSON_CONTRACT` (format; vars `box_array`, `box_corner`, `box_example`), `PDF_QUE_BOX_SYSTEM` / `PDF_SOL_BOX_SYSTEM` (system; var `json_contract`), `PDF_BOX_USER` (user →`PDF_BOX_JSON_CONTRACT`; vars `what`, `box_pairs`) | Parser `parse_question_boxes` (label may be `5`, `5a`, `23-24`; pass 1 still does not split `(a)(b)(c)`) |
| 9 | `PDF_PART_BOX_JSON_CONTRACT` (format; same box vars), `PDF_PART_BOX_SYSTEM` / `PDF_PART_SOL_BOX_SYSTEM` (system; vars `json_contract`, `expected_labels` on SOL), `PDF_PART_BOX_USER` (user →`PDF_PART_BOX_JSON_CONTRACT`; vars `what`, `box_pairs`, `expected_labels`) | Parser `parse_part_boxes`. Pass-2 crop-relative boxes; labels `stem` / `a` / `ci`. Used by PDF split-detect and the Split tool |
| 10 | `PDF_GENERIC_BOX_JSON_CONTRACT` (format; vars `box_array`, `box_corner`, `box_example`), `PDF_GENERIC_BOX_SYSTEM` (system; vars `instruction`, `json_contract`), `PDF_GENERIC_BOX_USER` (user →`PDF_GENERIC_BOX_JSON_CONTRACT`; vars `instruction`, `box_pairs`) | Parser `parse_generic_boxes`. Also powers Toolbox → PDF Tool → Find & Mark "AI detect" |
| 11 | `PDF_ANCHOR_JSON_CONTRACT` (format), `PDF_ANCHOR_SYSTEM` (system; vars `what`, `json_contract`), `PDF_ANCHOR_USER` (user →`PDF_ANCHOR_JSON_CONTRACT`; var `what`) | Parser `parse_question_anchors` (`segment` method) |
| 12 | `PDF_PAPER_NAME_SYSTEM` (system, no format item), `PDF_PAPER_NAME_USER` (user; vars `filename`, `subjects`) | STRICT JSON `{paper, confidence}`; parser `parse_paper_name` |
| 13 | `SMART_IMPORT_SYSTEM` (system, no format item), `SMART_IMPORT_USER` (user; vars `subject`, `versions`, `tree`) | Reply is one JSON object of folder-level defaults parsed by `smart_import._parse_json_object` (not a registry parser) |

The `*_JSON_CONTRACT` items serve double duty: they are substituted into the system prompt via `{{json_contract}}` AND appended to the user turn as the format block — one source of truth per contract. Figure + PDF parsers share `_normalize_box` and honour `PDF_IMPORT_COORD_ORDER`.

### Math delimiter contract

`MD_FORMAT`, `SOLVE_GEN_FORMAT` and `EXPLAIN_FORMAT` share `_MATH_DELIMITER_RULES`: only `$...$` / `$$...$$` for math; never `[ ]` display blocks or bare `(AD)` labels (a GPT habit). The only post-generation rewrite is `normalize_inline_math` (tight `$ x $` → `$x$`). Do not add bracket / paren rewriting — it breaks KaTeX.

### Variable substitution

- Syntax is `{{name}}` (`_VAR_RE = \{\{(\w+)\}\}`). Single `{` / `}` are literal so JSON examples such as `{"status": "ok"}` pass through unchanged.
- Only names declared in the spec's `variables` list are substituted. A declared name that the call site did not pass stays literal (`{{name}}`) so prompt-design errors surface in the model's reply rather than crashing.
- Single pass: no recursive expansion of `{{json_contract}}` bodies.

Helper builders (all accept a trailing `endpoint_id=None` and append the user-turn format block themselves):

- `build_check_user_text(typed_version, ref_version, asset_type, endpoint_id=None)`
- `build_md_user_text(source_version, asset_type, endpoint_id=None)`
- `build_solve_gen_user_text(kind, target_version, endpoint_id=None)`, `build_solve_check_user_text(kind, target_version, endpoint_id=None)`
- `build_tag_user_text(subject_name, fields, taxonomy, endpoint_id=None)` (+ `build_tag_taxonomy(subject_id, fields)`, `parse_tag_result`, `TAG_FIELDS`, `TAG_FIELD_LABELS`)
- `build_explain_initial_user_text(endpoint_id=None)`
- `build_figure_box_system(coord_order, endpoint_id=None)` / `build_figure_box_user_text(coord_order, endpoint_id=None)`
- `build_pdf_box_system(asset_type, coord_order, endpoint_id=None)` / `build_pdf_box_user_text(asset_type, coord_order, endpoint_id=None)`
- `build_pdf_part_system(asset_type, coord_order, expected_labels=None, endpoint_id=None)` / `build_pdf_part_user_text(asset_type, coord_order, expected_labels=None, endpoint_id=None)`
- `build_pdf_generic_system(instruction, coord_order, endpoint_id=None)` / `build_pdf_generic_user_text(instruction, coord_order, endpoint_id=None)`
- `build_pdf_anchor_system(asset_type, endpoint_id=None)` / `build_pdf_anchor_user_text(asset_type, endpoint_id=None)`
- `build_pdf_paper_name_system(endpoint_id=None)` / `build_pdf_paper_name_user_text(filename, subjects, endpoint_id=None)`
- `pdf_box_order_vars(coord_order)` → `{box_array, box_corner, box_example, box_pairs}` for `xyxy` or `yxyx`

Call sites must pass `config.id` as `endpoint_id` and use `ai_prompts.system_prompt(key, config.id)` for `*_SYSTEM` prompts that carry a `format_key`; otherwise per-endpoint pins are silently ignored.

### Resolver flow

```
get_prompt(key, endpoint_id=None):
  KeyError on unknown key
  cache[(key, endpoint_id)] hit → return
  _load_resolved:
    1. PromptEndpointAssignment(key, endpoint_id) → that variant
    2. else the is_active variant for key
    3. variant content blank/NULL, or any DB exception → None
  None → PROMPTS_REGISTRY[key]['default']
  cache, return

render_prompt(key, endpoint_id, **vars) = get_prompt + {{var}} substitution (declared vars only)
format_block(key, endpoint_id, **vars)  = render_prompt(spec.format_key) or ''
system_prompt(key, endpoint_id, **vars) = base + "\n\n" + format_block   (when format_key)
append_format(key, text, endpoint_id, **vars) = text + "\n\nOUTPUT FORMAT (mandatory):\n" + format_block
```

Every variant / assignment write calls `invalidate_cache()` (whole cache, or per key via `invalidate_cache(key)`). The cache is a module-level dict guarded by a `Lock` — per process.

### Seeding and lifecycle

- `ensure_seeded()` runs at boot (`app/__init__.py`) and on every `GET /admin/prompts/data`. For each registry key: create the built-in row if missing (copying any legacy `prompt_overrides` content into it the FIRST time only), and guarantee one active variant (activates the built-in if none). Existing rows are never touched, so admin edits survive restarts. DB errors are swallowed and rolled back.
- `_validate_content`: strips BOM + trailing whitespace; rejects empty or > `MAX_PROMPT_CHARS = 32000`.
- `delete_variant`: built-in rejected; removes the variant's assignments; re-activates the built-in when the deleted variant was active.
- `set_active`: clears `is_active` on siblings, sets it on the target.
- `reset_builtin`: nulls the built-in's `content` (row stays).
- `set_variant_endpoints(variant_id, endpoint_ids)`: validates ids against `llm_configs`; deletes this variant's pins not in the list; re-points sibling pins that are in the list; adds new pins.
- Endpoint lifecycle (`app/admin.py`): create → no assignments (uses active variants); duplicate → clones the source's assignment rows; delete → explicit assignment cleanup + FK CASCADE, then `invalidate_cache()`.

### Parsers

- All LLM-JSON parsers route through `_strip_reasoning` (drops `<think>`-style blocks — tags `think|thinking|reason|reasoning|analysis|scratchpad`) → `_balanced_spans` → `_json_candidates(text, prefer='['|'{')` (balanced spans, longest first). Reuse `_json_candidates` for any new LLM-JSON parser.
- Box parsers (`parse_question_boxes`, `parse_part_boxes`, `parse_generic_boxes`, `parse_figure_boxes`) fall back to `_salvage_box_objects` + targeted regexes so one corrupt object cannot drop a whole page.
- `_normalize_box(coords, img_w, img_h, coord_order)`: applies axis order (`xyxy` / `yxyx`) then auto-detects range — max ≤ 1 → fractional; max ≤ 1024 → /1000; else pixels divided by the downscaled dims the model saw, else /max.
- `parse_check_result` returns `{status: ok|issues, issues: [{severity, location, description}]}` or `None`.
- `parse_paper_name` returns `(paper_or_None, confidence)`.

### UI (`templates/admin_prompts.html`)

One card per key:

- Variant pills (built-in marked when active; per-pill count badge = explicit pin count; the active pill shows `default (+N)`), plus **Add variant** (seeds from the selected variant's content).
- Editor textarea with per-variant unsaved-edit tracking (`edits` Map keyed by variant id — switching pills never loses work), char counter, role badge (`system` purple / `user` cyan / `format` orange), expandable bootstrap-default panel.
- Buttons: Save, Set active, Rename (custom), Delete (custom) / Reset (built-in, enabled only when edited).
- Endpoint chips under the editor: pinned endpoints with × to unpin, plus a picker to pin more. Pin / unpin POST immediately.
- Group TOC, navigate-away guard, cross-links to System Settings / LLM Endpoints.

### Adding a new prompt

1. Add `_DEFAULT_NEW_KEY = (...)` near its category in `app/ai_prompts.py`; put output-format rules in a separate `_DEFAULT_NEW_KEY_FORMAT`.
2. Append `('NEW_KEY', _prompt(group=..., label=..., description=..., default=..., variables=[...], role='system'|'user'|'format', format_key='NEW_FORMAT'|None))` to `PROMPTS_REGISTRY`.
3. At the call site use `ai_prompts.system_prompt('NEW_KEY', config.id)` / `render_prompt('NEW_KEY', endpoint_id=config.id, **vars)` + `append_format` for the user turn.
4. The UI and seeding auto-discover it (`ensure_seeded` creates the built-in row on next boot / data fetch).
5. Write the purpose in `description` — it is shown verbatim in the admin UI.

### Checklist for any new LLM-powered feature

Never inline a hard-coded prompt at a call site. Always:

1. Register every system + user (+ format) prompt in `PROMPTS_REGISTRY`. Reuse an existing prompt where the contract matches (Toolbox Find & Mark "AI detect" reuses `PDF_GENERIC_BOX_*`) and mention the new surface in its `description`.
2. Register every tunable in `app/settings.py` REGISTRY and `app/config.py`; no magic numbers.
3. Pick the endpoint via a `*_DEFAULT_LLM` setting (`choices_fn=_llm_endpoint_choices`) and pass `config.id` as `endpoint_id` to the resolver.
4. Stream long-running calls over SSE (job → progress → done) with a real Stop via `ai_tools.new_job` / `pdf_import.new_job` and `app.parallel.run_parallel`; never a single blocking POST that can 504.

## Settings & config keys

None owned by this module. Prompts read `PDF_IMPORT_COORD_ORDER` indirectly through the `build_*` helpers (callers pass it in). See [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

All routes `@login_required @super_admin_required` (same bar as System Settings). The resolver itself is unauthenticated library code used by every AI feature. See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

None. Resolution is synchronous with a process-local cache; `_CACHE_LOCK` makes reads/writes thread-safe for the parallel batch workers in [ai-tools.md](ai-tools.md).

## Gotchas

1. Do not change the JSON contracts of `CHECK_FORMAT`, `SOLVE_CHECK_FORMAT`, `TAG_FORMAT`, `FIGURE_BOX_JSON_CONTRACT`, `PDF_BOX_JSON_CONTRACT`, `PDF_PART_BOX_JSON_CONTRACT`, `PDF_GENERIC_BOX_JSON_CONTRACT`, `PDF_ANCHOR_JSON_CONTRACT`, `PDF_PAPER_NAME_SYSTEM` without updating the coupled parser (`parse_check_result`, `parse_tag_result`, `parse_figure_boxes`, `parse_question_boxes`, `parse_part_boxes`, `parse_generic_boxes`, `parse_question_anchors`, `parse_paper_name`). This applies to every admin-created variant too — a variant that breaks the contract breaks the feature for whichever endpoints resolve to it.
2. Variable syntax is `{{name}}`, not `{name}`. New variable = declare in `variables` + use `{{name}}` in the default + pass the kwarg at the call site. Undeclared kwargs are ignored silently.
3. Keep the `[FIGURE: ...]` sentinel in `MD_FORMAT`; `ai_tools._embed_figures` / `FIGURE_RE` depend on it.
4. Cache is per process — multi-worker deployments see stale prompts until each worker refreshes (same caveat as system settings).
5. 32 000-character limit per variant content (`MAX_PROMPT_CHARS`).
6. Plaintext only — never put secrets in prompts.
7. Built-in rows are never deleted; reset nulls the content. `delete_variant` re-activates the built-in when the deleted variant was active.
8. `MD_FORMAT` numbering: escaped dots after question numbers (`8\.`), one MC option per line. Do not revert to bare `8. `.
9. The resolver works without the DB — every step degrades to the bootstrap default, so boot order or a broken DB never blocks LLM calls (and never raises from `get_prompt` except `KeyError` for an unknown key).
10. A pin wins over the active variant; the active variant wins over the built-in default only when it has non-blank content. A pinned variant with blank content (only possible for the built-in) falls through to the registry default, not to the active variant.
11. `_load_resolved` needs `endpoint_id` to be truthy for pins to apply — passing `0`/`None` resolves to the active variant.
12. `as_dict()` synthesises an `id: null` built-in before seeding; the UI must not POST saves against a null id (the data route seeds first, so this only matters for callers that skip `/prompts/data`).

## Related

- [ai-tools.md](ai-tools.md) — the primary consumer (check / MD / solve / tag prompts), `LLMConfig`, per-feature default endpoints
- [pdf-import.md](pdf-import.md) — PDF box / anchor / generic / paper-name prompts and the coordinate-order contract
- [dashboard.md](dashboard.md) — Explain tutor (`EXPLAIN_*`)
- [md-format.md](md-format.md) — `normalize_inline_math`, KaTeX rendering constraints behind the math delimiter contract
- [../core/06-system-settings.md](../core/06-system-settings.md), [../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md](../decisions/ADR-004-db-backed-settings-with-env-bootstrap.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md)

# Subject AI tuning (Auto Tag per subject)

> Subject admins shape how Auto Tag classifies THEIR subject — free-text instructions, per-node taxonomy hints, prompt preview, a no-write evaluation run against already-tagged questions, and a teacher-corrections report that can be fed back into the prompt — without touching the super-admin prompt registry.

The super-admin prompt registry ([ai-prompts.md](ai-prompts.md)) decides the *frame* of the Auto Tag prompt (variants, endpoint pins, JSON contract). This module adds a **subject layer** underneath it. It deliberately does not fine-tune a model ([ADR-012](../decisions/ADR-012-subject-prompt-layer-over-fine-tuning.md)): "learning" here is (1) structured hints on taxonomy nodes, (2) free-text instructions, (3) a correction log whose aggregated disagreement patterns are injected into the prompt, plus (4) an evaluation loop so the admin can measure whether a change helped.

## Files

| File | Role |
|---|---|
| `app/subject_ai_service.py` | Note CRUD (`get_note`, `save_note`, `note_as_dict`), taxonomy hints (`taxonomy_tree`, `set_description`), label helpers (`normalize_label`, `label_text`, `compare_labels`, `question_labels`, `labels_from_form_state`, `display_to_labels`), correction log (`log_corrections`, `corrections_report`, `clear_corrections`, `correction_patterns_text`), evaluation (`sample_tagged_questions`, `evaluate_one`, `iter_tag_evaluate`) |
| `app/subject_ai.py` | Blueprint `subject_ai_bp` (`url_prefix='/admin'`): page, data, note / hint / correction routes, `preview`, `evaluate` (SSE), `log_tag_corrections` |
| `app/ai_tools.py` | `build_tag_prompt(question, versions, fields, config, image_max_dim, source_path, include_images=True)` — the single place the Auto Tag prompt is assembled (subject note + correction patterns included); `suggest_tags` calls it and now also returns `confidence` / `reasons` |
| `app/ai_prompts.py` | `{{subject_instructions}}` variable on `TAG_USER`; `format_subject_instructions`, `SUBJECT_INSTRUCTIONS_HEADER`, `CORRECTION_PATTERNS_HEADER`; `system_prompt_with_body`; `build_tag_taxonomy` renders `description` hints and skips hidden nodes; `parse_tag_result` returns `confidence` / `reasons`; `TAG_FORMAT` documents the optional diagnostics keys |
| `app/models.py` | `SubjectPromptNote`, `TagCorrection`; `description` on `Topic` / `Subtopic` / `Chapter` / `Subchapter` |
| `app/__init__.py` | Boot patch: creates the two tables, adds `description VARCHAR(300) NULL` to the four taxonomy tables |
| `templates/admin_subject_ai.html` | The tuning page (instructions card, taxonomy hints, run settings, preview, evaluate, report) |
| `templates/partials/edit_question_modal_js.html` | `_lastAutoTagSuggestion` + `_logTagCorrections(qId)`: after **Suggest tags** the suggestion is remembered; **Save Tags** on the same question posts the (suggested vs saved) pairs. Auto Tag status also shows the model's per-field reasons |
| `templates/base.html` | Admin dropdown → **AI Tagging Tuning** (`subject_ai.index`) |

## Tables

Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Table / model | Columns | Notes |
|---|---|---|
| `subject_prompt_notes` (`SubjectPromptNote`) | `id`, `subject_id` FK `subjects.id` CASCADE, `feature` (`'tag'`), `mode` (`append` / `replace`), `content` TEXT NULL, `examples_limit` INT (0 = off, max 30), `updated_at`, `updated_by` FK `users.id` | unique `(subject_id, feature)`. `feature` is a feature key, not a `PROMPTS_REGISTRY` key. Blank content + `examples_limit 0` deletes the row |
| `tag_corrections` (`TagCorrection`) | `id`, `question_id` FK `questions.id` CASCADE, `subject_id` idx, `field` (a `TAG_FIELDS` key), `suggested` VARCHAR(500), `saved` VARCHAR(500), `agreed` bool, `reason` VARCHAR(400), `model` VARCHAR(200), `user_id` FK `users.id`, `created_at` | Display **names**, never IDs (list fields comma-joined, sorted). One row per requested field where either side has a value |
| `topics`, `subtopics`, `chapters`, `subchapters` | `+ description VARCHAR(300) NULL` | Teachers' one-line hint; rendered as `Name — hint` in the taxonomy block. Never shown to students |

## Routes

All `@login_required`; subject routes add `@subject_admin_required` (subject id is in the URL so the decorator fires; super-admins pass). Prefix `/admin`.

| Method | Path | Authz | Purpose / shapes |
|---|---|---|---|
| GET | `/admin/subject-ai` | admin | 302 to `/admin/subjects/<first admin subject>/ai`; 403 when the user administers nothing |
| GET | `/admin/subjects/<subject_id>/ai` | subject-admin | Page (`admin_subject_ai.html`) with a subject switcher over `get_user_admin_subjects()` |
| GET | `/admin/subjects/<subject_id>/ai/data` | subject-admin | `{subject, note, builtin_system, taxonomy: {topics[{id,kind,name,description,hidden,children[]}], chapters[...]}, report, endpoints[{id,name,model_name,supports_vision,kind,max_concurrency}], default_endpoint_id, ai_enabled}`; `?days=N` scopes the report |
| POST | `/admin/subjects/<subject_id>/ai/note` | subject-admin | `{mode, content, examples_limit}` → `{success, note}`; 400 on validation (`replace` needs content; ≤ 8000 chars; limit clamped 0–30) |
| POST | `/admin/subjects/<subject_id>/ai/taxonomy-description` | subject-admin | `{kind: topic|subtopic|chapter|subchapter, id, description}` → `{success, description}`; 404 when the node is not in this subject; 400 > 300 chars |
| GET | `/admin/subjects/<subject_id>/ai/corrections` | subject-admin | `corrections_report` → `{total, agreed, rate, fields[{field,label,total,agreed,rate}], confusions[{field,label,suggested,saved,count}], recent[...]}`; `?days=N` |
| POST | `/admin/subjects/<subject_id>/ai/corrections/clear` | subject-admin | Deletes every row for the subject → `{success, deleted}` |
| POST | `/admin/subjects/<subject_id>/ai/preview` | subject-admin | `{question_id | qid, fields[], versions[], endpoint_id?}` → `{success, question, endpoint, system, user, image_count, text_block_count, subject_note, existing}`. Assembles via `build_tag_prompt(include_images=False)`; **no LLM call**. 400 when `AI_TOOLS_ENABLED` is off; `{success:false, error}` (200) when the question has no usable content |
| GET | `/admin/subjects/<subject_id>/ai/evaluate` | subject-admin | SSE. Query `question_ids` (csv, subject-scoped) **or** `sample` (int, default 20, ≤ 500 random tagged non-stem questions), `fields`, `versions`, `endpoint_id?`, `parallel`. Events as [ai-tools.md](ai-tools.md) plus `type: issue` (compared, not all agree); compared events carry `detail{field: {existing, suggested, applicable, agree, reason, confidence}}`, `question_id`, `qid`, `agreed`, `applicable`. `done.stats = {compared, skipped, errors, agreed, applicable, rate, fields{f: {applicable, agreed, rate}}}`. **Writes nothing.** Uses `admin._ai_stream` / `_ai_parallel` / `_ai_load_endpoint('AUTOTAG_DEFAULT_LLM')`; cancel via `POST /admin/questions/ai/cancel` |
| POST | `/admin/questions/<question_id>/ai/tag-corrections` | admin + explicit subject check | `{fields[], suggested_display{field: name|[names]|int}, saved{readCurrentTagFormState shape}, model?, reasons{field: str}?}` → `{success, logged}`. Called by the edit modal after a successful Save Tags that followed Suggest tags |

## Business rules / invariants

- **Prompt assembly has one owner:** `ai_tools.build_tag_prompt`. `suggest_tags`, batch auto-tag, per-slot suggest, preview and evaluate all go through it, so what the admin previews is exactly what is sent.
- **Modes.** `append`: the note text is rendered under `SUBJECT_INSTRUCTIONS_HEADER` into the `{{subject_instructions}}` slot of `TAG_USER`. `replace`: the note text becomes the `TAG_SYSTEM` **body** via `system_prompt_with_body`; the `TAG_FORMAT` block is still attached to the system prompt and re-appended to the user turn, so a subject admin cannot break `parse_tag_result`. In replace mode the note is not repeated in the user turn.
- **Slot resilience.** If a custom `TAG_USER` variant lacks `{{subject_instructions}}`, `build_tag_user_text` appends the block before the format contract instead of dropping it.
- **Correction patterns** (`examples_limit > 0`): `correction_patterns_text` aggregates *disagreed* rows for the requested fields into the top-N `(field, suggested → saved, count)` lines under `CORRECTION_PATTERNS_HEADER`. No question content is sent (many questions are image-only), only the bias pattern.
- **Taxonomy block:** hidden subtopics / subchapters are excluded; `description` is appended as `Name — hint` (whitespace collapsed). `TAG_SYSTEM` tells the model the hint wins over its own reading of the name.
- **Comparison semantics** (`compare_labels`): case-insensitive string equality for scalars, set equality for `minor_topics` / `subtopics`; a field is `applicable` only when the existing side has a value; `level` is compared as a string.
- **Evaluation** skips stems (tags live on parts) and questions with no existing value in any requested field; `sample_tagged_questions` picks random non-stem questions holding at least one requested field. It never calls `apply_tags`.
- **Correction logging** fires only when Save Tags follows Suggest tags for the *same* question in the modal (`_lastAutoTagSuggestion.questionId` match), then clears the memo. Rows record every requested field where either side has a value (`agreed` may be true); the report shows agreement rate, per-field stats, top confusions and recent rows.
- `suggest_tags` returns `confidence{field: 0..1}` and `reasons{field: str}` when the model supplies them (optional keys in `TAG_FORMAT`); `apply_tags` ignores them. The modal shows reasons in a collapsible list; evaluate shows them in the disagreement table.

## Settings & config keys

None new. Uses `AI_TOOLS_ENABLED`, `AUTOTAG_DEFAULT_LLM`, `LLM_IMAGE_MAX_DIM` ([../core/06-system-settings.md](../core/06-system-settings.md)). Limits are module constants in `subject_ai_service`: `MAX_NOTE_CHARS = 8000`, `MAX_DESCRIPTION_CHARS = 300`, `MAX_EXAMPLES_LIMIT = 30`.

## Permissions

- Everything under `/admin/subjects/<subject_id>/ai*` is `@subject_admin_required` (subject id in the URL). This is a **lower trust tier** than the super-admin prompt page — which is exactly why the format contract is server-controlled and a note can only affect its own subject.
- `/admin/subject-ai` is `@admin_required` and redirects to a subject the user administers.
- `/admin/questions/<id>/ai/tag-corrections` is `@admin_required` + explicit `question.subject ∈ get_user_admin_subjects()` (super-admins pass).
- See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

`evaluate` streams through `admin._ai_stream` (job → events → done, cancel registry in `ai_tools`), runs under `app.app_context()`, and honours the cloud-only parallel gate (`_ai_parallel`). Workers are pure (`evaluate_one`); counters and events are shaped on the consumer thread, like `iter_auto_tag`.

## Gotchas

1. `replace` mode replaces the **body only**. If an admin pastes the full built-in text including its JSON contract, the contract appears twice — harmless but noisy. The UI's "Built-in rules" panel shows the body as resolved for the default endpoint (`get_prompt('TAG_SYSTEM')`, no pin).
2. Correction rows store display names. Renaming a topic afterwards leaves old rows with the old name; the patterns block will then mention a name that is no longer in the taxonomy. Clear the log after a big taxonomy rename.
3. `examples_limit` injects *disagreements only*; a subject with a high agreement rate produces a short or empty block. The block is recomputed per call (one query), no cache.
4. `sample_tagged_questions` uses `ORDER BY RAND()` — fine for the sizes involved (≤ 500 × 2 rows), not for a full-library sweep; pass `question_ids` for a fixed benchmark set so runs are comparable.
5. Hidden nodes are no longer sent to the model. A subject that relied on the model picking a hidden subtopic will see those fields come back `null` / unmatched — un-hide the node or accept it.
6. Preview does not call the model and is therefore safe to hammer; evaluate does (one call per question).
7. The per-question correction route accepts whatever the browser sends as `saved`; it re-resolves IDs to names server-side and never writes to the question.

## Related

- [ai-tools.md](ai-tools.md) — `suggest_tags`, `iter_auto_tag`, endpoint loading, cancel/parallel plumbing
- [ai-prompts.md](ai-prompts.md) — `TAG_*` registry entries, `system_prompt_with_body`, variable rules
- [admin-questions.md](admin-questions.md) — the edit modal that emits correction rows
- [admin-panel.md](admin-panel.md) — Topics / Chapters pages (where nodes are created; hints are edited here)
- [../decisions/ADR-012-subject-prompt-layer-over-fine-tuning.md](../decisions/ADR-012-subject-prompt-layer-over-fine-tuning.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md)

# Reference — schema history

> There is no migration framework. This is the consolidated record of how the schema reached its current shape, so you can recognise a deployment that predates a change. Current schema: `app/models.py`; how to change it: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md). Append a row whenever you add a boot patch.

Order is roughly chronological (oldest first). "Boot patch" = idempotent code in `create_app()` (`app/__init__.py`); "script" = a historical root-level one-off, now superseded; "create_all" = created by `init_db.py` / `db.create_all()` only.

| Change | Mechanism | Notes |
|---|---|---|
| Initial tables: `users`, `subjects`, `topics`, `subtopics`, `questions`, `question_assets` (`language` enum `EN/CH/BI`, `file_format` `IMG/DOC`), `question_minor_topics`, `question_subtopics` | create_all | |
| `questions.major_subtopic_id`, `questions.description` | create_all (nullable, no patch needed) | |
| `users.is_super_admin`; `user_subject_permissions` (per-subject `viewer/user/admin`) | create_all | `users.is_admin` left as legacy |
| `chapters`, `subchapters`; `questions.chapter_id`, `subchapter_id` (`ON DELETE SET NULL`); `questions.answer`, `comment` | create_all | |
| `saved_filters` | create_all | |
| `generated_files` (background generation tracking) | create_all | stale `pending/generating` reset to `failed` at boot |
| `questions.correct_percentage` | create_all | |
| `saved_filters.is_starred`, `is_shared`; `saved_generation_profiles` | script `migrate_starring.py` | |
| `question_assets.file_format` enum widened to `IMG/DOC/MD` | script `migrate_md_format.py` | |
| `saved_question_sets` | create_all | |
| `system_settings` | boot patch (`create checkfirst`) | |
| `file_sections`, `file_shares`; `generated_files.section_id` (+ index + FK `SET NULL`), `generated_files.manual_position` | boot patch (`INFORMATION_SCHEMA` guard) | |
| `question_assets.language` → `version`, enum widened to `EN/CH/BI/ENO/CHO` | boot patch (`CHANGE COLUMN`, or `MODIFY` if already renamed) and script `migrate_versions.py` | unique index carries over |
| `llm_configs` | boot patch (`create checkfirst`) | |
| `llm_configs.kind`, `max_concurrency` (+ back-fill `cloud`/4 for well-known hosted API hosts), `service_tier`, `service_tier_batch`, `api_protocol`, `reasoning_effort`, `reasoning_summary`, `reasoning_max_tokens`, `request_extra_json` | boot patch | |
| `question_assets.check_state`, `check_result`, `checked_at`, `check_raw` | boot patch | |
| `questions.verified`, `verified_at`, `verified_by` | boot patch | |
| `prompt_overrides` (legacy), `prompt_variants`, `prompt_endpoint_assignments` | boot patch (`create checkfirst`) + `ai_prompts.ensure_seeded()` migrates override content into built-in variants once | |

Non-schema data conventions that behave like migrations:

- `system_settings.FILE_BROWSER_EXTRA_ROOTS` — JSON list row managed outside the REGISTRY.
- JSON blobs in `filter_data` / `options_data` / `generation_options` gained keys over time (`version_priority` replacing `preferred_language`, `sort_group_order`, `format_priority`); readers accept old shapes via `utils.parse_version_priority(raw, legacy_preferred)` and defaults. Never rewrite stored blobs.
- Storage relocation (generated files → `User/<name>/generated/`, thumbnails → `System/`, `Source_PDF` → `Shared/_archive`) is a filesystem migration via `cli.py migrate-storage`, not a schema change.

# Status — living implementation map

> Read second. Check the relevant row before you code so you do not rebuild something that exists. Update the row in the same turn whenever a component changes state.

Key: `[done]` implemented, tested (where tests exist), documented · `[partial]` skeleton exists, logic/UI incomplete · `[pending]` not started · `[blocked]` waiting on a named dependency

## Backend modules

| Area | State | Code | Doc | Notes |
|---|---|---|---|---|
| Auth (login / logout / register) | `[done]` | `app/auth.py` | [core/02](docs/core/02-auth-and-permissions.md) | Flask-Login sessions; first user bootstrap via `init_db.py` (`admin/admin123`) |
| Subject RBAC (super admin / admin / user / viewer) | `[done]` | `app/utils.py`, `app/models.py` | [core/02](docs/core/02-auth-and-permissions.md) | Decorators + `User` helper methods |
| Dashboard filter / search / multi-sort / pagination | `[done]` | `app/dashboard.py` | [modules/dashboard.md](docs/modules/dashboard.md) | Python-side sort with manual block order; leaves-only list grouped under stem headers |
| Dashboard Selection + Set Operations (union / intersection / difference, scratch sets) | `[done]` | `app/dashboard.py`, `templates/dashboard.html` | [modules/dashboard.md](docs/modules/dashboard.md) | Selection independent of filter (ADR-008) |
| Explain (AI tutor chat, SSE streaming) | `[done]` | `app/dashboard.py` | [modules/dashboard.md](docs/modules/dashboard.md) | Gated on `AI_TOOLS_ENABLED`; endpoint picker for admins |
| Document generation (.docx, answer modes, compact MC keys, split ZIP, styles) | `[done]` | `app/generator.py` | [modules/generator.md](docs/modules/generator.md) | Background thread; status polling; `hierarchy_mode` + `resolve_render_plan` |
| PDF output (lazy, on demand from My Files) | `[done]` | `app/generator.py`, `app/word_com.py` | [modules/generator.md](docs/modules/generator.md) | Requires Word; rejected without it |
| Viewer / presentation mode | `[done]` | `app/generator.py`, `templates/viewer.html` | [modules/generator.md](docs/modules/generator.md) | Does not extend `base.html`. Slides are leaves; stem QUE in `#stemPanel` |
| My Files (sections, drag-move, share, ZIP, rename, auto-refresh) | `[done]` | `app/user.py` | [modules/my-files.md](docs/modules/my-files.md) | Files live under `User/<name>/generated/` |
| Saved search profiles (star, share, load on dashboard) | `[done]` | `app/user.py` | [modules/my-files.md](docs/modules/my-files.md) | |
| Saved generation presets | `[done]` | `app/user.py` | [modules/my-files.md](docs/modules/my-files.md) | |
| Question Sets (per-subject saved QID lists, set algebra builder) | `[done]` | `app/user.py` | [modules/question-sets.md](docs/modules/question-sets.md) | |
| Admin: Topics / Chapters CRUD + reorder | `[done]` | `app/admin.py` | [modules/admin-panel.md](docs/modules/admin-panel.md) | |
| Admin: Manage Subjects | `[done]` | `app/admin.py` | [modules/admin-panel.md](docs/modules/admin-panel.md) | Delete blocked while questions exist |
| Admin: Users + per-subject permissions, username policy, rename moves home dir | `[done]` | `app/admin.py`, `app/utils.py` | [modules/admin-panel.md](docs/modules/admin-panel.md) | |
| Admin: Export / Import CSV | `[done]` | `app/admin.py` | [modules/admin-panel.md](docs/modules/admin-panel.md) | |
| Admin: Database Health + orphan sync + anomaly jump links | `[done]` | `app/admin.py`, `app/ingestor.py` | [modules/admin-panel.md](docs/modules/admin-panel.md) | |
| Question Management (list, filters, Status filter, select-all-matching) | `[done]` | `app/admin.py` | [modules/admin-questions.md](docs/modules/admin-questions.md) | Create/rename/delete understand stems/parts; Part column + Tree filter (`all`/`roots`/`leaves`); bulk Combine / Split |
| Unified Edit modal (Tags / Assets / Details, Prev/Next, rename) | `[done]` | `templates/partials/edit_question_modal*.html` | [modules/admin-questions.md](docs/modules/admin-questions.md) | Shared by dashboard + admin. Rename cascades descendants. Details: breadcrumb, create child, set parent, Split link, Combine parts. Assets: WHOLE archive strip on roots. Stems cannot be tagged |
| Batch ops (update tags, delete, delete assets, copy/move assets, Set MCQ ANS, Generate IMG, verify, check-state) | `[done]` | `app/admin.py`, `app/batch_image_gen.py`, `app/question_combine.py` | [modules/admin-questions.md](docs/modules/admin-questions.md) | SSE where long-running. Whole-question delete 409s unless `delete_children` when a stem is selected. Bulk **Combine parts** (collapse overlapping stems) and **Split into parts** (exactly one id → crop page) |
| AI Tools: proofread / generate MD / solve gen+check / auto-tag / verify | `[done]` | `app/ai_tools.py`, `app/llm_client.py` | [modules/ai-tools.md](docs/modules/ai-tools.md) | Batch SSE + per-slot sync routes; parallel for cloud endpoints. Ancestor QUE images prepended for parts (tag / MD QUE / Explain); auto-tag skips stems |
| LLM Endpoints (CRUD, Chat/Responses protocol, reasoning, service tier, duplicate, chat console) | `[done]` | `app/admin.py`, `app/llm_client.py` | [modules/ai-tools.md](docs/modules/ai-tools.md) | Super admin |
| AI Prompts (registry, variants, per-endpoint pins, format blocks) | `[done]` | `app/ai_prompts.py` | [modules/ai-prompts.md](docs/modules/ai-prompts.md) | Seeded at boot. Includes PDF pass-2 `PDF_PART_*` keys |
| PDF Batch Import (3-step wizard, LLM/refine/segment detection, review, commit) | `[done]` | `app/pdf_import.py`, `app/pdf_layout.py` | [modules/pdf-import.md](docs/modules/pdf-import.md) | Plan labels `5` / `5a` / `23-24`; nested parts (`4d` + `4di`); roles derived from labels; manual Add-part-below / Delete key; `depends_prev` → `needs_prev_parts` at commit. Optional pass-2 part split (`split_parts_default` seeds the checkbox). Step 1 **Pages** (1-based source range before A3 split). Commit uses `ensure_question`; split roots write `WHOLE` from pass-1 `source_box` before tight stem QUE |
| PDF Import AI agent (outline → locate → split → verify, attention list) | `[done]` | `app/pdf_agent.py` | [modules/pdf-agent.md](docs/modules/pdf-agent.md) | `/admin/pdf-import/agent` SSE + `/attention`; Step 2 **Run AI agent** after Load PDF (no Setup auto-start); stage strip, Needs-attention panel with row highlights; agent runs on a daemon thread (15 s SSE heartbeats, `attach=1` rejoins — a dropped tab no longer aborts verify); per-page verify ignores parts already on other pages; `PDF_AGENT_*` prompts/parsers/settings; exercised against ICT DSE 2023 P1B (12 QUE pages). ADR-010 |
| Question hierarchy (stem / parts) | `[done]` | `app/hierarchy.py`, `app/question_split.py`, `app/question_combine.py`, `app/models.py` | [modules/question-hierarchy.md](docs/modules/question-hierarchy.md) | Schema, grammar, ingest/create/rename/delete, dashboard grouping, generator/viewer render plan, admin tree UI, IMG Split (stitch multi-image QUE then SSE pass-2 `detect_parts` once) + Combine (restore WHOLE / reconstruct), PDF two-pass, AI ancestor images, `derive_roles` / `next_part_label`, `needs_prev_parts` (earlier siblings as background). `WHOLE` asset_type is root-only unsplit original. ADR-009 |
| Ingestion (library scan) + Smart Import (folder heuristics, AI analyze) | `[done]` | `app/ingestor.py`, `app/smart_import.py`, `cli.py` | [modules/ingestion.md](docs/modules/ingestion.md) | QNO token from `hierarchy.py`; sync skips stems that still have children. Legacy `/admin/ingestion` redirects to Smart Import |
| Unified Storage tree + `safe_join` + per-user dirs | `[done]` | `app/storage.py` | [core/05](docs/core/05-storage-and-paths.md) | `cli.py migrate-storage` is idempotent |
| File Browser (user scope + super-admin scope, extra roots, selector modal) | `[done]` | `app/files.py`, `app/files_service.py` | [modules/file-browser.md](docs/modules/file-browser.md) | |
| Toolbox PDF Tool (split, adjust, assemble, Find & Mark, redact, export, sessions) | `[done]` | `app/toolbox/pdf.py`, `app/pdf_tools.py`, `app/pdf_text.py` | [modules/toolbox-pdf.md](docs/modules/toolbox-pdf.md) | Admin only; OCR needs Tesseract |
| Markup PWA (canvas, autosave, share target) | `[done]` | `app/toolbox/markup.py`, `app/pwa.py`, `static/markup/` | [modules/markup.md](docs/modules/markup.md) | iOS has no Web Share Target |
| DOC source format (Word COM merge, thumbnails, lock model) | `[done]` | `app/word_com.py`, `app/doc_thumbnails.py` | [modules/doc-format.md](docs/modules/doc-format.md) | Windows + Word only |
| MD source format (render, editor, pandoc conversion) | `[done]` | `app/md_render.py` | [modules/md-format.md](docs/modules/md-format.md) | pandoc required |
| System Settings (DB-backed registry, hot reload) | `[done]` | `app/settings.py` | [core/06](docs/core/06-system-settings.md) | Single-process hot reload only |
| Boot schema patches (no migration framework) | `[done]` | `app/__init__.py` | [core/03](docs/core/03-data-model-and-migrations.md) | Idempotent; runs against the live DB on every start |

## Frontend routes (pages)

| Page | URL | Template | Nav location | State |
|---|---|---|---|---|
| Login / Register | `/login`, `/register` | `login.html`, `register.html` | — | `[done]` |
| Dashboard | `/dashboard` | `dashboard.html` | navbar | `[done]` |
| Generate | `/generate` | `generate.html` | from dashboard selection | `[done]` |
| Viewer | `/generate/viewer` | `viewer.html` | from dashboard / generate | `[done]` |
| My Files | `/user/files` | `my_files.html` | My Stuff | `[done]` |
| Search Profiles | `/user/profiles` | `saved_filters.html` | My Stuff | `[done]` |
| Generation Presets | `/user/gen-profiles` | `saved_gen_profiles.html` | My Stuff | `[done]` |
| Question Sets | `/user/sets` | `saved_question_sets.html` | My Stuff | `[done]` |
| File Browser (user) | `/files/browser` | `files_browser.html` | My Stuff | `[done]` |
| Toolbox hub | `/admin/toolbox` | `admin_toolbox.html` | navbar (all users) | `[done]` |
| Markup | `/admin/toolbox/markup` | `markup.html` | Toolbox | `[done]` |
| PDF Tool | `/admin/toolbox/pdf` | `admin_toolbox_pdf.html` | Toolbox (admin) | `[done]` |
| Admin index | `/admin` | `admin_index.html` | Admin dropdown | `[done]` |
| Topics / Chapters / Subjects | `/admin/topics`, `/admin/chapters`, `/admin/subjects` | `admin_topics.html`, `admin_chapters.html`, `admin_subjects.html` | Admin dropdown | `[done]` |
| Question Management | `/admin/questions` | `admin_questions.html` | Admin dropdown | `[done]` |
| Split into parts | `/admin/questions/<id>/split` | `admin_question_split.html` | from Edit modal | `[done]` |
| MD fullscreen editor | `/admin/questions/<qid>/assets/<aid>/md/edit` | `admin_md_editor.html` | from Edit modal | `[done]` |
| Users | `/admin/users` | `admin_users.html` | Admin dropdown (super) | `[done]` |
| Export / Import | `/admin/export-import` | `admin_export_import.html` | Admin dropdown | `[done]` |
| Smart Import | `/admin/import` | `admin_smart_import.html` | Admin dropdown | `[done]` |
| PDF Batch Import | `/admin/pdf-import` | `admin_pdf_import.html` | Admin dropdown | `[done]` |
| Database Health | `/admin/health` | `admin_health.html` | Admin dropdown (super) | `[done]` |
| File Browser (super admin) | `/admin/files` | `admin_files.html` | Admin dropdown (super) | `[done]` |
| System Settings | `/admin/settings` | `admin_settings.html` | Admin dropdown (super) | `[done]` |
| LLM Endpoints | `/admin/llm-endpoints` | `admin_llm_endpoints.html` | Admin dropdown (super) | `[done]` |
| AI Prompts | `/admin/prompts` | `admin_prompts.html` | Admin dropdown (super) | `[done]` |

Exact paths for module-internal routes are in each module doc; verify against `@<bp>.route(` before relying on one.

## Engineering / operations

| Item | State | Notes |
|---|---|---|
| Automated tests | `[partial]` | 10 `unittest` modules in `tests/` (hierarchy incl. grouping/split-box/derived roles, combine/WHOLE filenames/`whole_source_crops`, PDF import plan labels + page-range + split-pipeline helpers, PDF agent pure logic, sorting, MC keys, generate template UX, PDF tool split, AI prompts incl. agent parsers, LLM client). No DB-backed tests, no pytest, no CI. `tests/test_hierarchy.py`, `tests/test_question_combine.py` and `tests/test_pdf_import_plan.py` are pure (no `create_app()`). `tests/test_llm_client.py` calls `create_app()` and therefore touches the live DB. |
| Test / disposable database | `[pending]` | Only the live MariaDB exists. Any DB-touching test or script runs against production data. |
| Migration framework | `[done]` (by decision) | Boot patches instead of Alembic — [ADR-002](docs/decisions/ADR-002-no-migration-framework-boot-patches.md). |
| `.env.example` complete vs `app/config.py` | `[done]` | Replaces the former `env_template.txt`. |
| Repo hygiene: one-off scripts at root (`migrate_versions.py`, `migrate_starring.py`, `migrate_md_format.py`, `import_dse_p2.py`, `tag_topics.py`) | `[pending]` | Historical; superseded by boot patches. Safe to archive or delete after user confirmation. |
| `debug_env.py` hardcodes a password and prints `.env` values | `[pending]` | Should be deleted; do not run it. |
| Multi-worker deployment (settings fan-out, cancel registry, Word lock) | `[pending]` | App assumes a single process. |
| `WORD_COM_TIMEOUT` per-job watchdog | `[pending]` | Registered in `Config` + Settings REGISTRY but never read by `app/word_com.py`; only the lock wait (`WORD_COM_LOCK_TIMEOUT`) is enforced. |
| Download authz for share recipients | `[partial]` | `/generate/download/<id>` is owner/super-admin only while `/generate/pdf/<id>` and `/user/files/bulk-download` accept share recipients (`_user_can_view_file`). Decide one policy and align. |
| Dead PDF-at-create code path | `[pending]` | `output_format` is pinned to `DOCX`; PDF branches in `_generate_in_background` and the `onOutputFormatChange` shim are dead — remove when convenient. |
| Agent-first documentation tree (`OVERVIEW`, `STATUS`, `ARCHITECTURE`, `docs/`) | `[done]` | CHANGELOG and DEVELOPER_SPEC retired; manuals moved to `docs/manuals/`. |

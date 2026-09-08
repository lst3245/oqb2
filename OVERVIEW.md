# OQB2 — Online Question Bank System

> Read this first. Then [STATUS.md](STATUS.md), then [ARCHITECTURE.md](ARCHITECTURE.md), then only the `docs/` files your task touches.

This repository is **built and maintained primarily by AI coding agents** across many
fresh sessions. Documentation and Cursor rules are first-class deliverables: a change is
not done until the matching doc and (if a new invariant or footgun appeared) the matching
rule are updated in the same turn. See [`.cursor/rules/docs-maintenance.mdc`](.cursor/rules/docs-maintenance.mdc).

## What it is

A Flask web app for a school department that manages a library of exam questions
(images, Word documents, Markdown) tagged by subject / topic / chapter / level, and
generates printable exam papers (`.docx`, lazily `.pdf`) from any filtered selection.
It also hosts admin tooling around that library: file ingestion, AI-assisted proofreading /
transcription / tagging, PDF batch import of scanned papers, a PDF workbench, and a
mobile handwriting PWA.

**Audience:** teachers (browse, generate), subject admins (tag, ingest, AI tools),
one or more super admins (users, subjects, settings, everything).

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.14, Flask 3, Flask-SQLAlchemy, Flask-Login, MariaDB via `pymysql` |
| Frontend | Server-rendered Jinja2 + Bootstrap 5.3 + HTMX 1.9 partials; SortableJS; KaTeX; no SPA (see [ADR-001](docs/decisions/ADR-001-server-rendered-htmx-no-spa.md)) |
| Documents | python-docx + docxcompose + Pillow; Microsoft Word COM (`pywin32`, Windows-only) for native DOC merge and PDF export ([ADR-003](docs/decisions/ADR-003-word-com-for-doc-merge-and-pdf.md)); pandoc for MD to docx |
| PDF / images | PyMuPDF, pypdf, NumPy, Tesseract (optional OCR) |
| AI | OpenAI-compatible Chat Completions / Responses API over `requests`; keys Fernet-encrypted |
| Runtime | Windows host, system Python (no venv), one live MariaDB — see [docs/core/01-runtime-and-ops.md](docs/core/01-runtime-and-ops.md) |

## Hard invariants (never violate)

- **There is one database and it is live.** No test DB exists. Never run `init_db.py`, `migrate_*.py`, `cli.py sync --no-dry-run`, or any script that writes to MariaDB unless the user explicitly asks. Starting `create_app()` itself runs idempotent `ALTER TABLE` patches against the live schema. See [`.cursor/rules/safety-ops.mdc`](.cursor/rules/safety-ops.mdc).
- **`.env` holds real secrets** (DB password, LLM API key). Never print, copy, or commit it. `.env.example` is the documented template.
- **Every file the app serves goes through an authenticated route**; the filesystem is never exposed directly. All path joins under a root use `app/storage.safe_join`, never `startswith`.
- **Authorization is declared at the route edge** with the decorators in `app/utils.py` (`@admin_required`, `@super_admin_required`, `@subject_access_required`, `@subject_admin_required`); services trust their caller. See [docs/core/02-auth-and-permissions.md](docs/core/02-auth-and-permissions.md).
- **`app/models.py` is the schema source of truth.** Schema changes are made by editing the model *and* adding an idempotent boot patch in `app/__init__.py` (no Alembic; [ADR-002](docs/decisions/ADR-002-no-migration-framework-boot-patches.md)).
- **Runtime tunables are read from `current_app.config[KEY]`**, never `os.getenv` or the `Config` class, because DB-backed System Settings override `.env` at runtime ([ADR-004](docs/decisions/ADR-004-db-backed-settings-with-env-bootstrap.md)).
- **QIDs, `Subject.id`, and asset filenames are stable public identifiers** embedded in the on-disk layout under `SOURCE_PATH`; never rename them casually. Grammar in [docs/reference/filename-convention.md](docs/reference/filename-convention.md).
- **Asset versions are `EN / CH / BI / ENO / CHO`** (formerly "languages"); the canonical list lives in `app/utils.VERSIONS` and templates get it via a context processor. Do not hardcode the list ([ADR-006](docs/decisions/ADR-006-versions-replace-languages.md)).
- **Dashboard Selection is independent of the Filter** ([ADR-008](docs/decisions/ADR-008-selection-independent-of-filter.md)).
- **Questions form a tree** (`parent_id`) rather than peer links. A part is its own `Question`. Shared grammar and render-plan expansion live in `app/hierarchy.py` ([ADR-009](docs/decisions/ADR-009-question-hierarchy-over-linking.md)).
- **The PDF import agent is a fixed state machine, not a free-running agent**: strict-JSON LLM steps, code applies every fix, doubts go to an attention list, the human commits ([ADR-010](docs/decisions/ADR-010-agent-layer-over-pdf-import-tools.md)).

## Area map

| Area | Blueprint / prefix | Code | Doc |
|---|---|---|---|
| Auth | `auth_bp` `/` | `app/auth.py` | [core/02](docs/core/02-auth-and-permissions.md) |
| Dashboard (browse, filter, select, Explain chat) | `dashboard_bp` `/dashboard` | `app/dashboard.py` | [modules/dashboard.md](docs/modules/dashboard.md) |
| Document generation + viewer | `generator_bp` `/generate` | `app/generator.py`, `app/word_com.py` | [modules/generator.md](docs/modules/generator.md) |
| My Files, search profiles, generation presets | `user_bp` `/user` | `app/user.py` | [modules/my-files.md](docs/modules/my-files.md) |
| Question Sets | `user_bp` `/user/sets` | `app/user.py` | [modules/question-sets.md](docs/modules/question-sets.md) |
| Admin panel (topics, chapters, subjects, users, export/import, health, settings pages) | `admin_bp` `/admin` | `app/admin.py` | [modules/admin-panel.md](docs/modules/admin-panel.md) |
| Question Management + Edit modal + batch ops | `admin_bp` `/admin/questions` | `app/admin.py`, `app/batch_image_gen.py` | [modules/admin-questions.md](docs/modules/admin-questions.md) |
| AI Tools (proofread, generate MD, solve, auto-tag, LLM endpoints) | `admin_bp` | `app/ai_tools.py`, `app/llm_client.py`, `app/parallel.py` | [modules/ai-tools.md](docs/modules/ai-tools.md) |
| AI Prompts registry + variants | `admin_bp` `/admin/prompts` | `app/ai_prompts.py` | [modules/ai-prompts.md](docs/modules/ai-prompts.md) |
| PDF Batch Import | `admin_bp` `/admin/pdf-import` | `app/pdf_import.py`, `app/pdf_layout.py` | [modules/pdf-import.md](docs/modules/pdf-import.md) |
| PDF Import AI agent | `admin_bp` `/admin/pdf-import/agent` | `app/pdf_agent.py` | [modules/pdf-agent.md](docs/modules/pdf-agent.md) |
| Ingestion + Smart Import | `admin_bp` `/admin/import`, `cli.py` | `app/ingestor.py`, `app/smart_import.py` | [modules/ingestion.md](docs/modules/ingestion.md) |
| Question hierarchy (stem / parts) | cross-cutting | `app/hierarchy.py` | [modules/question-hierarchy.md](docs/modules/question-hierarchy.md) |
| File Browser (user + super-admin) | `files_bp` `/files`, `admin_bp` `/admin/files` | `app/files.py`, `app/files_service.py`, `app/storage.py` | [modules/file-browser.md](docs/modules/file-browser.md), [core/05](docs/core/05-storage-and-paths.md) |
| Toolbox PDF Tool | `toolbox_bp` `/admin/toolbox/pdf` | `app/toolbox/pdf.py`, `app/pdf_tools.py`, `app/pdf_text.py` | [modules/toolbox-pdf.md](docs/modules/toolbox-pdf.md) |
| Markup PWA | `toolbox_bp` `/admin/toolbox/markup`, `pwa_bp` `/` | `app/toolbox/markup.py`, `app/pwa.py`, `static/markup/` | [modules/markup.md](docs/modules/markup.md) |
| DOC source format (Word COM, thumbnails) | cross-cutting | `app/word_com.py`, `app/doc_thumbnails.py` | [modules/doc-format.md](docs/modules/doc-format.md) |
| MD source format (render, editor, pandoc) | cross-cutting | `app/md_render.py` | [modules/md-format.md](docs/modules/md-format.md) |
| System Settings | `admin_bp` `/admin/settings` | `app/settings.py` | [core/06](docs/core/06-system-settings.md) |

## Key decisions (summaries; the why is in `docs/decisions/`)

| ADR | Decision |
|---|---|
| [001](docs/decisions/ADR-001-server-rendered-htmx-no-spa.md) | Server-rendered Jinja + HTMX partials; no SPA framework, no build step |
| [002](docs/decisions/ADR-002-no-migration-framework-boot-patches.md) | No Alembic; idempotent `INFORMATION_SCHEMA`-guarded patches in `create_app()` + rare one-off scripts |
| [003](docs/decisions/ADR-003-word-com-for-doc-merge-and-pdf.md) | Microsoft Word COM for DOC merge, PDF export, and DOC thumbnails (Windows-only by design) |
| [004](docs/decisions/ADR-004-db-backed-settings-with-env-bootstrap.md) | Tunables live in a DB-backed registry with `.env` as bootstrap default; secrets and paths stay in `.env` |
| [005](docs/decisions/ADR-005-unified-storage-tree.md) | One `STORAGE_PATH` tree (`Shared/`, `System/`, `User/<name>/`) beside a read-mostly `SOURCE_PATH` |
| [006](docs/decisions/ADR-006-versions-replace-languages.md) | "Language" became "version" (`EN/CH/BI/ENO/CHO`) with a user-ordered priority list |
| [007](docs/decisions/ADR-007-self-contained-markdown-assets.md) | Markdown assets are single-file, self-contained (LaTeX + base64 images), converted via pandoc |
| [008](docs/decisions/ADR-008-selection-independent-of-filter.md) | Dashboard Selection is a separate set from the Filter result; set algebra composes them |
| [009](docs/decisions/ADR-009-question-hierarchy-over-linking.md) | Stem/parts are a `Question` tree (`parent_id`), not peer links; QNO token `Q5` / `Q5a` / `Q23-24` |
| [010](docs/decisions/ADR-010-agent-layer-over-pdf-import-tools.md) | PDF import agent is a fixed state machine over pass 1/2 with strict-JSON LLM steps and an attention list; roles derived from labels; sibling dependency is a per-part flag |

## Documentation map

| Path | Purpose |
|---|---|
| `OVERVIEW.md` / `STATUS.md` / `ARCHITECTURE.md` | Orientation trio (this layer) |
| `docs/core/0N-*.md` | Cross-cutting foundations every module obeys (runtime, authz, data model, conventions, storage, settings) |
| `docs/modules/<module>.md` | One spec per module: files, tables, routes, rules, settings, permissions, gotchas |
| `docs/decisions/ADR-NNN-*.md` | Why a choice was locked |
| `docs/frontend/conventions.md` | Templates, partials, HTMX, shared JS helpers, SortableJS |
| `docs/reference/*.md` | Domain facts: filename grammar, schema history, seeded data |
| `docs/manuals/USER_MANUAL.md`, `docs/manuals/ADMIN_GUIDE.md` | Human-facing manuals (end users / operators) |
| `.cursor/rules/*.mdc` | Session bootloader: always-on orientation, docs-maintenance, safety; scoped per-area rules that point here |
| `README.md` | Human quick start for someone cloning the repo |

There is no CHANGELOG. Feature state lives in `STATUS.md`; feature behaviour lives in the module docs.

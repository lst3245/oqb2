# OQB2 — Online Question Bank System

Flask web app for managing, tagging, and browsing a library of exam questions
(images / Word / Markdown) and generating exam papers (`.docx`, `.pdf`) from any selection.
Includes admin tooling: file ingestion, AI-assisted proofreading and tagging, PDF batch import,
a PDF workbench, and a mobile handwriting PWA.

**If you are an AI agent starting a task: read [OVERVIEW.md](OVERVIEW.md) first**, then
[STATUS.md](STATUS.md) and [ARCHITECTURE.md](ARCHITECTURE.md). The `.cursor/rules/` bootloader
tells you the rest.

## Quick start (humans)

Windows host with MariaDB running. Microsoft Word is required for DOC-source merging, PDF output,
and DOC thumbnails; pandoc for Markdown sources; Tesseract (optional) for OCR in the PDF Tool.

```cmd
copy .env.example .env          :: then edit DB_*, SECRET_KEY, SOURCE_PATH, STORAGE_PATH
pip install -r requirements.txt
python init_db.py               :: ONLY on a fresh, empty database
python run.py                   :: http://localhost:5000  (default login admin / admin123 - change it)
```

`dev-server.bat` wraps `python run.py` in a restart loop; `quickstart.bat` does the steps above.

Tests: `python -m unittest discover -s tests` (see [docs/core/01-runtime-and-ops.md](docs/core/01-runtime-and-ops.md)).

## Documentation

| Audience | Start here |
|---|---|
| AI agents / developers | [OVERVIEW.md](OVERVIEW.md) → [STATUS.md](STATUS.md) → [ARCHITECTURE.md](ARCHITECTURE.md) → `docs/core/`, `docs/modules/`, `docs/decisions/` |
| End users (teachers) | [docs/manuals/USER_MANUAL.md](docs/manuals/USER_MANUAL.md) |
| Administrators / operators | [docs/manuals/ADMIN_GUIDE.md](docs/manuals/ADMIN_GUIDE.md) |
| Domain facts (filename grammar, schema history, seeded data) | [docs/reference/](docs/reference/) |

## Layout

```
app/          Flask package: blueprints, services, models (app/models.py is the schema source of truth)
templates/    Jinja2 templates; partials/ hold HTMX fragments and shared modals
static/       logos, Markup PWA assets
tests/        unittest suites
docs/         agent-first documentation tree
resources/    static PNGs used by batch operations
cli.py        ingest | sync | migrate-storage
```

Internal use only.

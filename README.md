# Online Question Bank System (OQB2)

**Version 2.3** | Last Updated: May 2026

A Flask web application for managing, browsing, tagging, and generating exam question papers from a structured library of image and document assets.

---

## Quick Start

```cmd
# 1. Copy and configure environment
copy env_template.txt .env
# Edit .env with your MariaDB credentials and SOURCE_PATH

# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialise database
python init_db.py

# 4. Start server
python run.py

# 5. Open browser
# http://localhost:5000
# Default login: admin / admin123  (change immediately)
```

---

## Features

### Dashboard
- Filter questions by subject, source (DSE/CE/AL/QB), year, section, paper
- Topic and subtopic filtering with **AND/OR logic** and cross-topic/subtopic search
- Chapter and subchapter filtering
- Level filter (1/2/3 + "Not Assigned")
- Question type filter (MC / CQ)
- **Direct QID search** with loose tokenised or strict wildcard (`*`) modes
- **Multi-level sorting** — drag to set priority (e.g. Topic → Level → Year)
- Natural sort (Q1, Q2, Q10 — not Q1, Q10, Q2)
- Configurable page size (10 / 20 / 50 / 100)
- Language preference for previews (English / Chinese first)
- Question cards with image / Markdown / Word preview (Markdown rendered inline with KaTeX math), answer/solution modals, comment display
- Select questions individually, by page, or all results
- **Save and restore filter profiles** (named search presets)

### Document Generation
- Generate Word (`.docx`) documents from selected questions
- **5 answer modes**: Questions Only, Q+Answer, Q+Solution, All Qs then Answers, All Qs then Solutions
- Answer content preference: image-first or text-first fallback
- Preferred language: English or Chinese (falls back to Bilingual → Other)
- Separate **MC and CQ spacing** — before/after: N lines or new page
- Optional QID labels on questions and/or answers
- Optional correct percentage display (e.g. `MATC_DSE_2024_P1_Q5 [75%]`)
- Optional sequential numbering with configurable start number
- Optional footer page numbers
- Per-question info line: topic / subtopic / chapter / subchapter
- Section headings that auto-insert when a field changes
- **Split to ZIP**: separate `.docx` per topic / subtopic / chapter / subchapter group
- Denote cross-topic questions with `[Cross Topic: X, Y]` annotation
- Custom Word styles (OQB Section Heading, OQB Question ID, OQB Question Info, OQB Body Text)
- **Background generation** — non-blocking; progress tracked in database
- **Viewer / Presentation Mode** — slide-style question review with language and ANS/SOL toggle
- Regenerate previous documents with saved options

### My Files & Saved Profiles
- **My Files** (`/user/files`) — list, download, re-generate, and delete past generated documents
  - Auto-refreshes when a generation is in progress
  - Super admin can view all users' files
- **Search Profiles** (`/user/profiles`) — save and restore named filter configurations
  - Load a profile on the dashboard to instantly restore a complex filter set
- **Question Sets** (`/user/sets`) — per-subject named lists of questions (built from a selection)
  - Dashboard **Set** button opens a chip-builder for **Union (∪) / Intersection (∩) / Difference (\\)** of the live Selection and any saved sets
  - Apply a saved set to instantly populate the dashboard selection; save a new set from the current selection or from an evaluated result
  - Super admin can share a set with all users that have access to its subject

### Admin Panel
- **Topic Management** (`/admin/topics`) — CRUD + drag-to-reorder for topics and subtopics; hidden subtopic flag
- **Chapter Management** (`/admin/chapters`) — same structure as topics; textbook organisation
- **Question Management** (`/admin/questions`) — full list with filter, edit modal with Details/Assets/Tags tabs
  - Create questions manually, rename QID, upload/delete/reorder assets
  - **Markdown live editor** (modal + fullscreen) for `.md` assets: EasyMDE editor, live KaTeX preview, base64 image insert, optimistic concurrency
  - Batch update (level, type, section, topic/subtopic, correct %) and batch delete
- **User Management** (`/admin/users`) — super admin only; per-subject permission assignment
- **Export / Import** (`/admin/export-import`) — CSV round-trip for question tags, topics, chapters
- **Ingestion** (`/admin/ingestion`) — scan SOURCE_PATH and import files into DB; live streaming log
- **Database Health** (`/admin/health`) — super admin only; DB stats, anomaly detection, orphan sync
- **File Browser** (`/admin/files`) — super admin only; browse, upload, download, rename, delete source files

### Security
- Flask-Login session authentication
- Subject-level RBAC: No Access / View Only / User / Admin / Super Admin
- All file serving through authenticated routes (no direct filesystem access)

---

## Project Structure

```
oqb2/
├── app/
│   ├── __init__.py        # App factory (create_app), extension init
│   ├── models.py          # All DB models — source of truth for schema
│   ├── auth.py            # Login / logout / register
│   ├── dashboard.py       # Question browse/filter (dashboard_bp)
│   ├── admin.py           # Admin panel (admin_bp) — ~2500 lines
│   ├── generator.py       # Word doc generation + viewer (generator_bp)
│   ├── user.py            # My Files + Saved Profiles (user_bp)
│   ├── ingestor.py        # File scanner, sync, health stats
│   ├── config.py          # Config class reading from .env
│   └── utils.py           # Permission decorators, sort helpers
├── templates/             # Jinja2 HTML templates
│   └── partials/          # HTMX partial templates
├── static/                # CSS and JS assets
├── output/                # Generated .docx / .zip files (OUTPUT_PATH)
├── .cursor/rules/         # AI agent context rules
├── cli.py                 # CLI commands (ingest, sync)
├── init_db.py             # DB initialisation + default data
├── run.py                 # Dev server entry point
├── requirements.txt       # Python dependencies
└── .env                   # Environment config (not committed)
```

---

## File Naming Convention

### Past Paper
```
SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE[_PART].EXT
MATC_DSE_2024_P1_Q5_EN_QUE.png
MATC_DSE_2024_P1_Q5_EN_QUE_2.png   ← part 2 of a multi-image question
```

### Question Bank
```
SUBJ_QB_DETAIL_QNO_LANG_TYPE[_PART].EXT
MATC_QB_MATHSMART2024_Q1_EN_QUE.png
```

| Component | Values |
|---|---|
| SUBJ | `MATC`, `MAT1`, `MAT2`, `ICT`, … |
| SOURCE | `DSE`, `CE`, `AL` |
| LANG | `EN` (English), `CH` (Chinese), `BI` (Bilingual) |
| TYPE | `QUE` (Question), `ANS` (Answer), `SOL` (Solution) |
| PART | Optional integer ≥ 2 for multi-image questions |
| EXT | `png`, `jpg`, `jpeg`, `gif`, `bmp`, `doc`, `docx`, `md`, `markdown` |

> `.md` files are self-contained (LaTeX math via `$...$` / `$$...$$`, base64-embedded images). They render inline on the dashboard/viewer and convert to `.docx` during generation via **pandoc** + docxcompose. Install pandoc separately (it is not a pip package): `apt install pandoc` / `brew install pandoc` / [Windows installer](https://github.com/jgm/pandoc/releases). If pandoc is not on `PATH`, set `PANDOC_PATH=...` in `.env`.

---

## Environment Variables (`.env`)

| Variable | Description | Example |
|---|---|---|
| `DB_HOST` | MariaDB host | `localhost` |
| `DB_USER` | DB username | `root` |
| `DB_PASSWORD` | DB password | `secret` |
| `DB_NAME` | Database name | `oqb2` |
| `SECRET_KEY` | Flask secret key | `change-this` |
| `FLASK_DEBUG` | Debug mode | `1` (dev) / `0` (prod) |
| `SOURCE_PATH` | Path to question asset files | `Q:\Source` |
| `OUTPUT_PATH` | Path to save generated files | `C:\oqb2\output` |

---

## CLI Commands

```bash
# Ingest all questions from SOURCE_PATH into database
python cli.py ingest

# Ingest from a custom path
python cli.py ingest --source-path "D:\Questions"

# Preview orphaned DB records (dry-run, no deletions)
python cli.py sync

# Delete orphaned DB records
python cli.py sync --no-dry-run
```

Ingestion and sync can also be run from the web UI at `/admin/ingestion` and `/admin/health`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask 3.0, SQLAlchemy 3.1, Flask-Login 0.6 |
| Database | MariaDB / MySQL (pymysql driver) |
| Document output | python-docx 1.1, Pillow 10 |
| Frontend | Bootstrap 5.3, HTMX 1.9, Bootstrap Icons 1.11 |
| Sorting | natsort 8.4 |

---

## Default Subjects

Created by `init_db.py`:

| ID | Name |
|---|---|
| `MATC` | Mathematics Compulsory Part |
| `MAT1` | Mathematics Module 1 (Calculus and Statistics) |
| `MAT2` | Mathematics Module 2 (Algebra and Calculus) |
| `ICT` | Information and Communication Technology |

Additional subjects can be added via the database.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Can't login | Restart server, check DB connection |
| Images not showing | Verify `SOURCE_PATH` in `.env`, check file exists at that path |
| Ingestion skips files | Check filename matches the naming convention exactly |
| Generation fails | Check `output/` directory exists and is writable (`OUTPUT_PATH`) |
| Database error | Ensure MariaDB is running and credentials in `.env` are correct |
| Port in use | Change port in `run.py` |

---

## Production Deployment

1. Set `FLASK_DEBUG=0` and a strong `SECRET_KEY` in `.env`
2. Run with a production WSGI server: `gunicorn -w 4 "app:create_app()"`
3. Put behind a reverse proxy (nginx / Apache) with HTTPS
4. Change the default `admin` password immediately after first login
5. Set up regular database backups

---

## Documentation

| Document | Audience | Purpose |
|---|---|---|
| `README.md` | Everyone | Quick start, project overview |
| `USER_MANUAL.md` | End users | Dashboard, generation, My Files, profiles |
| `ADMIN_GUIDE.md` | Administrators | Setup, config, admin panel operations |
| `DEVELOPER_SPEC.md` | Developers | Architecture, DB schema, API reference |
| `CHANGELOG.md` | Everyone | Version history |
| `.cursor/rules/` | AI agents | Context rules for efficient coding assistance |

---

Copyright © 2024–2026. Internal use only.

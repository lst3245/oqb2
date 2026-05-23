# Online Question Bank System — Developer Specification

**Version 2.3** | Last Updated: May 2026

Technical reference for developers working on or extending OQB2.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Tech Stack & Dependencies](#2-tech-stack--dependencies)
3. [Project Structure](#3-project-structure)
4. [Configuration](#4-configuration)
5. [Database Schema](#5-database-schema)
6. [Permission System](#6-permission-system)
7. [Blueprint Reference](#7-blueprint-reference)
8. [API Endpoints](#8-api-endpoints)
9. [File Ingestion System](#9-file-ingestion-system)
10. [Document Generation](#10-document-generation)
11. [Frontend Architecture](#11-frontend-architecture)
12. [CLI Commands](#12-cli-commands)
13. [Development Workflow](#13-development-workflow)

---

## 1. Architecture

```
Browser (Bootstrap 5 + HTMX)
        │ HTTP
        ▼
Flask Application (app factory: create_app())
  ├── auth_bp       /              Login, logout
  ├── dashboard_bp  /dashboard     Question browse, filter, search
  ├── admin_bp      /admin         Admin panel (topics, questions, users, ingest, export)
  ├── generator_bp  /generate      Word doc generation, viewer mode
  └── user_bp       /user          My Files, Saved Profiles
        │
        ├── SQLAlchemy ORM ──► MariaDB
        ├── SOURCE_PATH       Question asset files (images / Word docs)
        └── OUTPUT_PATH       Generated .docx / .zip files
```

**Design patterns**:
- **App Factory** (`create_app()`) — supports multiple instances, clean extension init
- **Blueprint** — modular routes, easy to extend
- **HTMX partials** — server renders HTML fragments on filter/sort, no SPA complexity
- **Background thread** — document generation is non-blocking; status tracked in DB

---

## 2. Tech Stack & Dependencies

| Package | Version | Use |
|---|---|---|
| Flask | 3.0.0 | Web framework |
| Flask-SQLAlchemy | 3.1.1 | ORM |
| Flask-Login | 0.6.3 | Session auth |
| pymysql | 1.1.0 | MariaDB driver |
| python-docx | 1.1.0 | Word document creation |
| Pillow | 10.1.0 | Image dimensions for Word layout |
| python-dotenv | 1.0.0 | `.env` loading |
| natsort | 8.4.0 | Natural sort (Q1, Q2, Q10) |
| click | 8.1.7 | CLI framework |
| cryptography | 41.0.7 | pymysql dependency |

Frontend (CDN-loaded):
- Bootstrap 5.3 + Bootstrap Icons 1.11
- HTMX 1.9.10

---

## 3. Project Structure

```
oqb2/
├── app/
│   ├── __init__.py        # create_app(), db, login_manager; stale-job cleanup on startup
│   ├── models.py          # All SQLAlchemy models — always the schema source of truth
│   ├── auth.py            # auth_bp: login, logout, register
│   ├── dashboard.py       # dashboard_bp: filter_questions(), API endpoints
│   ├── admin.py           # admin_bp: ~2500 lines, 8 major sections
│   ├── generator.py       # generator_bp: create_word_document(), viewer, background thread
│   ├── user.py            # user_bp: SavedFilter + SavedGenerationProfile CRUD, GeneratedFile list/delete
│   ├── ingestor.py        # File scanning, DB sync, health stats, streaming generators
│   ├── config.py          # Config class (reads .env via python-dotenv)
│   └── utils.py           # Permission decorators, apply_multi_sort(), SORT_FIELDS
├── templates/
│   ├── base.html          # Shared layout (navbar, Bootstrap, HTMX)
│   ├── dashboard.html     # Main browse page + filter sidebar + sort panel
│   ├── generate.html      # Generation options form
│   ├── viewer.html        # Presentation mode
│   ├── my_files.html      # Generated files list
│   ├── saved_filters.html # Saved search profiles
│   ├── saved_gen_profiles.html # Saved generation presets
│   ├── admin_*.html       # Admin panel pages
│   └── partials/
│       ├── question_list.html     # HTMX target: question cards
│       ├── tag_editor_form.html   # Reusable tag editor (used in dashboard + admin modal)
│       └── tag_editor_js.html     # JS for tag editor
├── static/                # css/ and js/ (currently mostly empty; libs loaded from CDN)
├── output/                # Generated documents (OUTPUT_PATH)
├── .cursor/rules/         # AI agent context rules
├── cli.py                 # click CLI: ingest, sync
├── init_db.py             # Create tables + default subjects + admin user
├── run.py                 # Dev server (app.run debug=True, port 5000)
├── requirements.txt
└── .env                   # Not committed; see env_template.txt
```

---

## 4. Configuration

`app/config.py` reads from `.env` via python-dotenv.

| Key | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-key-change-in-production` | Flask session signing |
| `DB_HOST` | `localhost` | MariaDB host |
| `DB_USER` | `root` | DB username |
| `DB_PASSWORD` | `` | DB password |
| `DB_NAME` | `oqb2` | Database name |
| `SOURCE_PATH` | `../Source` (relative to project root) | Root of question asset files |
| `OUTPUT_PATH` | `../output` (relative to project root) | Where generated docs are saved |
| `QUESTIONS_PER_PAGE` | `20` | Default page size (hardcoded, not in .env) |
| `SQLALCHEMY_ENGINE_OPTIONS` | pool_pre_ping=True, recycle=300 | Connection pool health |

---

## 5. Database Schema

**Always read `app/models.py` directly — it is the definitive schema reference.**

### Tables

#### `users`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `username` | VARCHAR(80) | Unique, indexed |
| `password_hash` | VARCHAR(255) | Werkzeug hash |
| `is_admin` | BOOL | Legacy field, kept for compatibility |
| `is_super_admin` | BOOL | Full system access |
| `created_at` | DATETIME | |

#### `user_subject_permissions`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `user_id` | INT FK → users | |
| `subject_id` | VARCHAR(10) FK → subjects | |
| `role` | VARCHAR(10) | `'viewer'`, `'user'`, `'admin'` |
Unique constraint: `(user_id, subject_id)`

#### `subjects`
| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR(10) PK | e.g. `'MATC'` |
| `name` | VARCHAR(100) | Display name |

#### `topics`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `subject_id` | VARCHAR(10) FK → subjects | |
| `name` | VARCHAR(200) | |
| `sort_order` | INT | For drag-to-reorder |

#### `subtopics`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `topic_id` | INT FK → topics | |
| `name` | VARCHAR(200) | |
| `hidden` | BOOL | Hidden from dashboard (shown in admin) |
| `sort_order` | INT | |

#### `chapters`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `subject_id` | VARCHAR(10) FK → subjects | |
| `name` | VARCHAR(200) | |
| `sort_order` | INT | |

#### `subchapters`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `chapter_id` | INT FK → chapters | |
| `name` | VARCHAR(200) | |
| `hidden` | BOOL | |
| `sort_order` | INT | |

#### `questions`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `qid` | VARCHAR(100) | Unique, indexed. Format: `MATC_DSE_2024_P1_Q5` |
| `subject` | VARCHAR(10) FK → subjects | |
| `source` | VARCHAR(20) | `DSE`, `CE`, `AL`, `QB` |
| `year` | INT | NULL for QB |
| `paper` | VARCHAR(10) | `P1`, `P2`, etc. NULL for QB |
| `section` | VARCHAR(50) | Free text, e.g. `A`, `Section I` |
| `qno` | INT | Numeric question number |
| `q_type` | VARCHAR(10) | `MC`, `CQ`, or NULL |
| `level` | INT | 1–3, NULL = untagged |
| `major_topic_id` | INT FK → topics | Primary topic |
| `major_subtopic_id` | INT FK → subtopics | Primary subtopic (must belong to major_topic) |
| `chapter_id` | INT FK → chapters | SET NULL on chapter delete |
| `subchapter_id` | INT FK → subchapters | SET NULL on subchapter delete |
| `description` | TEXT | Optional description |
| `correct_percentage` | INT | 0–100, NULL if unknown |
| `answer` | TEXT | Text answer (alternative to ANS image) |
| `comment` | TEXT | Notes / commentary |
| `created_at` | DATETIME | |

Many-to-many via association tables:
- `question_minor_topics` — `(question_id, topic_id)` 
- `question_subtopics` — `(question_id, subtopic_id)`

#### `question_assets`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `question_id` | INT FK → questions | Cascade delete |
| `asset_type` | ENUM | `QUE`, `ANS`, `SOL` |
| `file_format` | ENUM | `IMG`, `DOC` |
| `language` | ENUM | `EN`, `CH`, `BI` |
| `file_path` | VARCHAR(500) | Relative to SOURCE_PATH, always forward slashes |
| `part_number` | INT | ≥ 1; for multi-image questions |
Unique constraint: `(question_id, asset_type, language, file_format, part_number)`

#### `saved_filters`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `user_id` | INT FK → users | |
| `name` | VARCHAR(200) | Profile name |
| `filter_data` | TEXT | JSON blob of dashboard filter state |
| `is_starred` | BOOLEAN | Indexed; starred profiles sort first |
| `created_at` | DATETIME | |

#### `saved_generation_profiles`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `user_id` | INT FK → users | |
| `name` | VARCHAR(200) | Preset name (unique per user; save acts as upsert) |
| `options_data` | TEXT | JSON blob of generation options (no `question_ids`) |
| `is_starred` | BOOLEAN | Indexed; starred presets sort first |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | Refreshed on every save |

#### `generated_files`
| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `user_id` | INT FK → users | |
| `display_name` | VARCHAR(200) | User-chosen name |
| `filename` | VARCHAR(300) | Actual filename on disk (in OUTPUT_PATH) |
| `status` | VARCHAR(20) | `pending` → `generating` → `completed` \| `failed` |
| `error_message` | TEXT | Set on failure |
| `filter_data` | TEXT | JSON — dashboard filter used |
| `generation_options` | TEXT | JSON — all generation options |
| `question_count` | INT | |
| `created_at` | DATETIME | |
| `completed_at` | DATETIME | |

---

## 6. Permission System

### Roles (per subject)
- `viewer` — read-only: can browse, cannot generate
- `user` — can browse and generate documents
- `admin` — full subject admin (tagging, ingestion, batch ops, export)

`is_super_admin` flag on `User` overrides all per-subject checks — full access everywhere.

### Decorators (`app/utils.py`)
| Decorator | Requirement |
|---|---|
| `@login_required` | Any authenticated user |
| `@admin_required` | `is_super_admin` OR has `admin` role for any subject |
| `@super_admin_required` | `is_super_admin` only |
| `@subject_admin_required` | `is_super_admin` OR `admin` role for the specific subject |
| `@subject_access_required` | `is_super_admin` OR any role for the specific subject |

### User Model Helper Methods
```python
user.has_subject_access(subject_id)   # any role (incl. viewer)
user.is_subject_admin(subject_id)     # admin role or super admin
user.can_generate()                   # has user/admin for any subject
user.is_all_view_only()               # all permissions are viewer
user.get_subject_roles()              # {subject_id: role}
user.get_accessible_subjects()        # [subject_id, ...]
user.get_admin_subjects()             # [subject_id, ...]
```

---

## 7. Blueprint Reference

### `auth_bp` — `app/auth.py` — prefix: `/`
| Route | Method | Description |
|---|---|---|
| `/` | GET | Redirect to dashboard or login |
| `/login` | GET, POST | Login form |
| `/logout` | GET | Logout |
| `/register` | GET, POST | Create user (admin only) |

### `dashboard_bp` — `app/dashboard.py` — prefix: `/dashboard`
| Route | Method | Description |
|---|---|---|
| `/` | GET | Main dashboard page |
| `/filter` | GET, POST | Filter questions (HTMX partial or full page) |
| `/api/topics/<subject_id>` | GET | Topics list for subject |
| `/api/subtopics` | GET | Subtopics for topic IDs, with question counts |
| `/api/chapters/<subject_id>` | GET | Chapters list for subject |
| `/api/subchapters` | GET | Subchapters for chapter IDs |
| `/api/years/<subject_id>/<source>` | GET | Available years |
| `/api/sections/<subject_id>/<source>` | GET | Available sections |
| `/api/asset/<asset_id>` | GET | Asset info |
| `/api/asset_preview/<asset_id>` | GET | Serve asset file for preview |
| `/api/question/<id>/assets/<type>` | GET | All asset parts for a question + type |
| `/files/<path>` | GET | Serve source asset file (authenticated) |

**Key notes on `filter_questions()`:**
- Accepts 15+ filter params (see `dashboard-search.mdc` rule for full table)
- Sorting is Python-side via `apply_multi_sort()` — intentional for natural sort support
- Pagination is also Python-side (slice after sort)
- HTMX detection: `HX-Request` header → return partial `partials/question_list.html`

### `generator_bp` — `app/generator.py` — prefix: `/generate`
| Route | Method | Description |
|---|---|---|
| `/` | GET, POST | Generation options page; accepts question_ids |
| `/viewer` | GET, POST | Viewer/presentation mode |
| `/api/viewer_asset/<question_id>/<type>` | GET | Asset URL(s) for viewer; supports ANS↔SOL fallback |
| `/create` | POST | Start background generation; returns `{id, status, filename}` |
| `/status/<file_id>` | GET | Poll generation status |
| `/download/<file_id>` | GET | Download completed file |

**Generation flow**: `create_document()` creates `GeneratedFile` record → spawns `threading.Thread` → `_generate_in_background()` runs inside `with app.app_context()`. Always pass `current_app._get_current_object()` to the thread — not the proxy.

### `admin_bp` — `app/admin.py` — prefix: `/admin`

File is ~2500 lines. Key sections (use `# ===` comments to navigate):

| Section | Routes |
|---|---|
| Topic Management | `/topics`, `/topics/add`, `/topics/<id>/edit`, `/topics/<id>/delete`, `/topics/reorder`, `/subtopics/*` |
| Chapter Management | `/chapters`, `/chapters/add`, …, `/subchapters/*` |
| Question Tagging | `/questions/<id>/update` |
| Question Deletion | `/questions/delete` (batch) |
| Batch Update | `/questions/batch-update` |
| Question Management | `/questions`, `/questions/api/list`, `/questions/<id>/details`, `/questions/<id>/assets`, `/questions/<id>/rename`, `/questions/<id>/assets/upload`, `/questions/<id>/assets/<aid>/delete`, `/questions/<id>/assets/reorder`, `/questions/create` |
| User Management | `/users`, `/users/add`, `/users/<id>/edit`, `/users/<id>/delete`, `/users/<id>/permissions`, `/users/<id>/permissions/get` |
| Export / Import | `/export-import`, `/export/question-tags`, `/import/question-tags`, `/export/topics`, `/import/topics`, `/export/chapters`, `/import/chapters` |
| Ingestion | `/ingestion`, `/ingestion/preview`, `/ingestion/start` (SSE) |
| Database Health | `/health`, `/health/stats`, `/health/untracked`, `/health/sync` (SSE) |
| File Browser | `/files`, `/files/list`, `/files/download`, `/files/upload`, `/files/rename`, `/files/delete`, `/files/mkdir` |

### `user_bp` — `app/user.py` — prefix: `/user`
| Route | Method | Description |
|---|---|---|
| `/profiles` | GET | Saved search profiles page |
| `/profiles/list` | GET | JSON list of profiles (starred first, then by name) |
| `/profiles/save` | POST | Save new profile |
| `/profiles/<id>/data` | GET | Get filter data for a profile |
| `/profiles/<id>` | DELETE | Delete profile |
| `/profiles/bulk-delete` | POST | Delete multiple profiles |
| `/profiles/<id>/star` | POST | Toggle starred status of a filter profile |
| `/gen-profiles` | GET | Saved generation presets page |
| `/gen-profiles/list` | GET | JSON list of presets (starred first, then by name) |
| `/gen-profiles/save` | POST | Save/upsert a preset (by `(user_id, name)`) |
| `/gen-profiles/<id>/data` | GET | Get options data for a preset |
| `/gen-profiles/<id>` | DELETE | Delete preset |
| `/gen-profiles/bulk-delete` | POST | Delete multiple presets |
| `/gen-profiles/<id>/star` | POST | Toggle starred status of a preset |
| `/files` | GET | My Files page |
| `/files/list` | GET | JSON list of generated files |
| `/files/<id>/filter` | GET | Get saved filter data from a file |
| `/files/<id>/generation_options` | GET | Get saved generation options from a file |
| `/files/<id>` | DELETE | Delete file (DB + disk) |
| `/files/bulk-delete` | POST | Delete multiple files (DB + disk) |

---

## 8. API Endpoints

All API endpoints require authentication. JSON responses unless noted.

### Dashboard API
```
GET  /dashboard/api/topics/<subject_id>
GET  /dashboard/api/subtopics?topic_ids=1,2&include_hidden=0&q_type=all
GET  /dashboard/api/chapters/<subject_id>
GET  /dashboard/api/subchapters?chapter_ids=1,2&include_hidden=0
GET  /dashboard/api/years/<subject_id>/<source>
GET  /dashboard/api/sections/<subject_id>/<source>
GET  /dashboard/api/asset/<asset_id>
GET  /dashboard/api/asset_preview/<asset_id>
GET  /dashboard/api/question/<id>/assets/<type>
```

### Generation API
```
POST /generate/create           → {id, status, filename}
GET  /generate/status/<id>      → {id, status, error_message, display_name, filename}
GET  /generate/download/<id>    → file download
GET  /generate/api/viewer_asset/<question_id>/<type>?lang=EN
     → {parts: [{id, type, format, language, part_number, url}], id, type, format, language, url}
```

### Admin API (selected)
```
POST /admin/questions/<id>/update           → {success: true}
POST /admin/questions/batch-update          → {success, updated_count, qids}
POST /admin/questions/delete                → {success, deleted_count, qids}
POST /admin/questions/create                → {success, question_id, qid}
POST /admin/questions/<id>/rename           → {success, new_qid}
POST /admin/questions/<id>/assets/upload    → {success, asset_id}
GET  /admin/health/stats                    → stats dict
GET  /admin/health/untracked                → [{file_path, qid, filename}]
GET  /admin/health/sync?dry_run=1           → SSE stream of progress events
GET  /admin/ingestion/start?subject=MATC    → SSE stream of progress events
```

**SSE event format** (ingestion and sync streams):
```json
{"type": "info|success|skip|error|progress|done|warning", "message": "...", "current": 5, "total": 100}
```

---

## 9. File Ingestion System

See `app/ingestor.py` and `.cursor/rules/file-ingestion.mdc` for full details.

### Filename Patterns
```
PP:  SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE[_PART].EXT
QB:  SUBJ_QB_DETAIL_QNO_LANG_TYPE[_PART].EXT
```

### Folder Structure
```
SOURCE_PATH/
  MATC/PP/DSE/2024/P1/MATC_DSE_2024_P1_Q1_EN_QUE.png
  MATC/QB/MATHSMART2024/MATC_QB_MATHSMART2024_Q1_EN_QUE.png
```

### Auto Question Type Detection
- `MATC DSE P1` → `CQ`
- `MATC DSE P2` → `MC`
- `MAT1/MAT2 DSE` → `CQ`
- All others → `NULL`

### Key Functions
```python
parse_filename(filename)                            # → dict or None
construct_qid(parsed)                               # → QID string
scan_directory_stream(source_path, base_path)       # → generator (for UI/SSE)
sync_database_stream(source_path, dry_run)          # → generator (for UI/SSE)
find_untracked_files(source_path)                   # → [{file_path, qid, filename}]
get_database_stats(source_path)                     # → stats dict
```

Always pass `base_path=SOURCE_PATH` when scanning a subject subfolder so stored `file_path` values include the subject prefix.

---

## 10. Document Generation

See `app/generator.py` and `.cursor/rules/document-generation.mdc` for full details.

### Answer Modes
`QUE_ONLY`, `QUE_ANS`, `QUE_SOL`, `QUE_THEN_ANS`, `QUE_THEN_SOL`

### Custom Word Styles (defined in `_define_oqb_styles()`)
| Style | Appearance | Used for |
|---|---|---|
| `OQB Section Heading` | Centred bold 14pt | Section change headings |
| `OQB Question ID` | Bold 12pt | QID / seq number / % line |
| `OQB Question Info` | Italic 10pt grey | Topic/chapter info line |
| `OQB Body Text` | 11pt | Text answers, placeholders |

### Split to ZIP
If any `split_fields` (topic/subtopic/chapter/subchapter) are enabled, `_split_questions_into_groups()` partitions questions into an `OrderedDict` of `{label: [questions]}`, and one `.docx` is generated per group, then zipped.

### Page Setup
A4 (`21cm × 29.7cm`) with 1.27cm margins on all sides.

---

## 11. Frontend Architecture

- **Bootstrap 5** for layout and components
- **HTMX** for dynamic updates without full-page reloads
  - Dashboard filter form posts to `/dashboard/filter` → replaces `#questionList`
  - No custom fetch/XHR needed for filtering
- **Vanilla JS** for:
  - Multi-select dropdowns (topics, subtopics, chapters, years) — custom built
  - Sort drag-and-drop (plain HTML5 drag API)
  - Generation status polling (setInterval + fetch)
  - SSE consumption (ingestion, health sync) via `EventSource`
  - My Files / Profiles auto-refresh (setInterval)
- **Tag editor** shared partial: `templates/partials/tag_editor_form.html` + `tag_editor_js.html`
  - Used in both the admin question edit modal and admin question management page
- `base.html` includes Bootstrap, Bootstrap Icons, and HTMX from CDN

---

## 12. CLI Commands

```bash
python cli.py ingest [--source-path PATH]
python cli.py sync   [--source-path PATH] [--no-dry-run] [--force]
```

`sync` defaults to dry-run. Use `--no-dry-run` to actually delete. `--force` skips the confirmation prompt.

---

## 13. Development Workflow

### Running the Dev Server
```bash
python run.py
# Listens on 0.0.0.0:5000 with debug=True
```

### Initialising the Database
```bash
python init_db.py
# Creates all tables, inserts default subjects (MATC, MAT1, MAT2, ICT) and admin user
```

### Adding a New Feature Checklist
1. Add/modify models in `app/models.py` — SQLAlchemy columns are auto-created by `db.create_all()` only for new tables; existing tables need an `ALTER TABLE` migration
2. Add routes to the appropriate blueprint
3. Add/update templates
4. Update `CHANGELOG.md` with the change
5. Update the relevant `.cursor/rules/*.mdc` file so AI agents have current context

### Adding a New Sort Field
Add an entry to `SORT_FIELDS` in `app/utils.py`:
```python
'my_field': {
    'label': 'My Field',
    'key': lambda q: q.my_field if q.my_field else '',
    'natural': False   # True = use natsort
}
```

### Adding a New Blueprint
1. Create `app/my_feature.py`, define `my_bp = Blueprint('my_feature', __name__, url_prefix='/my')`
2. Register in `create_app()` in `app/__init__.py`
3. Add navigation links to `templates/base.html`

### Production Deployment
```bash
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"
```

Key production settings in `.env`:
```
FLASK_DEBUG=0
SECRET_KEY=<strong-random-key>
```

Use a reverse proxy (nginx) in front of gunicorn for HTTPS, static files, and connection handling.

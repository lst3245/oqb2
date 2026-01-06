---
name: Online Question Bank System
overview: Build a complete Flask-based question bank system with file ingestion, filtering/browsing dashboard, admin tagging interface, and Word document generation. Includes full authentication with admin/user roles.
todos:
  - id: project-setup
    content: Setup Flask structure, dependencies, config, .env template
    status: completed
  - id: database-models
    content: Create SQLAlchemy models and database init script
    status: completed
  - id: auth-system
    content: Implement Flask-Login authentication with admin roles
    status: completed
  - id: ingestor-module
    content: Build file scanner with regex parsing and DB upsert logic
    status: completed
  - id: dashboard-backend
    content: Create filter routes, query builder, pagination logic
    status: completed
  - id: dashboard-frontend
    content: Build Bootstrap UI with HTMX filter form and question cards
    status: completed
  - id: admin-topics
    content: Implement topic/subtopic CRUD interface
    status: completed
  - id: admin-tagging
    content: Build question tagging interface with preview
    status: completed
  - id: word-generator
    content: Create Word generation module with image insertion
    status: completed
  - id: file-serving
    content: Setup secure image serving route for previews
    status: completed
  - id: testing
    content: Test ingestor, filters, generation with real data
    status: completed
---

# Online Question Bank System Implementation Plan

## Architecture Overview

```mermaid
graph TB
    FileSystem[Source_Files_Folder]
    Ingestor[Ingestor_Module]
    Database[(MariaDB)]
    Flask[Flask_Backend]
    Dashboard[Dashboard_UI]
    Admin[Admin_UI]
    Generator[Word_Generator]
    
    FileSystem -->|scan| Ingestor
    Ingestor -->|upsert| Database
    Database -->|query| Flask
    Flask -->|render| Dashboard
    Flask -->|render| Admin
    Admin -->|update_tags| Database
    Dashboard -->|select_questions| Generator
    Generator -->|create_docx| WordFile[Generated_Document]
```

## 1. Project Structure Setup

Create Flask application structure:

```
oqb2/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── config.py            # Database & app configuration
│   ├── models.py            # SQLAlchemy models (all 7 tables)
│   ├── auth.py              # Authentication routes & decorators
│   ├── dashboard.py         # Dashboard routes
│   ├── admin.py             # Admin panel routes
│   ├── generator.py         # Document generation routes
│   ├── ingestor.py          # File scanner module
│   └── utils.py             # Helper functions
├── templates/
│   ├── base.html            # Base template with Bootstrap
│   ├── login.html
│   ├── dashboard.html       # Main question browser
│   ├── admin_topics.html    # Topic/subtopic management
│   ├── admin_tags.html      # Question tagging interface
│   └── generate.html        # Generation options
├── static/
│   ├── css/
│   └── js/
├── uploads/                 # NOT USED - files stay in Source/
├── output/                  # Generated Word documents
├── requirements.txt
└── run.py                   # Application entry point
```

**Key Dependencies** (`requirements.txt`):

- Flask, Flask-SQLAlchemy, Flask-Login
- pymysql (MariaDB connector)
- python-docx, docxcompose
- Pillow (image dimension detection)
- python-dotenv (environment variables)

## 2. Database Configuration

Create [`app/config.py`](app/config.py):

- Load connection details from `.env` file (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)
- Set SQLAlchemy connection string: `mysql+pymysql://user:pass@host/dbname`
- Configure session secret key, upload folders

Create [`app/models.py`](app/models.py) with 7 SQLAlchemy models:

- **User** (id, username, password_hash, is_admin, created_at)
- **Subject** (id as VARCHAR PK, name)
- **Topic** (id, subject FK, name)
- **Subtopic** (id, topic_id FK, name)
- **Question** (id, qid unique, subject FK, source, year, paper, section, qno, q_type, level, major_topic_id FK, created_at)
- **QuestionAsset** (id, question_id FK, asset_type ENUM, file_format ENUM, language ENUM, file_path)
- **Association tables**: question_minor_topics, question_subtopics

Create database initialization script [`init_db.py`](init_db.py):

- Import all models
- Call `db.create_all()` to generate schema
- Insert default subjects (MATC, MAT1, MAT2, ICT)
- Create default admin user

## 3. Authentication System

Implement in [`app/auth.py`](app/auth.py):

- Use **Flask-Login** for session management
- Routes: `/login`, `/logout`, `/register` (admin-only can register new users)
- Password hashing with **werkzeug.security** (generate_password_hash, check_password_hash)
- Decorators: `@login_required`, `@admin_required`
- Store user in session, redirect unauthorized access

## 4. File Ingestor Module

Create [`app/ingestor.py`](app/ingestor.py):

**Core Logic**:

1. **scan_directory(source_path)**: Walk through `Source/` folder recursively
2. **parse_filename(filename)** using regex:

   - PP format: `^(?P<subj>\w+)_(?P<source>\w+)_(?P<year>\d+)_(?P<paper>\w+)_(?P<qno>Q\d+)_(?P<lang>\w+)_(?P<type>\w+)\.(?P<ext>\w+)$`
   - QB format: `^(?P<subj>\w+)_(?P<source>QB)_(?P<detail>.+?)_(?P<qno>Q\d+)_(?P<lang>\w+)_(?P<type>\w+)\.(?P<ext>\w+)$`

3. **construct_qid**: Remove language and type → `MATC_DSE_2025_P2_Q5`
4. **upsert_question**:

   - Check if qid exists in `questions` table
   - If not, create with defaults (level=1, q_type from filename or 'CQ')
   - Get question.id

5. **upsert_asset**:

   - Check if asset already exists (question_id + asset_type + language + file_format)
   - Insert or update file_path

6. **Run command**: Flask CLI command `flask ingest --source-path /path/to/Source`

**Extract metadata from folder structure**:

- For PP: `/MATC/PP/DSE/2024/P1/` → source=DSE, year=2024, paper=P1
- For QB: `/MATC/QB/MathSmart2024/` → source=QB, detail=MathSmart2024

## 5. Dashboard (Question Browser)

Implement in [`app/dashboard.py`](app/dashboard.py) and [`templates/dashboard.html`](templates/dashboard.html):

**Filter Form** (using HTMX for dynamic updates):

1. **Subject dropdown** → triggers subtopic load
2. **Source Type radio**: PP (DSE/CE/AL) or QB
3. **Year checkboxes** (multi-select, only for PP)
4. **Section dropdown** (A, B, All)
5. **Topic multi-select** → dynamically load subtopics when topics selected
6. **Subtopic multi-select** (conditional display)
7. **Cross-topic checkbox** (include questions with selected topics as minor)
8. **Level checkboxes** (1, 2, 3)
9. **Question Type radio** (All, MC, CQ)

**Backend Route** `/dashboard/filter` (POST):

- Build SQLAlchemy query with filters
- Join with topics, subtopics, assets
- **Natural ordering**: Use `natsort` library or SQL REGEXP to sort Q1, Q2, Q10 correctly
- Paginate results (20 per page)
- Return JSON for HTMX to update question list

**Question Display**:

- Each row shows: qid, source, year, paper, qno, level, type, major_topic
- **Image preview**: Display QUE image (prefer EN, fallback to CH/BI)
- **View ANS/SOL buttons**: Open modal popup with image
- **Checkbox** for selection (default all checked)

**Selection & Generation**:

- "Generate Document" button → POST selected question IDs to `/generate`

## 6. Admin Panel

### 6A. Topic/Subtopic Management

Route `/admin/topics` in [`app/admin.py`](app/admin.py):

- List all subjects
- For each subject, show topics (expandable)
- For each topic, show subtopics (indented)
- **Actions**: Add Topic, Edit Topic, Delete Topic (cascade to subtopics)
- **Actions**: Add Subtopic under Topic, Edit, Delete
- Use HTMX for inline editing without page reload

### 6B. Question Tagging Interface

Route `/admin/tags` in [`app/admin.py`](app/admin.py):

- Similar filter as dashboard (to find questions to tag)
- For each question:
  - Show QUE preview image
  - **Buttons**: Preview ANS, Preview SOL (modal popups)
  - **Edit form** (inline or modal):
    - Major Topic (dropdown)
    - Minor Topics (multi-select)
    - Subtopics (multi-select, filtered by selected topics)
    - Level (1/2/3 dropdown)
    - Question Type (MC/CQ dropdown)
    - Section (A/B dropdown)
  - **Save** button → AJAX update to `/admin/update_question/<id>`

## 7. Word Document Generator

Implement in [`app/generator.py`](app/generator.py):

**Route** `/generate` (POST):

- Receive: question_ids[], sort_by, answer_mode, skip_lines, new_page_per_question, show_qid

**Generation Options**:

- **Sort by**: question_id (natural order), level, year, topic
- **Answer Modes**: QUE_ONLY, QUE_ANS, QUE_SOL, QUE_THEN_ANS (all questions, then all answers)
- **Skip lines**: Insert 0-5 blank lines between questions
- **New page**: Create page break before each question (boolean)
- **Show QID**: Print filename as heading before each question (boolean)

**Image Mode Implementation** (MVP focus):

1. Create new Document with `python-docx`
2. Set page size to A4 (`section.page_width = Inches(8.27)`)
3. Set narrow margins (0.5 inches)
4. Loop through sorted questions:

   - If show_qid: Add heading with qid
   - Find QUE asset (prefer IMG format, EN language)
   - **Resize logic**: 
     - Open image with Pillow to get dimensions
     - If width > 6 inches, calculate proportional height
     - Insert with `document.add_picture(path, width=Inches(6))`
   - Add blank paragraphs (skip_lines parameter)
   - If new_page_per_question: `document.add_page_break()`
   - Handle ANS/SOL based on answer_mode

5. Save to `output/generated_{timestamp}.docx`
6. Return file download response

**Word Mode** (defer to future):

- Use `docxcompose` to merge existing .docx assets
- Handle style conflicts carefully

## 8. Frontend with Bootstrap 5 + HTMX

**Base Template** [`templates/base.html`](templates/base.html):

- Bootstrap 5 CSS from CDN
- HTMX from CDN
- Navigation: Dashboard | Admin | Logout (if logged in)
- Flash messages container

**HTMX Usage Examples**:

- Filter form: `hx-post="/dashboard/filter" hx-target="#question-list" hx-trigger="change"`
- Pagination: `hx-get="/dashboard/filter?page=2" hx-swap="innerHTML"`
- Topic selector: When topics change, trigger `hx-get="/api/subtopics?topic_ids=1,2"` to update subtopic dropdown
- Modal popups for ANS/SOL: `hx-get="/api/asset/<id>" hx-target="#preview-modal-body"`

**Styling**:

- Use Bootstrap cards for question display
- Sticky filter sidebar on desktop, collapsible on mobile
- Image thumbnails with max-height constraint, clickable to enlarge

## 9. Implementation Sequence

1. **Setup** (30 min): Create project structure, install dependencies, configure `.env`
2. **Database** (45 min): Write models, init script, test connection, create schema
3. **Auth** (30 min): Implement login/logout, user model, decorators
4. **Ingestor** (60 min): Write file scanner, regex parser, upsert logic, CLI command
5. **Dashboard Backend** (60 min): Filter route, query builder, pagination, natural sorting
6. **Dashboard Frontend** (90 min): Bootstrap layout, filter form, HTMX integration, question cards
7. **Admin Topics** (45 min): CRUD routes for topics/subtopics, HTMX inline editing
8. **Admin Tagging** (60 min): Tagging interface, preview buttons, update endpoint
9. **Generator** (90 min): Image resizing, document creation, sorting logic, download response
10. **Testing & Polish** (60 min): Test with your files, fix bugs, improve UX

**Total Estimate**: 10-12 hours of focused development

## Key Technical Decisions

1. **Natural Sorting**: Use custom SQL ORDER BY with CAST/REGEXP or Python `natsort` library after query
2. **Image Preview**: Serve images via Flask route `/files/<path:filepath>` that validates access and returns static file
3. **Session Storage**: Store filter state in Flask session to preserve selections across pagination
4. **Performance**: Add indexes on `questions.qid`, `questions.subject`, `questions.major_topic_id`
5. **Error Handling**: Ingestor logs skipped files to `ingest_errors.log` for review

## Testing Strategy

1. Run ingestor on your existing files: `flask ingest --source-path "D:/Online Question Bank/oqb2/Source"`
2. Verify questions and assets inserted correctly in phpMyAdmin
3. Test dashboard filters with various combinations
4. Test admin tagging on sample questions
5. Generate test documents with different options (QUE only, QUE+ANS, sorting variations)
6. Verify Word output opens correctly in Microsoft Word with proper formatting
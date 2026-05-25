# Online Question Bank System — Administrator Guide

**Version 2.3** | Last Updated: May 2026

---

## Table of Contents

1. [Installation & Setup](#1-installation--setup)
2. [Environment Configuration](#2-environment-configuration)
3. [Database Initialisation](#3-database-initialisation)
4. [Starting the Application](#4-starting-the-application)
5. [User Management](#5-user-management)
6. [Topic & Chapter Management](#6-topic--chapter-management)
7. [Question Management & Tagging](#7-question-management--tagging)
8. [File Ingestion](#8-file-ingestion)
9. [Export & Import](#9-export--import)
10. [Database Health & Sync](#10-database-health--sync)
11. [File Browser](#11-file-browser)
12. [System Settings](#12-system-settings-super-admin)
13. [Backup & Recovery](#13-backup--recovery)
14. [Production Deployment](#14-production-deployment)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Installation & Setup

### Requirements
- Python 3.8+
- MariaDB 10.x or MySQL 8.x
- Windows or Linux

### Steps

```bash
# 1. Clone / copy project to server
# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/Mac

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment (see Section 2)
copy env_template.txt .env

# 5. Create the database in MariaDB
# In MySQL client:
CREATE DATABASE oqb2 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 6. Initialise tables and default data
python init_db.py

# 7. Start the application
python run.py
```

### Windows Quick Start

Run `quickstart.bat` to set up the virtual environment and install dependencies automatically.

---

## 2. Environment Configuration

Copy `env_template.txt` to `.env` and edit:

```ini
# Database
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=oqb2

# Flask
SECRET_KEY=change-this-to-a-long-random-string
FLASK_DEBUG=1          # Set to 0 in production

# Paths
SOURCE_PATH=Q:\Source              # Where question image/doc files live
OUTPUT_PATH=C:\oqb2\output         # Where generated .docx files are saved
```

**Important**:
- `SOURCE_PATH` must be readable by the Python process. On Windows, this can be a network share (e.g. `Q:\Source`).
- `OUTPUT_PATH` must be writable. It is created automatically if it doesn't exist.
- Never commit `.env` to version control.

---

## 3. Database Initialisation

```bash
python init_db.py
```

This creates all tables and inserts:
- **Default subjects**: MATC, MAT1, MAT2, ICT
- **Default admin user**: `admin` / `admin123` — **change this password immediately**
- **Sample topics** for MATC (optional, for testing)

To add additional subjects, insert rows directly into the `subjects` table:
```sql
INSERT INTO subjects (id, name) VALUES ('PHY', 'Physics');
```

---

## 4. Starting the Application

### Development
```bash
python run.py
# Listens on http://0.0.0.0:5000 with debug=True
```

### Production (gunicorn + nginx)
```bash
gunicorn -w 4 -b 127.0.0.1:5000 "app:create_app()"
```

Configure nginx to proxy to `127.0.0.1:5000` and serve HTTPS.

---

## 5. User Management

Navigate to **Admin → Users** (Super Admin only).

### Roles Per Subject
Each user has a permission level for each subject independently:

| Role | Browse | Generate Docs | Admin Panel |
|---|---|---|---|
| **No Access** | ❌ | ❌ | ❌ |
| **View Only** | ✅ | ❌ | ❌ |
| **User** | ✅ | ✅ | ❌ |
| **Admin** | ✅ | ✅ | ✅ (their subjects) |
| **Super Admin** | ✅ | ✅ | ✅ (all subjects + user mgmt) |

### Adding a User
1. Click **Add User**
2. Enter username, password, and whether to grant super admin
3. Save, then set per-subject permissions from the user card

### Editing Permissions
Use the permission dropdowns on each user's card — changes save immediately (one permission at a time).

### Deleting a User
Click **Delete** on the user card. Cannot delete your own account.

---

## 6. Topic & Chapter Management

### Topics (`/admin/topics`)
Topics represent the **curriculum taxonomy**:
- **Topic** → broad category (e.g. "Number and Algebra")
- **Subtopic** → specific unit (e.g. "Polynomials")
  - Subtopics can be marked **hidden** — they won't appear in the dashboard filter by default (toggle the eye icon to show them; useful for textbook-chapter subtopics that should only appear in admin)

**CRUD**: Add, rename, delete. Deletion cascades — deleting a topic removes its subtopics and un-tags linked questions.

**Reorder**: Drag rows to set display order.

### Chapters (`/admin/chapters`)
Chapters represent **textbook organisation** (separate from the topic/subtopic system):
- **Chapter** → textbook chapter
- **Subchapter** → section within a chapter

Same CRUD and reorder behaviour as topics.

Questions can be linked to both a topic AND a chapter — they serve different filtering purposes.

---

## 7. Question Management & Tagging

### Question List (`/admin/questions`)
Full-featured list with the same filters as the dashboard. Useful for finding untagged questions or performing bulk operations.

### Tagging a Question
Click the edit (pencil) icon → a modal opens with three tabs:

**Details tab**: level, question type, section, description, correct percentage, answer text, comment

**Tags tab**: major topic, major subtopic, minor topics (M2M), subtopics (M2M), chapter, subchapter

**Assets tab**: view, upload, delete, and reorder asset files

Rules:
- Major subtopic must belong to major topic if both are set
- Minor topics allow cross-topic tagging (question appears in multiple topic filters)

### Batch Operations
Select multiple questions (checkboxes or "Select All"), then use the toolbar:
- **Batch Update** — choose which fields to update (level, type, section, topics, correct %) and set their values. Only ticked fields are changed.
- **Batch Delete** — permanently removes questions and their assets from DB and disk. Requires typing `DELETE` to confirm.
- **Generate IMG from DOC/MD** — bulk-renders the DOC/MD source assets of the selected questions into PNG IMG assets via Microsoft Word. Modal options: asset types (QUE/ANS/SOL), languages, source format preference (DOC > MD), one tall PNG per slot **or** one PNG per source page, overwrite existing IMG, render width (px), transparent background. Streams progress live; refresh-free.

  **Use cases:**
  - Convert a question library authored in Word to flat images (e.g. for export to a system that can't read DOCX).
  - Replace stale 3-part IMG scans with a single high-fidelity image rendered from the updated DOCX source.
  - Bake the current rendering of an MD question (with pandoc-converted equations) into a plain PNG snapshot.

  **Notes:** Requires Microsoft Word on the server (same path as DOC thumbnails / PDF output). Word is run once per question with the global lock serialising other Word jobs — a 100-question batch takes 2–5 minutes. The output PNG preserves MathType OLE objects, embedded images, and native tables because the path is Word → PDF → PyMuPDF rasterisation. Resolution defaults to the **System Settings → Batch IMG Generation → Default render width** value (default 1500 px).

### Creating a Question Manually
Click **Add Question** → 3-step wizard:
1. Question details (QID, subject, source, year, paper, etc.)
2. Upload asset files
3. Set tags

### Renaming a Question (QID Change)
Edit → Details tab → change QID field. This renames all associated files on disk to match the new QID.

### Asset Management
Edit → Assets tab:
- **Upload**: select file, choose type (QUE/ANS/SOL), language (EN/CH/BI), and part number. Supported formats: images (`.png`/`.jpg`/`.gif`/`.bmp`), Word (`.doc`/`.docx`), and **Markdown** (`.md`/`.markdown`).
- **Delete**: removes from DB and disk
- **Reorder**: drag to change part_number order (for multi-image questions)

### Markdown assets
Markdown assets are **self-contained**: LaTeX math goes inside `$...$` (inline) or `$$...$$` (display), and images are embedded as `data:image/...;base64,...` URIs (not separate files). Each (QUE/ANS/SOL × EN/CH/BI) slot can hold **at most one** `.md` asset (single-part only).

The Edit Question modal exposes two editors per slot:
- **Inline modal** — click **Edit** on an MD asset card (pencil icon), or **New Markdown (inline)** in an empty slot. EasyMDE toolbar, live KaTeX preview, paste-image-as-base64, optimistic concurrency (Ctrl/Cmd+S to save).
- **Fullscreen page** — click **Open fullscreen** on a card, or **New Markdown (fullscreen)** in an empty slot. Same editor, full viewport, beforeunload guard.

### Pandoc requirement (for generation)
Document generation converts Markdown assets to Word via **pandoc** + docxcompose, so pandoc must be installed on the server. Install once:
- **Windows**: [pandoc releases](https://github.com/jgm/pandoc/releases); if pandoc is not on `PATH`, set `PANDOC_PATH=C:\Program Files\Pandoc\pandoc.exe` in `.env`.
- **Linux**: `apt install pandoc` (or distro equivalent).
- **macOS**: `brew install pandoc`.
Verify with `pandoc --version`. Without pandoc, MD assets fall back to an italic placeholder in generated `.docx` (with a clear error message in the log).

### Microsoft Word requirement (for DOC source files + PDF output + DOC thumbnails)

DOCX source assets are merged into the generated document via Microsoft Word COM (`pywin32`), and PDF output uses Word's `ExportAsFixedFormat`. This gives full fidelity for MathType OLE objects, embedded images, drawings, custom fonts, and native tables — features that XML-splicing libraries can't preserve reliably.

**Requirements (Windows only):**
- Microsoft Word installed on the application server (any modern licensed version).
- `pywin32` (installed automatically by `pip install -r requirements.txt` on Windows; the requirement is gated with `sys_platform == "win32"`).
- `PyMuPDF` (used to rasterise PDF pages to PNG for DOC asset thumbnails).
- Optional: `pip install psutil` for precise orphan-WINWORD.EXE cleanup.

**Behaviour without Word:**
- DOC source files render as the legacy italic placeholder (`[Word document: ...]`) in generated `.docx`.
- PDF output is rejected at the API with HTTP 400 ("PDF output requires Microsoft Word + pywin32...").
- DOC thumbnails are silently skipped; the dashboard / viewer show the old download-only stub.

**Configuration keys (in `.env`):**
| Key | Default | Purpose |
|---|---|---|
| `WORD_COM_TIMEOUT` | `300` | Per-job watchdog (seconds) — kill WINWORD if a single call hangs. |
| `WORD_COM_LOCK_TIMEOUT` | `600` | Max wait for the global Word lock when another generation is running. |
| `DOC_THUMBNAIL_PATH` | `<OUTPUT_PATH>/.doc_thumbnails` | Where the cached first-page PNGs live. |
| `DOC_THUMBNAIL_WIDTH` | `1000` | Render width in pixels (~A4 at 96 DPI). |

**Concurrency:** only ONE Word session runs at a time per server (a module-level `threading.Lock`). Concurrent generations queue up to `WORD_COM_LOCK_TIMEOUT`; on timeout the job fails with a clear error message in the GeneratedFile row.

**Source DOCX format expectations:** the merger automatically strips section properties (`<w:sectPr>`) from each source DOCX before insertion, so the master document's A4 / narrow-margins / page numbers always win. Authors do not need to remove their own page setup manually.

**Backfilling thumbnails for an existing library:**
- Open **Admin → Database Health** as a super admin and scroll to the **DOC Asset Thumbnails** card.
- **Backfill Missing** walks every DOC asset and renders a PNG when one isn't already on disk. Skips slots where an IMG eclipses the DOC.
- **Force Re-render All** wipes and regenerates every DOC thumbnail. Useful after changing `DOC_THUMBNAIL_WIDTH`, `THUMBNAIL_TRANSPARENT`, `THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP`, or any other rendering tunable.
- **Delete All** drops every cached PNG; the lazy resolver re-creates them on demand.
- You can also let the lazy-render kick in automatically: any time a user opens a question card / modal / viewer that resolves to a DOC, a render is scheduled in the background. The thumbnail appears in-place within a few seconds (live JS poller — no manual refresh needed).

**Per-preview "Re-render" button:**
Every rendered DOC thumbnail (dashboard card, preview modal, admin question modal) shows a small refresh icon next to the download link — visible only to admins. Clicking it deletes the cached PNG, schedules a fresh render with the current settings, and swaps in the new image live. Great for spot-checking changes to thumbnail tunables without doing a Force Re-render All on the whole library.

**Troubleshooting:**
- **Stuck job / orphan WINWORD.EXE process**: open Task Manager and end any Word processes; subsequent generations will start a fresh instance. The `word_session` cleanup falls back to `taskkill /f /im WINWORD.EXE` if Word.Quit fails.
- **Thumbnails never appear** for a freshly-uploaded `.docx`: check the Flask log for `DOC thumbnail render failed` or `DOC thumbnail worker crashed`. Usually means Word is missing, hung, or the source file is corrupt. The lazy resolver auto-retries after a 5-second cooldown, so a transient failure recovers automatically.
- **Card shows "preview rendering…" indefinitely**: usually means the global Word lock is held by another long-running generation. The poll budget is 3 minutes; if it expires, hard-refresh the page or click the per-preview Re-render button to retry.
- **Rerender doesn't show the new look**: hard-refresh once (Ctrl+F5 / Cmd+Shift+R) to evict any pre-fix browser cache entries. After that, the `Cache-Control: no-cache` headers force the browser to revalidate every fetch.
- **"PDF requires Microsoft Word + pywin32" error**: install pywin32 (`pip install pywin32`) and ensure Word is licensed and runnable as the same user the Flask process runs as.

---

## 8. File Ingestion

Navigate to **Admin → Ingestion**.

### Source Directory Structure
Files must be organised under `SOURCE_PATH` following this structure:
```
SOURCE_PATH/
  MATC/
    PP/
      DSE/2024/P1/   MATC_DSE_2024_P1_Q1_EN_QUE.png
      CE/2005/P1/    MATC_CE_2005_P1_Q1_EN_QUE.png
    QB/
      MATHSMART2024/ MATC_QB_MATHSMART2024_Q1_EN_QUE.png
```

### Filename Convention
```
SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE[_PART].EXT
MATC_DSE_2024_P1_Q5_EN_QUE.png
MATC_DSE_2024_P1_Q5_EN_QUE_2.png   ← multi-image part 2
MATC_DSE_2024_P1_Q5_EN_QUE.md      ← Markdown (no _PART; single-part only)
```

### Running Ingestion

**From the web UI** (recommended):
1. Go to Admin → Ingestion
2. Optionally select a specific subject to scan only that subfolder
3. Click **Preview** to see what files will be processed
4. Click **Start Ingestion**
5. Watch the live log for progress and errors

**From CLI**:
```bash
python cli.py ingest
python cli.py ingest --source-path "D:\NewFiles"
```

### What Ingestion Does
- Scans `SOURCE_PATH` recursively
- Parses filenames with regex patterns
- Creates `Question` records (if new QID) and `QuestionAsset` records (insert or update path)
- Skips files that don't match any naming pattern
- Reports errors to `ingest_errors.log`

### After Ingestion
Run the tag editor in Admin → Questions to assign topics, levels, etc. to newly ingested questions. Use batch update for efficiency.

---

## 9. Export & Import

Navigate to **Admin → Export / Import**.

All exports are CSV. Imports are **idempotent** — safe to re-run.

### Question Tags

**Export** produces one row per question with all tag fields:
```
qid, major_topic, major_subtopic, level, q_type, section, minor_topics, subtopics, chapter, subchapter
```
`minor_topics` and `subtopics` are semicolon-separated lists.

**Import** reads the CSV and updates each question's tags. Choose which fields to import using the checkboxes before uploading.

### Topics / Subtopics

**Export** produces:
```
subject_id, topic_name, sort_order, subtopics
```
`subtopics` is a semicolon-separated list like `Polynomials;Equations;Inequalities`.

**Import** creates topics and subtopics that don't already exist (by name match within subject).

### Chapters / Subchapters
Same format as Topics, but for the chapter system.

**Workflow tip**: Export → edit in Excel → import to bulk-create or update topics/chapters.

---

## 10. Database Health & Sync

Navigate to **Admin → Health** (Super Admin only).

### DB Statistics (auto-loads on page open)
- Total questions, assets, subjects
- Per-subject question and asset counts
- Anomalies: untagged questions, questions with no assets, questions with no level or q_type, duplicate QIDs, path mismatches

Click any anomaly count to see the full list of affected QIDs.

### Untracked Files
Click **Scan Untracked Files** to find files on disk that have no corresponding DB record. Use this to catch files that were added to `SOURCE_PATH` but never ingested.

### Orphan Sync
Click **Dry Run** first to preview what would be removed:
- **Orphaned assets** — DB records whose file no longer exists on disk
- **Orphaned questions** — questions with no assets remaining (grace period: < 24h old questions are skipped)

Click **Delete Mode** (or run `python cli.py sync --no-dry-run`) to execute the deletions.

---

## 11. File Browser

Navigate to **Admin → Files** (Super Admin only).

Provides a web interface to browse, upload, download, rename, delete files and create directories within `SOURCE_PATH`. Useful for correcting filenames without direct file system access.

**Note**: After renaming files here, re-run ingestion or use the question rename function in Admin → Questions to keep the database in sync.

---

## 12. System Settings (Super Admin)

Navigate to **Admin → System Settings**.

A DB-backed page for runtime tunables. Changes apply immediately to the running server — **no restart needed**. Settings persist in the `system_settings` table and override the `.env` / `Config` bootstrap default. Reset any single setting back to its `.env` default with the per-row "Reset to .env default" link.

### Categories

| Group | Keys | Notes |
|---|---|---|
| Dashboard | `QUESTIONS_PER_PAGE` | Default page size for question lists (users can still override per session). |
| Markdown | `MD_MAX_SIZE_BYTES` | Hard cap on individual `.md` asset uploads. |
| Word COM | `WORD_COM_TIMEOUT`, `WORD_COM_LOCK_TIMEOUT` | Per-job watchdog / global-lock wait. |
| Thumbnails | `DOC_THUMBNAIL_WIDTH`, `THUMBNAIL_TRANSPARENT`, `THUMBNAIL_WHITENESS_THRESHOLD`, `THUMBNAIL_BOTTOM_PADDING_PX` | Apply to new renders only — after changing, run **Database Health → DOC Asset Thumbnails → Force Re-render All** to apply to existing cache. |
| Batch IMG Generation | `BATCH_IMG_DEFAULT_WIDTH`, `BATCH_IMG_DEFAULT_STITCH` | Pre-fill the **Generate IMG** modal in Question Management. |

### Settings that stay in `.env`

Secrets and infrastructure paths are intentionally NOT exposed here:

- `SECRET_KEY`
- DB credentials (`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`)
- `SOURCE_PATH`, `OUTPUT_PATH`, `DOC_THUMBNAIL_PATH`
- `PANDOC_PATH`

Edit these in `.env` and restart the server.

### Tips

- After flipping **THUMBNAIL_TRANSPARENT** on/off, the existing cached PNGs still use the old setting. Run **Force Re-render All** in DB Health to apply.
- **WORD_COM_LOCK_TIMEOUT** governs how long an incoming generation will wait if another Word job is in progress. Raise it for large batch generations; lower it for snappier UX.
- **MD_MAX_SIZE_BYTES** is enforced on upload. Existing oversized MD assets remain readable.

---

## 13. Backup & Recovery

### Database Backup
```bash
mysqldump -u root -p oqb2 > oqb2_backup_$(date +%Y%m%d).sql
```

### File Backup
Back up the entire `SOURCE_PATH` directory. Generated files in `OUTPUT_PATH` can be regenerated at any time and don't strictly need backing up.

### Recovery
```bash
# Restore database
mysql -u root -p oqb2 < oqb2_backup_20260523.sql

# Re-ingest if source files were modified since backup
python cli.py ingest
```

---

## 14. Production Deployment

### Security Checklist
- [ ] Change default `admin` password
- [ ] Set a strong, random `SECRET_KEY` in `.env`
- [ ] Set `FLASK_DEBUG=0`
- [ ] Use a strong database password
- [ ] Enable HTTPS via reverse proxy
- [ ] Restrict DB access to localhost only
- [ ] Review user permissions regularly

### Gunicorn + Nginx Example

`gunicorn.service` (systemd):
```ini
[Service]
WorkingDirectory=/opt/oqb2
ExecStart=/opt/oqb2/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 "app:create_app()"
Restart=always
```

Nginx config snippet:
```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    # Needed for SSE (ingestion/health streams):
    proxy_buffering off;
    proxy_cache off;
}
```

---

## 15. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Images not displaying | `SOURCE_PATH` wrong or drive not mounted | Verify `.env`, ensure drive is accessible |
| Ingestion skips all files | Wrong folder structure or filename convention | Check the naming rules in Section 8 |
| Generation stays "pending" | Background thread crashed at startup (likely DB error) | Check terminal for errors; restart app |
| Can't access admin panel | User has no admin permissions | Super admin must assign `admin` role |
| "stale generating" on startup | Server restarted mid-generation | Auto-reset to `failed` — safe to regenerate |
| Sync deletes too much | Files on a network drive that was temporarily disconnected | Only run sync when `SOURCE_PATH` is fully accessible |
| Path mismatch in health stats | File was moved/renamed externally | Use Admin → Questions rename, or re-ingest |

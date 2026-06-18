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
8. [File Ingestion (Smart Import)](#8-file-ingestion-smart-import)
9. [Export & Import](#9-export--import)
10. [Database Health & Sync](#10-database-health--sync)
11. [Toolbox](#11-toolbox)
12. [File Browser](#12-file-browser)
13. [System Settings](#13-system-settings-super-admin)
14. [Backup & Recovery](#14-backup--recovery)
15. [Production Deployment](#15-production-deployment)
16. [Troubleshooting](#16-troubleshooting)

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
SOURCE_PATH=Q:\Source              # Where question image/doc files live (read-mostly library)
STORAGE_PATH=Q:\Storage            # Unified app-managed tree (Shared / System / User)
# Optional fine-grained overrides (default to subfolders of STORAGE_PATH):
#   SHARED_PATH, SYSTEM_PATH, USER_PATH, PDF_SOURCE_PATH, DOC_THUMBNAIL_PATH
OUTPUT_PATH=C:\oqb2\output         # LEGACY fallback for pre-migration generated docs

# AI Tools (optional — proofreading + Markdown generation)
LLM_API_KEY=                       # Global fallback LLM API key (per-endpoint keys override it)
LLM_KEY_SECRET=                    # Optional Fernet secret for encrypting UI-entered endpoint keys (else SECRET_KEY)
AI_TOOLS_ENABLED=1                 # Master on/off (also editable in System Settings)
LLM_IMAGE_MAX_DIM=1600             # Image downscale long-edge in px (also in System Settings)
```

**Important**:
- `SOURCE_PATH` must be readable by the Python process. On Windows, this can be a network share (e.g. `Q:\Source`).
- `STORAGE_PATH` must be **writable and on the same drive as `SOURCE_PATH`** (the File Browser refuses roots on other drives). Its `Shared` / `System` / `User` subfolders are created automatically at startup.
- `OUTPUT_PATH` is now only a legacy fallback for generated documents created before the storage migration; new documents are written to `STORAGE_PATH/User/<username>/generated`. Run `python cli.py migrate-storage` to relocate existing files (see §14).
- Never commit `.env` to version control.
- AI Tools keys are optional — leave them blank if you don't use the feature. Rotating `LLM_KEY_SECRET` (or `SECRET_KEY` when it's blank) invalidates any endpoint API keys stored encrypted in the DB.

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

### Upgrading an existing database (Versions refactor)
The asset **language** column was renamed to **version** and the enum widened to include `ENO`/`CHO`. Existing deployments auto-upgrade on first startup (an idempotent `INFORMATION_SCHEMA` check in `app/__init__.py` runs `ALTER TABLE question_assets CHANGE COLUMN language version ENUM('EN','CH','BI','ENO','CHO')`). To run it manually instead — e.g. on a DB copy first — use the idempotent standalone script:
```bash
python migrate_versions.py
```
It is safe to run repeatedly; it renames `language`→`version` (carrying the `uq_asset_identity` unique index over), or widens an existing `version` enum, or does nothing if already up to date.

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
- **Generate IMG from DOC/MD** — bulk-renders the DOC/MD source assets of the selected questions into PNG IMG assets via Microsoft Word. Modal options: asset types (QUE/ANS/SOL), versions (EN/CH/BI/ENO/CHO), source format preference (DOC > MD), one tall PNG per slot **or** one PNG per source page, overwrite existing IMG, render width (px), transparent background. Streams progress live; refresh-free.

  **Use cases:**
  - Convert a question library authored in Word to flat images (e.g. for export to a system that can't read DOCX).
  - Replace stale 3-part IMG scans with a single high-fidelity image rendered from the updated DOCX source.
  - Bake the current rendering of an MD question (with pandoc-converted equations) into a plain PNG snapshot.

  **Notes:** Requires Microsoft Word on the server (same path as DOC thumbnails / PDF output). Word is run once per question with the global lock serialising other Word jobs — a 100-question batch takes 2–5 minutes. The output PNG preserves MathType OLE objects, embedded images, and native tables because the path is Word → PDF → PyMuPDF rasterisation. Resolution defaults to the **System Settings → Batch IMG Generation → Default render width** value (default 1500 px).

- **Copy/Move Assets** — copies or moves matching assets between slots inside each selected question. Add one or more operations, then choose Copy or Move, source versions, source asset types, source formats, target versions, target asset types, target formats, and whether to overwrite target assets. Operations run sequentially and stream a live log. The modal requires a cannot-undo confirmation checkbox before running and shows a large warning when more than 100 questions are selected. IMG parts keep their part numbers/order; MD and DOC/DOCX remain single-slot. If exactly one source version or asset type is selected, it can map to any target version/type (for example `QUE→SOL`); if multiple source versions/types are selected, the corresponding target axis is greyed out and mirrors the source selections. Formats are always same-format only.

- **AI Tools** — runs a configured LLM (local or cloud) over the selected questions. Main operations:
  - **Check images (proofread):** pick the *typed* version to check (e.g. EN) and the *official* reference version (e.g. ENO), plus the asset types (QUE/ANS/SOL). For each slot the typed and official images are sent to a vision LLM, which reports discrepancies (typos, wrong numbers, altered math, swapped options). The result is recorded on each asset and shown in the edit-question modal as both a compact header badge and a prominent **status bar** above the slot's images: green **OK**, red **issues** (with the full issue list), amber **check error**, or grey **Not proofread**. Tick *Re-check* to redo slots already checked. The status bar is **editable** — use the inline buttons to **mark correct**, **mark issue…** (prompts for a note + severity), or **clear** (set back to unchecked) without re-running the model.
  - **Generate Markdown:** pick the *source* image version (typed or official) and the *target* version, plus asset types. The LLM transcribes the image(s) into a Markdown asset (math as LaTeX). *Embed figures* controls diagram handling: with it on, a **text-only** question produces clean Markdown with **no image**, while a question that **has a diagram/graph/geometry** gets a base64 image placed where the figure belongs — **cropped to just that figure** when the model can locate it, otherwise the whole source image. *Overwrite* replaces an existing target MD.
  - **Generate ANS/SOL (solve):** pick target versions (EN/CH/BI/ENO/CHO) and targets (**ANS Markdown**, **SOL Markdown**, and/or **ANS Text**). The LLM reads the QUE, works out the answer, and writes the requested output. This is different from Generate Markdown, which only transcribes source images. Optional **Also send ENO/CHO SOL** supplies official solutions as context when they exist. *Overwrite* replaces existing target MD / Answer Text.
  - **Check ANS/SOL (solve):** pick versions, targets, and formats (IMG/MD/DOCX). The LLM independently solves the QUE, then checks the existing ANS/SOL target. ANS/SOL asset issues are stored in the same proofread status fields and appear in the edit modal; **ANS Text** checks are reported in the run log only and do not persist check state.

  A live console streams per-slot results with a progress bar, and **Stop** is a genuine server-side cancel (it stops after the current item, so no more LLM calls fire). Subject-admins can run this on their own subjects. The button only appears when AI Tools are enabled (System Settings → AI Tools). **Endpoints must be configured first** — see [§12 System Settings → LLM Endpoints](#12-system-settings-super-admin). A vision-capable model is required for these operations.

  **Per-question shortcuts:** inside the edit-question modal — on **both** the Question Management page and the dashboard — each version/asset-type slot's **Markdown** area has a **Generate with AI** button (and a robot *regenerate* button when a Markdown asset already exists) whenever **any** version has source images for that type (e.g. you can build EN Markdown from ENO scans). ANS/SOL sections also have **Solve Gen** and **solve-check** controls that work from the QUE, and the **Answer Text** field has **Generate** / **Check** buttons. These per-slot controls appear for any admin when AI Tools are enabled.

### Creating a Question Manually
Click **Add Question** → 3-step wizard:
1. Question details (QID, subject, source, year, paper, etc.)
2. Upload asset files
3. Set tags

### Renaming a Question (QID Change)
Edit → Details tab → change QID field. This renames all associated files on disk to match the new QID.

### Asset Management
Edit → Assets tab:
- **Upload**: select file, choose type (QUE/ANS/SOL), version (EN/CH/BI/ENO/CHO), and part number. The Assets tab has one tab per version. Supported formats: images (`.png`/`.jpg`/`.gif`/`.bmp`), Word (`.doc`/`.docx`), and **Markdown** (`.md`/`.markdown`).
- **Delete**: removes from DB and disk
- **Reorder**: drag to change part_number order (for multi-image questions)

### Markdown assets
Markdown assets are **self-contained**: LaTeX math goes inside `$...$` (inline) or `$$...$$` (display), and images are embedded as `data:image/...;base64,...` URIs (not separate files). Each (QUE/ANS/SOL × version) slot can hold **at most one** `.md` asset (single-part only).

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
| `DOC_THUMBNAIL_PATH` | `<SYSTEM_PATH>/doc_thumbnails` | Where the cached first-page PNGs live. |
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

## 8. File Ingestion (Smart Import)

Navigate to **Admin → Smart Import**. It has two modes:
- **Library scan** — the classic strict scan of `SOURCE_PATH` (described below).
- **Folder import** — match an arbitrary folder of files onto questions, with review (see [Folder import](#folder-import-heuristic)).

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
SUBJ_SOURCE_YEAR_PAPER_QNO_VERSION_TYPE[_PART].EXT
MATC_DSE_2024_P1_Q5_EN_QUE.png
MATC_DSE_2024_P1_Q5_EN_QUE_2.png   ← multi-image part 2
MATC_DSE_2024_P1_Q5_EN_QUE.md      ← Markdown (no _PART; single-part only)
MATC_DSE_2024_P1_Q5_ENO_QUE.png    ← ENO = official public-exam scan
```
`VERSION` ∈ `EN` / `CH` / `BI` / `ENO` (English Official) / `CHO` (Chinese Official).

### Running a Library scan

**From the web UI** (recommended):
1. Go to Admin → Smart Import → **Library scan** mode
2. Optionally select a specific subject to scan only that subfolder
3. Click **Preview** to see what files will be processed
4. Click **Start Ingestion**
5. Watch the live log for progress and errors

**From CLI**:
```bash
python cli.py ingest
python cli.py ingest --source-path "D:\NewFiles"
```

### What a Library scan Does
- Scans `SOURCE_PATH` recursively
- Parses filenames with regex patterns
- Creates `Question` records (if new QID) and `QuestionAsset` records (insert or update path)
- Skips files that don't match any naming pattern
- Reports errors to `ingest_errors.log`

<a name="folder-import-heuristic"></a>
### Folder import (heuristic match + review)

Use this when you have a folder of files that are **not** canonically named/placed — for example, updated/fixed images exported as `2012/P1/Q9.png`, or a fresh dump you have not renamed yet. Supports **IMG, DOC, and MD**.

1. Go to Admin → Smart Import → **Folder import** mode.
2. **Choose** a folder on the server (any location you can access in the file browser, e.g. under `Shared/<subject>`).
3. Set the **profile** — the defaults applied to every file: Subject, Source (DSE/CE/AL/QB), default Version (EN/CH/…) and Type (QUE/ANS/SOL), and optionally the QB Detail. These fill in whatever the folder/filenames don't already encode.
   - Flags: **Overwrite existing slot** (replace files already in the target slot), **Back up replaced files** (copies them to `System/ImportBackups/<timestamp>/` first), **Create missing questions** (also create the `Question` record when the QID does not exist yet — handy for ingesting a brand-new folder).
   - Optional **Analyze with AI**: asks an LLM to infer the folder's structure (which defaults to use) and re-runs the match. Requires AI Tools to be enabled.
4. Click **Analyze folder**. Each file becomes a proposal with a status — **Overwrite** / **Add** / **Unmatched** / **Ambiguous** / **Skip** — plus a confidence and an **old-vs-new preview**.
5. **Review**: tick the files to apply (or use *Accept all matched*), fix any QID or slot (version/type) inline, and filter the list (e.g. show only unmatched/ambiguous).
6. Click **Apply**. Accepted files are copied into their canonical `SOURCE_PATH` location and the slot is overwritten (images replace the whole slot; DOC/MD replace the single slot). Watch the live log.

Opening Folder import from **Admin → Questions** via the **Import Files** button restricts matching to the currently-selected questions (and disables create-missing) — ideal for batch-fixing flagged questions.

### After importing
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

## 11. Toolbox

Navigate to **My Stuff → Toolbox**. The Toolbox is a home for self-service utilities. The landing page is available to all logged-in users; individual tools apply their own permissions.

### Markup (`/admin/toolbox/markup`)

Markup is available to all logged-in users. It is a mobile-friendly infinite white canvas for handwritten solutions and image annotation:

1. Open from **Toolbox → Markup**, from Present mode's QUE/SOL panel pencil button, by uploading/pasting an image inside the tool, or by sharing an image to the installed PWA on Android.
2. Draw with pen or highlighter. Use one finger/stylus to draw and two fingers to pan/pinch-zoom. The Hand tool pans with one finger.
3. Use lasso to select strokes and move/delete them. Add text or perfect line/rectangle/ellipse shapes from the toolbar.
4. Export/share creates a white-background PNG cropped to the regions containing the imported image or drawings.

Markup autosaves locally in the browser's IndexedDB so an in-progress drawing can be restored on the same device. It does not save drawings on the server.

**PWA notes**:
- Android Chrome can install Markup and expose it as a Share target for image files after the PWA has been opened once.
- iOS Safari can install the PWA and use upload/paste/photo-picker workflows, but iOS does not support Web Share Target. Use an iOS Shortcut workaround only as a manual fallback.
- PWA install/share features require HTTPS in production (localhost also works for development).

### PDF Tool (`/admin/toolbox/pdf`)

The PDF Tool is admin-only. It is a workbench for preparing scanned PDFs:

1. **Load a PDF** — upload a file, or **Pick from server** to choose one via the unified file selector (your accessible Shared subject folders + your personal My Files home, with filter / sort / paste-path). You can also **drag a PDF or image file straight onto the preview grid** — it is uploaded and its pages are added untouched (no split / filters, default DPI; images become single pages), skipping steps 1–2.
2. **Process & add to preview** — pick the active source, set the **Resolution (DPI)** (page-size independent: 200 DPI ≈ A4 1654 px / A3 2339 px wide; 150 draft, 300 print), choose a *Rotate first* angle, an *A3 split mode*, and optional **deskew**, then add the resulting pages to the preview grid. Brightness, contrast, sharpen, grayscale, and black & white are applied in Step 3 (preview toolbar). The active-source page strip is shown here.
   - **Split modes**: *None* (whole pages); *Split each A3 down the middle* (left then right half); *Mode 1 — folded individual copies* (2 A3 sheets per student scanned folded → 4 ordered pages per student); *Mode 2 — destapled A4 booklet stack* (the split halves are reordered back into reading order; assumes an even number of A3 pages).
3. **Assemble** — **select** pages (click; Ctrl/Cmd-click to toggle; Shift-click for a range; or drag a box over them — on touch devices tap **Select** or **long-press** a page to enter tap-to-select mode) and act on them with the **operations toolbar** (rotate, the **Adjust** menu for brightness / contrast / sharpen / grayscale / B&W / deskew, **Crop** — draw/move/resize a crop box and apply it to all selected pages; re-cropping narrows further and *Remove crop* restores the full page — Mark up, Find & Mark, reset, delete, copy/cut/paste). Keyboard shortcuts: **Del**, **Ctrl+A**, **Ctrl+C/X/V**, **Ctrl+Z**, **Esc**. Drag selected pages to **reorder** them together, **double-click** a page for a large preview (with a **Quick / Full res** toggle and ←/→ navigation), and load more PDFs to **merge** them into the same preview. A slider above the grid adjusts the thumbnail size (remembered per browser).
4. **Redact / Highlight / Mark up** (optional) —
   - **Mark up** opens a fullscreen editor: draw redact boxes, **Remove boxes** (erase content and leave plain white page — true removal on export, not just a cover), highlights, text, freehand pen strokes, or **insert an image** (signature, stamp, logo — PNG transparency preserved); move/resize/delete marks; **Copy to → all/selected pages** stamps a mark across the document. The **Text** tool is Acrobat-style — click the page and type directly (Enter = new line, click away to commit, Esc to cancel; click an existing text mark to re-edit), with **typeface** (Helvetica/Times/Courier) and **font size** controls that apply live while you type. The editor has its own per-page **Undo/Redo** (Ctrl+Z / Ctrl+Y) and **zoom** (mouse wheel, pinch, +/− buttons; pan with right-mouse drag, two fingers, middle-drag, or space+drag).
   - **Find & Mark** searches for phrases and marks every match: engines are **Auto**, **Digital** (PDF text layer), **OCR** (scanned pages — needs Tesseract, see Notes), or **AI** (vision LLM; reuses the PDF Import endpoints). Each term can **Highlight**, **Redact**, or **Remove** (erase to white) its matches. The AI engine has two modes: **Find the search terms** (each term keeps its own style) or **Custom instruction** (describe anything — "every signature", "all personal names" — with its own mark style). All searches **stream live progress** (per-page) and have a **Stop** button, so even long scanned PDFs never time out, and a **Run pages in parallel** option speeds them up (OCR across CPU cores, or AI round-trips across the endpoint's concurrency). Fuzzy matching with a threshold slider handles OCR noise; phrases broken across lines/pages still match; sideways/rotated scans are auto-detected (OCR retries other orientations). Results arrive as **pending** marks (dashed orange) — review and **Accept all** (or **Discard pending**), or adjust/delete them in the editor first. Pending marks are never exported.
5. **Export** — the **file name** defaults to the loaded PDF's name. Choose *Combined PDF* or *ZIP of page images*, an **Output** mode when marks exist (**Digital** keeps the PDF vector and applies **true redaction** — the covered text/images are removed from the file, not just hidden; **Image (flatten)** rasterises everything so redacted content is unrecoverable pixels), and an optional **Compression**: *Light* (recompress images, keep resolution), *Medium* (~150 DPI), *Strong* (~100 DPI), or *Fit to size…* with a max-MB limit (the server retries down a quality ladder until the file fits — best effort). Then **Export selected** (download) or **Save to server** (the file selector opens in folder-pick mode so you choose any writable location — a Shared subject folder or your My Files home; picking a folder root falls back to a `Saved` subfolder — so Batch PDF Import can pick it up). With a partial selection you are asked **Selected only or All pages**; with nothing selected, all pages export. If the server file name already exists you are asked to **Overwrite** or **Rename**. For Mode 1, tick *Separate file every 4 pages* to get one file per student.

**Notes**:
- Pages with only lossless operations (a single 90° rotate, or a crop) are exported without re-rasterising via `pypdf`; any pixel adjustment (deskew / brightness / sharpen / B&W) rasterises that page at the chosen DPI.
- Deskew requires NumPy (the control is disabled if NumPy is unavailable).
- **OCR search requires Tesseract**: install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (Windows installer) plus the `pytesseract` Python package. Common install paths are auto-detected; otherwise set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`. The OCR option is greyed out when unavailable. OCR render resolution is the **Toolbox OCR DPI** System Setting (default 300); parallel worker count is **Toolbox Find & Mark parallel workers** (`TOOLBOX_OCR_WORKERS`, default 4, capped by CPU count); sideways-scan handling is **Toolbox OCR auto-orientation** (`TOOLBOX_OCR_AUTO_ORIENT`, default on).
- **AI detect** requires AI Tools to be enabled with a vision-capable LLM endpoint (defaults to the PDF Import endpoint). Its prompts are editable on **Admin → AI Prompts** (group "PDF Batch Import — Generic Extraction", shared with Generic Extraction).
- The same processing controls appear in **PDF Batch Import → Step 1 → Pre-process scans**, so scans can be rotated / split / cleaned up as they are staged for import.

### PDF Batch Import (3 steps)

1. **Setup** — choose Exam paper or Generic extraction, enter the paper name (exam), upload or pick question/solution PDFs, set deskew/pre-process, then **Load PDF**.
2. **Bounding boxes** — optionally **Run LLM detection**, or draw/edit boxes on each page (drag, resize, Add box, per-page Re-run). Set detection method, custom prompt, uniform width, and debug here.
3. **Import** — exam: pick QUE/SOL versions, overwrite/trim options, **Import to database**; generic: **Download all as ZIP**.
- Config keys: `TOOLBOX_DEFAULT_DPI` (default resolution; also in Admin → System Settings), `TOOLBOX_OCR_DPI`, `TOOLBOX_OCR_WORKERS`, `TOOLBOX_OCR_AUTO_ORIENT` (System Settings), `TOOLBOX_RASTER_WIDTH`, `TOOLBOX_EXPORT_WIDTH`, `TOOLBOX_SAVE_SUBDIR`, `TESSERACT_CMD` (`.env` only). Dependencies: `pypdf>=4.0`, `rapidfuzz`, `pytesseract` (+ local Tesseract install for OCR).

---

## 12. File Browser

There are now **two** browsers sharing one backend:

**Super-admin browser** — **Admin → Files** (Super Admin only). Browse, upload, download, rename, delete, copy and create directories across multiple **roots**: `Source`, the whole `Storage` tree, and any extra roots you register via **Manage roots** (must be on the same drive as `SOURCE_PATH`; they persist in System Settings). Useful for correcting filenames without direct file-system access.

**Per-user browser** — **My Stuff → File Browser** (`/files/browser`), available to everyone except pure viewers. Each user sees:
- their personal **My Files** home (`Storage/User/<username>`) — full read/write;
- the **Shared** folder for each subject they can access — read/write for subject **admins**, read-only for **users** (viewers are excluded);
- their `generated/` subfolder is shown **read-only** (manage generated documents from **My Files** instead, so database records stay in sync).

**Note**: After renaming asset files in `Source`, re-run ingestion or use the question rename function in Admin → Questions to keep the database in sync.

---

## 13. System Settings (Super Admin)

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
| AI Tools | `AI_TOOLS_ENABLED`, `LLM_IMAGE_MAX_DIM`, `*_DEFAULT_LLM` | Master on/off switch, image downscale size, and per-feature default LLM endpoints (`AUTOTAG`, `MD`, `CHECK`, `SOLVEGEN`, `SOLVECHECK`, `PDF_IMPORT`, `EXPLAIN`). |
| Markup | `MARKUP_NORMALIZED_MAX_DIM` | Longest-edge canvas size (world units) for imported Markup images (default 2400). Larger = bigger default fit view and thicker default pen sizes. |

### LLM Endpoints (AI Tools)

The **AI Tools** feature (proofreading, Markdown generation, solve-generation/checking, and auto-tagging in Question Management) needs at least one configured LLM endpoint. Open **Admin → LLM Endpoints** (linked from the System Settings header and the Admin navbar; super-admin only).

- Click **Add Endpoint** and fill in: **Name**, **Model name**, **Base URL** (the API root — e.g. `https://api.openai.com/v1`, `https://openrouter.ai/api/v1`, `https://api.poe.com/v1`, or `http://localhost:11434/v1`; do **not** include `/chat/completions` or `/responses`), **API key** (optional — blank uses the `.env` `LLM_API_KEY`), provider, max output tokens, temperature, timeout, and the **Vision** toggle.
- **API protocol**: **Chat Completions** (default — local Ollama/LM Studio, OpenRouter GPT-5.5) or **Responses API** (recommended for Poe reasoning models).
- **Reasoning**: set **Reasoning effort** (`off` / `low` / `medium` / `high`, or inherit from System Settings → **Default reasoning effort**). For Responses endpoints, set **Reasoning summary** (`auto` / `none`). Raise **Max output tokens** (e.g. 8192+) for reasoning models. Use **Advanced request JSON** for provider-specific body fields (Poe Chat Completions may need keys here because top-level `reasoning_effort` is ignored).
- **Vision is required** for both AI Tools operations. Local models must be vision-capable (e.g. Qwen-VL, Llava, Llama-Vision).
- API keys entered here are **encrypted at rest** (Fernet). The plaintext is never shown again — leave the key field blank when editing to keep the stored key, type a new value to replace it, or tick **Remove the stored key** to fall back to `.env`.
- Use the **Test** button to send a tiny ping and confirm connectivity / auth / model name.
- Use the **Chat** button for a raw direct conversation with the endpoint. Vision endpoints support pasted, dragged, or attached images; reasoning models show a collapsible **Reasoning** panel while streaming.
- Cloud (OpenAI, OpenRouter, Poe) and local (Ollama, LM Studio, vLLM) endpoints all work, as do Anthropic/Gemini behind an OpenAI-compatible proxy.

### Settings that stay in `.env`

Secrets and infrastructure paths are intentionally NOT exposed here:

- `SECRET_KEY`
- DB credentials (`DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`)
- `SOURCE_PATH`, `OUTPUT_PATH`, `DOC_THUMBNAIL_PATH`
- `PANDOC_PATH`
- `LLM_API_KEY` (global fallback LLM key), `LLM_KEY_SECRET` (Fernet secret for encrypting endpoint keys — rotating it invalidates stored keys)

Edit these in `.env` and restart the server.

### Tips

- After flipping **THUMBNAIL_TRANSPARENT** on/off, the existing cached PNGs still use the old setting. Run **Force Re-render All** in DB Health to apply.
- **WORD_COM_LOCK_TIMEOUT** governs how long an incoming generation will wait if another Word job is in progress. Raise it for large batch generations; lower it for snappier UX.
- **MD_MAX_SIZE_BYTES** is enforced on upload. Existing oversized MD assets remain readable.

---

## 14. Backup & Recovery

### Database Backup
```bash
mysqldump -u root -p oqb2 > oqb2_backup_$(date +%Y%m%d).sql
```

### File Backup
Back up the entire `SOURCE_PATH` directory (the question library) and the `STORAGE_PATH` tree. Within `STORAGE_PATH`, the important data is `Shared/` (per-subject shared files) and `User/` (personal homes + generated documents); `System/` holds only regenerable caches/temp and can be skipped. Generated documents can also be re-created from the app, so they are lower priority than `Source` + `Shared`.

### Migrating an existing deployment to the Storage tree
After upgrading, relocate legacy files into the new layout. **Preview first** (no changes are made):
```bash
python cli.py migrate-storage --dry-run
```
Review the printed plan, then apply:
```bash
python cli.py migrate-storage --no-dry-run
```
This creates the `Storage` tree + one `Shared/<SUBJECT>` folder per subject, moves the DOC thumbnail cache into `System/doc_thumbnails`, moves each generated document (and its PDF sibling) into `User/<username>/generated`, and archives the old flat `Source_PDF` into `Shared/_archive` (super-admin only — subject admins refile from there into each subject's Shared folder). It is idempotent and skips anything already in place. Use `--old-pdf-source` / `--old-thumbnails` if your legacy folders aren't at the defaults.

### Recovery
```bash
# Restore database
mysql -u root -p oqb2 < oqb2_backup_20260523.sql

# Re-ingest if source files were modified since backup
python cli.py ingest
```

---

## 15. Production Deployment

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

## 16. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Images not displaying | `SOURCE_PATH` wrong or drive not mounted | Verify `.env`, ensure drive is accessible |
| Ingestion skips all files | Wrong folder structure or filename convention | Check the naming rules in Section 8 |
| Generation stays "pending" | Background thread crashed at startup (likely DB error) | Check terminal for errors; restart app |
| Can't access admin panel | User has no admin permissions | Super admin must assign `admin` role |
| "stale generating" on startup | Server restarted mid-generation | Auto-reset to `failed` — safe to regenerate |
| Sync deletes too much | Files on a network drive that was temporarily disconnected | Only run sync when `SOURCE_PATH` is fully accessible |
| Path mismatch in health stats | File was moved/renamed externally | Use Admin → Questions rename, or re-ingest |

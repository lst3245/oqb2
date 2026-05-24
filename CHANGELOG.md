# Changelog

All notable changes to the Online Question Bank System are documented in this file.

## [Unreleased]

### ✨ Enhanced Features

#### Show Selected Only — paginates the FULL selection (was previously broken)
- Fixed: when `Show Selected Only` was on, the dashboard only showed the selected questions that happened to be on the current page of the active filter. With a 156-question selection and a filter that returned 5 of them on page 1, the user saw 5 and had no way to view the other 151 — the feature was effectively useless across pages.
- The toggle now drives a server-side override: it writes the current selection into the new hidden `#idsInput` and re-submits the filter form. The backend's new `ids` parameter (mirrors the existing `qids` override) returns ONLY those questions, ignores all sidebar filters, and paginates them with the user's chosen `page_size`. The user can now browse their entire selection regardless of the sidebar filter.
- The banner now reads "Showing selected questions only — N selected total, page shows M. Sidebar filters are temporarily ignored." Pagination links work normally.
- Selection-changing actions while the toggle is on (Clear button, Set Operations Apply, Select All) automatically re-sync the visible page to the new selection.
- Files: `app/dashboard.py` (new `ids` filter param), `templates/dashboard.html` (`#idsInput`, `syncShowSelectedOnlyToServer()`, refactored `applyShowSelectedOnly`).

#### Set Operations modal — new "Filter Result" chip
- Added a fourth source chip (after Selection, Result, Saved Sets) labelled **Filter Result**. It resolves to every DB ID matching the current sidebar filter (across all pages, not just the visible one).
- Common use case: build `Selection ∩ Filter Result` and click `Replace Selection` to trim the selection down to whatever the current filter returns. Or `Selection ∪ Filter Result` to add the entire current filter's results to the selection.
- Files: `templates/dashboard.html` (chip button + `getCurrentFilterResultIds()` resolver).

### 🐛 Bug Fixes

#### Selection vs Filter — independence policy and stale-tick fix
- **Stale checkbox ticks** after a Replace Selection: `initializeSelectionState()` previously only set `cb.checked = true` for selected IDs and never reset it for the deselected branch. Switching from a 5-question selection to a 2-question result left 3 ticks stranded on screen. Now both branches go through `cb.checked = isSelected`.
- **Auto-prune of selection on every filter change**: `pruneSelectionsToFilteredResults()` ran after every HTMX swap of `#questionList` and silently deleted selected IDs not in the new filter results. This violated the "selection survives across filter changes" mental model. Function and its caller removed; selection now strictly survives any filter / sort / paging change.
- **Subject change** clears selection automatically (selections are subject-tied and cannot be generated as a mix). A `suppressSelectionClearOnSubjectChange` flag bypasses this for the `?question_set_id=` flow when its target subject differs from the current one.
- **`?question_set_id=` apply** is now pure category (1) when the set's subject matches the active dashboard subject: only the selection changes; the sidebar filter is left untouched. (Different-subject apply still resets the filter to subject-only defaults since old topic IDs are stale.)
- New rule file `.cursor/rules/dashboard-selection-vs-filter.mdc` documents the full touchpoint matrix and category (1) / (2) / (3) semantics for future agents.
- Files: `templates/dashboard.html`, `.cursor/rules/dashboard-selection-vs-filter.mdc` (new), `.cursor/rules/dashboard-search.mdc`, `.cursor/rules/question-sets.mdc`.

### 🗄️ Database Changes

#### New `saved_question_sets` table
- Added by `db.create_all()` in `init_db.py` — additive only, safe to run on existing databases.
- Columns: `id`, `user_id` (FK), `name`, `subject` (FK to subjects.id), `question_ids` (JSON list of int IDs), `is_starred`, `is_shared`, `created_at`, `updated_at`.
- Backs the new Question Sets feature (see below).

### ✨ New Features

#### Question Sets — saved per-subject question collections + set-algebra builder
- New "**Set**" button on the dashboard (between Manage and Generate) opens a **Set Operations** modal that lets you combine the live **Selection** with one or more **saved sets** using **Union (∪)**, **Intersection (∩)**, and **Difference (\\)** in a freeform formula chip expression bar. The result can replace the current selection, be appended to it, or be saved as a new named set.
- Sets are **subject-scoped** — each saved set stores a snapshot of question DB IDs for one subject and is only listed when that subject is active in the dashboard. Subject permissions still apply when applying or loading a set.
- New page **My Stuff → Question Sets** lists, stars, renames, deletes, bulk-deletes, and (super-admin only) shares saved sets with all users that have access to that subject. Admin-shared sets show a green **Shared** badge.
- Apply directly via URL: `?question_set_id=<id>` on the dashboard restores the subject, replaces the selection with the set's questions, and auto-enables **Show Selected Only**.
- Save flow upserts by `(user, subject, name)` — saving with the same name in the same subject overwrites the contents.
- Backend: new `SavedQuestionSet` model and `/user/sets/*` JSON API mirroring the existing `SavedFilter` pattern (list, save, data, delete, bulk-delete, star, share, rename).
- Files: `app/models.py`, `app/user.py`, `init_db.py`, `templates/saved_question_sets.html` (new), `templates/dashboard.html`, `templates/base.html`.

#### DB Health anomaly view — jump to questions in Dashboard / Question Management
- The anomaly detail modal (Admin → Database Health → Anomaly Detection → **View**) now has two extra buttons in the footer:
  - **View in Dashboard** — opens the dashboard in a new tab, filtered to exactly the QIDs from that anomaly
  - **View in Question Management** — same, but lands on `/admin/questions`
- Backend: both `/dashboard/filter` and `/admin/questions/api/list` now accept a `qids` parameter (comma-separated list of QIDs). When set, it overrides all other filters and returns only the matching questions. Order is preserved on the admin page.
- The destination page shows a dismissible **"Filtering by N specific QIDs"** banner; clicking *Clear list filter* returns to normal browsing. Any sidebar filter change on the dashboard automatically clears the list filter too.
- For very large QID lists (above ~6,000 URL chars) the modal falls back to a one-shot `localStorage` token (`?qids_token=…`) so the request never hits browser URL limits.
- Files: `app/dashboard.py`, `app/admin.py`, `templates/admin_health.html`, `templates/dashboard.html`, `templates/admin_questions.html`.

### ✨ Enhanced Features

#### Generate page — quick preview button on each selected question
- Each row in the **Selected Questions** panel now has a small eye (👁) button.
- Clicking it opens a modal showing the question image without leaving the page.
- The image is fetched via the existing `/generate/api/viewer_asset/<id>/QUE?lang=` endpoint, using whichever **Preferred Language** is currently selected in the form (EN or CH), with the same `BI` → other fallback logic used during document generation.
- No new backend route needed; button tap is isolated from SortableJS drag via `touch-action: manipulation`.
- File: `templates/generate.html`.

#### Generator Sort Order — clearer labels and auto-switch on manual reorder
- Renamed the two Sort Order modes on the Generate page for clarity:
  - **"Custom Sort" → "Auto Sort"** (sorts automatically by tags like Year, Level, Topic, etc.)
  - **"Selection Order" → "Manual Sort"** (uses the order shown in the Selected Questions panel)
- Updated the toggle icons (`bi-magic`, `bi-hand-index`) and added explanatory tooltips to each button.
- Dragging a row in the **Selected Questions** panel now automatically switches the Sort Order to **Manual Sort** — previously the reorder would be silently discarded if Auto Sort was active.
- The "Drag to reorder" hint on the Selected Questions panel is now always visible (with a sub-hint that dragging switches modes), and the info banner under Manual Sort got tightened wording.
- Underlying form values (`custom` / `selection`) are unchanged, so existing saved generation presets continue to work.
- File: `templates/generate.html`.

### 🐛 Bug Fixes

#### Drag-to-reorder now works on mobile/touch and gives clear visual feedback
- The dashboard's **Sort By** list, and the generator's **Sort By** list + **Selected Questions** list, previously used the HTML5 native drag-and-drop API. That API does not fire on mobile/touch devices at all, and on desktop gave only a faint opacity change with no insertion indicator.
- Replaced with [SortableJS](https://github.com/SortableJS/Sortable) (loaded from CDN in `templates/base.html`), configured with `forceFallback: true` so mouse and touch use the same code path.
- New visual cues: a blue dashed ghost shows where the item will land, the dragged row gets a subtle shadow and tilt, and the touched item gets a blue focus ring.
- Dragging is limited to the grip handle (`.drag-handle`) so vertical scrolling still works elsewhere on the row.
- Touch handles are enlarged automatically on small screens / coarse pointers, and a 100 ms hold delay (`delayOnTouchOnly`) prevents drags from being triggered by an accidental scroll-start.
- Files: `templates/base.html`, `templates/dashboard.html`, `templates/generate.html`.

#### Regenerate — search profile always copied from original file
- Previously, if the user had visited the generator from the dashboard before clicking Regenerate, the stale session `generator_filter_data` would silently override the original file's saved search profile, causing the new file to record the wrong filter.
- Fixed: the original file's `filter_data` now always wins when regenerating, regardless of session state (`app/generator.py`).

### ✨ Enhanced Features

#### "Reuse Filter" also restores the saved question selection
- Clicking the funnel (🔽) button on the My Files page now pre-selects the question IDs that were used to generate that file, in addition to restoring the dashboard filter.
- The question IDs are fetched from the file's saved generation options and added to the active selection before the filter re-run. Any IDs that no longer appear in the restored filter results are pruned (e.g. questions deleted since the file was generated).

### ✨ New Features

#### Profile Sharing (Super Admin)
- Super admins can mark any Search Profile or Generation Preset as "Shared" — visible to every user
- New share toggle (green share-icon) on `/user/profiles` and `/user/gen-profiles` pages (super admin only)
- Other users see shared profiles/presets in their dropdowns under a "Shared by admins" optgroup, and in their list pages with a green **Shared** badge and the owner's username
- Non-owners cannot delete, star, or bulk-select shared profiles they don't own
- New `is_shared` column on `saved_filters` and `saved_generation_profiles`
- New `POST /user/profiles/<id>/share` and `POST /user/gen-profiles/<id>/share` endpoints (super-admin only)
- `/data` endpoints for both now accept any logged-in user when the profile is shared

#### Dashboard "Load Profile" Dropdown
- Added a "Load profile…" dropdown at the top of the Dashboard filters card, matching the Generate page pattern
- Three optgroups: ★ Starred, My profiles, Shared by admins
- Selecting a profile restores the filters and re-runs the search via HTMX (no page reload, no URL juggling)
- Newly-saved profiles appear in the dropdown immediately after the save modal closes

#### Saved Generation Presets
- New `/user/gen-profiles` page lists reusable generation-option presets per user
- "Save as preset" button + "Load preset…" dropdown on the Generate page ([templates/generate.html](templates/generate.html)) — pick a preset to instantly restore every option (answer mode, spacing, info/section/split fields, language, sort, etc.) without changing the current question selection
- Presets store options only — they are independent of the current question selection or filter
- Saving with an existing name overwrites that preset (upsert by name)
- Manage / bulk-delete / star presets at `/user/gen-profiles`

#### Starring (Favorites) for Saved Profiles
- Both Search Profiles and Generation Presets can be starred
- Starred items always sort to the top of list pages and the Generate-page dropdown (with an `★ Starred` optgroup)
- Click the star icon in either list to toggle

### 🗄️ Database Changes
- New table `saved_generation_profiles` (id, user_id, name, options_data JSON, is_starred, is_shared, created_at, updated_at)
- New columns on `saved_filters`: `is_starred` and `is_shared` (BOOLEAN NOT NULL DEFAULT FALSE), each with its own index
- Migration: run `python migrate_starring.py` once on existing deployments (idempotent — safe to re-run)

### 🔌 API Changes
- New `POST /user/profiles/<id>/star` — toggle star on a filter profile (owner / super admin)
- New `POST /user/profiles/<id>/share` — toggle share on a filter profile (super admin only)
- New `GET /user/gen-profiles` — page
- New `GET /user/gen-profiles/list` — JSON list (starred first, then by name; includes own + shared)
- New `POST /user/gen-profiles/save` — upsert preset by name
- New `GET /user/gen-profiles/<id>/data` — fetch preset for restore (now accepts shared)
- New `DELETE /user/gen-profiles/<id>` — delete one (owner / super admin)
- New `POST /user/gen-profiles/bulk-delete` — delete many
- New `POST /user/gen-profiles/<id>/star` — toggle star on a preset
- New `POST /user/gen-profiles/<id>/share` — toggle share on a preset (super admin only)
- Updated `GET /user/profiles/list` ordering: `is_starred DESC, name ASC`; now also returns shared profiles owned by other users plus `is_own` / `is_shared` flags
- Updated `GET /user/profiles/<id>/data`: any logged-in user can fetch a shared profile

## [2.3.0] - 2026-05-23

### ✨ New Features

#### Subject-Based Permission System (Replaces Legacy Role)
- Replaced the simple `is_admin` boolean with a full subject-level RBAC system
- New `UserSubjectPermission` model with roles: `viewer`, `user`, `admin`
- `is_super_admin` flag for god-mode access (all subjects, all operations)
- `viewer` role: read-only access, cannot generate documents
- `user` role: can browse + generate documents for that subject
- `admin` role: full subject admin (tagging, ingestion, export)
- Permission decorators: `@admin_required`, `@super_admin_required`, `@subject_admin_required`

#### Background Document Generation
- Generation now runs in a **background thread** instead of blocking the HTTP request
- New `GeneratedFile` DB model tracks generation status (`pending` → `generating` → `completed` / `failed`)
- Frontend polls `GET /generate/status/<id>` for live status updates
- Stale `pending`/`generating` records auto-reset to `failed` on app restart

#### My Files & Saved Filter Profiles (user_bp)
- New `/user/files` page: list, download, re-generate, and delete previously generated documents
- New `/user/profiles` page: save and restore named dashboard filter configurations
- Bulk delete for both files and profiles
- Super admin can view all users' files/profiles with `?show_all=1`

#### Viewer / Presentation Mode
- New `GET /generate/viewer` page for slide-style question review (no Word generation)
- Supports all question types with ANS/SOL toggle, language preference
- ANS ↔ SOL automatic fallback if one type is missing
- Asset API: `GET /generate/api/viewer_asset/<question_id>/<type>?lang=EN`

#### Answer & Comment Text Fields on Questions
- `answer` (TEXT): text-based answer, alternative to ANS image asset
- `comment` (TEXT): notes/commentary, displayed in dashboard card and viewer
- In generation: `answer_preference` controls whether text or image is used first for ANS

#### Chapter / Subchapter System
- New `Chapter` and `Subchapter` models parallel to Topic/Subtopic
- Questions can be linked to a `chapter_id` / `subchapter_id` (textbook organisation)
- Chapters visible on dashboard filter and in generated documents
- Admin CRUD at `/admin/chapters` (same reorder/hidden behaviour as topics)

#### Admin File Browser
- New `/admin/files` page (super admin only) for managing files directly within `SOURCE_PATH`
- Upload, download, rename, delete files; create directories
- Useful for correcting filenames without SSH/file system access

#### Database Health Dashboard
- New `/admin/health` page (super admin only) with fast DB-only statistics
- Reports: total counts, untagged questions, questions with no assets, duplicates, file-path mismatches
- `GET /admin/health/untracked` — files on disk that have no DB record
- `GET /admin/health/sync?dry_run=1` — SSE stream to find and optionally delete orphaned DB records

#### Export / Import (CSV Round-trip)
- `/admin/export-import` page with full CSV export and import for:
  - **Question Tags**: `qid, major_topic, major_subtopic, level, q_type, section, minor_topics, subtopics, chapter, subchapter`
  - **Topics/Subtopics**: per-subject topic tree
  - **Chapters/Subchapters**: per-subject chapter tree
- Imports are idempotent (safe to re-run)

### ✨ Enhanced Generation Options
- **Split to ZIP**: enable per-topic/chapter splits — one `.docx` per group, zipped together
- **Sequential numbering**: show `1. 2. 3.` prefixes with configurable start number
- **Page numbers**: footer page numbers on generated documents
- **Keep together**: Word `keep_with_next` on headings/info lines
- **Apply spacing to answers**: in `QUE_THEN_ANS` / `QUE_THEN_SOL` modes, apply full MC/CQ spacing to the answer section (default: minimal 1-line spacing)
- **Denote cross-topic**: adds `[Cross Topic: X, Y]` to info line if question has minor topics

### 🗄️ Database Changes
- New table: `user_subject_permissions (id, user_id, subject_id, role)`
- New table: `saved_filters (id, user_id, name, filter_data)`
- New table: `generated_files (id, user_id, display_name, filename, status, ...)`
- New table: `chapters (id, subject_id, name, sort_order)`
- New table: `subchapters (id, chapter_id, name, hidden, sort_order)`
- New columns on `questions`: `answer TEXT`, `comment TEXT`, `chapter_id INT`, `subchapter_id INT`
- New columns on `users`: `is_super_admin BOOL`

---

## [2.1.0] - 2026-01-09

### ✨ New Feature: Correct Percentage Tracking

Added a new `correct_percentage` field to track public exam performance statistics for questions.

#### Features
- **Database Field**: New `correct_percentage` column (integer 0-100, nullable) in questions table
- **Dashboard Display**: Shows correct percentage in question cards when available (e.g., "75% correct")
- **Sorting Support**: Sort by correct percentage in both dashboard and document generation
  - NULL values always sort last regardless of sort direction
- **Edit Question**: New input field in edit question modal (0-100 validation)
- **Batch Edit**: New toggle to batch update correct percentage for multiple questions
- **Document Generation**: 
  - New checkbox option to show correct percentage in generated documents
  - Format: `MATC_DSE_2024_P1_Q5 [75%]` (shown on same line as question ID)

#### Database Migration Required

Run the following SQL command to add the new column:

```sql
ALTER TABLE questions ADD COLUMN correct_percentage INT NULL;
```

#### API Changes
- `POST /admin/questions/<id>/update`: Now accepts `correct_percentage` parameter
- `POST /admin/questions/batch-update`: Now accepts `update_correct_pct` and `correct_percentage` parameters
- `POST /generate/create`: Now accepts `show_correct_pct` parameter

---

## [2.0.0] - 2026-01-09

### 🎉 Major Features Added

#### Multi-level Sorting
- **Dashboard Sorting**: Sort questions by multiple fields with custom priority
  - Example: Topic → Level → Year
  - Available fields: QID, Year, Level, Topic, Subtopic, Source, Section, Type, Created Time
  - Natural sorting for text fields (Q1, Q2, Q10 instead of Q1, Q10, Q2)
  - Sort configuration persists in session

#### Batch Operations
- **Batch Update**: Update metadata for multiple questions simultaneously
  - Selectively update: level, question type, section, or topic/subtopic relationships
  - Checkbox system to choose which fields to update
- **Batch Delete**: Permanently remove multiple questions at once
  - Confirmation dialog to prevent accidental deletion
  - Cascade deletion of associated assets
  - Returns count and list of deleted QIDs

#### Database Sync
- **Sync Command**: Remove orphaned database records when source files are deleted
  - Dry-run mode to preview what would be deleted
  - Detects orphaned assets (files that no longer exist)
  - Detects orphaned questions (questions with no assets)
  - CLI: `python cli.py sync` (preview) or `python cli.py sync --no-dry-run` (delete)

#### Smart Document Spacing
- **Separate MC and CQ Settings**: Different spacing for multiple choice vs conventional questions
  - Before question: Skip N lines OR start from new page
  - After question: Skip N lines OR start from new page
  - Intelligent page break handling (avoids duplicate breaks)
- **Flexible Sort Modes**: 
  - Preserve selection order
  - Custom multi-level sort

### ✨ Enhanced Features

#### Dashboard Enhancements
- **Direct QID Search**: Search by question ID with wildcard support
  - Example: `MATC_DSE_2024*` finds all 2024 DSE MATC questions
  - Supports SQL wildcards (`%` or `*`)
- **Topic Mode**: AND/OR logic for multi-topic filtering
  - AND mode: Questions must have ALL selected topics
  - OR mode: Questions can have ANY selected topics (default)
- **Preview Language Preference**: Prioritize English or Chinese in preview
  - Order: Preferred language → Bilingual → Other language
- **Configurable Page Size**: Choose 10, 20, 50, or 100 questions per page
- **Dynamic Year Loading**: Years are loaded based on selected subject and source
- **Level Filter**: Added "Not Assigned" option to filter untagged questions
- **Session Persistence**: Filter and sort settings persist across page loads

#### Question Model Enhancements
- **Major Subtopic**: Assign a primary subtopic to each question
  - Separate from many-to-many subtopics
  - Must belong to the major topic (validation enforced)
- **Description Field**: Optional text description for questions
- **Enhanced Validation**: Ensures subtopic belongs to major topic when set

#### Document Generation Enhancements
- **Additional Answer Mode**: "All Questions, Then All Solutions"
- **Language Preference**: Prefer English or Chinese assets in generated documents
  - Automatic fallback to Bilingual if preferred not available
  - Format preference: IMG before DOC
- **Optional QID Display**: Separate toggles for showing QIDs on questions vs answers
- **Graceful Error Handling**: Missing assets show italic placeholder instead of crashing

#### Ingestor Enhancements
- **Automatic Question Type Detection**: 
  - MATC DSE P1 → CQ
  - MATC DSE P2 → MC
  - MAT1/MAT2 DSE → CQ
  - Others → NULL (to be tagged manually)
- **Natural Sorting**: Files are processed in natural order (Q1, Q2, Q10)

### 🐛 Bug Fixes
- Fixed language preference ordering in preview and generation
- Fixed page break logic to avoid duplicate breaks
- Improved asset selection with format preference (IMG over DOC)
- Enhanced error handling for missing files
- Fixed subtopic loading when topics are deselected

### 🔧 Technical Improvements
- Refactored sorting logic into reusable `apply_multi_sort()` function
- Added `SORT_FIELDS` configuration for extensible sort options
- Improved database query optimization for large datasets
- Enhanced session management for filter persistence
- Better separation of concerns in document generation
- Added comprehensive inline documentation

### 📚 Documentation Updates
- Updated README.md with all v2.0 features
- Updated SETUP.md with new CLI commands and features
- Updated PROJECT_SUMMARY.md with technical details
- Added this CHANGELOG.md to track version history
- Updated API endpoint documentation

### 🗄️ Database Changes
- Added `major_subtopic_id` column to questions table
- Added `description` column (TEXT) to questions table
- No migration needed for existing data (new columns allow NULL)

### 🔐 Security
- No changes to authentication or authorization
- Batch delete requires admin privileges
- Sync command requires file system access

---

## [1.0.0] - 2025-12-XX

### Initial Release

#### Core Features
- **Flask Application**: Complete web application structure
- **Database Layer**: SQLAlchemy ORM with MariaDB
  - 7 models: User, Subject, Topic, Subtopic, Question, QuestionAsset
  - 2 association tables: question_minor_topics, question_subtopics
- **Authentication**: Flask-Login with role-based access (Admin/Regular)
- **File Ingestor**: 
  - Recursive directory scanning
  - Regex-based filename parsing
  - Support for PP and QB formats
  - Error logging
- **Dashboard**:
  - Advanced filtering (subject, source, year, topic, level, type)
  - Cross-topic search
  - Question preview
  - Answer/solution preview modals
  - Pagination (20 per page)
- **Document Generator**:
  - Word document creation (python-docx)
  - A4 page size
  - Multiple answer modes
  - Configurable spacing
  - Natural sorting
- **Admin Panel**:
  - Topic/subtopic management (CRUD)
  - Question tagging
  - User registration
- **Multi-language Support**: EN, CH, BI
- **UI**: Bootstrap 5 + HTMX for dynamic interactions

#### Initial Documentation
- README.md - Project overview
- SETUP.md - Installation guide
- TESTING.md - Testing procedures
- PROJECT_SUMMARY.md - Technical summary

---

## Version Comparison

| Feature | v1.0.0 | v2.0.0 |
|---------|--------|--------|
| Multi-level Sorting | ❌ | ✅ |
| Batch Operations | ❌ | ✅ |
| Database Sync | ❌ | ✅ |
| Smart MC/CQ Spacing | ❌ | ✅ |
| QID Search | ❌ | ✅ |
| Topic AND/OR Mode | ❌ | ✅ |
| Language Preference | ❌ | ✅ |
| Major Subtopic | ❌ | ✅ |
| Question Description | ❌ | ✅ |
| Configurable Page Size | 20 only | 10/20/50/100 |
| Answer Modes | 4 | 5 |
| Auto Question Type | ❌ | ✅ |
| Session Persistence | ❌ | ✅ |

---

## Upgrade Guide: v1.0 → v2.0

### Database Migration

No manual migration required! The new columns (`major_subtopic_id`, `description`) allow NULL values, so existing data will work without changes.

If you want to explicitly add the columns (optional):

```sql
-- Only if not auto-created by SQLAlchemy
ALTER TABLE questions ADD COLUMN major_subtopic_id INT NULL;
ALTER TABLE questions ADD COLUMN description TEXT NULL;
ALTER TABLE questions ADD FOREIGN KEY (major_subtopic_id) REFERENCES subtopics(id);
```

### Configuration Changes

No `.env` changes required. All new features work with existing configuration.

### Feature Migration

1. **Sorting**: Old sort parameters are automatically converted to new format
2. **Filtering**: All old filters continue to work, new ones are optional
3. **Generation**: Old answer modes work as before, new modes available

### Testing After Upgrade

1. Test existing workflows (filtering, generation, tagging)
2. Try new features (batch operations, multi-level sort, QID search)
3. Run `python cli.py sync` (dry-run) to check for orphaned records
4. Verify language preferences work correctly

### Rollback

If needed, you can roll back by:
1. Restore database backup
2. Checkout v1.0.0 code
3. Restart application

---

## Roadmap

### Future Enhancements (v2.1)
- Export/Import question sets
- Question statistics and analytics
- Advanced search with full-text search
- Question versioning/history
- Collaborative tagging with change tracking
- Mobile app integration

### Future Enhancements (v3.0)
- AI-powered question tagging suggestions
- Automatic difficulty level detection
- Question similarity detection
- LaTeX/MathML support for equations
- Multi-user collaborative editing
- Real-time notifications

---

## Support

For issues or questions about any version:
- Check documentation: README.md, SETUP.md, TESTING.md
- Review error logs: Terminal output, `ingest_errors.log`
- Check database: phpMyAdmin or MySQL client

## Contributors

Developed and maintained by the OQB Team.

---

**Last Updated**: January 9, 2026

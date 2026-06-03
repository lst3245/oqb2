# Online Question Bank System — User Manual

**Version 2.3** | Last Updated: May 2026

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Dashboard — Browsing & Filtering](#2-dashboard--browsing--filtering)
3. [Sorting Questions](#3-sorting-questions)
4. [Selecting Questions](#4-selecting-questions)
5. [Viewer / Presentation Mode](#5-viewer--presentation-mode)
6. [Generating Word Documents](#6-generating-word-documents)
7. [My Generated Files](#7-my-generated-files)
8. [Saved Search Profiles](#8-saved-search-profiles)
9. [Saved Generation Presets](#9-saved-generation-presets)
10. [Question Sets](#10-question-sets)
11. [Troubleshooting](#11-troubleshooting)
12. [Quick Reference](#12-quick-reference)

---

## 1. Getting Started

### Logging In

1. Go to the system URL (default: `http://localhost:5000`)
2. Enter your username and password
3. Click **Login**

### User Roles

Your role determines what you can do:

| Role | Browse | Generate Docs | Admin Panel |
|---|---|---|---|
| **View Only** | ✅ | ❌ | ❌ |
| **User** | ✅ | ✅ | ❌ |
| **Admin** | ✅ | ✅ | ✅ (their subjects) |
| **Super Admin** | ✅ | ✅ | ✅ (all subjects + user management) |

Roles are set **per subject** by your administrator. You may be a User for MATC but View Only for ICT, for example.

### Navigation

The top navigation bar has:
- **Dashboard** — browse and filter questions
- **Generate** — only available after selecting questions
- **Viewer** — presentation/review mode for selected questions
- **My Files** — download or manage your generated documents
- **Profiles** — saved search presets
- **Admin** — visible to admins only

---

## 2. Dashboard — Browsing & Filtering

The dashboard is your main workspace. The left sidebar holds all filters; the main area shows question cards.

### Step-by-Step: Finding Questions

**Step 1 — Select a Subject**  
Choose from the Subject dropdown. This loads the available topics, years, and sections for that subject.

**Step 2 — Choose Source Type**  
Use the radio buttons: **DSE**, **CE**, **AL**, or **QB**. DSE/CE/AL are past papers; QB is the custom question bank.

**Step 3 — Apply Filters**

| Filter | How It Works |
|---|---|
| **Years** | Multi-select dropdown (past papers only). Click "All" or "None" to toggle. |
| **Section** | Selects from available sections for the chosen subject and source (e.g. Section A, Section B). |
| **Topics** | Multi-select dropdown. Use **OR** mode (any selected topic) or **AND** mode (all selected topics). |
| **Include tagged in minor** | (Topics) Also includes questions where the topic is a *minor* topic, not just the primary one. |
| **Subtopics** | Appears after selecting topics. Also has OR/AND mode and an "Include tagged in minor" option. |
| **Show hidden** | (Subtopics) Toggle the eye icon to show hidden subtopics (e.g. textbook chapters). |
| **Chapters** | Filter by textbook chapter (separate from the topic system). |
| **Subchapters** | Appears after selecting chapters. |
| **Level** | 1, 2, 3, or "Not Assigned". Multi-select. |
| **Question Type** | MC (Multiple Choice), CQ (Conventional), or All. |
| **QID Search** | Search directly by question ID. See below. |
| **Version Priority** | A drag-to-reorder list (EN / CH / BI / ENO / CHO; ENO/CHO are official public-exam scans). The card preview shows the highest-priority version available for each question. Drag (or use the up/down arrows) to reorder; the top wins. Your order is remembered in the browser. |

**Step 4 — Click Search**  
The results panel updates (without a full page reload, powered by HTMX).

### QID Search

The QID search box at the top of the filter panel lets you find questions directly by their ID.

**Loose mode** (default — "Strict" toggle is OFF):  
Enter any words or numbers; they are matched in order anywhere in the QID.  
Example: `2025 Q1` finds `MATC_DSE_2025_P1_Q1`, `MATC_DSE_2025_P2_Q1`, etc.

**Strict mode** (toggle "Strict" ON):  
You control the exact pattern. Use `*` as a wildcard.  
Example: `MATC_DSE_2024*` finds all 2024 MATC DSE questions.  
Example: `*P2_Q5*` finds all Paper 2 Question 5 entries.

When QID search is active, other filters are ignored.

### Question Cards

Each card shows:
- QID, year, level, section, type badge
- Topic and subtopic
- Preview — image (click to expand full-size), inline Markdown (rendered with KaTeX math), or a Word-document first-page thumbnail (auto-generated; click to expand) with a download link for the original `.docx`. Format priority is **image > markdown > Word** by default; you can override it on the Generate page. Word thumbnails appear shortly after the first card load (a small "preview rendering…" placeholder is replaced live with the rendered PNG once the server has it).
- Answer/Solution buttons (if assets exist)
- **Explain** opens an AI tutor chat. Its modal includes small QUE / ANS / SOL preview buttons for any assets that exist, so you can inspect the source material without leaving the chat.
- Comment text (if set)
- Correct percentage (if recorded, e.g. "75% correct")
- An edit button (admins only)

### Page Size

Use the page size selector in the header bar to show 10, 20, 50, or 100 questions per page.

---

## 3. Sorting Questions

Click the **Sort** button (or the sort icon) in the question header bar to open the sort panel.

- **Add criteria** with the "+ Add" button
- **Drag rows** to change priority — top row has highest priority
- **Toggle direction** (↑ / ↓) per criterion
- Click **Apply Sort**

Available sort fields: QID, Year, Level, Topic, Subtopic, Source, Section, Question Type, Correct %, Chapter, Subchapter, Created Time.

Sort configuration is preserved in your session as you navigate pages.

---

## 4. Selecting Questions

### Selection Controls (in the question header bar)

| Button | Action |
|---|---|
| **Select Page** | Select all questions on the current page |
| **Select All (N)** | Select all questions matching current filters (across all pages) |
| **Deselect All** | Clear all selections |

Individual questions can also be selected/deselected with their checkbox.

### Selection survives filter changes

Your selection is **independent** of the sidebar filter. Searching, paging, sorting, applying a saved search profile — none of these touch your selection. Only these actions change the selection:

- Ticking / unticking individual checkboxes, or Select Page / Select All / Deselect All
- The header **Clear** button
- Set Operations modal Apply (Replace / Append)
- Applying a Question Set
- **Changing the subject** (selections are subject-tied, so this auto-clears)

### Show Selected Only — viewing your full selection

Open the **View** dropdown in the question-list header and toggle **Show Selected Only**. While on:

- The page paginates through your **complete** selection (every selected question, regardless of the sidebar filter).
- Sidebar filters are temporarily ignored — a banner reminds you of this and offers a one-click **Show All** to turn the toggle off.
- Unticking a card while in this mode hides it immediately (it's no longer in the selection).

### What To Do With Selected Questions

Once you have a selection, the action buttons become active:

- **Set** — open the Set Operations modal to save the selection as a named **Question Set**, or combine it with other saved sets via Union (∪), Intersection (∩), or Difference (\\). See [Question Sets](#10-question-sets).
- **Generate** — go to the document generation page
- **Viewer** — open presentation/review mode
- **Clear** — deselect all

The selection count is shown in the header.

---

## 5. Viewer / Presentation Mode

The viewer lets you review questions one by one in a large, clean layout — useful for classroom display or review sessions.

**Getting there**: Select questions on the dashboard, then click **Viewer**.

### Viewer Controls

- **← / →** arrow buttons (or keyboard arrow keys) to navigate between questions
- **QUE / ANS / SOL** toggle buttons to switch what is shown
- **Version Priority** drag-to-reorder list (EN / CH / BI / ENO / CHO) in the settings panel — the viewer shows the highest-priority version available
- Keyboard shortcut: `Q` = question, `A` = answer, `S` = solution

The viewer automatically falls back to ANS if SOL is not available and vice versa.

---

## 6. Generating Word Documents

### Getting There

1. Select questions on the dashboard
2. Click **Generate**
3. Configure options on the generation page
4. Click **Generate Document**
5. The document is created in the background — a progress indicator appears
6. When complete, a download link appears. You can also find the file in **My Files**

### Generation Options

#### Sort Order
- **Custom Sort** — drag-to-reorder multi-level sort (same as dashboard sort)
- **Selection Order** — output questions in the exact order you selected them

#### Answer Mode

| Mode | What Gets Generated |
|---|---|
| Questions Only | Just the question images |
| Question + Answer | Each question immediately followed by its answer |
| Question + Solution | Each question immediately followed by its solution |
| All Questions, Then All Answers | Full question section, then a separate "ANSWERS" section |
| All Questions, Then All Solutions | Full question section, then a separate "SOLUTIONS" section |

#### Answer Preference (for modes that include answers)
- **Image First** — uses the ANS image; falls back to the Answer Text field if no image
- **Text First** — uses the Answer Text field; falls back to the ANS image

#### Output Format
Generation always produces **Word (.docx)** — there is no longer a DOCX/PDF radio on this page.

If you also want a PDF version you have two equivalent options:
- **On the Generate page**: once the green "Document <name> is ready!" banner appears, click the red **Get PDF** button right next to **Download**. The button shows a spinner while Word builds the PDF on the server, downloads it, then flips into a solid red **Download PDF** link so any subsequent clicks are instant.
- **On the My Files page**: click the red PDF button beside the green Download button on the row. Same flow, same caching.

Either way, the first click builds the PDF using Microsoft Word on the server (equations, MathType objects, and embedded objects are preserved with full fidelity) and the result is cached on disk. Deleting the row also removes its cached PDF. For split jobs the PDF button produces a `.zip` of `.pdf` files (one per group) with the same structure as the source ZIP.

#### Format Priority
When a question has both an image (IMG), a Markdown (MD), and a Word document (DOC) for the same slot, this widget controls which format wins. Drag the chips to reorder. Default is `IMG → MD → DOC`.

- For Word-document-heavy questions where MathType / embedded objects matter, move **DOC** to the top.

#### Version Priority
A drag-to-reorder list of all versions: **EN** (English), **CH** (Chinese), **BI** (Bilingual), **ENO** (English Official), **CHO** (Chinese Official). For each question, generation uses the highest-priority version available (then the best format within it). Drag — or use the up/down arrows — to reorder; the top wins. ENO/CHO (low-quality official public-exam scans) sit last by default.

#### Spacing
Set separately for MC and CQ questions:
- **Before question**: skip N lines, or start from a new page
- **After question**: skip N lines, or start from a new page

In "All Questions Then..." modes, you can also choose whether to apply the same spacing to the answer section.

#### Display Options

| Option | Description |
|---|---|
| Show QID on questions | Prints the QID (e.g. `MATC_DSE_2024_P1_Q5`) above each question |
| Show QID on answers | Same, but for the answer section |
| Show correct % | Appends the correct percentage: `MATC_DSE_2024_P1_Q5 [75%]` |
| Sequential numbering | Adds `1.`, `2.`, `3.` before each question |
| Starting number | The first sequential number (default 1) |
| Page numbers | Adds a page number in the footer of every page |
| Keep together | Prevents the QID/info line from being split from its image by a page break |

#### Info Line (per question)
Adds a small italic line below the QID with selected metadata:
- Topic, Subtopic, Chapter, Subchapter (tick any combination)

#### Section Headings
Inserts a centred bold heading whenever the selected field changes as you go through the questions. Useful for grouping:
- "Number and Algebra | Chapter 3" as a heading before the first question in that group

#### Split to ZIP
Tick one or more fields under "Split documents by". Instead of one `.docx`, the output is a `.zip` containing one file per group.
- Example: tick "Topic" → one file per topic
- To get the PDF version of a split job, use the **PDF** button beside Download in My Files — it builds a `.zip` of `.pdf` files mirroring the source structure.

#### Denote Cross-Topic
Appends `[Cross Topic: X, Y]` to the info line when a question has minor topics in addition to its primary topic.

### File Name
Enter a name (without extension). A timestamp is appended automatically. Leave blank to use `questions_TIMESTAMP`.

---

## 7. My Generated Files

Navigate to **My Files** (top navigation).

Generated files are organised into **sections** (folders). Every account starts with a built-in **Latest** section where new files automatically land — you can drag files out into your own sections to keep things tidy.

Each section row shows its file count, sort, page size, and a status:
- **Pending / Generating** — being created in the background (only those sections auto-refresh, every 5 seconds)
- **Completed** — ready to download
- **Failed** — hover the status for an error message

### Sections (folders)
- **New section** button (top-right) — create a section with any name.
- **Drag the grip** at the left of a section header to reorder sections vertically (Latest is always first).
- **Three-dot menu** on the section header → **Rename**, **Share section…** (super admin), **Delete**. Deleting a section moves its files back to **Latest** — nothing is destroyed.
- **Collapse** with the chevron next to the name; the open/closed state is remembered server-side.
- **Sort dropdown** in the header switches the sort order: Created, Name, Completed, Question count, or **Manual order** (drag files into the order you want).
- **Per-page dropdown** chooses 5 / 10 / 25 / 50 / 100. Each section paginates independently.

### Drag-and-drop files
Every file row has a grip handle. Drag a file onto another section's body to move it; drag within the same section to set a manual order (the section's sort is then set to "Manual"). Shared-with-me rows are read-only and cannot be dragged.

### Inline rename
Double-click any section name (except Latest) or file name to rename in place. Press Enter to save or Esc to cancel.

### Actions Per File

| Button | Action |
|---|---|
| Download (green) | Download the source `.docx` (or `.zip`) file |
| PDF (red) | Download a PDF version of the file. First click for a row builds it on the server (Word converts your DOCX → PDF; for split ZIPs, every inner DOCX is converted and re-zipped); subsequent clicks on the same row are instant — the PDF is cached on disk and the button turns solid red. Hidden for legacy rows where the source is already a PDF |
| Re-generate | Go back to the generation page with all the same settings pre-filled |
| Re-filter | Restore the original dashboard filter that was used for this file |
| Share (super admin) | Pick users who should see this file in their **Shared with me** section |
| Delete | Remove the file, its cached PDF (if any), and the database record |

### Bulk operations
Select files (checkbox per row, **Ctrl/Cmd+A** to select all, **Delete** key to bulk-delete). A sticky bar appears at the top of the page:
- **Download ZIP** — packages every selected file into a single `.zip` and downloads it.
- **Move to** — drop the selection into any of your sections.
- **Share to users…** (super admin only) — share every selected file with the chosen users.
- **Delete** — remove the selection.

### Shared with me
If a super admin shares any file or section to you, an extra **Shared with me** section appears at the bottom of your page. The rows are read-only — you can download them, but cannot drag, rename, or delete. The sharer's username is shown next to each row.

### Search
The top-bar **Filter files by name** box filters across **every page of every section** (server-side) and highlights matches inline. Section file-count badges automatically reflect the post-filter total.

> Super admins can toggle **Show all users** to see and manage everyone's files.

---

## 8. Saved Search Profiles

Navigate to **Profiles** (top navigation).

### Saving a Profile

1. Set up your filters on the Dashboard
2. Click the **floppy disk** (💾) icon in the Filters panel header
3. Enter a profile name and click Save

### Loading a Profile (two ways)

**From the Dashboard (fastest):**
1. Click the small **folder** icon in the Filters panel header (next to the floppy save icon)
2. Pick a profile from the menu — starred profiles appear first, then your other profiles, then any profile **Shared by admins**
3. Your filters and question list update immediately

**From the Profiles page:**
1. Go to **My Stuff → Search Profiles**
2. Click **Apply** next to any profile — you are taken to the Dashboard with that filter applied

### Starring Profiles

Click the **star** icon next to any profile to mark it as a favourite. Starred profiles always appear at the top of the list and in the Dashboard dropdown. Click again to unstar.

### Shared Profiles (admin-curated)

Super admins can mark any profile as **Shared** so it appears in every user's dropdown and Profiles page (under the "Shared by admins" optgroup or with a green "Shared" badge). You can apply shared profiles freely, but only the owner or a super admin can edit, delete, star, or unshare them.

### Managing Profiles

- **Delete** individual profiles with the trash icon (own profiles or, for super admins, any profile)
- **Bulk Delete** using the checkboxes and "Delete Selected"
- **(Super admin)** click the green share icon to share / unshare a profile with all users

---

## 9. Saved Generation Presets

Generation presets are reusable templates of the **generation options** (answer mode, spacing, info/section/split fields, version priority, sort, etc.). They are independent of any specific question selection — load a preset to instantly restore all the options without changing which questions are selected.

Navigate to **My Stuff → Generation Presets** to view and manage your presets.

### Saving a Preset

1. Go to the **Generate** page (after selecting questions on the Dashboard)
2. Configure all the options the way you want them
3. Click **Save as preset** in the Presets bar at the top
4. Enter a name and click **Save**

> Saving with an existing name **overwrites** that preset, so you can iterate quickly.

### Loading a Preset

1. On the **Generate** page, open the **Load preset…** dropdown
2. Three sections appear: **★ Starred** (your own favourites), **My presets** (your other presets), and **Shared by admins** (presets shared by super admins, with the owner's name shown)
3. Select one — all your generation options are instantly restored
4. Your current question selection is **not** touched

### Starring Presets

On the **Generation Presets** page, click the star icon next to any preset to favourite it. Starred presets are pinned to the top of both the management page and the Generate-page dropdown.

### Shared Presets (admin-curated)

Super admins can mark any preset as **Shared** so it appears in every user's preset dropdown and Generation Presets page. Shared presets show a green "Shared" badge plus the owner's name. You can use shared presets freely, but only the owner or a super admin can edit, delete, star, or unshare them.

### Managing Presets

- **Delete** individual presets with the trash icon (own presets or, for super admins, any preset)
- **Bulk Delete** using the checkboxes and "Delete Selected"
- **(Super admin)** click the green share icon to share / unshare a preset with all users

---

## 10. Question Sets

A **Question Set** is a saved, named list of questions for a single subject. Sets let you build up a custom collection of questions across multiple searches and combine them with **set algebra** — Union (∪), Intersection (∩), Difference (\\) — to produce arbitrary groupings.

Typical workflow: search Topic A and select some questions, switch to Topic B and select more, save each round as its own set, then on a later session combine them with `SetA ∪ SetB ∩ ¬SomeOther` (well — minus `SomeOther`) to get exactly the mix you want.

### Opening the Set Operations modal

On the Dashboard, click the **Set** button (between **Manage** and **Generate**, with an intersect icon). The modal opens with three areas:

- **Available Sets (left)** — five kinds of source chips:
  - **Selection** (live, mirrors the dashboard's tick boxes)
  - **Filter Result** (every question matching your current sidebar filter, across all pages — useful for trimming the selection to whatever the filter currently shows)
  - **Result** (the previous Evaluate output; appears after the first Evaluate)
  - **Scratch Sets** (named snapshots saved in your browser only, see below)
  - Your saved Question Sets for the current subject, grouped Starred / My sets / Shared by admins
- **Expression bar (top right)** — a chip-by-chip view of your formula. Tap a set on the left to add it; tap operator buttons (∪, ∩, \\) and parentheses to build up the expression. Backspace removes the last chip; Clear empties the bar.
- **Result (bottom right)** — appears after you click **Evaluate**, with three actions: **Replace Selection**, **Append to Selection**, and **Save Result as set…**.

> Tip: `Selection ∩ Filter Result` followed by **Replace Selection** is the way to "trim my selection to the current filter" — useful after you change a filter and want to drop the questions that no longer match.

### Scratch sets (browser-only snapshots)

Click the small copy icon next to **Selection**, **Filter Result**, or **Result** to save a frozen snapshot under a name you choose. These **scratch sets**:

- Are saved in your browser (localStorage) and survive across reloads.
- Are tied to the current subject — they only show up when that subject is active.
- Can be **renamed** or **deleted** with the inline icons next to each row.
- Can be cleared in bulk for the current subject via **Clear all** at the top of the section.
- Are NOT synced to the server. To make a scratch shareable / persistent across browsers, append it as the only chip, click Evaluate, then **Save Result as set…** to promote it to a regular saved Question Set.

Workflow example:

1. Filter Topic A, select 5 questions, click **Set** → Duplicate Selection as `Topic A picks`.
2. Filter Topic B, select 8 questions, Duplicate Selection as `Topic B picks`.
3. Open Set Operations again, build `Topic A picks ∪ Topic B picks`, **Evaluate**, **Replace Selection**.
4. You now have a 13-question selection combining both topics, without ever committing them to a server-side set.

### Saving a set

You can save a set in three ways:

1. From the modal: **"Save current Selection as set…"** (no formula needed) — names and saves whatever is currently selected on the dashboard.
2. From the modal: **Evaluate** an expression first, then **"Save Result as set…"**.
3. From the manage page (`My Stuff → Question Sets`): rename existing sets.

> Saving with an existing name (for the same subject) **overwrites** that set's contents, so you can iterate quickly without producing duplicates.

### Applying a saved set

- From the manage page: click **Apply** next to a set. The dashboard opens with the matching subject preselected, the saved questions loaded as your selection, and "Show Selected Only" enabled so you immediately see them.
- From the Set Operations modal: tap a saved set chip → tap a `∪` / `∩` / `\\` operator → tap another set → **Evaluate** → **Replace Selection** or **Append to Selection**.

### Sharing sets

Super admins can mark any set as **Shared** so it appears for every user with access to that subject (under "Shared by admins" inside the modal, with a green badge on the manage page). You can use shared sets freely, but only the owner or a super admin can edit, delete, star, share, or rename them.

### Subject scoping

Sets are tied to a single subject. The modal only shows sets for the subject currently selected in the dashboard. Saving a set always uses the active dashboard subject. If you switch subjects, the available-sets list changes accordingly. (Cross-subject expressions are not supported.)

---

## 11. Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| "No Access" on Dashboard | No subject permissions assigned | Ask your administrator to grant you access |
| Images not loading | SOURCE_PATH misconfigured, or file missing | Check with your administrator |
| Generation "Failed" | File permission issue, missing asset file | Check the error message; contact administrator |
| QID search returns wrong results | Using wrong mode | Try toggling Strict mode on/off |
| Viewer shows wrong version | Version priority order | Reorder the Version Priority list in the viewer settings panel so your preferred version is on top |
| Profile doesn't restore correctly | Subject may have changed | Re-apply filters manually and save a new profile |

---

## 12. Quick Reference

### Keyboard Shortcuts (Viewer)
| Key | Action |
|---|---|
| `←` / `→` | Previous / Next question |
| `Q` | Show question |
| `A` | Show answer |
| `S` | Show solution |

### QID Format
```
MATC_DSE_2024_P1_Q5
 │     │    │   │  └─ Question number
 │     │    │   └──── Paper
 │     │    └──────── Year
 │     └───────────── Source (DSE/CE/AL)
 └─────────────────── Subject
```

### Sort Fields Available
QID · Year · Level · Topic · Subtopic · Source · Section · Question Type · Correct % · Chapter · Subchapter · Created Time

### Answer Modes Summary
| Code | Short name |
|---|---|
| `QUE_ONLY` | Questions Only |
| `QUE_ANS` | Q + Answer |
| `QUE_SOL` | Q + Solution |
| `QUE_THEN_ANS` | All Qs → All Answers |
| `QUE_THEN_SOL` | All Qs → All Solutions |

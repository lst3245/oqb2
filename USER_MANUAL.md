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
10. [Troubleshooting](#10-troubleshooting)
11. [Quick Reference](#11-quick-reference)

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
| **Preview Language** | Chooses whether to show English or Chinese images on the question cards. |

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
- Preview image (click to expand full-size)
- Answer/Solution buttons (if assets exist)
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

### What To Do With Selected Questions

Once you have a selection, the action buttons become active:

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
- **Language** toggle (EN / CH) to switch the displayed language
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

#### Language
Choose **English First** or **Chinese First**. Falls back to Bilingual, then the other language.

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
Tick one or more fields under "Split documents by". Instead of one `.docx`, the output is a `.zip` containing one `.docx` per group.
- Example: tick "Topic" → one file per topic

#### Denote Cross-Topic
Appends `[Cross Topic: X, Y]` to the info line when a question has minor topics in addition to its primary topic.

### File Name
Enter a name (without extension). A timestamp is appended automatically. Leave blank to use `questions_TIMESTAMP`.

---

## 7. My Generated Files

Navigate to **My Files** (top navigation).

The table shows all your generated documents with their status:
- **Pending / Generating** — being created in the background (auto-refreshes every 5 seconds)
- **Completed** — ready to download
- **Failed** — generation failed (hover the status for an error message)

### Actions Per File

| Button | Action |
|---|---|
| Download | Download the `.docx` or `.zip` file |
| Re-generate | Go back to the generation page with all the same settings pre-filled |
| Re-filter | Restore the original dashboard filter that was used for this file |
| Delete | Remove the file and its database record |

### Bulk Delete
Tick the checkbox column to select multiple files, then click **Delete Selected**.

> Super admins can toggle "Show all users" to see and manage everyone's files.

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

Generation presets are reusable templates of the **generation options** (answer mode, spacing, info/section/split fields, language, sort, etc.). They are independent of any specific question selection — load a preset to instantly restore all the options without changing which questions are selected.

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

## 10. Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| "No Access" on Dashboard | No subject permissions assigned | Ask your administrator to grant you access |
| Images not loading | SOURCE_PATH misconfigured, or file missing | Check with your administrator |
| Generation "Failed" | File permission issue, missing asset file | Check the error message; contact administrator |
| QID search returns wrong results | Using wrong mode | Try toggling Strict mode on/off |
| Viewer shows wrong language | Language toggle | Use the EN/CH toggle in the viewer toolbar |
| Profile doesn't restore correctly | Subject may have changed | Re-apply filters manually and save a new profile |

---

## 11. Quick Reference

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

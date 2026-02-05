# Online Question Bank System - User Manual

**Version 2.1.0** | Last Updated: February 5, 2026

A comprehensive guide for using the Online Question Bank System to browse, filter, select, and generate custom question papers.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Dashboard Guide](#dashboard-guide)
3. [Document Generation](#document-generation)
4. [Tips & Best Practices](#tips--best-practices)
5. [Troubleshooting](#troubleshooting)
6. [Quick Reference](#quick-reference)
7. [FAQ](#faq)

---

## Getting Started

### Accessing the System

1. Open your web browser and navigate to the system URL (default: `http://localhost:5000`)
2. You'll be redirected to the login page
3. Enter your credentials provided by your administrator
4. Click "Login"

**Default credentials** (change after first login):
- Username: `admin`
- Password: `admin123`

### Understanding User Roles

The system has two types of users:

- **Regular User**: Can browse, filter, and generate documents
- **Admin User**: Has full access including topic management, question tagging, and batch operations
- **Super Admin**: Has access to all subjects and can manage users

### First Time Login

After logging in for the first time:

1. Familiarize yourself with the navigation menu
2. Explore the dashboard to see available questions
3. If you're an admin, check the Admin panel to configure topics
4. Change your password (ask your administrator for instructions)

---

## Dashboard Guide

The dashboard is your main workspace for browsing and selecting questions.

### Interface Overview

The dashboard consists of:

- **Left Sidebar**: Filter panel with all search criteria
- **Main Area**: Question cards with previews
- **Top Bar**: Navigation, page size, and action buttons
- **Bottom**: Pagination controls

### Filter Panel

Use filters to narrow down questions based on your needs.

#### 1. Subject Selection

**Purpose**: Choose which subject to work with

**Options**: 
- MATC (Mathematics Compulsory)
- MAT1 (Mathematics Module 1)
- MAT2 (Mathematics Module 2)
- ICT (Information and Communication Technology)
- Additional subjects as configured by your administrator

**Tip**: Selecting a subject loads available topics and years for that subject.

#### 2. Source Type

**Options**:
- **DSE**: Hong Kong Diploma of Secondary Education Examination
- **CE**: Certificate of Education Examination
- **AL**: Advanced Level Examination
- **QB**: Question Bank (custom questions)
- **All**: Show all sources

**Note**: Year filter only appears when PP (Past Paper) sources are selected.

#### 3. Years (Past Papers Only)

- Multi-select dropdown showing available years
- Only displays years that have questions in the database
- Example: Select 2023, 2024, 2025 to see questions from those years
- Dynamic loading based on selected subject and source

#### 4. Topics

**Purpose**: Filter by subject topics (e.g., Calculus, Algebra, Statistics)

**Features**:
- **Multi-select**: Choose one or more topics
- **Cross-topic Toggle**: 
  - OFF: Only shows questions where selected topic is the MAJOR topic
  - ON: Includes questions where selected topic is a MINOR topic
- **Topic Mode** (AND/OR):
  - **OR** (default): Shows questions with ANY of the selected topics
  - **AND**: Shows only questions with ALL selected topics (useful for finding cross-topic questions)

**Example Use Cases**:
- Find all Calculus questions → Select "Calculus" topic
- Find questions combining Calculus AND Algebra → Select both, choose AND mode
- Find all Statistics-related questions (even as minor) → Select "Statistics", enable Cross-topic

#### 5. Subtopics

- **Auto-loads**: Based on selected topics
- **Multi-select**: Choose specific skills within topics
- Example: Under "Calculus", select "Integration" and "Differentiation"
- Dynamic: Updates when you change topic selection

#### 6. Levels (Difficulty)

**Options**:
- **1**: Easy questions (basic application)
- **2**: Medium difficulty (moderate complexity, multiple steps)
- **3**: Hard questions (advanced, synthesis of concepts)
- **Not Assigned**: Questions that haven't been tagged yet
- **All**: Show all levels

**Tip**: Select multiple levels to broaden your search.

#### 7. Question Type

**Options**:
- **MC**: Multiple Choice questions
- **CQ**: Conventional Questions (long-form)
- **All**: Both types

#### 8. Section

**Options**:
- **A**, **B**, **C**, etc.: Specific exam sections
- **All**: Questions from all sections

**Use Case**: Useful for organizing questions by paper structure.

#### 9. QID Search (Direct Search)

**Purpose**: Quick search by Question ID

**Usage**:
- Exact match: Type `MATC_DSE_2024_P1_Q5` to find that specific question
- Wildcard search: Type `MATC_DSE_2024*` to find all 2024 DSE MATC questions
- Partial match: `*Integration*` finds all questions with "Integration" in QID

**Tip**: Fastest way to find specific questions when you know the identifier.

### Display Options

#### Page Size

Choose how many questions to display per page:
- **10**: For slow connections or focused browsing
- **20**: Default, balanced
- **50**: For faster browsing of large sets
- **100**: Maximum, for comprehensive viewing

#### Preview Language

**Purpose**: Choose which language to prioritize in preview images

**Options**:
- **English**: Shows EN assets first → BI (Bilingual) → CH (Chinese)
- **Chinese**: Shows CH assets first → BI → EN

**Note**: System automatically selects the best available asset based on your preference.

### Multi-Level Sorting

Sort questions by multiple criteria with custom priority.

**How to Use**:
1. Click on any column header (e.g., "Topic")
2. Column is added as first sort level
3. Click another column (e.g., "Level")
4. Now sorted by Topic first, then Level within each topic
5. Click a header again to reverse direction (ascending ↔ descending)

**Available Sort Fields**:
- QID (Question ID)
- Year
- Level
- Topic
- Subtopic
- Source
- Section
- Type
- Created Time
- Correct % (percentage of students who answered correctly)

**Example Sorting**:
- **Topic → Level → Year**: Groups by topic, then difficulty, then chronological
- **Level → Year (desc)**: Hardest questions from recent years first
- **Correct % (asc)**: Questions students find most difficult first

**Tip**: Sort settings persist in your session until you close the browser.

### Question Cards

Each question is displayed as a card showing:

**Header Information**:
- **QID**: Unique identifier (e.g., MATC_DSE_2024_P1_Q5)
- **Source Badge**: DSE/CE/AL/QB with year and paper
- **Level Badge**: 1, 2, or 3 (color-coded)
- **Type Badge**: MC or CQ
- **Topic Badge**: Major topic name
- **Correct %**: If available, shows percentage of correct answers

**Preview**:
- Question image (based on language preference)
- Automatic fallback if preferred language not available

**Action Buttons**:
- **View Answer**: Opens modal with answer image (if available)
- **View Solution**: Opens modal with solution image (if available)
- **Edit Tags**: (Admin only) Edit question metadata

**Selection**:
- Checkbox at top of card
- Selection persists across page navigation

### Selecting Questions

**Individual Selection**:
- Click checkbox on each question card
- Checkmark indicates selected

**Bulk Selection**:
- Click "Select All on Page" button at top
- Selects all questions on current page

**Multi-Page Selection**:
1. Select questions on page 1
2. Navigate to page 2
3. Select more questions
4. All selections preserved
5. Selection counter shows total selected

**Deselection**:
- Click checkbox again to deselect
- "Deselect All" button to clear all selections

### Pagination

Located at bottom of question list:

- **Page Numbers**: Click to jump to specific page
- **Previous/Next**: Navigate sequentially
- **Info**: Shows current range and total (e.g., "Showing 21-40 of 156")

**Tip**: Use page size controls to adjust how many questions appear per page.

### Preview Modals

**Question Preview**:
- Click "View" on question card
- Opens full-size question image
- Close with X button or click outside modal

**Answer Preview**:
- Click "View Answer" button
- Shows answer image in modal
- If multiple languages available, shows based on preference

**Solution Preview**:
- Click "View Solution" button
- Shows detailed solution image
- Useful for reviewing step-by-step solutions

---

## Document Generation

After selecting questions, generate professional Word documents.

### Step 1: Select Questions

1. Use dashboard filters to find desired questions
2. Select questions using checkboxes
3. Verify selection count (e.g., "15 questions selected")
4. Click **"Generate Document"** button (top-right or bottom)

### Step 2: Configure Generation Options

You'll be taken to the generation options page with three sections:

#### A. Sort Mode

**Selection Order**:
- Questions appear in the exact order you selected them
- Useful when you want manual control over order
- Example: Q5, Q3, Q1, Q7 (as you clicked them)

**Custom Multi-Level Sort**:
- Configure automatic sorting with priority
- Click "Add Sort Level" to add more criteria
- Drag to reorder (first has highest priority)
- Example configuration:
  1. First sort by: Topic (ascending)
  2. Then sort by: Level (ascending)
  3. Then sort by: Year (descending)
- Result: Questions grouped by topic, then by difficulty within each topic, newest first within each level

**Common Sort Patterns**:
- **Practice Sets**: Topic → Subtopic → Level
- **Mock Exams**: Type (MC first) → Section → QID
- **Revision**: Level → Topic → Year (desc)
- **Chronological**: Year → Paper → QID

#### B. Answer Mode

Choose what to include in the document:

**1. Questions Only**
- Only question images included
- No answers or solutions
- **Use for**: Creating exam papers, worksheets for students

**2. Question + Answer**
- Each question followed immediately by its answer
- Paired together
- **Use for**: Practice sheets with immediate feedback, homework with answers

**3. Question + Solution**
- Each question followed immediately by its detailed solution
- Paired together
- **Use for**: Study materials, worked examples, tutoring resources

**4. All Questions, Then All Answers**
- Part 1: All questions in sequence
- Part 2: All answers in sequence (starts new page)
- Maintains same order in both sections
- **Use for**: Exam papers with separate answer key, timed practice tests

**5. All Questions, Then All Solutions**
- Part 1: All questions in sequence
- Part 2: All detailed solutions in sequence (starts new page)
- **Use for**: Exams with detailed answer booklets, comprehensive review materials

#### C. Spacing Settings

Configure spacing separately for **MC** (Multiple Choice) and **CQ** (Conventional Questions).

**Multiple Choice Settings**:

*Before Question*:
- **Skip N lines**: Add 0-10 blank lines before each question
- **Start from new page**: Each question begins on a new page

*After Question*:
- **Skip N lines**: Add 0-10 blank lines after each question
- **Start from new page**: Page break after each question

**Conventional Questions Settings**:
- Same options as MC
- Configure independently

**Smart Spacing**: 
- System avoids duplicate page breaks
- If previous question ended with page break, "Start from new page" won't add another

**Recommended Settings**:

| Use Case | MC Before | MC After | CQ Before | CQ After |
|----------|-----------|----------|-----------|----------|
| Compact Practice | 0 lines | 1 line | 0 lines | 2 lines |
| Standard Worksheet | 1 line | 1 line | 0 lines | 3 lines |
| Exam (students write) | 0 lines | 2-3 lines | New page | New page |
| Printed Answers | 0 lines | 0 lines | 0 lines | 1 line |
| Mock Exam | 0 lines | 1 line | New page | 0 lines |

#### D. Display Options

**Show Question ID**:
- ON: Each question starts with bold QID heading
- OFF: Questions appear without ID
- **Use for**: Reference copies, marking schemes, internal use

**Show Question ID on Answers/Solutions**:
- ON: Answers/solutions show QID heading
- OFF: Answers/solutions have no heading
- Independent of question ID setting
- **Use for**: Answer keys with clear matching

**Show Correct Percentage**:
- ON: Displays correct percentage alongside Question ID
- Format: `MATC_DSE_2024_P1_Q5 [75%]`
- Only shown when QID display is enabled and percentage available
- **Use for**: Difficulty analysis, strategic practice planning

**Language Preference**:
- **Prefer English**: Uses EN assets when available (fallback: BI → CH)
- **Prefer Chinese**: Uses CH assets when available (fallback: BI → EN)
- System automatically selects best available based on preference

**Asset Format Preference**:
- Images (IMG) preferred over Word documents (DOC) for quality
- Automatic format selection based on availability

### Step 3: Review and Generate

**Preview Panel** (Right Side):
- Shows all selected questions
- Displays count and QIDs
- Verify before generation

**Generate Button**:
1. Review all settings one final time
2. Click **"Generate & Download"** button
3. Wait for processing (may take a few seconds for many questions)
4. Document downloads automatically as `questions_YYYYMMDD_HHMMSS.docx`

**Generation Time**:
- 10 questions: ~2-3 seconds
- 50 questions: ~10-15 seconds
- 100+ questions: ~30+ seconds

### Step 4: Review Generated Document

1. Locate downloaded file in your Downloads folder
2. Open in Microsoft Word (or compatible software)
3. Check the document:
   - ✓ Questions in correct order
   - ✓ Images clear and properly sized
   - ✓ Spacing as configured
   - ✓ Answer mode correct
   - ✓ All selected questions present
   - ✓ QIDs shown/hidden as specified
   - ✓ Page breaks appropriate

**Document Specifications**:
- **Page Size**: A4 (29.7 cm × 21.0 cm)
- **Margins**: Narrow (1.27 cm all sides)
- **Image Width**: Maximum 6 inches (auto-scaled with aspect ratio)
- **Font**: Default (editable in Word after generation)
- **Format**: .docx (Microsoft Word 2007+)

### Step 5: Edit and Finalize (Optional)

After generation, you can edit in Word:

- Add headers/footers (name, date, class)
- Adjust fonts and styles
- Add instructions or notes
- Insert page numbers
- Modify spacing if needed
- Add school logo or branding
- Save as PDF for distribution

**Tip**: Save the original .docx file before making edits for future reference.

---

## Tips & Best Practices

### Efficient Filtering

**Start Broad, Then Narrow**:
1. Select subject first (loads topics and years)
2. Choose source type (DSE/CE/AL/QB)
3. Add year if applicable
4. Select topics
5. Add level if needed
6. Fine-tune with type and section

**Save Common Filter Combinations**:
- Write down filter settings you use frequently
- Filters persist in your browser session
- Example: "MATC DSE 2024 P1 Level 2 Calculus"

**Use QID Search for Known Questions**:
- Fastest method when you know the identifier
- Wildcards help find related questions
- Example: `*Calculus*` finds all with Calculus in metadata

### Document Generation Best Practices

**Test Small First**:
1. Generate 5-10 questions first
2. Review spacing, formatting, order
3. Adjust settings based on result
4. Then generate full set

**Spacing Guidelines by Use Case**:

*Handwritten Student Responses*:
- More after-spacing (3-5 lines for CQ)
- Consider page breaks for longer questions

*Printed Exam*:
- Start new page per CQ
- Minimal spacing for MC (1 line after)

*Practice Sheet*:
- Compact spacing (0-1 lines)
- Questions only or with solutions

*Answer Key*:
- Questions then answers mode
- Minimal spacing
- Show QIDs for matching

**Create Multiple Versions**:
- Generate same questions with different sort orders
- Create parallel versions of exams (reorder questions)
- Useful for preventing copying during tests

### Effective Question Tagging (Admin)

**Tag in Batches**:
1. Filter by year/paper/source
2. Select all similar questions
3. Use batch update for common attributes
4. Save time vs editing individually

**Major vs Minor Topics**:
- **Major Topic**: Primary focus of the question
- **Minor Topic**: Additional concepts required
- Example: Integration question requiring trigonometric identities
  - Major: Calculus
  - Minor: Trigonometry

**Level Guidelines**:
- **Level 1**: Basic application, single concept, straightforward
- **Level 2**: Moderate complexity, multiple steps, standard approach
- **Level 3**: Advanced, synthesis of multiple concepts, non-routine, tricky

**Use Descriptions**:
- Note special requirements or characteristics
- Mark calculator-allowed/not-allowed
- Flag common student errors
- Example: "Requires implicit differentiation" or "Graph sketching needed"

**Correct Percentage Usage**:
- Enter public exam statistics when available
- Helps identify challenging questions
- Useful for targeting practice
- Leave blank if data not available

### Search Strategies

**Finding Difficult Questions**:
- Level: 3
- Sort by: Correct % (ascending)
- Shows hardest questions students struggle with

**Topic Mastery Set**:
- Select one topic
- All levels (1, 2, 3)
- Sort by: Level → Year
- Progression from easy to hard

**Recent Past Papers**:
- Source: DSE
- Years: Last 3 years
- Sort by: Year (descending)
- Current exam style and trends

**Cross-Topic Integration**:
- Select 2-3 topics
- Topic Mode: AND
- Find questions requiring multiple concepts

### Performance Optimization

**Large Datasets (1000+ questions)**:
- Use specific filters (don't browse all)
- Set page size to 20-50 (not 100)
- Avoid "Select All" across many pages
- Filter by year/topic to narrow results

**Document Generation**:
- Generate in batches if over 100 questions
- Close other applications during large generation
- Check available disk space
- Consider splitting into multiple documents

**Database Maintenance**:
- Ask admin to run sync monthly
- Keep questions tagged for better filtering
- Report any missing or incorrect questions

### Organization Tips

**Consistent Naming**:
- Follow exact naming conventions for any custom additions
- Use full names, not abbreviations
- Be consistent across all subjects

**File Management**:
- Keep generated documents organized by date/purpose
- Name files descriptively (e.g., "MATC_2024_Mock_Exam_A.docx")
- Backup important generated documents
- Delete old drafts regularly

**Workflow**:
1. Morning: Identify question needs
2. Filter and select questions
3. Generate first draft
4. Review and adjust settings
5. Generate final version
6. Edit in Word (add headers, etc.)
7. Save as PDF for distribution

---

## Troubleshooting

### Common Issues and Solutions

#### "No questions found"

**Possible Causes**:
1. Filters too restrictive → Relax some filters (select more years, levels, topics)
2. Questions not tagged → Ask admin to tag questions, or select "Not Assigned" in levels
3. Wrong subject selected → Verify correct subject in filter
4. Database empty for that criteria → Check with admin about available questions

**Solution**: Start with minimal filters (just subject) and add filters gradually.

#### Images Not Displaying

**Possible Causes**:
1. SOURCE_PATH incorrect → Report to admin
2. Files moved/deleted → Report missing files
3. Browser cache → Hard refresh (Ctrl+F5)
4. File permissions → Report to admin
5. Slow connection → Wait and refresh

**Solution**: 
- Clear browser cache: Press Ctrl+Shift+Delete
- Hard refresh page: Ctrl+F5 (Windows) or Cmd+Shift+R (Mac)
- Try different browser
- Report to administrator if persistent

#### Generation Fails or Download Doesn't Start

**Possible Causes**:
1. No questions selected → Select at least one question
2. Output folder missing → Report to admin
3. Source files missing → Report missing files
4. Browser blocking download → Check browser settings
5. Insufficient permissions → Report to admin

**Solution**:
- Check selection count before generating
- Disable popup blocker for this site
- Check Downloads folder (may have downloaded)
- Try generating fewer questions
- Try different browser

#### Login Fails

**Possible Causes**:
1. Wrong password → Verify with admin or reset
2. Account not created → Contact admin
3. Database issue → Report to admin
4. Session expired → Close browser and try again

**Solution**:
- Double-check username and password (case-sensitive)
- Try clearing cookies
- Contact administrator for password reset

#### Slow Performance

**Possible Causes**:
1. Too many results → Use more specific filters
2. Page size too large → Reduce to 20-50
3. Many simultaneous users → Wait and try again
4. Server overload → Report to admin

**Solution**:
- Be more specific with filters (year, topic, level)
- Reduce page size
- Close unused browser tabs
- Try during off-peak hours

#### Preview Buttons Don't Work

**Possible Causes**:
1. Answer/Solution files don't exist → File not available
2. JavaScript disabled → Enable JavaScript
3. Browser compatibility → Use modern browser

**Solution**:
- If button is disabled/grayed, file doesn't exist
- Check browser allows JavaScript
- Update browser to latest version
- Try different browser (Chrome, Firefox, Edge)

#### Selection Not Saving Across Pages

**Possible Causes**:
1. Cookies disabled → Enable cookies
2. Browser in private mode → Use normal mode
3. Session expired → Reselect questions

**Solution**:
- Enable cookies in browser settings
- Use regular (non-incognito) browser window
- Complete selection and generation in one session

### Getting Help

**1. Check Error Messages**:
- Read any error messages displayed
- Note exact error text
- Take screenshot if needed

**2. Review Logs** (ask admin):
- Terminal output (where server runs)
- Browser console: Press F12, check Console tab
- Look for red error messages

**3. Check Documentation**:
- This user manual
- Quick Reference section below
- FAQ section

**4. Contact Administrator**:
- Provide specific error messages
- Describe steps to reproduce
- Mention browser and OS version
- Share screenshot if helpful

**5. Common Solutions**:
- Restart browser
- Clear browser cache
- Try different browser
- Check internet connection
- Logout and login again

### Browser Compatibility

**Recommended Browsers**:
- Google Chrome (latest)
- Mozilla Firefox (latest)
- Microsoft Edge (latest)
- Safari (latest, macOS/iOS)

**Minimum Requirements**:
- JavaScript enabled
- Cookies enabled
- Modern CSS support
- Stable internet connection

---

## Quick Reference

### File Naming Convention

#### Past Paper Format
```
MATC_DSE_2024_P1_Q5_EN_QUE.png
└─┬┘ └┬┘ └─┬┘ └┬ └┬ └┬ └─┬ └─┬┘
  │   │    │   │  │  │   │   └─ Extension (png, jpg, docx)
  │   │    │   │  │  │   └───── Type (QUE/ANS/SOL)
  │   │    │   │  │  └─────────Language (EN/CH/BI)
  │   │    │   │  └────────────Question (Q1, Q2, Q10)
  │   │    │   └───────────────Paper (P1, P2)
  │   │    └───────────────────Year (2024)
  │   └────────────────────────Source (DSE/CE/AL)
  └────────────────────────────Subject (MATC/MAT1/MAT2/ICT)
```

#### Question Bank Format
```
MATC_QB_MATHSMART2024_Q1_EN_QUE.png
└─┬┘ └┬ └──────┬─────┘ └┬ └┬ └─┬ └─┬┘
  │   │        │        │  │   │   └─ Extension
  │   │        │        │  │   └───── Type (QUE/ANS/SOL)
  │   │        │        │  └─────────Language (EN/CH/BI)
  │   │        │        └────────────Question number
  │   │        └─────────────────────Detail (no underscores!)
  │   └──────────────────────────────"QB" literal
  └──────────────────────────────────Subject
```

### Common Filter Combinations

**All 2024 DSE Questions**:
- Subject: MATC
- Source: DSE
- Years: ☑ 2024

**Difficult Calculus Questions**:
- Subject: MATC
- Topics: ☑ Calculus
- Level: ☑ 3

**Untagged Questions**:
- Level: ☑ Not Assigned

**Cross-Topic Questions (Calculus + Algebra)**:
- Topics: ☑ Calculus ☑ Algebra
- Topic Mode: AND
- Cross-topic: ON

**All Multiple Choice**:
- Q Type: MC

### Sort Examples

**Natural Order** (Default):
- QID ascending
- Result: Q1, Q2, Q3, ..., Q10, Q11

**By Difficulty Then Topic**:
1. Level (ascending)
2. Topic (ascending)

**Topic → Subtopic → Level**:
1. Topic (ascending)
2. Subtopic (ascending)
3. Level (ascending)

**Newest First**:
- Year (descending)

**Hardest First** (by student performance):
- Correct % (ascending)

### Keyboard Shortcuts

- **ESC**: Close modal
- **Enter**: Submit form/filter
- **Ctrl+F5**: Hard refresh (clear cache)
- **F12**: Open browser console (debugging)

### Status Indicators

**Question Card Badges**:
- 🟦 Blue: Level 1 (Easy)
- 🟧 Orange: Level 2 (Medium)
- 🟥 Red: Level 3 (Hard)
- ⚪ Gray: Not assigned
- 🔵 MC: Multiple Choice
- 🟢 CQ: Conventional Question

---

## FAQ

### General Questions

**Q: Can I use this system on my phone or tablet?**
A: Yes, the interface is responsive and works on mobile devices. However, document generation works best on desktop for easier editing.

**Q: Can I edit question images?**
A: No, images are read-only from the source. Contact your administrator if images need correction.

**Q: Can I export questions to PDF?**
A: Generate Word document first, then open in Word and "Save As PDF".

**Q: How many questions can I select at once?**
A: No hard limit, but for best performance, keep selections under 200 questions per document.

**Q: Can I share my filter settings with colleagues?**
A: Currently filters don't persist across sessions or users. Manually share your filter criteria, or request this feature from administrator.

### Document Generation

**Q: Why are some images low quality in the document?**
A: Source image quality affects output. Report low-quality sources to administrator for replacement.

**Q: Can I change fonts or styling in generated documents?**
A: Yes, open the .docx file in Word and edit formatting as needed. The system uses defaults for compatibility.

**Q: Why is my document generation taking so long?**
A: Large selections (100+ questions) take time. Be patient, or generate in smaller batches.

**Q: Can I include question metadata (level, topic) in the document?**
A: Currently only QID and correct % can be shown. Additional metadata display may be added in future versions.

**Q: The answer order doesn't match questions in "Questions then Answers" mode. Why?**
A: This is a bug. Report to administrator. System should maintain order.

### Filtering and Search

**Q: What does "Cross-topic" mean?**
A: When enabled, includes questions where your selected topic is tagged as a minor (secondary) topic, not just the major topic.

**Q: Why don't I see any subtopics?**
A: Subtopics only appear after selecting at least one topic. Select topics first.

**Q: Can I save custom filters?**
A: Not currently. Filters reset when you close browser. Note down your commonly used filters.

**Q: What's the difference between AND and OR topic modes?**
A: 
- OR (default): Question has ANY of the selected topics
- AND: Question has ALL of the selected topics (rare, used for truly cross-topic questions)

**Q: Why are there no questions for the year I selected?**
A: Either questions haven't been imported yet, or they haven't been tagged. Contact administrator.

### Account and Access

**Q: How do I change my password?**
A: Currently requires administrator assistance. Contact your admin for password reset.

**Q: Can I access this from home?**
A: Depends on deployment. If hosted on local network only, you need VPN or on-site access. Ask administrator.

**Q: Why can't I see the Admin menu?**
A: Only admin users have access. Regular users see only Dashboard and Generate sections.

**Q: Can I have read-only access to prevent accidental generation?**
A: Current system doesn't have read-only mode. Be careful with actions, or request feature from administrator.

### Troubleshooting

**Q: I selected questions but they disappeared when I changed pages.**
A: This shouldn't happen. Selections should persist. Clear browser cookies and try again, or report bug to administrator.

**Q: Preview images show wrong language.**
A: Check language preference setting in dashboard. System prioritizes based on this setting with automatic fallback.

**Q: Generated document is missing some questions.**
A: Check if source files exist for those questions. Missing source files are skipped. Report missing files to administrator.

**Q: Why do I get logged out frequently?**
A: Session timeout or security settings. Ask administrator to adjust session duration if needed.

---

## Appendix: Glossary

**Asset**: A physical file (image or document) containing question, answer, or solution

**BI (Bilingual)**: File containing both English and Chinese text

**CQ (Conventional Question)**: Long-form, written-response question

**Cross-topic**: Questions that involve multiple topics

**DSE**: Hong Kong Diploma of Secondary Education Examination

**Major Topic**: The primary topic a question focuses on

**MC (Multiple Choice)**: Question with multiple choice answers

**Minor Topic**: Secondary topic(s) a question also involves

**PP (Past Paper)**: Questions from actual past examinations

**QB (Question Bank)**: Custom or compiled questions not from past papers

**QID (Question ID)**: Unique identifier for each question

**Subtopic**: Specific skill or concept within a broader topic

---

## Support and Contact

For assistance:

1. **Technical Issues**: Contact your system administrator
2. **Missing Questions**: Report to administrator with QID
3. **Feature Requests**: Submit to administrator for consideration
4. **Training**: Ask administrator for training sessions

---

**End of User Manual**

*Last Updated: February 5, 2026*  
*Online Question Bank System v2.1.0*

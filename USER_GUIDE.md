# Online Question Bank System - User Guide

**Version 2.0.0** | Last Updated: January 9, 2026

A comprehensive guide for using the Online Question Bank System.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Dashboard Guide](#dashboard-guide)
3. [Document Generation](#document-generation)
4. [Admin Features](#admin-features)
5. [Tips & Best Practices](#tips--best-practices)
6. [Troubleshooting](#troubleshooting)

---

## Getting Started

### First Login

1. Open your browser and navigate to: `http://localhost:5000`
2. You'll be redirected to the login page
3. Enter credentials:
   - **Username**: `admin`
   - **Password**: `admin123`
4. Click "Login"

**⚠️ Important**: Change the default password after first login!

### User Roles

- **Admin**: Full access to all features, including topic management and batch operations
- **Regular User**: Can browse, filter, and generate documents (no admin panel access)

---

## Dashboard Guide

The dashboard is your main workspace for browsing and selecting questions.

### Filter Panel (Left Sidebar)

#### 1. Subject Selection
- **Purpose**: Choose which subject to work with
- **Options**: MATC, MAT1, MAT2, ICT
- **Effect**: Filters questions and loads available topics

#### 2. Source Type
- **DSE/CE/AL**: Past papers from different exam sources
- **QB**: Question Bank items
- **Tip**: Select "All" to see everything

#### 3. Years (Past Papers Only)
- **Multi-select**: Choose one or more years
- **Dynamic**: Only shows years with available questions
- **Example**: Select 2023, 2024, 2025 to see questions from those years

#### 4. Topics
- **Major Topics**: Loaded based on selected subject
- **Cross-topic Toggle**: 
  - OFF: Only shows questions where selected topic is the MAJOR topic
  - ON: Includes questions where selected topic is a MINOR topic
- **Topic Mode**:
  - **OR** (default): Questions matching ANY selected topic
  - **AND**: Questions matching ALL selected topics (useful for cross-topic questions)

**Example Use Cases**:
- Find all Calculus questions → Select "Calculus" topic
- Find questions that combine Calculus AND Algebra → Select both, choose AND mode
- Find all questions related to Statistics (even as minor topic) → Select "Statistics", enable Cross-topic

#### 5. Subtopics
- **Auto-loads**: Based on selected topics
- **Multi-select**: Choose specific skills within topics
- **Example**: Select "Integration" and "Differentiation" under Calculus

#### 6. Levels
- **1, 2, 3**: Difficulty levels (1 = easy, 3 = hard)
- **Not Assigned**: Questions that haven't been tagged yet
- **Multi-select**: Choose multiple levels

#### 7. Question Type
- **MC**: Multiple Choice
- **CQ**: Conventional Question
- **All**: Both types

#### 8. Section
- **A, B, etc.**: Exam paper sections
- **All**: Questions from all sections

#### 9. QID Search
- **Purpose**: Quick search by question ID
- **Exact**: Type `MATC_DSE_2024_P1_Q5` to find that specific question
- **Wildcard**: Type `MATC_DSE_2024*` to find all 2024 DSE MATC questions

### Display Options

#### Page Size
- Choose how many questions to display per page: 10, 20, 50, or 100
- Useful for large result sets or slower connections

#### Preview Language
- **English**: Shows English assets first (then Bilingual, then Chinese)
- **Chinese**: Shows Chinese assets first (then Bilingual, then English)
- Affects preview images in dashboard

#### Multi-level Sorting
- **Click column headers** to add sort levels
- **Example**: Click "Topic" → Click "Level" → Click "Year"
  - Result: Sorted by topic first, then level within each topic, then year within each level
- **Direction**: Click again to reverse order (ascending ↔ descending)
- **Clear**: Reload page to reset to default (QID ascending)

### Question Cards

Each question is displayed as a card showing:

- **QID**: Unique question identifier (e.g., MATC_DSE_2024_P1_Q5)
- **Badges**: 
  - Source/Year/Paper (e.g., DSE 2024 P1)
  - Level (1/2/3)
  - Type (MC/CQ)
  - Topic
- **Preview Image**: Question image (if available)
- **Action Buttons**:
  - **View Answer**: Opens modal with answer image
  - **View Solution**: Opens modal with solution image
- **Checkbox**: Select question for document generation

### Selecting Questions

1. **Individual**: Click checkbox on each question card
2. **All on Page**: Click "Select All on Page" button
3. **Across Pages**: 
   - Select questions on page 1
   - Go to page 2, select more questions
   - Selection persists across pages

### Pagination

- Shows at bottom of results
- Numbers indicate page count
- "Previous" and "Next" buttons for navigation
- Total count displayed (e.g., "Showing 21-40 of 156")

---

## Document Generation

After selecting questions, generate a Word document.

### Step 1: Selection

1. Filter and select desired questions on dashboard
2. Click **"Generate Document"** button (top-right)
3. You'll be taken to the generation options page

### Step 2: Configure Options

#### Sort Mode

**Selection Order**
- Questions appear in the order you selected them
- Useful when you want manual control over order

**Custom Multi-level Sort**
- Configure sorting criteria with priority
- Click "Add Sort Level" to add more criteria
- Example configuration:
  1. First by: Topic (ascending)
  2. Then by: Level (ascending)  
  3. Then by: Year (descending)
- Drag to reorder sort levels (if implemented)

#### Answer Mode

Choose what to include in the document:

1. **Questions Only**
   - Only question images
   - Useful for creating exam papers

2. **Question + Answer**
   - Question followed immediately by answer
   - Each pair grouped together
   - Useful for practice sets with immediate feedback

3. **Question + Solution**
   - Question followed immediately by solution
   - Each pair grouped together
   - Useful for learning materials

4. **All Questions, Then All Answers**
   - All questions in first section
   - All answers in second section (starts on new page)
   - Useful for exam papers with answer key

5. **All Questions, Then All Solutions**
   - All questions in first section
   - All solutions in second section (starts on new page)
   - Useful for detailed answer booklets

#### Spacing Settings

Configure spacing separately for MC and CQ questions:

**Multiple Choice (MC) Settings:**
- **Before Question**:
  - *Skip N lines*: Add blank lines before each question (0-10)
  - *Start from new page*: Each question starts on a new page
- **After Question**:
  - *Skip N lines*: Add blank lines after each question (0-10)
  - *Start from new page*: Each question ends with page break

**Conventional Questions (CQ) Settings:**
- Same options as MC
- Often want different spacing (e.g., MC tight, CQ spacious)

**Smart Spacing**: If previous question ended with page break, "Start from new page" won't add another break

**Recommended Settings:**
- **MC Practice**: Before: 0 lines, After: 1 line
- **MC Exam**: Before: Start new page, After: 0 lines
- **CQ Practice**: Before: 0 lines, After: 2 lines
- **CQ Exam**: Before: Start new page, After: Start new page

#### Display Options

**Show Question ID**
- ON: Each question starts with bold QID heading (e.g., "MATC_DSE_2024_P1_Q5")
- OFF: Questions appear without ID heading
- Useful for reference or marking schemes

**Show Question ID on Answers/Solutions**
- ON: Answers/solutions show QID heading
- OFF: Answers/solutions have no heading
- Independent of question ID setting

**Language Preference**
- **Prefer English**: Uses English assets when available (fallback: Bilingual → Chinese)
- **Prefer Chinese**: Uses Chinese assets when available (fallback: Bilingual → English)

### Step 3: Generate

1. Review your selections (shown on right panel)
2. Click **"Generate & Download"** button
3. Wait for document generation (may take a few seconds for many questions)
4. Document will download automatically as `questions_YYYYMMDD_HHMMSS.docx`

### Step 4: Review Document

1. Open the downloaded Word document
2. Check:
   - Questions are in correct order
   - Images are clear and properly sized
   - Spacing is as configured
   - Answer mode is correct
   - All selected questions are present

**Document Specifications:**
- Page size: A4 (29.7 cm × 21.0 cm)
- Margins: Narrow (1.27 cm all sides)
- Max image width: 6 inches (auto-scaled)
- Font: Default (can be changed in Word after generation)

---

## Admin Features

*Admin access required for these features*

### Topic Management

**Access**: Admin → Manage Topics

#### View Topics
- Topics are organized by subject
- Each topic shows its subtopics
- Expandable/collapsible view

#### Add Topic
1. Find the subject (e.g., MATC)
2. Click "Add Topic" button
3. Enter topic name (e.g., "Calculus")
4. Click "Save"
5. Topic appears in the list

#### Edit Topic
1. Click "Edit" button next to topic name
2. Change the name
3. Click "Save"

#### Delete Topic
1. Click "Delete" button next to topic name
2. Confirm deletion
3. **Warning**: All subtopics under this topic will also be deleted!
4. Questions with this as major topic will have it set to NULL

#### Add Subtopic
1. Find the topic (e.g., "Calculus")
2. Click "Add Subtopic" under that topic
3. Enter subtopic name (e.g., "Integration")
4. Click "Save"

#### Edit/Delete Subtopic
- Same process as topics
- Deleting subtopic removes it from all questions

### Question Tagging

**Access**: Admin → Tag Questions

#### Filter Questions to Tag
1. Use filters to find questions that need tagging
2. Look for "Not Assigned" levels to find untagged questions
3. Click on a question to see its current metadata

#### Edit Question Metadata
1. Click "Edit Tags" button on a question card
2. Modal opens with form

**Available Fields:**

- **Major Topic** (dropdown)
  - Choose the primary topic for this question
  - Required for filtering by topic
  - Only one major topic per question

- **Major Subtopic** (dropdown)
  - Choose the primary subtopic
  - Must belong to the major topic
  - Automatically updates when major topic changes

- **Minor Topics** (multi-select)
  - Add additional topics that this question relates to
  - Enables cross-topic search
  - Example: A question primarily about Integration (major) might also involve Trigonometry (minor)

- **Subtopics** (multi-select)
  - Add multiple subtopics that apply
  - Can be from different topics
  - Example: A question might involve both "Integration" and "Differentiation"

- **Level** (dropdown)
  - 1 = Easy, 2 = Medium, 3 = Hard
  - Leave blank if not yet assessed

- **Question Type** (dropdown)
  - MC = Multiple Choice
  - CQ = Conventional Question
  - Usually auto-detected during ingestion

- **Section** (text input)
  - Exam section (A, B, etc.)
  - Optional field

- **Description** (text area)
  - Optional notes about the question
  - E.g., "Uses implicit differentiation" or "Requires graph sketching"

3. Click "Save" to update
4. Success message appears

#### Preview Question Assets
- **Preview Question**: See the question image
- **Preview Answer**: See the answer image
- **Preview Solution**: See the solution image
- Opens in modal for quick reference

### Batch Operations

**Access**: Admin → Tag Questions (after selecting questions)

#### Batch Update

**Use Case**: You have 20 questions from 2024 DSE P2 that are all MC, Level 2, about Calculus

1. Filter to find these questions
2. Select all using checkboxes
3. Click "Batch Update" button
4. Modal opens with options:
   - Check "Update Level" → Set to 2
   - Check "Update Question Type" → Set to MC
   - Check "Update Topics" → Set Major Topic to Calculus
   - Leave "Update Section" unchecked (no change)
5. Click "Apply to Selected Questions"
6. Confirmation message shows count updated

**Benefits**:
- Save time vs editing one by one
- Ensure consistency across related questions
- Useful after bulk ingestion

#### Batch Delete

**Use Case**: You have duplicate or incorrect questions to remove

1. Filter to find the questions
2. Select questions to delete using checkboxes
3. Click "Batch Delete" button
4. **Warning dialog** appears with count
5. Type confirmation if prompted
6. Click "Delete"
7. Questions and their assets are permanently removed

**⚠️ Warning**: This action cannot be undone! Ensure:
- You're deleting the right questions
- You have backups if needed
- These are truly duplicates or errors

### User Management

**Access**: Admin → Register User

1. Click "Register" in navigation
2. Enter username and password
3. Check "Admin" if user should have admin privileges
4. Click "Create User"
5. New user can now log in

**Best Practices**:
- Use strong passwords
- Only grant admin to trusted users
- Regularly review active users

---

## Tips & Best Practices

### Efficient Filtering

1. **Start Broad, Then Narrow**
   - Select subject first
   - Add source type
   - Add more filters gradually

2. **Save Common Filters**
   - Write down filter combinations you use often
   - Filters persist in session until you close browser

3. **Use QID Search for Known Questions**
   - Fastest way to find specific questions
   - Use wildcards for related questions

### Document Generation

1. **Test with Small Sets First**
   - Generate 5-10 questions to test settings
   - Adjust spacing/options based on result
   - Then generate full set

2. **Spacing Recommendations**
   - **Handwritten responses**: More after-spacing
   - **Printed exam**: Start new page per CQ
   - **Practice sheet**: Minimal spacing
   - **Answer key**: Questions then answers mode

3. **Multiple Versions**
   - Generate same questions with different sort orders
   - Create parallel versions of exams

### Tagging Strategy

1. **Tag in Batches**
   - Filter by year/paper
   - Tag all similar questions together
   - Use batch update for common attributes

2. **Major vs Minor Topics**
   - Major: Primary focus of the question
   - Minor: Additional concepts needed
   - Example: Integration question requiring trig identities
     - Major: Calculus
     - Minor: Trigonometry

3. **Level Guidelines**
   - Level 1: Basic application, straightforward
   - Level 2: Moderate complexity, multiple steps
   - Level 3: Advanced, synthesis of concepts, tricky

4. **Use Descriptions**
   - Note special requirements
   - Mark calculator-allowed/not-allowed
   - Flag common student errors

### Topic Organization

1. **Consistent Naming**
   - Use full names, not abbreviations
   - Be consistent across subjects
   - Example: "Integration" not "Int."

2. **Logical Hierarchy**
   - Topics = broad areas
   - Subtopics = specific skills
   - Example: 
     - Topic: "Calculus"
     - Subtopics: "Differentiation", "Integration", "Applications"

3. **Don't Over-fragment**
   - Too many subtopics become hard to manage
   - Group related skills together

### File Management

1. **Consistent Naming**
   - Follow the exact naming convention
   - Use file name validator if available

2. **Backup Regularly**
   - Backup Source directory
   - Backup database
   - Keep backups before major ingestions

3. **Verify After Ingestion**
   - Check `ingest_errors.log`
   - Review imported questions
   - Fix any naming issues and re-ingest

4. **Clean Up Orphans**
   - Run `python cli.py sync` monthly
   - Remove files no longer needed
   - Keep database lean

---

## Troubleshooting

### Common Issues

#### "No questions found"

**Possible Causes:**
1. Filters too restrictive → Relax some filters
2. Questions not tagged → Check if questions need tagging first
3. Wrong subject selected → Verify subject choice
4. Database empty → Run ingestion first

#### Images not displaying

**Possible Causes:**
1. SOURCE_PATH incorrect → Check `.env` file
2. Files moved/deleted → Run database sync
3. Browser cache → Hard refresh (Ctrl+F5)
4. File permissions → Check file access rights

#### Generation fails

**Possible Causes:**
1. No questions selected → Select at least one question
2. Output folder missing → Check `output/` directory exists
3. Files missing → Some selected questions' files don't exist
4. Permissions → Check write permissions on output folder

#### Login fails

**Possible Causes:**
1. Wrong password → Verify credentials
2. Database not initialized → Run `python init_db.py`
3. Database connection issue → Check MariaDB is running

#### Ingestion skips files

**Check `ingest_errors.log`:**
- File naming doesn't match pattern
- Duplicate questions (already imported)
- File path issues

**Solutions:**
- Rename files to match convention exactly
- Check spelling and format (DSE not dse)
- Ensure file extensions are lowercase

### Getting Help

1. **Check Logs**
   - Terminal output when running application
   - `ingest_errors.log` for ingestion issues
   - Browser console (F12) for JavaScript errors

2. **Check Database**
   - Open phpMyAdmin
   - Verify tables exist and have data
   - Check `questions` and `question_assets` tables

3. **Check Documentation**
   - README.md - Overview and features
   - SETUP.md - Installation and configuration
   - TESTING.md - Testing procedures
   - CHANGELOG.md - Recent changes

4. **Common Solutions**
   - Restart Flask server
   - Clear browser cache
   - Re-run database initialization
   - Check `.env` configuration

### Performance Tips

1. **Large Datasets (1000+ questions)**
   - Use pagination (20-50 per page)
   - Be specific with filters
   - Avoid "Select All" across many pages

2. **Document Generation**
   - Generate in batches if > 100 questions
   - Use lower quality images if file size is issue
   - Close other applications while generating large docs

3. **Database Maintenance**
   - Run sync command monthly
   - Keep questions tagged for better filtering
   - Archive old question papers if not needed

---

## Keyboard Shortcuts

- **Filter Panel**: Click "Show Filters" to toggle (if implemented)
- **Pagination**: Use number keys 1-9 for pages (if implemented)
- **Modal Close**: Press ESC to close modals
- **Form Submit**: Press Enter in text fields

---

## FAQ

**Q: Can I edit question images?**
A: No, images are read-only. Edit source files, then re-ingest.

**Q: Can I export questions to PDF?**
A: Generate Word document first, then save as PDF from Word.

**Q: Can I share filters with other users?**
A: Not currently. Copy filter settings manually or request this feature.

**Q: What happens if I delete a topic?**
A: Questions with that major topic get set to NULL. Minor topic associations are removed. Questions are NOT deleted.

**Q: Can I undo batch delete?**
A: No. Always backup database before batch operations.

**Q: How do I change my password?**
A: Currently, admin must update in database directly. User profile page coming in v2.1.

**Q: Can I add my own subjects?**
A: Yes, add to `subjects` table in database:
```sql
INSERT INTO subjects (id, name) VALUES ('PHYS', 'Physics');
```

**Q: How many questions can the system handle?**
A: Tested with 10,000+ questions. Performance depends on server specs.

---

## Appendix: File Naming Reference

### Past Paper Format
```
SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE.EXT
```

**Example**: `MATC_DSE_2024_P1_Q5_EN_QUE.png`

- **SUBJ**: MATC, MAT1, MAT2, ICT
- **SOURCE**: DSE, CE, AL
- **YEAR**: 2024, 2025, etc.
- **PAPER**: P1, P2, etc.
- **QNO**: Q1, Q2, Q10, etc.
- **LANG**: EN, CH, BI
- **TYPE**: QUE, ANS, SOL
- **EXT**: png, jpg, docx, etc.

### Question Bank Format
```
SUBJ_QB_DETAIL_QNO_LANG_TYPE.EXT
```

**Example**: `MATC_QB_MATHSMART2024_Q1_EN_QUE.png`

- **SUBJ**: MATC, MAT1, MAT2, ICT
- **QB**: Literal "QB"
- **DETAIL**: Source name (no underscores)
- **QNO**: Q1, Q2, etc.
- **LANG**: EN, CH, BI
- **TYPE**: QUE, ANS, SOL
- **EXT**: png, jpg, docx, etc.

---

**End of User Guide**

For technical documentation, see:
- README.md - System overview
- SETUP.md - Installation
- PROJECT_SUMMARY.md - Technical details
- CHANGELOG.md - Version history

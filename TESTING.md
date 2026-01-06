# Testing Guide for Online Question Bank System

This guide provides instructions for testing the complete system with your real data.

## Pre-Testing Checklist

- [ ] Virtual environment activated
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] `.env` file configured with database credentials
- [ ] Database initialized (`python init_db.py`)
- [ ] MariaDB server running
- [ ] Source files organized in correct folder structure

## Test 1: Database Initialization

**Objective:** Verify database schema and default data

1. Run: `python init_db.py`
2. Check output for success messages
3. Login to phpMyAdmin or MySQL client
4. Verify tables exist:
   - users, subjects, topics, subtopics
   - questions, question_assets
   - question_minor_topics, question_subtopics
5. Verify default data:
   - 4 subjects (MATC, MAT1, MAT2, ICT)
   - Sample topics for MATC
   - Admin user exists

**Expected Result:** All tables created, default admin user and subjects present

## Test 2: File Ingestor

**Objective:** Import questions from file system

### Prepare Test Files

Create sample files following the naming convention:

**Past Paper Example:**
```
Source/MATC/PP/DSE/2024/P1/MATC_DSE_2024_P1_Q1_EN_QUE.png
Source/MATC/PP/DSE/2024/P1/MATC_DSE_2024_P1_Q1_EN_ANS.png
Source/MATC/PP/DSE/2024/P1/MATC_DSE_2024_P1_Q2_EN_QUE.png
```

**Question Bank Example:**
```
Source/MATC/QB/MathSmart2024/MATC_QB_MATHSMART2024_Q1_EN_QUE.png
Source/MATC/QB/MathSmart2024/MATC_QB_MATHSMART2024_Q1_EN_SOL.png
```

### Run Ingestor

```bash
python cli.py ingest
```

### Verify Results

1. Check terminal output for:
   - Number of files processed
   - Number of files skipped
2. Check `ingest_errors.log` (if exists) for any errors
3. In phpMyAdmin:
   - Check `questions` table - verify records created
   - Check `question_assets` table - verify file paths correct
4. Verify QIDs are constructed correctly (e.g., `MATC_DSE_2024_P1_Q1`)

### Test Cases

- [ ] PP files with QUE/ANS/SOL all create proper assets
- [ ] QB files are ingested correctly
- [ ] Files with Chinese (CH) or Bilingual (BI) are handled
- [ ] Files with different formats (PNG, JPG, DOCX) are accepted
- [ ] Re-running ingestor doesn't duplicate questions
- [ ] Invalid filenames are skipped and logged

**Expected Result:** Questions and assets imported, no crashes, errors logged properly

## Test 3: Authentication

**Objective:** Verify login system works

1. Start server: `python run.py`
2. Navigate to: `http://localhost:5000`
3. Should redirect to login page
4. Try invalid credentials - should show error
5. Login with: `admin` / `admin123`
6. Should redirect to dashboard
7. Check navigation shows Dashboard, Admin, Logout
8. Logout - should redirect back to login

**Expected Result:** Authentication works, unauthorized access blocked

## Test 4: Dashboard - Question Filtering

**Objective:** Test question filtering and display

### Test Filter Combinations

1. **Subject Filter:**
   - [ ] Select MATC - topics should load
   - [ ] Select different subject - topics should update

2. **Source Type Filter:**
   - [ ] Select DSE - questions filtered correctly
   - [ ] Select QB - questions filtered correctly
   - [ ] Years filter should only show for PP

3. **Year Filter:**
   - [ ] Select specific year - only that year shown
   - [ ] Select multiple years - combined results

4. **Topic Filter:**
   - [ ] Select single topic - questions with that major topic shown
   - [ ] Select multiple topics - combined results
   - [ ] Enable cross-topic - includes minor topics
   - [ ] Subtopics appear when topics selected

5. **Level Filter:**
   - [ ] Select level 1 only
   - [ ] Select multiple levels
   - [ ] Unselect all - no results (as expected)

6. **Question Type Filter:**
   - [ ] Filter by MC only
   - [ ] Filter by CQ only
   - [ ] Select All

### Test Question Display

1. Verify question cards show:
   - [ ] QID (e.g., MATC_DSE_2024_P1_Q1)
   - [ ] Source, year, paper, section
   - [ ] Level badge
   - [ ] Question type badge
   - [ ] Topic badge
   - [ ] Image preview loads
   - [ ] Checkbox for selection

2. Test Preview Buttons:
   - [ ] "View Answer" button appears if ANS exists
   - [ ] "View Solution" button appears if SOL exists
   - [ ] Clicking opens modal with correct image
   - [ ] Modal can be closed

3. Test Pagination:
   - [ ] If more than 20 questions, pagination appears
   - [ ] Clicking page numbers loads correct page
   - [ ] Page numbers update correctly

**Expected Result:** All filters work, questions display correctly, previews work

## Test 5: Question Selection and Generation

**Objective:** Test document generation

1. On dashboard, filter some questions
2. Check/uncheck some questions
3. Click "Generate Document"
4. Should navigate to generation options page
5. Verify selected questions listed on right side

### Test Generation Options

1. **Sort By:**
   - [ ] Question ID (natural order) - Q1, Q2, Q10 (not Q1, Q10, Q2)
   - [ ] Level - grouped by level
   - [ ] Year - grouped by year
   - [ ] Topic - grouped by topic

2. **Answer Modes:**
   - [ ] Questions Only - only QUE images
   - [ ] Question + Answer - QUE followed by ANS
   - [ ] Question + Solution - QUE followed by SOL
   - [ ] All Questions, Then All Answers - QUE section, then ANS section

3. **Formatting:**
   - [ ] Skip lines 0-5 - verify spacing
   - [ ] Show Question ID - QIDs appear as headings
   - [ ] New page per question - page breaks between questions

4. Click "Generate & Download"
5. Word file should download
6. Open in Microsoft Word

### Verify Generated Document

- [ ] Page size is A4 (not Letter)
- [ ] Margins are narrow (0.5 inches)
- [ ] Images are properly sized (max 6 inches wide)
- [ ] Questions appear in correct order
- [ ] Spacing is correct
- [ ] QIDs shown/hidden as selected
- [ ] Answer mode is correct
- [ ] Document opens without errors

**Expected Result:** Document generates and downloads, formatting correct

## Test 6: Admin - Topic Management

**Objective:** Test topic/subtopic CRUD operations

1. Navigate to Admin → Manage Topics
2. Should see subjects with existing topics

### Test Topic Operations

1. **Add Topic:**
   - [ ] Click "Add Topic" for MATC
   - [ ] Enter name "Test Topic"
   - [ ] Save - should appear in list

2. **Edit Topic:**
   - [ ] Click "Edit" on a topic
   - [ ] Change name
   - [ ] Save - name should update

3. **Add Subtopic:**
   - [ ] Click "Add Subtopic" for a topic
   - [ ] Enter subtopic name
   - [ ] Save - should appear under topic

4. **Edit Subtopic:**
   - [ ] Click edit on subtopic
   - [ ] Change name
   - [ ] Save - should update

5. **Delete Subtopic:**
   - [ ] Click delete on subtopic
   - [ ] Confirm - should be removed

6. **Delete Topic:**
   - [ ] Click delete on topic
   - [ ] Confirm - topic and subtopics removed

**Expected Result:** All CRUD operations work, changes persist

## Test 7: Admin - Question Tagging

**Objective:** Test question metadata editing

1. Navigate to Admin → Tag Questions
2. Questions should display with preview

### Test Tagging Operations

1. Click "Edit Tags" on a question
2. Modal should open with current values

3. **Test Updates:**
   - [ ] Change level - save - verify updated
   - [ ] Change question type - save - verify updated
   - [ ] Change section - save - verify updated
   - [ ] Select major topic - save - verify updated
   - [ ] Select minor topics - save - verify updated
   - [ ] Select subtopics - save - verify updated

4. **Test Preview Buttons:**
   - [ ] Preview Question - shows QUE image
   - [ ] Preview Answer - shows ANS or error if not exists
   - [ ] Preview Solution - shows SOL or error if not exists

5. **Verify Persistence:**
   - [ ] Go to dashboard
   - [ ] Filter by updated topic - question appears
   - [ ] Updated metadata displays correctly

**Expected Result:** Question metadata can be edited, changes persist

## Test 8: File Serving and Security

**Objective:** Verify file access control

1. While logged in, note URL of a question image
2. Image should load: `/dashboard/files/MATC/PP/DSE/...`
3. Logout
4. Try accessing that image URL directly
5. Should redirect to login (file access protected)

**Expected Result:** Files only accessible when logged in

## Test 9: Multi-Subject Testing

**Objective:** Test with multiple subjects

1. Create test files for MAT1, MAT2, ICT
2. Run ingestor
3. Test filtering by each subject
4. Test topic management for each subject
5. Test generating documents with mixed subjects

**Expected Result:** System handles multiple subjects correctly

## Test 10: Edge Cases and Error Handling

### File Naming Errors

1. Create file with wrong naming format
2. Run ingestor
3. Check `ingest_errors.log`
4. Should be logged, not crash system

### Missing Files

1. Delete a source file
2. Try to preview in dashboard
3. Should show error message, not crash

### Empty Filters

1. Set filters that match no questions
2. Should show "No questions found" message

### Large Datasets

1. Ingest 100+ questions
2. Test pagination works correctly
3. Test generation with many questions

**Expected Result:** System handles errors gracefully

## Performance Testing

### Ingestor Performance

- [ ] 100 files - time taken: ______
- [ ] 500 files - time taken: ______
- [ ] 1000 files - time taken: ______

### Dashboard Loading

- [ ] With 100 questions - load time: ______
- [ ] With 500 questions - load time: ______
- [ ] Pagination responsive

### Document Generation

- [ ] 10 questions - time: ______
- [ ] 50 questions - time: ______
- [ ] 100 questions - time: ______

## Troubleshooting Common Issues

### Images Not Showing
- Check SOURCE_PATH in .env
- Verify file paths in database match filesystem
- Check file permissions

### Ingestor Skipping Files
- Check file naming matches pattern exactly
- Check `ingest_errors.log` for details
- Verify folder structure matches expected format

### Database Errors
- Check MariaDB is running
- Verify credentials in .env
- Check database exists

### Generation Fails
- Check output directory exists and is writable
- Verify images exist at specified paths
- Check terminal for error messages

## Test Results Summary

Date: ______________
Tester: ______________

| Test | Status | Notes |
|------|--------|-------|
| 1. Database Init | ☐ Pass ☐ Fail | |
| 2. File Ingestor | ☐ Pass ☐ Fail | |
| 3. Authentication | ☐ Pass ☐ Fail | |
| 4. Dashboard Filtering | ☐ Pass ☐ Fail | |
| 5. Document Generation | ☐ Pass ☐ Fail | |
| 6. Topic Management | ☐ Pass ☐ Fail | |
| 7. Question Tagging | ☐ Pass ☐ Fail | |
| 8. File Security | ☐ Pass ☐ Fail | |
| 9. Multi-Subject | ☐ Pass ☐ Fail | |
| 10. Edge Cases | ☐ Pass ☐ Fail | |

## Next Steps

After successful testing:

1. Change default admin password
2. Create additional user accounts
3. Ingest all real question files
4. Tag questions with proper topics
5. Create comprehensive topic taxonomy
6. Set up regular backups
7. Consider production deployment

## Support

If tests fail, check:
1. Terminal output for errors
2. `ingest_errors.log`
3. Browser console (F12) for JavaScript errors
4. Database logs in phpMyAdmin

For persistent issues, review:
- SETUP.md for configuration
- Code comments for technical details
- Error messages for specific problems

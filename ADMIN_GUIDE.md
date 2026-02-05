# Online Question Bank System - Administrator Guide

**Version 2.1.0** | Last Updated: February 5, 2026

A comprehensive guide for system administrators covering installation, configuration, maintenance, and administrative features.

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Initial Setup](#initial-setup)
5. [File Management](#file-management)
6. [Admin Features](#admin-features)
7. [User Management](#user-management)
8. [Database Management](#database-management)
9. [Backup and Recovery](#backup-and-recovery)
10. [Maintenance](#maintenance)
11. [Troubleshooting](#troubleshooting)
12. [Production Deployment](#production-deployment)
13. [Testing Guide](#testing-guide)

---

## System Requirements

### Software Requirements

**Required**:
- **Python**: 3.8 or higher (3.10+ recommended)
- **Database**: MariaDB 10.x or MySQL 8.x
- **Operating System**: Windows, Linux, or macOS

**Recommended**:
- **Web Browser**: Chrome, Firefox, Edge, or Safari (latest versions)
- **Text Editor**: VS Code, Sublime, or similar (for configuration)
- **Database Manager**: phpMyAdmin, MySQL Workbench, or similar

### Hardware Requirements

**Minimum**:
- CPU: 2 cores
- RAM: 4 GB
- Storage: 10 GB (plus space for question files)
- Network: 100 Mbps

**Recommended** (for 100+ concurrent users):
- CPU: 4+ cores
- RAM: 8+ GB
- Storage: 50+ GB SSD
- Network: 1 Gbps

### Dependencies

All Python dependencies are listed in `requirements.txt`:
- Flask 3.0.0 (web framework)
- Flask-SQLAlchemy 3.1.1 (database ORM)
- Flask-Login 0.6.3 (authentication)
- PyMySQL 1.1.0 (database driver)
- python-docx 1.1.0 (Word document generation)
- docxcompose 1.4.0 (document composition)
- Pillow 10.1.0 (image processing)
- python-dotenv 1.0.0 (environment variables)
- natsort 8.4.0 (natural sorting)
- cryptography 41.0.7 (secure operations)
- click 8.1.7 (CLI commands)

---

## Installation

### Method 1: Quick Start (Windows)

1. **Extract Project Files**
   ```cmd
   cd C:\path\to\oqb2
   ```

2. **Run Quick Start Script**
   ```cmd
   quickstart.bat
   ```
   
   This script automatically:
   - Creates virtual environment
   - Installs all dependencies
   - Creates `.env` template

3. **Continue to Configuration** (see next section)

### Method 2: Manual Installation

#### Step 1: Navigate to Project Directory

```bash
cd /path/to/oqb2
```

#### Step 2: Create Virtual Environment

**Windows**:
```cmd
python -m venv venv
```

**Linux/macOS**:
```bash
python3 -m venv venv
```

#### Step 3: Activate Virtual Environment

**Windows (PowerShell)**:
```powershell
.\venv\Scripts\Activate.ps1
```

**Windows (CMD)**:
```cmd
venv\Scripts\activate.bat
```

**Linux/macOS**:
```bash
source venv/bin/activate
```

**Verification**: Your prompt should now show `(venv)` prefix.

#### Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

**Expected output**: All packages installed successfully without errors.

**Troubleshooting**:
- If `pip` command not found, try `python -m pip` instead
- If SSL errors occur, try: `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt`
- If specific package fails, install individually: `pip install flask`

---

## Configuration

### Step 1: Database Setup

#### Create Database

Using phpMyAdmin or MySQL client:

```sql
CREATE DATABASE oqb2 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

**Verification**:
```sql
SHOW DATABASES;
```
You should see `oqb2` in the list.

#### Create Database User (Optional but Recommended)

```sql
CREATE USER 'oqb_user'@'localhost' IDENTIFIED BY 'strong_password_here';
GRANT ALL PRIVILEGES ON oqb2.* TO 'oqb_user'@'localhost';
FLUSH PRIVILEGES;
```

### Step 2: Environment Configuration

#### Create .env File

Copy the template:
```bash
copy env_template.txt .env    # Windows
cp env_template.txt .env      # Linux/macOS
```

#### Edit .env File

Open `.env` in text editor and configure:

```env
# Database Configuration
DB_HOST=localhost              # Database server address
DB_USER=root                   # Database username (or 'oqb_user' if created)
DB_PASSWORD=your_password      # Database password
DB_NAME=oqb2                   # Database name

# Flask Configuration
SECRET_KEY=your-secret-key-change-this-in-production
FLASK_ENV=development          # 'development' or 'production'
FLASK_DEBUG=1                  # 1 for debug mode, 0 for production

# Application Settings
SOURCE_PATH=./Source           # Path to question files (absolute or relative)
OUTPUT_PATH=./output           # Path for generated documents
```

**Important Configuration Notes**:

1. **SECRET_KEY**: 
   - Generate random key: `python -c "import secrets; print(secrets.token_hex(32))"`
   - MUST be changed for production
   - Keep secret and secure

2. **SOURCE_PATH**:
   - Can be absolute: `D:/Question Files/Source`
   - Or relative to project: `./Source`
   - Must be readable by application

3. **OUTPUT_PATH**:
   - Must be writable by application
   - Generated documents stored here
   - Should be backed up regularly

4. **Database Password**:
   - Use strong password
   - Special characters may need escaping
   - Test connection before proceeding

### Step 3: Verify Configuration

Test database connection:

```bash
python debug_env.py
```

**Expected output**:
```
Environment Configuration:
DB_HOST: localhost
DB_USER: root
DB_NAME: oqb2
SOURCE_PATH: ./Source
OUTPUT_PATH: ./output
SECRET_KEY: [configured]

Database connection: SUCCESS
```

**If connection fails**:
- Verify MariaDB/MySQL is running
- Check credentials in `.env`
- Ensure database `oqb2` exists
- Check firewall settings

---

## Initial Setup

### Step 1: Initialize Database

Run the database initialization script:

```bash
python init_db.py
```

**What this does**:
1. Creates all database tables
2. Sets up relationships and constraints
3. Inserts default subjects (MATC, MAT1, MAT2, ICT)
4. Creates sample topics and subtopics for MATC
5. Creates default admin user (username: `admin`, password: `admin123`)

**Expected output**:
```
Dropping existing tables...
Creating all tables...
Tables created successfully!
Adding default subjects...
Adding sample topics and subtopics...
Creating default admin user...
Database initialized successfully!
Default admin credentials:
  Username: admin
  Password: admin123
IMPORTANT: Change the default password immediately!
```

**⚠️ Security Warning**: Change default admin password immediately after first login!

### Step 2: Create Directory Structure

Create required directories:

**Windows**:
```cmd
mkdir Source
mkdir Source\MATC Source\MAT1 Source\MAT2 Source\ICT
mkdir output
```

**Linux/macOS**:
```bash
mkdir -p Source/{MATC,MAT1,MAT2,ICT}/PP/{DSE,CE,AL}
mkdir -p Source/{MATC,MAT1,MAT2,ICT}/QB
mkdir -p output
```

**Standard Structure**:
```
Source/
├── MATC/
│   ├── PP/
│   │   ├── DSE/
│   │   │   ├── 2024/
│   │   │   │   ├── P1/
│   │   │   │   └── P2/
│   │   │   └── 2025/
│   │   ├── CE/
│   │   └── AL/
│   └── QB/
│       └── MathSmart2024/
├── MAT1/
│   └── (similar structure)
├── MAT2/
│   └── (similar structure)
└── ICT/
    └── (similar structure)
```

### Step 3: Test Application

Start the development server:

```bash
python run.py
```

**Expected output**:
```
 * Serving Flask app 'app'
 * Debug mode: on
WARNING: This is a development server. Do not use it in a production deployment.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
```

**Access the application**:
1. Open browser to `http://localhost:5000`
2. Should redirect to login page
3. Login with `admin` / `admin123`
4. Should reach dashboard

**If errors occur**:
- Check terminal output for error messages
- Verify database connection
- Ensure all dependencies installed
- Check firewall/antivirus settings

### Step 4: Initial Configuration

After successful login:

1. **Change Admin Password**
   - Currently requires database update (UI coming in future version)
   - Using MySQL client:
     ```sql
     UPDATE users SET password_hash = '<new_hash>' WHERE username = 'admin';
     ```
   - Generate hash in Python:
     ```python
     from werkzeug.security import generate_password_hash
     print(generate_password_hash('new_password'))
     ```

2. **Create Additional Users** (if needed)
   - Navigate to Admin → Register User
   - Create accounts for other administrators/users
   - Assign appropriate roles

3. **Configure Subjects** (if adding custom subjects)
   - Add to database manually:
     ```sql
     INSERT INTO subjects (id, name) VALUES ('PHYS', 'Physics');
     ```

4. **Set Up Topics**
   - Navigate to Admin → Manage Topics
   - Add topics and subtopics for each subject
   - Organize hierarchically

---

## File Management

### File Naming Convention

The system uses strict filename patterns to organize questions.

#### Past Paper (PP) Format

```
SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE.EXT
```

**Example**: `MATC_DSE_2024_P1_Q5_EN_QUE.png`

**Components**:
- **SUBJ**: Subject code (MATC, MAT1, MAT2, ICT, etc.)
- **SOURCE**: Exam source (DSE, CE, AL)
- **YEAR**: 4-digit year (2024, 2025, etc.)
- **PAPER**: Paper identifier (P1, P2, P3, etc.)
- **QNO**: Question number (Q1, Q2, Q10, etc.)
- **LANG**: Language code (EN, CH, BI)
- **TYPE**: Asset type (QUE, ANS, SOL)
- **EXT**: File extension (png, jpg, jpeg, docx, doc, etc.)

**More Examples**:
```
MATC_DSE_2024_P2_Q1_CH_QUE.png       # Chinese question
MATC_DSE_2024_P2_Q1_EN_ANS.png       # English answer
MATC_DSE_2024_P2_Q1_EN_SOL.docx      # English solution (Word)
MAT1_CE_2010_P1_Q3_BI_QUE.jpg        # Bilingual M1 question
```

#### Question Bank (QB) Format

```
SUBJ_QB_DETAIL_QNO_LANG_TYPE.EXT
```

**Example**: `MATC_QB_MATHSMART2024_Q1_EN_QUE.png`

**Components**:
- **SUBJ**: Subject code
- **QB**: Literal text "QB"
- **DETAIL**: Source/book name (NO UNDERSCORES!)
- **QNO**: Question number
- **LANG**: Language code (EN, CH, BI)
- **TYPE**: Asset type (QUE, ANS, SOL)
- **EXT**: File extension

**Important**: DETAIL field cannot contain underscores!

**Valid Examples**:
```
MATC_QB_MATHSMART2024_Q1_EN_QUE.png        ✓ Correct
MATC_QB_ARISTO2025_Q5_CH_ANS.png           ✓ Correct
ICT_QB_TEXTBOOK_Q10_BI_QUE.png             ✓ Correct
```

**Invalid Examples**:
```
MATC_QB_MATH_SMART_2024_Q1_EN_QUE.png      ✗ Underscores in DETAIL
MATC_QB_ARISTO_2025_Q5_CH_ANS.png          ✗ Underscore in DETAIL
```

### Supported File Formats

**Images**:
- PNG (.png) - Recommended
- JPEG (.jpg, .jpeg)
- GIF (.gif)

**Documents**:
- Microsoft Word (.docx) - Recommended
- Legacy Word (.doc)

**Language Codes**:
- **EN**: English only
- **CH**: Chinese only
- **BI**: Bilingual (both English and Chinese)

**Asset Types**:
- **QUE**: Question
- **ANS**: Answer (short answer/key)
- **SOL**: Solution (detailed solution with steps)

### File Organization

Place files in appropriate directories:

```
Source/
└── [SUBJECT]/
    ├── PP/
    │   └── [SOURCE]/
    │       └── [YEAR]/
    │           └── [PAPER]/
    │               └── files...
    └── QB/
        └── [DETAIL]/
            └── files...
```

**Example**:
```
Source/
└── MATC/
    ├── PP/
    │   ├── DSE/
    │   │   ├── 2024/
    │   │   │   ├── P1/
    │   │   │   │   ├── MATC_DSE_2024_P1_Q1_EN_QUE.png
    │   │   │   │   ├── MATC_DSE_2024_P1_Q1_EN_ANS.png
    │   │   │   │   ├── MATC_DSE_2024_P1_Q1_EN_SOL.png
    │   │   │   │   └── ...
    │   │   │   └── P2/
    │   │   └── 2025/
    │   ├── CE/
    │   └── AL/
    └── QB/
        ├── MathSmart2024/
        │   ├── MATC_QB_MATHSMART2024_Q1_EN_QUE.png
        │   └── ...
        └── Aristo2025/
            └── ...
```

### Ingesting Files

After organizing files, import them into the database.

#### Basic Ingestion

```bash
python cli.py ingest
```

Uses `SOURCE_PATH` from `.env` file.

#### Custom Path Ingestion

```bash
python cli.py ingest --source-path "D:/Custom/Path/Source"
```

#### What Happens During Ingestion

1. **Scanning**: Recursively scans all directories
2. **Parsing**: Extracts metadata from filenames using regex
3. **Validation**: Checks filename format validity
4. **Database Operations**:
   - Creates Question record (if new)
   - Updates Question record (if exists)
   - Creates QuestionAsset records
   - Links assets to questions
5. **Logging**: Errors logged to `ingest_errors.log`

#### Expected Output

```
Starting ingestion...
Scanning files in: ./Source
Processing: MATC/PP/DSE/2024/P1
  Found 45 files
  Processed: MATC_DSE_2024_P1_Q1_EN_QUE.png
  Processed: MATC_DSE_2024_P1_Q1_EN_ANS.png
  ...
Processing: MATC/PP/DSE/2024/P2
  Found 45 files
  ...
  
Summary:
  Total files scanned: 450
  Questions created: 150
  Questions updated: 0
  Assets created: 450
  Errors: 5 (see ingest_errors.log)
  
Ingestion complete!
```

#### Handling Errors

Check `ingest_errors.log` for details:

```
2026-02-05 10:30:15 - ERROR - Invalid filename: MATC_DSE_2024_P1_Q1.png (missing language and type)
2026-02-05 10:30:16 - ERROR - Invalid filename: MATC_QB_MATH_SMART_Q1_EN_QUE.png (underscores in detail)
```

**Common Errors**:
- Invalid filename format
- Missing components
- Incorrect folder structure
- File permissions issues

**Solutions**:
- Rename files to match pattern exactly
- Ensure all components present
- Check folder structure
- Verify file permissions

#### Re-ingesting Files

Safe to run multiple times:
- Existing questions are updated (by QID)
- New questions are created
- New assets are added
- Existing assets updated if paths changed

### Database Sync

Remove orphaned database records when files are deleted.

#### Preview Mode (Dry Run)

```bash
python cli.py sync
```

Shows what would be deleted without making changes.

**Output**:
```
Starting sync check (DRY RUN - no changes will be made)...
Checking assets against filesystem...
  Found 450 assets in database
  Checked 450 files...
  
Orphaned assets (files deleted):
  ID 123: MATC_DSE_2024_P1_Q5_EN_ANS.png (missing)
  ID 124: MATC_DSE_2024_P1_Q6_EN_ANS.png (missing)
  
Orphaned questions (no assets):
  ID 45: MATC_DSE_2024_P1_Q10 (no remaining assets)
  
Summary:
  Orphaned assets: 2
  Orphaned questions: 1
  
Dry run complete. Use --no-dry-run to actually delete orphaned records.
```

#### Delete Mode

```bash
python cli.py sync --no-dry-run
```

Actually removes orphaned records (with confirmation).

**With Force Flag** (skip confirmation):
```bash
python cli.py sync --no-dry-run --force
```

#### When to Run Sync

- After deleting or moving source files
- Monthly maintenance
- Before major imports
- When database seems inconsistent

**⚠️ Warning**: Deleted records cannot be recovered. Always run dry-run first!

---

## Admin Features

### Topic Management

Access: **Admin → Manage Topics**

#### View Topics

- Topics organized by subject
- Hierarchical display (topics contain subtopics)
- Sort order configurable

#### Add Topic

1. Find target subject (e.g., MATC)
2. Click **"Add Topic"** button
3. Enter topic name (e.g., "Calculus")
4. Optional: Set sort order
5. Click **"Save"**
6. Topic appears in list

**Database Operation**:
```sql
INSERT INTO topics (subject_id, name, sort_order) VALUES ('MATC', 'Calculus', 0);
```

#### Edit Topic

1. Click **"Edit"** next to topic name
2. Modify name or sort order
3. Click **"Save"**
4. Changes reflected immediately

**Note**: Editing topic name updates all questions using it.

#### Delete Topic

1. Click **"Delete"** next to topic
2. Confirm deletion
3. Topic and all subtopics removed

**⚠️ Important**:
- Cascade deletes subtopics
- Questions with this major topic → major_topic_id set to NULL
- Minor topic associations removed
- Questions themselves NOT deleted

**Database Operations**:
```sql
-- Delete subtopics
DELETE FROM subtopics WHERE topic_id = ?;
-- Remove from questions
UPDATE questions SET major_topic_id = NULL WHERE major_topic_id = ?;
DELETE FROM question_minor_topics WHERE topic_id = ?;
-- Delete topic
DELETE FROM topics WHERE id = ?;
```

#### Add Subtopic

1. Find parent topic
2. Click **"Add Subtopic"** under that topic
3. Enter subtopic name (e.g., "Integration")
4. Optional: Set sort order, hidden flag
5. Click **"Save"**

**Hidden Subtopics**: Used for organizational purposes (e.g., textbook chapters) but not displayed in main filtering.

#### Edit Subtopic

Similar to editing topics.

#### Delete Subtopic

1. Click **"Delete"** next to subtopic
2. Confirm deletion
3. Subtopic removed from all questions

**Database Operation**:
```sql
-- Remove from questions
UPDATE questions SET major_subtopic_id = NULL WHERE major_subtopic_id = ?;
DELETE FROM question_subtopics WHERE subtopic_id = ?;
-- Delete subtopic
DELETE FROM subtopics WHERE id = ?;
```

### Question Tagging

Access: **Admin → Tag Questions**

#### Browse Questions

1. Use filters to find questions to tag
2. Questions display with preview
3. Click **"Edit Tags"** on any question

#### Edit Question Metadata

Modal opens with form:

**Major Topic** (Dropdown):
- Select the primary topic
- Required for topic-based filtering
- Only one major topic per question

**Major Subtopic** (Dropdown):
- Select primary subtopic
- Must belong to major topic
- Auto-updates when major topic changes

**Minor Topics** (Multi-select):
- Add additional related topics
- Enables cross-topic search
- Multiple allowed

**Subtopics** (Multi-select):
- Add multiple relevant subtopics
- Can be from different topics
- Many-to-many relationship

**Level** (Dropdown):
- 1 (Easy), 2 (Medium), 3 (Hard)
- Leave blank if not yet assessed

**Question Type** (Dropdown):
- MC (Multiple Choice)
- CQ (Conventional Question)
- Usually auto-detected during ingestion

**Section** (Text):
- Exam section (A, B, C, etc.)
- Optional

**Correct Percentage** (Number):
- Public exam correct rate (0-100)
- Leave empty if unknown
- Displayed in dashboard and documents
- Useful for difficulty analysis

**Description** (Text Area):
- Optional notes about question
- E.g., "Requires implicit differentiation"
- Searchable

**Save**: Click to update question metadata.

#### Validation

- Major subtopic must belong to major topic
- Correct percentage must be 0-100
- All other fields optional

### Batch Operations

Access: **Admin → Tag Questions** (after selecting questions)

#### Batch Update

**Use Case**: Update metadata for multiple questions simultaneously.

**Process**:
1. Filter to find target questions
2. Select using checkboxes (or "Select All on Page")
3. Click **"Batch Update"** button
4. Modal opens with options

**Update Options** (checkboxes to enable):
- **Update Level**: Set level for all selected
- **Update Question Type**: Set type (MC/CQ) for all selected
- **Update Section**: Set section for all selected
- **Update Major Topic**: Set major topic for all selected
- **Update Major Subtopic**: Set major subtopic (must belong to major topic)
- **Update Minor Topics**: Set minor topics for all selected
- **Update Subtopics**: Set subtopics for all selected
- **Update Correct Percentage**: Set percentage for all selected

**Important**: Only checked fields will be updated. Unchecked fields remain unchanged.

**Example**:
```
Selected: 20 questions from MATC DSE 2024 P2
Update Level: ☑ (set to 2)
Update Type: ☑ (set to MC)
Update Major Topic: ☑ (set to Algebra)
Update Section: ☐ (leave unchanged)
```

**Confirmation**: Shows count of questions to be updated.

**Database Operation**: Updates all selected questions in single transaction.

#### Batch Delete

**Use Case**: Permanently remove multiple questions.

**⚠️ WARNING**: This action cannot be undone!

**Process**:
1. Filter to find questions to delete
2. **Carefully** select questions using checkboxes
3. Click **"Batch Delete"** button
4. **Warning dialog** appears with count
5. Review list of QIDs to be deleted
6. Type confirmation (if required)
7. Click **"Delete"** to confirm

**What Gets Deleted**:
- Question records
- All associated assets (cascade delete)
- Topic associations
- Subtopic associations

**What Doesn't Get Deleted**:
- Source files (remain on filesystem)

**Best Practices**:
- Double-check filter settings
- Review QID list carefully
- Run database backup before large deletions
- Consider running sync after to check consistency

**When to Use**:
- Remove duplicate questions
- Delete test/sample questions
- Clean up incorrect imports
- Remove questions no longer needed

### Chapter Management

Access: **Admin → Manage Chapters** (if enabled)

Chapters organize questions by textbook or curriculum structure.

**Features**:
- Create chapters per subject
- Assign questions to chapters
- Sort order for chapters
- Hierarchy independent of topics

**Use Case**: Organize questions by textbook sections (e.g., "Chapter 3: Quadratic Functions").

---

## User Management

### Creating Users

Access: **Admin → Register User**

**Process**:
1. Click **"Register"** in navigation
2. Enter username (alphanumeric, no spaces)
3. Enter password (strong password recommended)
4. Select role:
   - **Regular User**: Dashboard and generation access
   - **Admin**: Full admin panel access
   - **Super Admin**: Access to all subjects
5. (Optional) Select subject permissions
6. Click **"Create User"**

**Username Requirements**:
- Unique (not already in database)
- Alphanumeric characters
- No spaces
- Case-sensitive

**Password Requirements**:
- Minimum 8 characters (recommended)
- Mix of letters, numbers, symbols
- Not same as username

### Subject-Based Permissions

The system supports fine-grained subject permissions:

**Permission Levels**:
- **User**: Can view and generate questions for subject
- **Admin**: Can also tag and manage questions for subject
- **Super Admin**: Access to all subjects

**Setting Permissions**:
1. Create user
2. Assign subject permissions:
   - Select subjects user can access
   - Choose role per subject (user/admin)
3. Save

**Example**:
```
User: john_doe
- MATC: Admin (can tag questions)
- MAT1: User (can view only)
- MAT2: No access
- ICT: No access
```

### Managing Existing Users

**View Users**:
```sql
SELECT id, username, is_admin, is_super_admin, created_at FROM users;
```

**Update User Role**:
```sql
UPDATE users SET is_admin = 1 WHERE username = 'john_doe';
```

**Reset Password**:
1. Generate hash:
   ```python
   from werkzeug.security import generate_password_hash
   print(generate_password_hash('new_password'))
   ```
2. Update database:
   ```sql
   UPDATE users SET password_hash = '<generated_hash>' WHERE username = 'john_doe';
   ```

**Delete User**:
```sql
DELETE FROM users WHERE username = 'john_doe';
```

**Best Practices**:
- Regularly review user list
- Remove inactive accounts
- Use strong passwords
- Grant minimum necessary privileges
- Document who has super admin access

---

## Database Management

### Database Schema

#### Core Tables

**users**:
- `id` (INT, PK)
- `username` (VARCHAR, UNIQUE)
- `password_hash` (VARCHAR)
- `is_admin` (BOOLEAN) - legacy
- `is_super_admin` (BOOLEAN)
- `created_at` (DATETIME)

**user_subject_permissions**:
- `id` (INT, PK)
- `user_id` (INT, FK → users.id)
- `subject_id` (VARCHAR, FK → subjects.id)
- `role` (VARCHAR) - 'user' or 'admin'

**subjects**:
- `id` (VARCHAR(10), PK) - e.g., 'MATC'
- `name` (VARCHAR) - e.g., 'Mathematics Compulsory'

**topics**:
- `id` (INT, PK)
- `subject_id` (VARCHAR, FK → subjects.id)
- `name` (VARCHAR)
- `sort_order` (INT)

**subtopics**:
- `id` (INT, PK)
- `topic_id` (INT, FK → topics.id)
- `name` (VARCHAR)
- `hidden` (BOOLEAN)
- `sort_order` (INT)

**chapters**:
- `id` (INT, PK)
- `subject_id` (VARCHAR, FK → subjects.id)
- `name` (VARCHAR)
- `sort_order` (INT)

**questions**:
- `id` (INT, PK)
- `qid` (VARCHAR, UNIQUE) - e.g., 'MATC_DSE_2024_P1_Q5'
- `subject` (VARCHAR, FK → subjects.id)
- `source` (VARCHAR) - DSE/CE/AL/QB
- `year` (INT) - for PP
- `paper` (VARCHAR) - for PP
- `section` (VARCHAR)
- `qno` (VARCHAR) - Q1, Q2, etc.
- `q_type` (VARCHAR) - MC/CQ
- `level` (INT) - 1/2/3
- `major_topic_id` (INT, FK → topics.id)
- `major_subtopic_id` (INT, FK → subtopics.id)
- `chapter_id` (INT, FK → chapters.id)
- `description` (TEXT)
- `correct_percentage` (INT) - 0-100
- `created_at` (DATETIME)

**question_assets**:
- `id` (INT, PK)
- `question_id` (INT, FK → questions.id)
- `asset_type` (VARCHAR) - QUE/ANS/SOL
- `file_format` (VARCHAR) - IMG/DOC
- `language` (VARCHAR) - EN/CH/BI
- `file_path` (VARCHAR) - relative to SOURCE_PATH

**question_minor_topics** (M2M):
- `question_id` (INT, FK)
- `topic_id` (INT, FK)

**question_subtopics** (M2M):
- `question_id` (INT, FK)
- `subtopic_id` (INT, FK)

### Database Maintenance

#### Backup Database

**Using mysqldump**:
```bash
mysqldump -u root -p oqb2 > backup_oqb2_$(date +%Y%m%d).sql
```

**Automated Backup Script** (save as `backup.sh`):
```bash
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/path/to/backups"
mysqldump -u root -p'your_password' oqb2 > "$BACKUP_DIR/oqb2_$DATE.sql"
gzip "$BACKUP_DIR/oqb2_$DATE.sql"
# Keep last 30 days
find "$BACKUP_DIR" -name "oqb2_*.sql.gz" -mtime +30 -delete
```

**Schedule with cron** (Linux):
```cron
0 2 * * * /path/to/backup.sh
```

**Windows Task Scheduler**: Create batch file and schedule.

#### Restore Database

```bash
mysql -u root -p oqb2 < backup_oqb2_20260205.sql
```

#### Check Database Integrity

```sql
-- Check for orphaned assets (no question)
SELECT * FROM question_assets WHERE question_id NOT IN (SELECT id FROM questions);

-- Check for questions without assets
SELECT * FROM questions WHERE id NOT IN (SELECT DISTINCT question_id FROM question_assets);

-- Check for invalid major_subtopic_id (doesn't belong to major_topic)
SELECT q.id, q.qid, q.major_topic_id, q.major_subtopic_id, s.topic_id
FROM questions q
JOIN subtopics s ON q.major_subtopic_id = s.id
WHERE q.major_topic_id IS NOT NULL AND q.major_subtopic_id IS NOT NULL
  AND s.topic_id != q.major_topic_id;
```

#### Optimize Database

```sql
-- Analyze tables
ANALYZE TABLE questions, question_assets, topics, subtopics;

-- Optimize tables
OPTIMIZE TABLE questions, question_assets;

-- Check table status
SHOW TABLE STATUS FROM oqb2;
```

#### Database Statistics

```sql
-- Count questions by subject
SELECT subject, COUNT(*) FROM questions GROUP BY subject;

-- Count questions by source
SELECT source, COUNT(*) FROM questions GROUP BY source;

-- Count questions by level
SELECT level, COUNT(*) FROM questions GROUP BY level;

-- Count assets by type
SELECT asset_type, COUNT(*) FROM question_assets GROUP BY asset_type;

-- Average questions per topic
SELECT t.name, COUNT(q.id) as count
FROM topics t
LEFT JOIN questions q ON t.id = q.major_topic_id
GROUP BY t.id, t.name
ORDER BY count DESC;
```

### Database Migration

#### Upgrading from v2.0 to v2.1

Add correct_percentage column:

```sql
ALTER TABLE questions ADD COLUMN correct_percentage INT NULL;
```

Verify:
```sql
DESCRIBE questions;
```

Should see `correct_percentage` column.

#### Future Upgrades

Always:
1. Backup database before migration
2. Test on development copy first
3. Document changes
4. Verify data integrity after migration

---

## Backup and Recovery

### What to Backup

**Critical**:
1. Database (`oqb2`)
2. Source files (`Source/` directory)
3. Configuration (`.env` file)
4. Generated documents (`output/` directory) - optional but recommended

**Not Critical**:
- Python packages (in `venv/`) - can be reinstalled
- Application code (should be in version control)
- Log files - can be regenerated

### Backup Strategies

#### Strategy 1: Full Backup

**Frequency**: Weekly

**Process**:
1. Stop application
2. Backup database
3. Copy `Source/` directory
4. Copy `.env` file
5. Restart application

**Script Example**:
```bash
#!/bin/bash
DATE=$(date +%Y%m%d)
BACKUP_ROOT="/backup/oqb2"
mkdir -p "$BACKUP_ROOT/$DATE"

# Database
mysqldump -u root -p'password' oqb2 | gzip > "$BACKUP_ROOT/$DATE/database.sql.gz"

# Source files
tar -czf "$BACKUP_ROOT/$DATE/source.tar.gz" ./Source/

# Config
cp .env "$BACKUP_ROOT/$DATE/"

echo "Backup completed: $DATE"
```

#### Strategy 2: Incremental Backup

**Database**: Daily full backup
**Source files**: Incremental (only changed files)

**Tools**:
- rsync (Linux)
- robocopy (Windows)
- Backup software (e.g., Duplicati, Veeam)

**Example with rsync**:
```bash
rsync -avz --delete ./Source/ /backup/oqb2/source/
```

#### Strategy 3: Cloud Backup

**Options**:
- AWS S3
- Google Cloud Storage
- Azure Blob Storage
- OneDrive/Dropbox (for small installations)

**Considerations**:
- Encryption at rest and in transit
- Cost for storage and bandwidth
- Retention policies
- Geographic redundancy

### Recovery Procedures

#### Scenario 1: Database Corruption

**Recovery**:
1. Stop application
2. Drop corrupted database:
   ```sql
   DROP DATABASE oqb2;
   CREATE DATABASE oqb2 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```
3. Restore from backup:
   ```bash
   mysql -u root -p oqb2 < backup_oqb2_20260205.sql
   ```
4. Verify data:
   ```sql
   SELECT COUNT(*) FROM questions;
   SELECT COUNT(*) FROM question_assets;
   ```
5. Restart application

#### Scenario 2: Lost Source Files

**Recovery**:
1. Restore `Source/` directory from backup
2. Run database sync to verify:
   ```bash
   python cli.py sync
   ```
3. If mismatches, re-ingest:
   ```bash
   python cli.py ingest
   ```

#### Scenario 3: Complete System Failure

**Recovery**:
1. Install fresh system (OS, Python, MariaDB)
2. Clone application code
3. Restore `.env` configuration
4. Restore database
5. Restore `Source/` directory
6. Create virtual environment and install dependencies
7. Test application
8. Restore `output/` directory (optional)

#### Scenario 4: Accidental Data Deletion

**If recent backup available**:
1. Identify what was deleted
2. Extract specific data from backup
3. Import into current database

**If no recent backup**:
- Data likely unrecoverable
- Emphasizes importance of regular backups

### Backup Testing

**Monthly**: Perform test restore

**Process**:
1. Set up test environment
2. Restore database backup
3. Restore source files
4. Start application
5. Verify:
   - Login works
   - Questions display
   - Previews load
   - Generation works
6. Document any issues

---

## Maintenance

### Regular Tasks

#### Daily
- Monitor application logs
- Check disk space
- Verify application running

#### Weekly
- Review `ingest_errors.log`
- Check for pending questions to tag
- Review user activity logs (if enabled)
- Backup database

#### Monthly
- Run database sync
- Optimize database
- Review and clean generated documents
- Check for application updates
- Backup source files
- Test restore procedures

#### Quarterly
- Review user accounts (remove inactive)
- Audit admin access
- Review topic/subtopic organization
- Performance review
- Security audit

### Performance Monitoring

#### Application Performance

**Metrics to Monitor**:
- Page load times
- Document generation times
- Database query times
- Server CPU and memory usage

**Tools**:
- Flask debug toolbar (development)
- Application Performance Monitoring (APM) tools
- Server monitoring (htop, Task Manager)

#### Database Performance

**Check Slow Queries**:
```sql
-- Enable slow query log
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;  -- Queries longer than 1 second

-- View slow query log
SHOW VARIABLES LIKE 'slow_query_log_file';
```

**Index Usage**:
```sql
-- Check if indexes are being used
EXPLAIN SELECT * FROM questions WHERE major_topic_id = 1;

-- Add index if needed
CREATE INDEX idx_questions_topic ON questions(major_topic_id);
```

**Table Statistics**:
```sql
SHOW TABLE STATUS FROM oqb2;
```

### Log Management

**Application Logs**:
- Location: Terminal output (development) or log file (production)
- Rotation: Configure with logging handler
- Retention: Keep last 30 days

**Ingestion Errors**:
- File: `ingest_errors.log`
- Review after each ingestion
- Clean up periodically

**Database Logs**:
- MariaDB error log
- Slow query log
- General query log (if enabled)

**Log Rotation Example** (Linux):
```bash
# /etc/logrotate.d/oqb2
/var/log/oqb2/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
}
```

### Updates and Upgrades

#### Application Updates

**Process**:
1. Backup database and source files
2. Review CHANGELOG.md for changes
3. Check for database migrations needed
4. Update code (git pull or download)
5. Update dependencies:
   ```bash
   pip install -r requirements.txt --upgrade
   ```
6. Run database migrations (if any)
7. Test in development environment
8. Deploy to production
9. Verify all features working

#### Dependency Updates

**Check Outdated Packages**:
```bash
pip list --outdated
```

**Update Specific Package**:
```bash
pip install --upgrade flask
```

**Update All**:
```bash
pip install --upgrade -r requirements.txt
```

**⚠️ Warning**: Test thoroughly after updates, especially major version changes.

### Security Maintenance

**Regular Tasks**:
1. Update packages with security patches
2. Review user access levels
3. Audit admin actions (if logging enabled)
4. Check for unauthorized login attempts
5. Verify SSL certificate valid (production)
6. Review firewall rules
7. Change admin passwords periodically

**Security Checklist**:
- [ ] Default admin password changed
- [ ] Strong SECRET_KEY in production
- [ ] Database credentials secure
- [ ] `.env` file not publicly accessible
- [ ] FLASK_DEBUG=0 in production
- [ ] HTTPS enabled (production)
- [ ] Regular backups performed
- [ ] User accounts reviewed
- [ ] Dependencies up to date
- [ ] Server firewall configured

---

## Troubleshooting

### Common Issues

#### Cannot Connect to Database

**Symptoms**: Error on startup, can't login

**Possible Causes**:
1. MariaDB not running
2. Wrong credentials in `.env`
3. Database doesn't exist
4. Firewall blocking connection

**Solutions**:
1. Check MariaDB status:
   ```bash
   # Windows
   services.msc → look for MariaDB/MySQL
   
   # Linux
   sudo systemctl status mariadb
   ```
2. Verify credentials:
   - Open `.env` file
   - Check DB_USER, DB_PASSWORD, DB_NAME
   - Test connection with MySQL client
3. Ensure database exists:
   ```sql
   SHOW DATABASES;
   ```
4. Check firewall:
   - Allow port 3306 (MySQL/MariaDB default)

#### Ingestion Skips All Files

**Symptoms**: `python cli.py ingest` processes 0 files

**Possible Causes**:
1. Wrong SOURCE_PATH
2. Files don't match naming convention
3. No files in directory

**Solutions**:
1. Verify SOURCE_PATH in `.env`:
   ```bash
   # Check if path exists
   ls ./Source    # Linux/Mac
   dir Source     # Windows
   ```
2. Check file naming:
   - Must match exact pattern
   - Case-sensitive
   - Check `ingest_errors.log` for details
3. Verify directory structure:
   ```
   Source/MATC/PP/DSE/2024/P1/...
   ```

#### Images Not Displaying

**Symptoms**: Question cards show broken image icon

**Possible Causes**:
1. Wrong SOURCE_PATH
2. Files moved after ingestion
3. File permissions
4. Path stored in database incorrect

**Solutions**:
1. Verify SOURCE_PATH matches actual location
2. Check file exists:
   - Look at file_path in question_assets table
   - Verify file at that location
3. Check permissions:
   - Application user must have read access
4. Re-ingest if paths changed:
   ```bash
   python cli.py ingest
   ```

#### Generation Fails

**Symptoms**: Error when generating document, no download

**Possible Causes**:
1. output/ directory doesn't exist
2. No write permissions
3. Source files missing
4. python-docx issue

**Solutions**:
1. Create output directory:
   ```bash
   mkdir output
   ```
2. Check permissions:
   - Must be writable by application
3. Check terminal/console for error message
4. Verify source files exist
5. Test with small selection first (5 questions)

#### Slow Performance

**Symptoms**: Dashboard slow to load, filtering laggy

**Possible Causes**:
1. Too many questions in result
2. Missing database indexes
3. Large images
4. Insufficient server resources

**Solutions**:
1. Use more specific filters
2. Reduce page size
3. Add database indexes:
   ```sql
   CREATE INDEX idx_questions_subject ON questions(subject);
   CREATE INDEX idx_questions_source ON questions(source);
   CREATE INDEX idx_questions_year ON questions(year);
   CREATE INDEX idx_questions_level ON questions(level);
   CREATE INDEX idx_questions_major_topic ON questions(major_topic_id);
   ```
4. Optimize database:
   ```sql
   OPTIMIZE TABLE questions;
   OPTIMIZE TABLE question_assets;
   ```
5. Upgrade server resources

#### Login Loop (Redirects to Login After Successful Login)

**Possible Causes**:
1. SECRET_KEY changed
2. Cookies disabled
3. Session issues

**Solutions**:
1. Don't change SECRET_KEY after users logged in
2. Enable cookies in browser
3. Clear browser cookies and try again
4. Check browser console (F12) for errors

### Error Messages

#### "SQLALCHEMY_DATABASE_URI not configured"

**Cause**: `.env` file missing or not loaded

**Solution**:
1. Verify `.env` file exists
2. Check it contains DB_* variables
3. Restart application

#### "Table 'oqb2.users' doesn't exist"

**Cause**: Database not initialized

**Solution**:
```bash
python init_db.py
```

#### "No such file or directory: './Source'"

**Cause**: SOURCE_PATH directory doesn't exist

**Solution**:
```bash
mkdir Source
mkdir Source/MATC Source/MAT1 Source/MAT2 Source/ICT
```

#### "Permission denied" when starting application

**Cause**: Port 5000 already in use

**Solution**:
1. Kill process using port 5000:
   ```bash
   # Windows
   netstat -ano | findstr :5000
   taskkill /PID <pid> /F
   
   # Linux
   lsof -i :5000
   kill <pid>
   ```
2. Or change port in `run.py`:
   ```python
   app.run(debug=True, port=5001)
   ```

### Debugging

#### Enable Debug Mode

In `.env`:
```env
FLASK_DEBUG=1
FLASK_ENV=development
```

Restart application. Now detailed error messages shown in browser.

**⚠️ Never enable debug mode in production!**

#### Check Logs

**Application Terminal**:
- Shows all Flask output
- Error messages
- Request logs

**Ingestion Errors**:
```bash
cat ingest_errors.log     # Linux/Mac
type ingest_errors.log    # Windows
```

**Browser Console**:
- Press F12
- Click Console tab
- Shows JavaScript errors
- Network tab shows failed requests

#### Database Debugging

**Test Query**:
```sql
SELECT * FROM questions LIMIT 5;
```

**Check Relationships**:
```sql
SELECT q.qid, t.name as topic
FROM questions q
LEFT JOIN topics t ON q.major_topic_id = t.id
LIMIT 10;
```

**Asset Count per Question**:
```sql
SELECT question_id, COUNT(*) as asset_count
FROM question_assets
GROUP BY question_id
HAVING asset_count < 1;  -- Questions with no assets
```

---

## Production Deployment

### Preparation

**Security Checklist**:
- [ ] Change default admin password
- [ ] Set strong SECRET_KEY (32+ random characters)
- [ ] Set FLASK_ENV=production
- [ ] Set FLASK_DEBUG=0
- [ ] Use strong database password
- [ ] Configure firewall
- [ ] Enable HTTPS/SSL
- [ ] Review user permissions
- [ ] Secure `.env` file (chmod 600)

**Performance Checklist**:
- [ ] Use production WSGI server (gunicorn/uWSGI)
- [ ] Configure database connection pooling
- [ ] Set up reverse proxy (nginx/Apache)
- [ ] Enable caching where appropriate
- [ ] Optimize database indexes
- [ ] Configure log rotation

### WSGI Server Setup

#### Option 1: Gunicorn (Linux)

**Install**:
```bash
pip install gunicorn
```

**Run**:
```bash
gunicorn -w 4 -b 0.0.0.0:5000 'app:create_app()'
```

**Systemd Service** (`/etc/systemd/system/oqb2.service`):
```ini
[Unit]
Description=Online Question Bank System
After=network.target mariadb.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/oqb2
Environment="PATH=/opt/oqb2/venv/bin"
ExecStart=/opt/oqb2/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 'app:create_app()'

[Install]
WantedBy=multi-user.target
```

**Start Service**:
```bash
sudo systemctl start oqb2
sudo systemctl enable oqb2
```

#### Option 2: uWSGI

**Install**:
```bash
pip install uwsgi
```

**Config** (`uwsgi.ini`):
```ini
[uwsgi]
module = app:create_app()
callable = app
master = true
processes = 4
socket = 127.0.0.1:5000
vacuum = true
die-on-term = true
```

**Run**:
```bash
uwsgi --ini uwsgi.ini
```

#### Option 3: Windows Service

Use tools like NSSM (Non-Sucking Service Manager):

```cmd
nssm install OQB2 "C:\path\to\venv\Scripts\python.exe" "C:\path\to\run.py"
nssm start OQB2
```

### Reverse Proxy

#### Nginx Configuration

**File**: `/etc/nginx/sites-available/oqb2`

```nginx
server {
    listen 80;
    server_name oqb.example.com;

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name oqb.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Max upload size
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /opt/oqb2/static;
        expires 30d;
    }

    location /dashboard/files {
        internal;
        alias /opt/oqb2/Source;
    }
}
```

**Enable Site**:
```bash
sudo ln -s /etc/nginx/sites-available/oqb2 /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### Apache Configuration

**File**: `/etc/apache2/sites-available/oqb2.conf`

```apache
<VirtualHost *:80>
    ServerName oqb.example.com
    Redirect permanent / https://oqb.example.com/
</VirtualHost>

<VirtualHost *:443>
    ServerName oqb.example.com

    SSLEngine on
    SSLCertificateFile /path/to/cert.pem
    SSLCertificateKeyFile /path/to/key.pem

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:5000/
    ProxyPassReverse / http://127.0.0.1:5000/

    <Location /static>
        ProxyPass !
        Alias /opt/oqb2/static
    </Location>
</VirtualHost>
```

**Enable Site**:
```bash
sudo a2enmod ssl proxy proxy_http
sudo a2ensite oqb2
sudo systemctl reload apache2
```

### SSL/HTTPS Setup

#### Option 1: Let's Encrypt (Free)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d oqb.example.com
```

Auto-renewal:
```bash
sudo certbot renew --dry-run
```

#### Option 2: Commercial Certificate

1. Purchase certificate
2. Generate CSR
3. Receive certificate files
4. Configure in nginx/Apache
5. Set up auto-renewal

### Database Optimization for Production

**Increase Connection Pool**:
```python
# app/__init__.py
app.config['SQLALCHEMY_POOL_SIZE'] = 20
app.config['SQLALCHEMY_MAX_OVERFLOW'] = 40
```

**Configure MariaDB** (`/etc/mysql/mariadb.conf.d/50-server.cnf`):
```ini
[mysqld]
max_connections = 200
innodb_buffer_pool_size = 2G  # 70% of RAM
innodb_log_file_size = 256M
query_cache_size = 64M
query_cache_limit = 2M
```

**Restart MariaDB**:
```bash
sudo systemctl restart mariadb
```

### Monitoring

**Application Monitoring**:
- Sentry (error tracking)
- New Relic (APM)
- Datadog
- Custom logging

**Server Monitoring**:
- Nagios
- Zabbix
- Prometheus + Grafana

**Uptime Monitoring**:
- UptimeRobot
- Pingdom
- StatusCake

### Scaling

**Vertical Scaling** (single server):
- Increase CPU/RAM
- Faster SSD storage
- Optimize queries

**Horizontal Scaling** (multiple servers):
- Load balancer (HAProxy, nginx)
- Shared file storage (NFS, GlusterFS)
- Database replication
- Session storage (Redis)

---

## Testing Guide

### Pre-Deployment Testing

Before deploying to production, perform comprehensive testing.

#### Test 1: Database Initialization

**Objective**: Verify database schema correct

**Steps**:
1. Drop database (if exists):
   ```sql
   DROP DATABASE oqb2;
   ```
2. Recreate:
   ```sql
   CREATE DATABASE oqb2 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```
3. Run init script:
   ```bash
   python init_db.py
   ```
4. Check output for success messages
5. Verify tables in phpMyAdmin/MySQL client
6. Check default data exists:
   ```sql
   SELECT * FROM subjects;
   SELECT * FROM users;
   SELECT * FROM topics WHERE subject_id = 'MATC';
   ```

**Expected**: All tables created, default admin user exists, sample topics present.

#### Test 2: File Ingestion

**Objective**: Import test questions successfully

**Preparation**:
- Create 10-20 test files following naming convention
- Mix of QUE/ANS/SOL
- Different languages (EN/CH/BI)
- Different formats (PNG/JPG/DOCX)

**Steps**:
1. Place files in `Source/` directories
2. Run ingestion:
   ```bash
   python cli.py ingest
   ```
3. Check output for processed files count
4. Verify questions in database:
   ```sql
   SELECT COUNT(*) FROM questions;
   SELECT COUNT(*) FROM question_assets;
   ```
5. Check `ingest_errors.log` (should be empty or minimal)

**Expected**: All valid files imported, questions and assets created.

#### Test 3: Authentication

**Objective**: Login system works

**Steps**:
1. Start server:
   ```bash
   python run.py
   ```
2. Navigate to `http://localhost:5000`
3. Should redirect to `/login`
4. Try invalid credentials → should fail
5. Try valid credentials (`admin` / `admin123`) → should succeed
6. Should redirect to dashboard
7. Check navigation shows appropriate links
8. Logout → should return to login

**Expected**: Authentication working, unauthorized access blocked.

#### Test 4: Dashboard Filtering

**Objective**: All filters work correctly

**Test Cases**:
- [ ] Subject filter → loads topics
- [ ] Source filter → updates year dropdown
- [ ] Year filter → filters questions
- [ ] Topic filter → shows relevant questions
- [ ] Cross-topic toggle → includes minor topics
- [ ] Topic mode (AND/OR) → correct logic
- [ ] Subtopic filter → filters correctly
- [ ] Level filter → shows only selected levels
- [ ] Type filter (MC/CQ) → correct filtering
- [ ] Section filter → correct filtering
- [ ] QID search → exact and wildcard work
- [ ] Combined filters → intersection correct
- [ ] Page size → correct number displayed
- [ ] Pagination → all pages accessible
- [ ] Language preference → correct image shown

**Expected**: All filters functional, correct results.

#### Test 5: Multi-Level Sorting

**Objective**: Sorting by multiple criteria works

**Test Cases**:
- [ ] Sort by single field (QID)
- [ ] Sort by multiple fields (Topic → Level)
- [ ] Reverse sort direction
- [ ] Natural sorting (Q1, Q2, Q10)
- [ ] NULL values handled correctly
- [ ] Sort persists in session

**Expected**: Questions sorted correctly with priority.

#### Test 6: Document Generation

**Objective**: Generate valid Word documents

**Test Cases**:
- [ ] Selection order sort
- [ ] Custom multi-level sort
- [ ] Questions only
- [ ] Question + Answer
- [ ] Question + Solution
- [ ] All questions then answers
- [ ] All questions then solutions
- [ ] MC spacing settings
- [ ] CQ spacing settings
- [ ] Show QID on questions
- [ ] Show QID on answers
- [ ] Show correct percentage
- [ ] Language preference (EN/CH)
- [ ] Missing assets handled gracefully

**Verification**:
- Open generated .docx file
- Check: images present, order correct, spacing correct, format A4, QIDs shown/hidden as specified

**Expected**: Document generates successfully, formatting correct.

#### Test 7: Topic Management (Admin)

**Objective**: CRUD operations for topics work

**Test Cases**:
- [ ] View topics by subject
- [ ] Add new topic → appears in list
- [ ] Edit topic → name updates
- [ ] Add subtopic → appears under topic
- [ ] Edit subtopic → name updates
- [ ] Delete subtopic → removed from questions
- [ ] Delete topic → cascade deletes subtopics
- [ ] Topics load in dashboard filter

**Expected**: All CRUD operations successful, changes persist.

#### Test 8: Question Tagging (Admin)

**Objective**: Edit question metadata

**Test Cases**:
- [ ] View question with preview
- [ ] Edit major topic → saves
- [ ] Edit major subtopic → saves
- [ ] Add minor topics → saves
- [ ] Add subtopics → saves
- [ ] Set level → saves
- [ ] Set type → saves
- [ ] Set section → saves
- [ ] Set correct percentage → saves
- [ ] Add description → saves
- [ ] Invalid major subtopic → validation error
- [ ] Changes reflected in dashboard filter

**Expected**: All fields editable, validation works, changes persist.

#### Test 9: Batch Operations (Admin)

**Objective**: Bulk update and delete work

**Batch Update**:
- [ ] Select multiple questions
- [ ] Set level for all → updates
- [ ] Set type for all → updates
- [ ] Set major topic for all → updates
- [ ] Mixed updates (some fields) → correct
- [ ] Verify all selected questions updated

**Batch Delete**:
- [ ] Select questions to delete
- [ ] Confirmation dialog shows count
- [ ] Confirm deletion
- [ ] Questions removed from database
- [ ] Assets removed (cascade)
- [ ] Source files still exist

**Expected**: Batch operations work, correct questions affected.

#### Test 10: Database Sync

**Objective**: Orphaned records removed

**Steps**:
1. Create test questions
2. Delete some source files manually
3. Run sync dry-run:
   ```bash
   python cli.py sync
   ```
4. Verify correct files identified as orphaned
5. Run actual sync:
   ```bash
   python cli.py sync --no-dry-run --force
   ```
6. Verify orphaned assets deleted
7. Verify orphaned questions deleted

**Expected**: Sync identifies and removes orphaned records.

#### Test 11: Performance Testing

**Objective**: Acceptable performance under load

**Test Cases**:
- [ ] Dashboard with 100 questions → load time < 2s
- [ ] Dashboard with 1000 questions → load time < 5s
- [ ] Filter change → response time < 1s
- [ ] Generate 10 questions → < 5s
- [ ] Generate 50 questions → < 20s
- [ ] Generate 100 questions → < 40s
- [ ] Multiple concurrent users → no degradation

**Tools**:
- Browser DevTools (Network tab)
- Apache Bench (ab)
- Locust (load testing)

**Expected**: Acceptable response times, no crashes under load.

#### Test 12: Security Testing

**Objective**: Verify security measures

**Test Cases**:
- [ ] Access /dashboard without login → redirects to login
- [ ] Access /admin without admin role → access denied
- [ ] Access question files without login → access denied
- [ ] SQL injection attempts → safely handled
- [ ] XSS attempts → safely escaped
- [ ] Session hijacking → mitigated
- [ ] Password stored as hash (not plaintext)

**Expected**: Unauthorized access blocked, inputs sanitized.

#### Test 13: Error Handling

**Objective**: Graceful error handling

**Test Cases**:
- [ ] Invalid filename format → logged, not crash
- [ ] Missing source file → error message, not crash
- [ ] Database connection lost → error message
- [ ] Empty filter result → "No questions found"
- [ ] Generate with no selection → error message
- [ ] Invalid QID search → no results, no error
- [ ] Malformed request → 400 error

**Expected**: All errors handled gracefully, no crashes.

#### Test 14: Browser Compatibility

**Objective**: Works across browsers

**Test Browsers**:
- [ ] Chrome (latest)
- [ ] Firefox (latest)
- [ ] Edge (latest)
- [ ] Safari (latest) - if on macOS

**Test Features**:
- Login
- Dashboard filtering
- Modal dialogs
- Document generation
- Admin features

**Expected**: Full functionality in all modern browsers.

#### Test 15: Mobile Responsiveness

**Objective**: Usable on mobile devices

**Test Devices**:
- [ ] iPhone/Android phone
- [ ] iPad/Android tablet

**Test**:
- [ ] Login page responsive
- [ ] Dashboard readable
- [ ] Filters accessible
- [ ] Question cards visible
- [ ] Generation works (though editing best on desktop)

**Expected**: Functional on mobile, though optimized for desktop.

### Testing Checklist

Use this before each release:

**Functional Tests**:
- [ ] Database initialization
- [ ] File ingestion
- [ ] Authentication
- [ ] Dashboard filtering (all filters)
- [ ] Multi-level sorting
- [ ] Document generation (all modes)
- [ ] Topic management (CRUD)
- [ ] Question tagging
- [ ] Batch operations
- [ ] Database sync
- [ ] User management

**Non-Functional Tests**:
- [ ] Performance acceptable
- [ ] Security measures working
- [ ] Error handling graceful
- [ ] Browser compatibility
- [ ] Mobile responsiveness

**Configuration Tests**:
- [ ] .env file loading
- [ ] Database connection
- [ ] File paths correct
- [ ] Permissions adequate

**Documentation**:
- [ ] README accurate
- [ ] USER_MANUAL current
- [ ] ADMIN_GUIDE current
- [ ] DEVELOPER_SPEC current
- [ ] CHANGELOG updated

---

## DSE P2 Import Guide

Special procedures for importing MATC DSE Paper 2 questions.

### Overview

The `import_dse_p2.py` script automates importing DSE P2 questions from a specific source structure.

### Prerequisites

**Required Files in `Q:\Temp`**:
- `QUE\` folder with PNG files (format: `YYYY_NN.png`)
- `MATC MC ANS.csv` with answer keys
- `MATC MC Percentage.csv` with correct percentage data
- Letter images: `A.png`, `B.png`, `C.png`, `D.png`

**Configuration**:
- `.env` file configured
- Database initialized
- `SOURCE_PATH` writable

### CSV Format

**MATC MC ANS.csv**:
```csv
Year,Q1,Q2,Q3,...
2012,B,D,A,...
2013,C,A,B,...
```

**MATC MC Percentage.csv**:
```csv
Year,Q1,Q2,Q3,...
2012,94.0,85.5,no data,...
2013,78.3,92.1,83.7,...
```

### What the Script Does

1. **Read CSV Files**: Loads answer keys and percentages
2. **Create Folder Structure**: `SOURCE_PATH/MATC/PP/DSE/YYYY/P2/`
3. **Copy QUE Files**: Renames `2024_05.png` → `MATC_DSE_2024_P2_Q5_EN_QUE.png`
4. **Copy ANS Files**: Looks up answer (e.g., "C"), copies `C.png` → `MATC_DSE_2024_P2_Q5_EN_ANS.png`
5. **Run Ingestor**: Automatically imports files
6. **Update Percentages**: Sets correct_percentage field

### Running the Script

```bash
python import_dse_p2.py
```

### Expected Output

```
================================================================================
DSE P2 Question Import Script
================================================================================
Reading CSV files...
  Loaded answers for 14 years
  Loaded percentages for 14 years

Creating folder structure...
  Created: Q:\Source\MATC\PP\DSE

Scanning QUE files...
  Found 630 PNG files

Copying and renaming files...
  ✓ 2012_04.png -> MATC_DSE_2012_P2_Q4_EN_QUE.png
    ✓ Answer B -> MATC_DSE_2012_P2_Q4_EN_ANS.png
  ...

  Summary:
    QUE files copied: 630
    ANS files copied: 550
    Questions without answers: 80

================================================================================
Running ingestor to import files into database...
================================================================================
...

================================================================================
Updating correct percentages in database...
================================================================================
  ✓ Updated MATC_DSE_2012_P2_Q1: 94.0%
  ...

================================================================================
Import process completed!
================================================================================
```

### Verification

**Check Files**:
```bash
ls "Q:\Source\MATC\PP\DSE\2024\P2"
```

**Check Database**:
1. Start web application
2. Navigate to dashboard
3. Filter: Subject=MATC, Source=DSE, Year=2024, Section=P2
4. Verify questions appear
5. Check correct percentages displayed

### Customization

Edit script to customize:

```python
# Change language
LANGUAGE = "CH"  # or "BI"

# Change paper
PAPER = "P1"

# Change subject
SUBJECT = "MAT1"
```

### Troubleshooting

**CSV files not found**:
- Ensure files in `Q:\Temp` with exact names
- Check file encoding (should be UTF-8 or ANSI)

**Letter images not found**:
- Ensure A.png, B.png, C.png, D.png exist in `Q:\Temp`

**Permission denied**:
- Ensure write access to SOURCE_PATH
- Run as administrator if needed

**No files copied**:
- Check QUE folder path
- Verify file naming (YYYY_NN.png)

### After Import

1. Verify questions in dashboard
2. Tag questions with topics
3. Set levels if not auto-assigned
4. Add descriptions as needed

---

## Support

For technical assistance:

1. **Check Documentation**:
   - This guide for admin tasks
   - USER_MANUAL.md for end-user features
   - DEVELOPER_SPEC.md for technical details

2. **Review Logs**:
   - Application terminal output
   - `ingest_errors.log`
   - Database logs
   - Browser console (F12)

3. **Database Inspection**:
   - Use phpMyAdmin or MySQL client
   - Check data integrity
   - Review relationships

4. **Community**:
   - Check issue tracker (if using version control)
   - Search similar problems
   - Document solutions

---

**End of Administrator Guide**

*Last Updated: February 5, 2026*  
*Online Question Bank System v2.1.0*

For developer documentation, see DEVELOPER_SPEC.md

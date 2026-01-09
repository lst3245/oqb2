# Online Question Bank System

A comprehensive Flask-based web application for managing, filtering, and generating question papers from an organized repository of educational content.

## Features

### 📚 Question Management
- **Automated Ingestion**: Scan and import questions from organized file structures
- **Database Sync**: Remove orphaned records when source files are deleted
- **Flexible Tagging**: Tag questions with topics, subtopics, difficulty levels
- **Multi-Language Support**: Handle English, Chinese, and Bilingual content with language preference
- **Multiple Formats**: Support for images (PNG, JPG) and Word documents
- **Batch Operations**: Update or delete multiple questions at once
- **Direct QID Search**: Quick search by question ID with wildcard support

### 🔍 Advanced Filtering
- Filter by subject, source (DSE/CE/AL/QB), year, paper
- Topic and subtopic filtering with cross-topic search
- **Topic Mode**: AND/OR logic for multi-topic filtering
- Difficulty level selection (1, 2, 3) with "Not Assigned" option
- Question type filtering (Multiple Choice, Conventional)
- Section filtering (A, B, etc.)
- **Multi-level Sorting**: Sort by multiple fields simultaneously (e.g., topic → level → year)
- Natural ordering (Q1, Q2, Q10 - not Q1, Q10, Q2)
- Configurable page size (10, 20, 50, 100 items)
- Preview language preference (English or Chinese priority)

### 📄 Document Generation
- Generate custom Word documents from selected questions
- Multiple answer modes:
  - Questions only
  - Questions with answers
  - Questions with solutions
  - All questions, then all answers
  - All questions, then all solutions
- **Flexible Sorting**: 
  - Preserve selection order
  - Multi-level custom sorting (e.g., topic → subtopic → level)
- **Smart Spacing Control**:
  - Separate settings for MC and CQ questions
  - Line spacing or page breaks (before/after each question)
  - Intelligent page break handling (avoids duplicate breaks)
- **Language Preference**: Prioritize English or Chinese assets (with Bilingual fallback)
- Optional question ID display (for questions and/or answers)
- A4 page size with narrow margins
- Automatic image resizing for proper fit

### 🔧 Admin Features
- **Topic Management**: Full CRUD operations for topics and subtopics
- **Question Tagging**: 
  - Edit major topic and subtopic
  - Add multiple minor topics
  - Assign multiple subtopics
  - Set level, type, section, and description
- **Batch Operations**:
  - Batch update metadata for selected questions
  - Batch delete questions with confirmation
- **User Management**: Create users with admin/regular roles
- **Asset Preview**: Preview questions, answers, and solutions inline

### 🔒 Security
- User authentication with Flask-Login
- Role-based access control (Admin/Regular users)
- Protected file serving

## Tech Stack

- **Backend**: Flask (Python)
- **Database**: MariaDB with SQLAlchemy ORM
- **Frontend**: Bootstrap 5 + HTMX
- **Document Generation**: python-docx
- **Image Processing**: Pillow

## Quick Start

### Windows (Recommended)

1. **Run the Quick Start Script:**
   ```cmd
   quickstart.bat
   ```

2. **Configure Database:**
   - Copy `env_template.txt` to `.env`
   - Edit `.env` with your MariaDB credentials

3. **Start the Application:**
   ```cmd
   python run.py
   ```

4. **Access the System:**
   - Open browser to: http://localhost:5000
   - Login: `admin` / `admin123`

### Manual Setup

See [SETUP.md](SETUP.md) for detailed installation instructions.

## File Structure

```
oqb2/
├── app/                    # Flask application
│   ├── __init__.py        # App factory
│   ├── models.py          # Database models
│   ├── auth.py            # Authentication
│   ├── dashboard.py       # Question browser
│   ├── admin.py           # Admin panel
│   ├── generator.py       # Document generation
│   ├── ingestor.py        # File scanner
│   ├── config.py          # Configuration
│   └── utils.py           # Helper functions
├── templates/             # HTML templates
├── static/                # CSS, JS files
├── Source/                # Question files (your content)
├── output/                # Generated documents
├── init_db.py            # Database initialization
├── cli.py                # CLI commands
├── run.py                # Application entry point
└── requirements.txt      # Python dependencies
```

## File Naming Convention

### Past Paper (PP) Files
Format: `SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE.EXT`

Example: `MATC_DSE_2025_P2_Q5_EN_QUE.png`

- **SUBJ**: Subject code (MATC, MAT1, MAT2, ICT)
- **SOURCE**: DSE, CE, or AL
- **YEAR**: 4-digit year (2024, 2025, etc.)
- **PAPER**: Paper number (P1, P2, etc.)
- **QNO**: Question number (Q1, Q2, Q10, etc.)
- **LANG**: Language (EN=English, CH=Chinese, BI=Bilingual)
- **TYPE**: Asset type (QUE=Question, ANS=Answer, SOL=Solution)
- **EXT**: File extension (png, jpg, docx, etc.)

### Question Bank (QB) Files
Format: `SUBJ_QB_DETAIL_QNO_LANG_TYPE.EXT`

Example: `MATC_QB_MATHSMART2024_Q1_EN_QUE.png`

## Usage

### 1. Ingest Questions

Place your question files in the `Source/` directory following the folder structure:

```
Source/
└── [SUBJECT]/
    ├── PP/
    │   └── [DSE|CE|AL]/
    │       └── [YEAR]/
    │           └── [PAPER]/
    │               └── files...
    └── QB/
        └── [DETAIL]/
            └── files...
```

Run the ingestor:
```bash
python cli.py ingest
```

### 2. Sync Database (Optional)

If you've deleted or moved source files, sync the database to remove orphaned records:

```bash
# Preview what would be deleted (dry-run)
python cli.py sync

# Actually delete orphaned records
python cli.py sync --no-dry-run
```

### 3. Browse and Filter Questions

1. Login to the dashboard
2. Select filters:
   - **Subject**: Choose MATC, MAT1, MAT2, or ICT
   - **Source Type**: DSE, CE, AL, or QB
   - **Years**: Select multiple years (PP only)
   - **Topics**: Select one or more topics
   - **Topic Mode**: AND (must have all) or OR (any of them)
   - **Cross-topic**: Include questions with selected topics as minor topics
   - **Subtopics**: Auto-loads based on selected topics
   - **Levels**: 1, 2, 3, or "Not Assigned"
   - **Question Type**: MC, CQ, or All
   - **Section**: A, B, or All
   - **QID Search**: Direct search by question ID (supports wildcards)
3. Configure display:
   - **Page Size**: 10, 20, 50, or 100 questions per page
   - **Preview Language**: Prioritize English or Chinese
   - **Multi-level Sort**: Click column headers to add sort levels
4. View question previews with Answer/Solution buttons
5. Select questions with checkboxes

### 4. Generate Documents

1. Select desired questions (or use "Select All on Page")
2. Click "Generate Document" button
3. Choose generation options:
   - **Sort Mode**: 
     - Selection order (as you selected them)
     - Custom multi-level sort (e.g., Topic → Level → Year)
   - **Answer Mode**:
     - Questions Only
     - Question + Answer
     - Question + Solution
     - All Questions, Then All Answers
     - All Questions, Then All Solutions
   - **Spacing** (separate for MC and CQ):
     - Before: Skip lines or Start new page
     - After: Skip lines or Start new page
   - **Display Options**:
     - Show Question ID on questions
     - Show Question ID on answers/solutions
   - **Language**: Prefer English or Chinese assets
4. Click "Generate & Download"
5. Open the Word document

### 5. Manage Topics (Admin)

1. Go to **Admin → Manage Topics**
2. View topics organized by subject
3. Operations:
   - **Add Topic**: Create new topic under a subject
   - **Edit Topic**: Rename existing topic
   - **Delete Topic**: Remove topic (and its subtopics)
   - **Add Subtopic**: Add skill under a topic
   - **Edit Subtopic**: Rename subtopic
   - **Delete Subtopic**: Remove subtopic

### 6. Tag Questions (Admin)

1. Go to **Admin → Tag Questions**
2. Filter questions to find the ones to tag
3. Click "Edit Tags" on a question
4. Edit metadata:
   - **Major Topic**: Primary topic (one only)
   - **Major Subtopic**: Primary subtopic (from major topic)
   - **Minor Topics**: Additional topics (multiple allowed)
   - **Subtopics**: Additional skills (multiple allowed)
   - **Level**: 1, 2, or 3
   - **Question Type**: MC or CQ
   - **Section**: A, B, etc.
   - **Description**: Optional text description
5. Save changes

### 7. Batch Operations (Admin)

**Batch Update:**
1. Filter and select multiple questions
2. Click "Batch Update"
3. Choose which fields to update
4. Set values and apply to all selected

**Batch Delete:**
1. Filter and select multiple questions
2. Click "Batch Delete"
3. Confirm deletion (WARNING: This is permanent!)

## Testing

See [TESTING.md](TESTING.md) for comprehensive testing guide.

## Database Schema

- **users**: User accounts and authentication (username, password_hash, is_admin, created_at)
- **subjects**: Subject definitions (id: MATC/MAT1/MAT2/ICT, name)
- **topics**: Main topic categories (id, subject_id, name)
- **subtopics**: Specific skills under topics (id, topic_id, name)
- **questions**: Logical question records
  - Core fields: qid (unique), subject, source, year, paper, section, qno
  - Metadata: q_type (MC/CQ), level (1/2/3), description
  - Topic relations: major_topic_id, major_subtopic_id
- **question_assets**: Physical files (QUE/ANS/SOL)
  - Fields: question_id, asset_type, file_format (IMG/DOC), language (EN/CH/BI), file_path
- **question_minor_topics**: Many-to-many for cross-topic associations
- **question_subtopics**: Many-to-many for multiple subtopics per question

## Configuration

Key settings in `.env`:

```env
DB_HOST=localhost          # MariaDB host
DB_USER=root              # Database user
DB_PASSWORD=password      # Database password
DB_NAME=oqb2             # Database name
SECRET_KEY=random-key    # Flask secret key
SOURCE_PATH=./Source     # Question files location
OUTPUT_PATH=./output     # Generated documents location
```

## CLI Commands

```bash
# Ingest questions from default SOURCE_PATH
python cli.py ingest

# Ingest from custom path
python cli.py ingest --source-path "D:/Custom/Path"

# Sync database with filesystem (dry-run preview)
python cli.py sync

# Sync database with filesystem (actually delete orphaned records)
python cli.py sync --no-dry-run

# Sync from custom path without confirmation
python cli.py sync --source-path "D:/Custom/Path" --no-dry-run --force
```

## API Endpoints

### Dashboard
- `GET /dashboard/` - Main dashboard
- `GET/POST /dashboard/filter` - Filter questions (supports HTMX)
- `GET /dashboard/api/topics/<subject_id>` - Get topics for subject
- `GET /dashboard/api/subtopics?topic_ids=1,2` - Get subtopics for topics
- `GET /dashboard/api/years/<subject_id>/<source>` - Get available years
- `GET /dashboard/files/<path>` - Serve question files (login required)
- `GET /dashboard/api/asset/<asset_id>` - Get asset metadata
- `GET /dashboard/api/asset_preview/<asset_id>` - Get asset file for preview
- `GET /dashboard/api/question/<question_id>/assets/<asset_type>` - Get specific asset

### Admin
- `GET /admin/` - Admin dashboard
- `GET /admin/topics` - Topic management interface
- `POST /admin/topics/add` - Add new topic
- `POST /admin/topics/<id>/edit` - Edit topic
- `POST /admin/topics/<id>/delete` - Delete topic
- `POST /admin/subtopics/add` - Add new subtopic
- `POST /admin/subtopics/<id>/edit` - Edit subtopic
- `POST /admin/subtopics/<id>/delete` - Delete subtopic
- `POST /admin/questions/<id>/update` - Update question metadata
- `POST /admin/questions/delete` - Batch delete questions
- `POST /admin/questions/batch-update` - Batch update questions

### Generator
- `GET /generate/` - Generation options page with multi-level sort configuration
- `POST /generate/create` - Create and download Word document

### Authentication
- `GET /` - Redirect to dashboard or login
- `GET/POST /login` - User login
- `GET /logout` - User logout
- `GET/POST /register` - Register new user (admin only)

## Development

### Running in Development Mode

```bash
# Activate virtual environment
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# Set environment
set FLASK_ENV=development  # Windows
export FLASK_ENV=development  # Linux/Mac

# Run server
python run.py
```

### Adding New Subjects

1. In phpMyAdmin or MySQL client:
   ```sql
   INSERT INTO subjects (id, name) VALUES ('PHYS', 'Physics');
   ```

2. Create folder structure:
   ```
   Source/PHYS/PP/...
   Source/PHYS/QB/...
   ```

3. Run ingestor to import files

## Troubleshooting

### Database Connection Failed
- Verify MariaDB is running
- Check credentials in `.env`
- Ensure database `oqb2` exists

### Images Not Displaying
- Check `SOURCE_PATH` in `.env`
- Verify file paths in database
- Check file permissions

### Ingestor Skipping Files
- Verify file naming matches pattern exactly
- Check `ingest_errors.log` for details
- Ensure folder structure is correct

### Generation Fails
- Verify `output/` directory exists
- Check source files exist at specified paths
- Ensure python-docx is installed

## Production Deployment

1. Set `FLASK_ENV=production` in `.env`
2. Set `FLASK_DEBUG=0`
3. Use strong `SECRET_KEY`
4. Change default admin password
5. Use production WSGI server (gunicorn/uWSGI)
6. Set up SSL/HTTPS
7. Configure regular backups

## Contributing

This is a custom internal system. For modifications:

1. Understand the existing architecture
2. Test changes thoroughly
3. Update documentation
4. Maintain database schema compatibility

## License

Copyright © 2024. All rights reserved.

This system is for internal use only.

## Documentation

This project includes comprehensive documentation:

### Getting Started
- **[SETUP.md](SETUP.md)** - Complete installation and configuration guide
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - One-page reference for common tasks

### User Documentation  
- **[USER_GUIDE.md](USER_GUIDE.md)** - Comprehensive guide with examples and tips
- **[TESTING.md](TESTING.md)** - Testing procedures and checklist

### Technical Documentation
- **[PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)** - Technical architecture and implementation details
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and upgrade guide

## Support

For issues or questions:

1. **Check Documentation**:
   - Quick answers: [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
   - Detailed help: [USER_GUIDE.md](USER_GUIDE.md)
   - Setup issues: [SETUP.md](SETUP.md)
   - Recent changes: [CHANGELOG.md](CHANGELOG.md)

2. **Review Error Logs**:
   - Terminal output (when running application)
   - `ingest_errors.log` (file ingestion errors)
   - Browser console - Press F12 (JavaScript errors)

3. **Check Database**:
   - Open phpMyAdmin or MySQL client
   - Verify tables exist and contain data
   - Check `questions` and `question_assets` tables

4. **Common Solutions**:
   - Restart Flask server: `Ctrl+C` then `python run.py`
   - Clear browser cache: `Ctrl+F5`
   - Re-run database init: `python init_db.py`
   - Verify `.env` configuration

## Version

**Version 2.0.0** - Enhanced Release

### What's New in v2.0.0
- **Multi-level Sorting**: Sort by multiple fields simultaneously with custom priority
- **Batch Operations**: Update or delete multiple questions at once
- **Database Sync**: Remove orphaned records when files are deleted
- **Smart Spacing**: Separate MC/CQ spacing with intelligent page break handling
- **Direct QID Search**: Quick search by question ID with wildcard support
- **Topic Modes**: AND/OR logic for multi-topic filtering
- **Language Preference**: Prioritize English or Chinese in preview and generation
- **Major Subtopics**: Assign primary subtopic to questions
- **Enhanced Generation**: More answer modes including "Questions then Solutions"
- **Configurable Page Size**: Choose 10, 20, 50, or 100 items per page
- **Question Descriptions**: Add optional text descriptions to questions

### Features from v1.0.0
- Complete question management system
- Advanced filtering and search
- Word document generation with A4 page format
- Admin panel for topics and tagging
- User authentication with role-based access
- File ingestion from organized folders
- Multi-language support (EN, CH, BI)
- Natural sorting of questions (Q1, Q2, Q10)
- Cross-topic search capabilities

---

**Developed with Flask, Bootstrap, and HTMX**

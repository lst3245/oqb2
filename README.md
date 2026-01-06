# Online Question Bank System

A comprehensive Flask-based web application for managing, filtering, and generating question papers from an organized repository of educational content.

## Features

### 📚 Question Management
- **Automated Ingestion**: Scan and import questions from organized file structures
- **Flexible Tagging**: Tag questions with topics, subtopics, difficulty levels
- **Multi-Language Support**: Handle English, Chinese, and Bilingual content
- **Multiple Formats**: Support for images (PNG, JPG) and Word documents

### 🔍 Advanced Filtering
- Filter by subject, source (DSE/CE/AL/QB), year, paper
- Topic and subtopic filtering with cross-topic search
- Difficulty level selection (1, 2, 3)
- Question type filtering (Multiple Choice, Conventional)
- Natural ordering (Q1, Q2, Q10 - not Q1, Q10, Q2)

### 📄 Document Generation
- Generate custom Word documents from selected questions
- Multiple answer modes:
  - Questions only
  - Questions with answers
  - Questions with solutions
  - All questions, then all answers
- Sorting options: by ID, level, year, or topic
- A4 page size with configurable margins
- Automatic image resizing for proper fit

### 🔧 Admin Features
- Topic and subtopic management
- Question metadata editing
- User management with role-based access
- Preview questions, answers, and solutions

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

### 2. Browse and Filter Questions

1. Login to the dashboard
2. Select filters (subject, source, topics, etc.)
3. View question previews
4. Select questions for document generation

### 3. Generate Documents

1. Select desired questions
2. Click "Generate Document"
3. Choose generation options:
   - Sort order
   - Answer mode
   - Formatting preferences
4. Download the generated Word document

### 4. Manage Topics (Admin)

1. Go to Admin → Manage Topics
2. Add, edit, or delete topics and subtopics
3. Organize your question taxonomy

### 5. Tag Questions (Admin)

1. Go to Admin → Tag Questions
2. Filter questions to find ones to tag
3. Edit metadata:
   - Major and minor topics
   - Subtopics
   - Difficulty level
   - Question type
   - Section

## Testing

See [TESTING.md](TESTING.md) for comprehensive testing guide.

## Database Schema

- **users**: User accounts and authentication
- **subjects**: Subject definitions (MATC, MAT1, etc.)
- **topics**: Main topic categories
- **subtopics**: Specific skills under topics
- **questions**: Logical question records
- **question_assets**: Physical files (QUE/ANS/SOL)
- **question_minor_topics**: Cross-topic associations
- **question_subtopics**: Question-subtopic associations

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
```

## API Endpoints

### Dashboard
- `GET /dashboard/` - Main dashboard
- `POST /dashboard/filter` - Filter questions
- `GET /dashboard/api/topics/<subject_id>` - Get topics for subject
- `GET /dashboard/api/subtopics?topic_ids=1,2` - Get subtopics
- `GET /dashboard/files/<path>` - Serve question files

### Admin
- `GET /admin/topics` - Topic management
- `POST /admin/topics/add` - Add new topic
- `POST /admin/subtopics/add` - Add new subtopic
- `POST /admin/questions/<id>/update` - Update question metadata

### Generator
- `GET /generate/` - Generation options page
- `POST /generate/create` - Create and download document

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

## Support

For issues or questions:

1. Check documentation:
   - [SETUP.md](SETUP.md) - Installation guide
   - [TESTING.md](TESTING.md) - Testing procedures
2. Review error logs:
   - Terminal output
   - `ingest_errors.log`
   - Browser console (F12)
3. Check database in phpMyAdmin

## Version

**Version 1.0.0** - Initial Release

### Features in v1.0.0
- Complete question management system
- Advanced filtering and search
- Word document generation
- Admin panel for topics and tagging
- User authentication
- File ingestion from organized folders
- Multi-language support (EN, CH, BI)
- Natural sorting of questions

---

**Developed with Flask, Bootstrap, and HTMX**

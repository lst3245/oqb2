# Online Question Bank System - Setup Guide

## Prerequisites

- Python 3.8 or higher
- MariaDB 10.x or MySQL 8.x
- pip (Python package manager)

## Installation Steps

### 1. Clone or Download the Project

Navigate to the project directory:
```bash
cd "D:\Online Question Bank\oqb2"
```

### 2. Create Virtual Environment

```bash
python -m venv venv
```

Activate the virtual environment:

**Windows (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
```

**Windows (CMD):**
```cmd
venv\Scripts\activate.bat
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Database

Create a new database in MariaDB:

```sql
CREATE DATABASE oqb2 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Create a `.env` file in the project root with your database credentials:

```env
# Database Configuration
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password_here
DB_NAME=oqb2

# Flask Configuration
SECRET_KEY=your-secret-key-change-this-in-production
FLASK_ENV=development
FLASK_DEBUG=1

# Application Settings
SOURCE_PATH=D:/Online Question Bank/oqb2/Source
OUTPUT_PATH=D:/Online Question Bank/oqb2/output
```

**Important:** Replace `your_password_here` with your actual MariaDB root password.

### 5. Initialize Database

Run the initialization script to create tables and default data:

```bash
python init_db.py
```

This will:
- Create all database tables
- Insert default subjects (MATC, MAT1, MAT2, ICT)
- Create sample topics and subtopics for MATC
- Create default admin user (username: `admin`, password: `admin123`)

### 6. Prepare Source Files

Create the `Source` directory structure for your question files:

```
Source/
├── MATC/
│   ├── PP/
│   │   ├── DSE/
│   │   │   ├── 2024/
│   │   │   │   ├── P1/
│   │   │   │   │   ├── MATC_DSE_2024_P1_Q1_EN_QUE.png
│   │   │   │   │   ├── MATC_DSE_2024_P1_Q1_EN_ANS.png
│   │   │   │   │   └── ...
│   │   │   │   └── P2/
│   │   │   └── 2025/
│   │   └── CE/
│   └── QB/
│       └── MathSmart2024/
│           ├── MATC_QB_MATHSMART2024_Q1_EN_QUE.png
│           └── ...
└── MAT1/
    └── ...
```

### 7. Ingest Question Files

Run the ingestor to scan and import all question files:

```bash
python cli.py ingest
```

Or specify a custom source path:

```bash
python cli.py ingest --source-path "D:/Your/Custom/Path/Source"
```

The ingestor will:
- Scan all files recursively
- Parse filenames to extract metadata
- Create question records in the database
- Link asset files to questions
- Log any errors to `ingest_errors.log`

### 8. Run the Application

Start the Flask development server:

```bash
python run.py
```

The application will be available at: **http://localhost:5000**

### 9. Login

Use the default admin credentials:
- **Username:** `admin`
- **Password:** `admin123`

**Important:** Change this password after first login!

## Usage

### Dashboard
- Browse and filter questions by subject, source, year, topic, level, etc.
- Preview questions and answers
- Select questions and generate Word documents

### Admin Panel

**Manage Topics:**
- Add, edit, or delete topics and subtopics for each subject
- Organize topics hierarchically

**Tag Questions:**
- Browse questions and assign metadata
- Set major topic, minor topics, and subtopics
- Assign difficulty levels (1, 2, 3)
- Set question type (MC, CQ)

### Generate Documents

1. Filter and select questions on the dashboard
2. Click "Generate Document"
3. Choose options:
   - Sort order (by ID, level, year, topic)
   - Answer mode (questions only, with answers, with solutions, etc.)
   - Formatting options (spacing, page breaks, show IDs)
4. Download the generated Word document

## Troubleshooting

### Database Connection Error
- Check that MariaDB is running
- Verify credentials in `.env` file
- Ensure database `oqb2` exists

### Import Error: No module named 'app'
- Make sure virtual environment is activated
- Run `pip install -r requirements.txt` again

### Ingestor Not Finding Files
- Check `SOURCE_PATH` in `.env` file
- Verify file naming follows the correct format
- Check `ingest_errors.log` for details

### Images Not Displaying
- Verify file paths in database match actual file locations
- Check that SOURCE_PATH is correctly configured

## File Naming Convention

### Past Paper (PP) Files
Format: `SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE.EXT`

Example: `MATC_DSE_2025_P2_Q5_EN_QUE.png`

- SUBJ: Subject code (MATC, MAT1, MAT2, ICT)
- SOURCE: DSE, CE, or AL
- YEAR: 4-digit year
- PAPER: P1, P2, etc.
- QNO: Q1, Q2, Q10, etc.
- LANG: EN, CH, or BI (bilingual)
- TYPE: QUE (question), ANS (answer), SOL (solution)
- EXT: png, jpg, docx, etc.

### Question Bank (QB) Files
Format: `SUBJ_QB_DETAIL_QNO_LANG_TYPE.EXT`

Example: `MATC_QB_MATHSMART2024_Q1_EN_QUE.png`

## Re-ingesting Files

You can run the ingestor multiple times. It will:
- Skip existing questions (by QID)
- Update file paths if changed
- Add new questions and assets

## Production Deployment

For production use:
1. Change `SECRET_KEY` in `.env` to a strong random value
2. Set `FLASK_ENV=production` and `FLASK_DEBUG=0`
3. Change default admin password
4. Use a production WSGI server (gunicorn, uWSGI)
5. Set up proper backups for database and source files

## Support

For issues or questions, check the logs:
- Flask output in terminal
- `ingest_errors.log` for ingestion problems

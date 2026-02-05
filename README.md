# Online Question Bank System

**Version 2.1.0** | Last Updated: February 5, 2026

A comprehensive Flask-based web application for managing, filtering, and generating question papers from an organized repository of educational content.

---

## Quick Start

### Windows (Recommended)

```cmd
# 1. Run quick start script
quickstart.bat

# 2. Configure database
copy env_template.txt .env
# Edit .env with your MariaDB credentials

# 3. Initialize database
python init_db.py

# 4. Start application
python run.py

# 5. Access system
# Open browser to: http://localhost:5000
# Login: admin / admin123
```

### Manual Setup

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or venv\Scripts\activate  # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp env_template.txt .env
# Edit .env with your settings

# 4. Initialize database
python init_db.py

# 5. Run application
python run.py
```

---

## Key Features

### Question Management
- 📥 **Automated Ingestion**: Scan and import from organized file structures
- 🔄 **Database Sync**: Remove orphaned records when files deleted
- 🏷️ **Flexible Tagging**: Topics, subtopics, difficulty levels
- 🌐 **Multi-Language**: English, Chinese, and Bilingual support
- 📊 **Statistics**: Track correct percentages from public exams
- 🔍 **QID Search**: Direct search with wildcard support

### Advanced Filtering
- Filter by subject, source, year, paper, section
- Topic filtering with AND/OR logic and cross-topic search
- Multi-level sorting (e.g., Topic → Level → Year)
- Natural ordering (Q1, Q2, Q10 - not Q1, Q10, Q2)
- Configurable page size (10/20/50/100 items)
- Language preference (English or Chinese priority)

### Document Generation
- 📄 Generate custom Word documents from selected questions
- 5 answer modes (questions only, with answers, with solutions, etc.)
- Smart spacing control (separate MC/CQ settings)
- Multi-level custom sorting
- Language preference with automatic fallback
- Optional QID and correct percentage display
- A4 page size with proper formatting

### Admin Features
- 🛠️ **Topic Management**: Full CRUD operations for topics and subtopics
- ✏️ **Question Tagging**: Edit metadata, assign topics, set difficulty
- 👥 **User Management**: Create users with role-based permissions
- 📦 **Batch Operations**: Update or delete multiple questions at once
- 🔗 **Chapter Management**: Organize by textbook chapters
- 🎯 **Subject Permissions**: Fine-grained access control

### Security
- 🔐 User authentication with Flask-Login
- 👤 Role-based access control (Super Admin, Admin, User)
- 🔒 Subject-level permissions
- 🛡️ Protected file serving

---

## Documentation

### 📘 For End Users
**[USER_MANUAL.md](USER_MANUAL.md)** - Complete user guide
- Getting started and login
- Dashboard and filtering
- Document generation
- Tips and best practices
- Troubleshooting
- Quick reference

### 🔧 For Administrators
**[ADMIN_GUIDE.md](ADMIN_GUIDE.md)** - System administration guide
- Installation and setup
- Configuration
- File management
- Admin features
- User management
- Database maintenance
- Backup and recovery
- Testing procedures

### 💻 For Developers
**[DEVELOPER_SPEC.md](DEVELOPER_SPEC.md)** - Technical specification
- Architecture overview
- Database schema
- Data models
- API endpoints
- Extension guide
- Development workflow
- Deployment

### 📝 Version History
**[CHANGELOG.md](CHANGELOG.md)** - Version history and upgrade guide

---

## Tech Stack

**Backend**:
- Python 3.8+
- Flask 3.0.0
- SQLAlchemy 3.1.1
- MariaDB / MySQL
- python-docx 1.1.0

**Frontend**:
- Bootstrap 5
- HTMX 1.9.10
- Bootstrap Icons

---

## System Requirements

**Minimum**:
- Python 3.8+
- MariaDB 10.x or MySQL 8.x
- 4 GB RAM
- 10 GB storage

**Recommended**:
- Python 3.10+
- MariaDB 10.x
- 8 GB RAM
- 50 GB SSD

---

## Project Structure

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
│   └── utils.py           # Helper functions
├── templates/             # HTML templates
├── static/                # CSS, JS files
├── Source/                # Question files (your content)
├── output/                # Generated documents
├── cli.py                # CLI commands
├── init_db.py            # Database initialization
├── run.py                # Application entry point
├── requirements.txt      # Python dependencies
├── USER_MANUAL.md        # User documentation
├── ADMIN_GUIDE.md        # Admin documentation
├── DEVELOPER_SPEC.md     # Developer documentation
└── CHANGELOG.md          # Version history
```

---

## File Naming Convention

### Past Paper Format
```
SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE.EXT
Example: MATC_DSE_2024_P1_Q5_EN_QUE.png
```

### Question Bank Format
```
SUBJ_QB_DETAIL_QNO_LANG_TYPE.EXT
Example: MATC_QB_MATHSMART2024_Q1_EN_QUE.png
```

**Components**:
- **SUBJ**: Subject code (MATC, MAT1, MAT2, ICT)
- **SOURCE**: DSE, CE, or AL (for past papers)
- **YEAR**: 4-digit year (2024, 2025)
- **PAPER**: P1, P2, etc.
- **QNO**: Q1, Q2, Q10, etc.
- **LANG**: EN (English), CH (Chinese), BI (Bilingual)
- **TYPE**: QUE (Question), ANS (Answer), SOL (Solution)
- **EXT**: png, jpg, docx, etc.

---

## Common Tasks

### Import Questions
```bash
# Place files in Source/ directory
# Then run:
python cli.py ingest

# Check for errors:
cat ingest_errors.log
```

### Clean Database
```bash
# Preview what would be deleted
python cli.py sync

# Actually delete orphaned records
python cli.py sync --no-dry-run
```

### Start Application
```bash
# Development mode
python run.py

# Production (with gunicorn)
gunicorn -w 4 -b 0.0.0.0:5000 'app:create_app()'
```

---

## Troubleshooting

### Quick Fixes

| Problem | Solution |
|---------|----------|
| Can't login | Check password, restart server |
| Images not showing | Verify SOURCE_PATH in .env |
| Import skips files | Check file naming convention |
| Database error | Ensure MariaDB running, check credentials |
| Port in use | Kill process or change port in run.py |

### Getting Help

1. Check documentation (USER_MANUAL.md, ADMIN_GUIDE.md)
2. Review error logs (terminal output, ingest_errors.log)
3. Check database (phpMyAdmin or MySQL client)
4. Review CHANGELOG.md for recent changes

---

## Security Checklist

- [ ] Change default admin password
- [ ] Set strong SECRET_KEY in .env
- [ ] Use strong database password
- [ ] Set FLASK_DEBUG=0 in production
- [ ] Enable HTTPS in production
- [ ] Configure firewall
- [ ] Set up regular backups
- [ ] Review user permissions

---

## Production Deployment

**Key Steps**:
1. Set environment to production in .env
2. Use strong SECRET_KEY
3. Deploy with production WSGI server (gunicorn)
4. Set up reverse proxy (nginx/Apache)
5. Enable HTTPS/SSL
6. Configure regular backups
7. Set up monitoring

**See ADMIN_GUIDE.md for detailed deployment instructions.**

---

## Features by Version

### Version 2.1.0 (Current)
✅ Correct percentage tracking and display  
✅ Sortable by correct percentage  
✅ Batch update correct percentage  
✅ Show percentage in documents  

### Version 2.0.0
✅ Multi-level sorting  
✅ Batch operations  
✅ Database sync  
✅ Smart MC/CQ spacing  
✅ QID wildcard search  
✅ Topic AND/OR modes  
✅ Language preferences  
✅ Subject-based permissions  
✅ Chapter management  

### Version 1.0.0
✅ Core question management  
✅ File ingestion  
✅ Advanced filtering  
✅ Document generation  
✅ Topic management  
✅ User authentication  

---

## API Overview

### Authentication
- `POST /login` - User login
- `GET /logout` - User logout

### Dashboard
- `GET /dashboard/` - Main dashboard
- `GET /dashboard/filter` - Filter questions (HTMX)
- `GET /dashboard/api/topics/<subject_id>` - Get topics
- `GET /dashboard/api/subtopics` - Get subtopics
- `GET /dashboard/files/<path>` - Serve files (protected)

### Admin
- `GET /admin/topics` - Topic management
- `POST /admin/topics/add` - Add topic
- `POST /admin/questions/<id>/update` - Update question
- `POST /admin/questions/batch-update` - Batch update
- `POST /admin/questions/delete` - Batch delete

### Generator
- `GET /generate/` - Generation options
- `POST /generate/create` - Create document

**See DEVELOPER_SPEC.md for complete API documentation.**

---

## CLI Commands

```bash
# Ingest questions from Source directory
python cli.py ingest

# Ingest from custom path
python cli.py ingest --source-path "/path/to/source"

# Preview database sync (dry-run)
python cli.py sync

# Execute database sync (delete orphans)
python cli.py sync --no-dry-run

# Initialize database
python init_db.py

# Start application
python run.py
```

---

## Contributing

1. Read DEVELOPER_SPEC.md for technical details
2. Follow code style guidelines
3. Write tests for new features
4. Update documentation
5. Submit pull request

---

## Support

**Documentation**:
- [USER_MANUAL.md](USER_MANUAL.md) - User guide
- [ADMIN_GUIDE.md](ADMIN_GUIDE.md) - Admin guide
- [DEVELOPER_SPEC.md](DEVELOPER_SPEC.md) - Technical spec
- [CHANGELOG.md](CHANGELOG.md) - Version history

**Resources**:
- Check error logs
- Review database
- Restart application
- Clear browser cache

---

## License

Copyright © 2024-2026. All rights reserved.

This system is for internal use only.

---

## Acknowledgments

Built with:
- [Flask](https://flask.palletsprojects.com/)
- [SQLAlchemy](https://www.sqlalchemy.org/)
- [Bootstrap](https://getbootstrap.com/)
- [HTMX](https://htmx.org/)
- [python-docx](https://python-docx.readthedocs.io/)

---

**For detailed information, see the comprehensive documentation:**
- 📘 **[USER_MANUAL.md](USER_MANUAL.md)** - For end users
- 🔧 **[ADMIN_GUIDE.md](ADMIN_GUIDE.md)** - For administrators
- 💻 **[DEVELOPER_SPEC.md](DEVELOPER_SPEC.md)** - For developers

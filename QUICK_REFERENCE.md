# Quick Reference Guide

One-page reference for common tasks in the Online Question Bank System v2.0

---

## 🚀 Quick Start

```bash
# Start the application
python run.py

# Open browser
http://localhost:5000

# Login
Username: admin
Password: admin123
```

---

## 📝 Common Tasks

### 1. Import New Questions

```bash
# Place files in Source/ directory following naming convention
# Then run:
python cli.py ingest

# Check for errors
cat ingest_errors.log
```

### 2. Find Specific Questions

**By QID:**
- Dashboard → QID Search field
- Type: `MATC_DSE_2024*` (wildcards supported)

**By Criteria:**
- Subject: MATC
- Source: DSE  
- Year: 2024
- Topic: Calculus
- Level: 2

### 3. Generate Practice Set

1. Filter questions (Dashboard)
2. Select desired questions
3. Click "Generate Document"
4. Settings:
   - Sort: Topic → Level
   - Answer: Question + Solution
   - Spacing: 2 lines after each
   - Show QID: ON
5. Generate & Download

### 4. Generate Exam Paper

1. Filter and select questions
2. Click "Generate Document"
3. Settings:
   - Sort: Custom (or Selection order)
   - Answer: All Questions, Then All Answers
   - MC: After = 1 line
   - CQ: Before = New page
   - Show QID: OFF (or ON for reference)
5. Generate & Download

### 5. Tag Multiple Questions

1. Filter questions (e.g., DSE 2024 P1)
2. Select all on page
3. Click "Batch Update"
4. Set:
   - Level: 2
   - Type: CQ
   - Topic: Calculus
5. Apply to Selected

### 6. Clean Up Database

```bash
# Preview what would be deleted
python cli.py sync

# Actually delete orphaned records
python cli.py sync --no-dry-run
```

---

## 📋 File Naming Cheat Sheet

### Past Paper
```
MATC_DSE_2024_P1_Q5_EN_QUE.png
└─┬┘ └┬┘ └─┬┘ └┬ └┬ └┬ └─┬ └─┬┘
  │   │    │   │  │  │   │   └─ Extension
  │   │    │   │  │  │   └───── Type (QUE/ANS/SOL)
  │   │    │   │  │  └─────────Language (EN/CH/BI)
  │   │    │   │  └────────────Question (Q1, Q2, ...)
  │   │    │   └───────────────Paper (P1, P2)
  │   │    └───────────────────Year (4 digits)
  │   └────────────────────────Source (DSE/CE/AL)
  └────────────────────────────Subject
```

### Question Bank
```
MATC_QB_MATHSMART2024_Q1_EN_QUE.png
└─┬┘ └┬ └──────┬─────┘ └┬ └┬ └─┬ └─┬┘
  │   │        │        │  │   │   └─ Extension
  │   │        │        │  │   └───── Type
  │   │        │        │  └─────────Language
  │   │        │        └────────────Question
  │   │        └─────────────────────Detail (no underscores!)
  │   └──────────────────────────────"QB" literal
  └──────────────────────────────────Subject
```

**Valid**: `MATC_QB_MATHSMART2024_Q1_EN_QUE.png`  
**Invalid**: `MATC_QB_MATH_SMART_2024_Q1_EN_QUE.png` (underscores in detail)

---

## 🎯 Filter Combinations

### All 2024 DSE Questions
- Subject: MATC
- Source: DSE
- Years: ☑ 2024

### Difficult Calculus Questions
- Subject: MATC
- Topics: ☑ Calculus
- Level: ☑ 3

### Untagged Questions
- Level: ☑ Not Assigned

### Cross-topic Questions (Calculus + Algebra)
- Topics: ☑ Calculus ☑ Algebra
- Topic Mode: AND
- Cross-topic: ☑ ON

### All MC Questions
- Q Type: MC

### Section A Questions Only
- Section: A

---

## 🔧 Admin Quick Actions

### Add New Topic
1. Admin → Manage Topics
2. Find subject
3. Click "Add Topic"
4. Enter name → Save

### Add New Subtopic
1. Admin → Manage Topics
2. Find topic
3. Click "Add Subtopic"
4. Enter name → Save

### Tag a Question
1. Admin → Tag Questions (or Dashboard)
2. Find question
3. Click "Edit Tags"
4. Set metadata → Save

### Batch Tag Questions
1. Filter questions
2. Select all
3. Click "Batch Update"
4. Choose fields & values
5. Apply

### Delete Bad Questions
1. Filter to find them
2. Select with checkboxes
3. Click "Batch Delete"
4. Confirm

---

## 💡 Pro Tips

### Efficient Tagging
- Tag by paper (e.g., all 2024 P1)
- Use batch update for common fields
- Set level after reviewing difficulty

### Better Searches
- Use wildcards: `*2024*` finds all 2024 questions
- Combine filters: Subject + Source + Year + Topic
- Enable cross-topic for comprehensive results

### Document Generation
- Test with 5-10 questions first
- Adjust spacing based on question type
- Use selection order for custom arrangement

### Performance
- Use page size 20-50 for normal browsing
- Be specific with filters
- Run sync monthly to clean database

### Organization
- Consistent topic names
- Use descriptions for special cases
- Keep subtopics focused

---

## 🐛 Quick Fixes

| Problem | Solution |
|---------|----------|
| Can't login | Check password, restart server, check DB |
| Images not showing | Check SOURCE_PATH in .env |
| Ingestion skips files | Check naming exactly, see ingest_errors.log |
| No questions found | Relax filters or check if tagged |
| Generation fails | Check output/ exists, verify files present |
| Slow filtering | Reduce page size, be more specific |
| Duplicate questions | Run sync to clean up |

---

## 📊 Sort Examples

### Natural Order (Default)
```json
[{"field": "qid", "direction": "asc"}]
```
Result: Q1, Q2, Q3, ..., Q10, Q11

### By Difficulty Then Topic
```json
[
  {"field": "level", "direction": "asc"},
  {"field": "topic", "direction": "asc"}
]
```

### By Year (Newest First)
```json
[{"field": "year", "direction": "desc"}]
```

### Topic → Subtopic → Level
```json
[
  {"field": "topic", "direction": "asc"},
  {"field": "subtopic", "direction": "asc"},
  {"field": "level", "direction": "asc"}
]
```

---

## 🔐 Security Checklist

- [ ] Changed default admin password
- [ ] Created users with appropriate roles
- [ ] Set strong SECRET_KEY in .env
- [ ] Backup database regularly
- [ ] Backup Source files
- [ ] Set FLASK_ENV=production for production

---

## 📞 Getting Help

1. Check error logs (terminal, ingest_errors.log)
2. Review documentation (README.md, USER_GUIDE.md)
3. Check database in phpMyAdmin
4. Restart application
5. Review CHANGELOG.md for recent changes

---

## 🗂️ Folder Structure

```
oqb2/
├── app/              # Application code
├── templates/        # HTML templates
├── static/           # CSS, JS
├── Source/           # YOUR QUESTION FILES GO HERE
│   └── MATC/
│       ├── PP/
│       │   └── DSE/
│       │       └── 2024/
│       │           └── P1/
│       └── QB/
│           └── MathSmart2024/
├── output/           # Generated documents
├── .env              # Configuration (create from env_template.txt)
├── run.py            # Start application
├── cli.py            # CLI commands
└── init_db.py        # Database initialization
```

---

## 📚 Documentation Files

- **README.md** - Project overview and features
- **SETUP.md** - Installation and configuration
- **USER_GUIDE.md** - Comprehensive usage guide
- **TESTING.md** - Testing procedures
- **PROJECT_SUMMARY.md** - Technical summary
- **CHANGELOG.md** - Version history
- **QUICK_REFERENCE.md** - This file

---

## CLI Commands Reference

```bash
# Ingest from default path
python cli.py ingest

# Ingest from custom path
python cli.py ingest --source-path "D:/Path/To/Source"

# Preview sync (dry-run)
python cli.py sync

# Execute sync (delete orphans)
python cli.py sync --no-dry-run

# Sync with custom path
python cli.py sync --source-path "D:/Path/To/Source" --no-dry-run

# Initialize database
python init_db.py

# Start application
python run.py
```

---

## Environment Variables

```env
# Database
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=oqb2

# Flask
SECRET_KEY=change-this-to-random-string
FLASK_ENV=development
FLASK_DEBUG=1

# Paths
SOURCE_PATH=./Source
OUTPUT_PATH=./output
```

---

## Version Info

- **Current Version**: 2.0.0
- **Release Date**: January 9, 2026
- **Python**: 3.8+
- **Database**: MariaDB 10.x or MySQL 8.x
- **Framework**: Flask 3.0.0

---

**Print this page for quick reference at your desk!**

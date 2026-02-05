# Online Question Bank System - Developer Specification

**Version 2.1.0** | Last Updated: February 5, 2026

Complete technical specification for developers working on or extending the Online Question Bank System.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Technology Stack](#technology-stack)
3. [Project Structure](#project-structure)
4. [Database Schema](#database-schema)
5. [Data Models](#data-models)
6. [Application Components](#application-components)
7. [API Endpoints](#api-endpoints)
8. [Frontend Architecture](#frontend-architecture)
9. [File Processing](#file-processing)
10. [Document Generation](#document-generation)
11. [Authentication & Authorization](#authentication--authorization)
12. [Configuration](#configuration)
13. [Extension Guide](#extension-guide)
14. [Deployment](#deployment)
15. [Development Workflow](#development-workflow)

---

## Architecture Overview

### System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Web Browser                          │
│  (HTML/CSS/JavaScript + Bootstrap 5 + HTMX)               │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP/HTTPS
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    Flask Application                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Blueprints                                          │  │
│  │  ├─ auth_bp        (Authentication)                 │  │
│  │  ├─ dashboard_bp   (Question Browser)               │  │
│  │  ├─ admin_bp       (Admin Panel)                    │  │
│  │  └─ generator_bp   (Document Generation)            │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ORM Layer (SQLAlchemy)                             │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
       ▼                 ▼                 ▼
┌────────────┐    ┌────────────┐    ┌────────────┐
│  MariaDB   │    │   Source   │    │   Output   │
│  Database  │    │   Files    │    │   Files    │
│            │    │  (Images,  │    │  (Word     │
│            │    │   Docs)    │    │   Docs)    │
└────────────┘    └────────────┘    └────────────┘
```

### Design Patterns

**Application Factory Pattern**:
- `create_app()` function creates and configures Flask application
- Allows multiple instances for testing/production
- Enables extension initialization

**Blueprint Pattern**:
- Modular organization by functionality
- Each blueprint handles related routes
- Easy to maintain and extend

**Repository Pattern** (ORM):
- SQLAlchemy models abstract database operations
- Business logic separated from data access
- Type-safe operations

**MVC-like Structure**:
- Models: `app/models.py`
- Views: HTML templates in `templates/`
- Controllers: Blueprint route handlers

### Request Flow

1. **Request Received**: Browser sends HTTP request
2. **Routing**: Flask routes to appropriate blueprint handler
3. **Authentication**: Flask-Login verifies user session
4. **Authorization**: Check user permissions (if required)
5. **Business Logic**: Controller processes request
6. **Database Operations**: ORM queries database
7. **Template Rendering**: Jinja2 renders HTML
8. **Response**: Send HTML/JSON/File to browser

### Key Components

- **Flask**: Web framework and request handling
- **SQLAlchemy**: ORM for database operations
- **Flask-Login**: User session management
- **Jinja2**: Template rendering
- **python-docx**: Word document generation
- **Pillow**: Image processing
- **HTMX**: Dynamic page updates without JavaScript

---

## Technology Stack

### Backend

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.8+ | Application language |
| Flask | 3.0.0 | Web framework |
| Flask-SQLAlchemy | 3.1.1 | Database ORM |
| Flask-Login | 0.6.3 | Authentication |
| PyMySQL | 1.1.0 | MySQL database driver |
| python-docx | 1.1.0 | Word document creation |
| docxcompose | 1.4.0 | Document composition |
| Pillow | 10.1.0 | Image processing |
| python-dotenv | 1.0.0 | Environment configuration |
| natsort | 8.4.0 | Natural sorting |
| cryptography | 41.0.7 | Secure operations |
| click | 8.1.7 | CLI framework |

### Frontend

| Component | Version | Purpose |
|-----------|---------|---------|
| Bootstrap | 5.3.0 | UI framework |
| HTMX | 1.9.10 | Dynamic updates |
| Bootstrap Icons | 1.11.0 | Icon library |

### Database

| Component | Version | Purpose |
|-----------|---------|---------|
| MariaDB | 10.x | Primary database |
| MySQL | 8.x | Alternative database |

### Development Tools

- **Git**: Version control
- **VS Code**: Recommended IDE
- **Python venv**: Virtual environment
- **pip**: Package management
- **phpMyAdmin**: Database management (optional)

---

## Project Structure

```
oqb2/
├── app/                          # Flask application package
│   ├── __init__.py              # App factory, extension init
│   ├── config.py                # Configuration class
│   ├── models.py                # SQLAlchemy models (8 models)
│   ├── auth.py                  # Authentication blueprint
│   ├── dashboard.py             # Question browser blueprint
│   ├── admin.py                 # Admin panel blueprint
│   ├── generator.py             # Document generation blueprint
│   ├── ingestor.py              # File scanner & import
│   └── utils.py                 # Helper functions
│
├── templates/                    # Jinja2 HTML templates
│   ├── base.html                # Base template (Bootstrap 5)
│   ├── login.html               # Login page
│   ├── register.html            # User registration
│   ├── dashboard.html           # Main question browser
│   ├── viewer.html              # Question viewer (if separate)
│   ├── generate.html            # Generation options
│   ├── admin_index.html         # Admin dashboard
│   ├── admin_topics.html        # Topic management
│   ├── admin_users.html         # User management
│   ├── admin_chapters.html      # Chapter management
│   └── partials/                # HTMX partial templates
│       └── question_list.html   # Question cards + pagination
│
├── static/                       # Static assets
│   ├── css/                     # Custom CSS (if any)
│   └── js/                      # Custom JavaScript (if any)
│
├── Source/                       # Question files (not in repo)
│   ├── MATC/
│   │   ├── PP/
│   │   │   ├── DSE/
│   │   │   │   ├── 2024/
│   │   │   │   │   ├── P1/
│   │   │   │   │   └── P2/
│   │   │   │   └── 2025/
│   │   │   ├── CE/
│   │   │   └── AL/
│   │   └── QB/
│   │       └── MathSmart2024/
│   ├── MAT1/
│   ├── MAT2/
│   └── ICT/
│
├── output/                       # Generated Word documents
│   └── questions_YYYYMMDD_HHMMSS.docx
│
├── venv/                         # Virtual environment (not in repo)
│
├── .env                          # Environment variables (not in repo)
├── .gitignore                    # Git ignore rules
├── cli.py                        # CLI commands
├── debug_env.py                  # Configuration debug tool
├── env_template.txt              # Environment template
├── import_dse_p2.py              # DSE P2 import script
├── init_db.py                    # Database initialization
├── quickstart.bat                # Windows quick setup
├── requirements.txt              # Python dependencies
├── run.py                        # Application entry point
├── tag_topics.py                 # Topic tagging script
├── test_db.py                    # Database test script
│
├── USER_MANUAL.md                # End-user documentation
├── ADMIN_GUIDE.md                # Administrator documentation
├── DEVELOPER_SPEC.md             # This file
├── CHANGELOG.md                  # Version history
├── TOPIC_TAGGING_SUCCESS.txt     # Topic tagging log
└── ingest_errors.log             # Ingestion error log

Total Files: ~50+
Total Lines of Code: ~5,000+
```

### Key Directories

**app/**: Application core
- All Flask code
- Models, blueprints, utilities
- Business logic

**templates/**: HTML templates
- Jinja2 templates
- Base template with Bootstrap
- Partials for HTMX

**static/**: Static assets
- CSS, JavaScript, images (if any)
- Served directly by Flask

**Source/**: Question files
- Not in version control
- Organized by subject/source/year
- Scanned by ingestor

**output/**: Generated documents
- Created by generator
- Timestamped filenames
- Can be cleaned periodically

---

## Database Schema

### Entity Relationship Diagram

```
┌─────────────┐
│   subjects  │
│ ┌─────────┐ │
│ │id (PK)  │ │
│ │name     │ │
│ └─────────┘ │
└──────┬──────┘
       │
       │ 1:N
       │
┌──────▼──────────────────────────────────┐
│              topics                     │
│ ┌────────────────────────────────────┐ │
│ │id (PK)                             │ │
│ │subject_id (FK → subjects.id)      │ │
│ │name                                │ │
│ │sort_order                          │ │
│ └────────────────────────────────────┘ │
└──────┬──────────────────────────────────┘
       │
       │ 1:N
       │
┌──────▼──────────────────────────────────┐
│             subtopics                   │
│ ┌────────────────────────────────────┐ │
│ │id (PK)                             │ │
│ │topic_id (FK → topics.id)          │ │
│ │name                                │ │
│ │hidden                              │ │
│ │sort_order                          │ │
│ └────────────────────────────────────┘ │
└─────────────────────────────────────────┘

┌─────────────┐
│    users    │
│ ┌─────────┐ │
│ │id (PK)  │ │
│ │username │ │
│ │password │ │
│ │is_admin │ │
│ │is_super │ │
│ └─────────┘ │
└──────┬──────┘
       │
       │ 1:N
       │
┌──────▼──────────────────────────────────┐
│    user_subject_permissions             │
│ ┌────────────────────────────────────┐ │
│ │id (PK)                             │ │
│ │user_id (FK → users.id)            │ │
│ │subject_id (FK → subjects.id)      │ │
│ │role (user/admin)                   │ │
│ └────────────────────────────────────┘ │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│              questions                  │
│ ┌────────────────────────────────────┐ │
│ │id (PK)                             │ │
│ │qid (UNIQUE)                        │ │
│ │subject (FK → subjects.id)         │ │
│ │source                              │ │
│ │year                                │ │
│ │paper                               │ │
│ │section                             │ │
│ │qno                                 │ │
│ │q_type                              │ │
│ │level                               │ │
│ │major_topic_id (FK → topics.id)   │ │
│ │major_subtopic_id (FK)             │ │
│ │chapter_id (FK)                     │ │
│ │description                         │ │
│ │correct_percentage                  │ │
│ │created_at                          │ │
│ └────────────────────────────────────┘ │
└──────┬──────────────────────────────────┘
       │
       │ 1:N
       │
┌──────▼──────────────────────────────────┐
│          question_assets                │
│ ┌────────────────────────────────────┐ │
│ │id (PK)                             │ │
│ │question_id (FK → questions.id)    │ │
│ │asset_type (QUE/ANS/SOL)          │ │
│ │file_format (IMG/DOC)              │ │
│ │language (EN/CH/BI)                │ │
│ │file_path                           │ │
│ └────────────────────────────────────┘ │
└─────────────────────────────────────────┘

Many-to-Many Relationships:

┌─────────────────────────────────────────┐
│      question_minor_topics (M:M)        │
│ ┌────────────────────────────────────┐ │
│ │question_id (FK → questions.id)    │ │
│ │topic_id (FK → topics.id)          │ │
│ └────────────────────────────────────┘ │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│       question_subtopics (M:M)          │
│ ┌────────────────────────────────────┐ │
│ │question_id (FK → questions.id)    │ │
│ │subtopic_id (FK → subtopics.id)   │ │
│ └────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

### Table Specifications

#### users

User accounts and authentication.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INT | PK, AUTO_INCREMENT | User ID |
| username | VARCHAR(80) | UNIQUE, NOT NULL | Login username |
| password_hash | VARCHAR(255) | NOT NULL | Hashed password |
| is_admin | BOOLEAN | NOT NULL, DEFAULT 0 | Legacy admin flag |
| is_super_admin | BOOLEAN | NOT NULL, DEFAULT 0 | Super admin flag |
| created_at | DATETIME | NOT NULL | Account creation time |

**Indexes**: username (UNIQUE)

#### user_subject_permissions

Fine-grained subject permissions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INT | PK, AUTO_INCREMENT | Permission ID |
| user_id | INT | FK → users.id, NOT NULL | User reference |
| subject_id | VARCHAR(10) | FK → subjects.id, NOT NULL | Subject reference |
| role | VARCHAR(10) | NOT NULL, DEFAULT 'user' | 'user' or 'admin' |

**Indexes**: user_id, subject_id
**Unique**: (user_id, subject_id)

#### subjects

Subject definitions (MATC, MAT1, etc.).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | VARCHAR(10) | PK | Subject code (e.g., 'MATC') |
| name | VARCHAR(100) | NOT NULL | Subject full name |

#### topics

Main topic categories.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INT | PK, AUTO_INCREMENT | Topic ID |
| subject_id | VARCHAR(10) | FK → subjects.id, NOT NULL | Subject reference |
| name | VARCHAR(200) | NOT NULL | Topic name |
| sort_order | INT | NOT NULL, DEFAULT 0 | Display order |

**Indexes**: subject_id

#### subtopics

Specific skills within topics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INT | PK, AUTO_INCREMENT | Subtopic ID |
| topic_id | INT | FK → topics.id, NOT NULL | Topic reference |
| name | VARCHAR(200) | NOT NULL | Subtopic name |
| hidden | BOOLEAN | NOT NULL, DEFAULT 0 | Hidden flag |
| sort_order | INT | NOT NULL, DEFAULT 0 | Display order |

**Indexes**: topic_id

#### chapters

Textbook chapter organization.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INT | PK, AUTO_INCREMENT | Chapter ID |
| subject_id | VARCHAR(10) | FK → subjects.id, NOT NULL | Subject reference |
| name | VARCHAR(200) | NOT NULL | Chapter name |
| sort_order | INT | NOT NULL, DEFAULT 0 | Display order |

**Indexes**: subject_id

#### questions

Logical question records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INT | PK, AUTO_INCREMENT | Question ID |
| qid | VARCHAR(255) | UNIQUE, NOT NULL | Question identifier |
| subject | VARCHAR(10) | FK → subjects.id, NOT NULL | Subject code |
| source | VARCHAR(10) | NOT NULL | DSE/CE/AL/QB |
| year | INT | NULL | Year (for PP) |
| paper | VARCHAR(10) | NULL | Paper (P1, P2, etc.) |
| section | VARCHAR(10) | NULL | Section (A, B, etc.) |
| qno | VARCHAR(10) | NOT NULL | Question number |
| q_type | VARCHAR(5) | NULL | MC or CQ |
| level | INT | NULL | 1, 2, or 3 |
| major_topic_id | INT | FK → topics.id, NULL | Primary topic |
| major_subtopic_id | INT | FK → subtopics.id, NULL | Primary subtopic |
| chapter_id | INT | FK → chapters.id, NULL | Chapter reference |
| description | TEXT | NULL | Optional description |
| correct_percentage | INT | NULL | Correct rate (0-100) |
| created_at | DATETIME | NOT NULL | Creation timestamp |

**Indexes**: qid (UNIQUE), subject, major_topic_id
**Constraints**: 
- major_subtopic must belong to major_topic (validated in application)
- correct_percentage between 0 and 100 if not NULL

#### question_assets

Physical files (QUE/ANS/SOL).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INT | PK, AUTO_INCREMENT | Asset ID |
| question_id | INT | FK → questions.id, NOT NULL | Question reference |
| asset_type | VARCHAR(10) | NOT NULL | QUE/ANS/SOL |
| file_format | VARCHAR(10) | NOT NULL | IMG or DOC |
| language | VARCHAR(10) | NOT NULL | EN/CH/BI |
| file_path | VARCHAR(500) | NOT NULL | Relative file path |

**Indexes**: question_id
**Cascade**: ON DELETE CASCADE (when question deleted, assets deleted)

#### question_minor_topics

Many-to-many: questions ↔ topics (cross-topic).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| question_id | INT | FK → questions.id, PK | Question reference |
| topic_id | INT | FK → topics.id, PK | Topic reference |

**Composite PK**: (question_id, topic_id)
**Cascade**: ON DELETE CASCADE

#### question_subtopics

Many-to-many: questions ↔ subtopics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| question_id | INT | FK → questions.id, PK | Question reference |
| subtopic_id | INT | FK → subtopics.id, PK | Subtopic reference |

**Composite PK**: (question_id, subtopic_id)
**Cascade**: ON DELETE CASCADE

### Relationships Summary

- **subjects** 1:N **topics**
- **topics** 1:N **subtopics**
- **subjects** 1:N **questions**
- **topics** 1:N **questions** (as major_topic)
- **subtopics** 1:N **questions** (as major_subtopic)
- **questions** M:N **topics** (minor topics)
- **questions** M:N **subtopics** (multiple subtopics)
- **questions** 1:N **question_assets**
- **users** 1:N **user_subject_permissions**
- **subjects** 1:N **user_subject_permissions**
- **subjects** 1:N **chapters**
- **chapters** 1:N **questions**

---

## Data Models

All models defined in `app/models.py`.

### User Model

```python
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_super_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    subject_permissions = db.relationship('UserSubjectPermission', 
                                         backref='user', 
                                         lazy='dynamic', 
                                         cascade='all, delete-orphan')
    
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if password matches"""
        return check_password_hash(self.password_hash, password)
    
    def has_subject_access(self, subject_id):
        """Check if user has access to subject"""
        if self.is_super_admin:
            return True
        return UserSubjectPermission.query.filter_by(
            user_id=self.id, subject_id=subject_id
        ).first() is not None
    
    def is_subject_admin(self, subject_id):
        """Check if user is admin for subject"""
        if self.is_super_admin:
            return True
        perm = UserSubjectPermission.query.filter_by(
            user_id=self.id, subject_id=subject_id
        ).first()
        return perm is not None and perm.role == 'admin'
```

**Key Methods**:
- `set_password(password)`: Hash and store password
- `check_password(password)`: Verify password
- `has_subject_access(subject_id)`: Check user access
- `is_subject_admin(subject_id)`: Check admin rights
- `get_accessible_subjects()`: List accessible subjects
- `has_any_admin_access()`: Check if admin anywhere

### Question Model

```python
class Question(db.Model):
    __tablename__ = 'questions'
    
    id = db.Column(db.Integer, primary_key=True)
    qid = db.Column(db.String(255), unique=True, nullable=False, index=True)
    subject = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    source = db.Column(db.String(10), nullable=False)
    year = db.Column(db.Integer)
    paper = db.Column(db.String(10))
    section = db.Column(db.String(10))
    qno = db.Column(db.String(10), nullable=False)
    q_type = db.Column(db.String(5))
    level = db.Column(db.Integer)
    major_topic_id = db.Column(db.Integer, db.ForeignKey('topics.id'), index=True)
    major_subtopic_id = db.Column(db.Integer, db.ForeignKey('subtopics.id'))
    chapter_id = db.Column(db.Integer, db.ForeignKey('chapters.id'))
    description = db.Column(db.Text)
    correct_percentage = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    assets = db.relationship('QuestionAsset', backref='question', lazy='dynamic', cascade='all, delete-orphan')
    minor_topics = db.relationship('Topic', secondary=question_minor_topics, backref='minor_questions', lazy='dynamic')
    subtopics = db.relationship('Subtopic', secondary=question_subtopics, backref='questions', lazy='dynamic')
```

**Key Properties**:
- `qid`: Unique identifier (e.g., MATC_DSE_2024_P1_Q5)
- `source`: DSE, CE, AL, or QB
- `year`: NULL for QB questions
- `level`: 1 (easy), 2 (medium), 3 (hard), or NULL
- `correct_percentage`: 0-100 or NULL

**Relationships**:
- `assets`: 1:N relationship to QuestionAsset
- `minor_topics`: M:N relationship for cross-topic
- `subtopics`: M:N relationship for multiple skills
- `major_topic`: N:1 relationship (backref from Topic)
- `major_subtopic`: N:1 relationship (backref from Subtopic)

### QuestionAsset Model

```python
class QuestionAsset(db.Model):
    __tablename__ = 'question_assets'
    
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id'), nullable=False, index=True)
    asset_type = db.Column(db.String(10), nullable=False)
    file_format = db.Column(db.String(10), nullable=False)
    language = db.Column(db.String(10), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
```

**Key Properties**:
- `asset_type`: QUE (question), ANS (answer), SOL (solution)
- `file_format`: IMG (image) or DOC (document)
- `language`: EN (English), CH (Chinese), BI (Bilingual)
- `file_path`: Relative to SOURCE_PATH

### Topic and Subtopic Models

```python
class Topic(db.Model):
    __tablename__ = 'topics'
    
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    
    # Relationships
    subtopics = db.relationship('Subtopic', backref='topic', lazy='dynamic', 
                               cascade='all, delete-orphan', order_by='Subtopic.sort_order')
    major_questions = db.relationship('Question', backref='major_topic', 
                                     foreign_keys='Question.major_topic_id', lazy='dynamic')

class Subtopic(db.Model):
    __tablename__ = 'subtopics'
    
    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey('topics.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    hidden = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
```

**Hidden Subtopics**: Used for organizational purposes (e.g., textbook chapters) but not displayed in main filtering.

### Complete Model List

1. **User**: User accounts
2. **UserSubjectPermission**: Subject-level permissions
3. **Subject**: Subject definitions
4. **Topic**: Main topics
5. **Subtopic**: Skills within topics
6. **Chapter**: Textbook organization
7. **Question**: Logical questions
8. **QuestionAsset**: Physical files
9. **question_minor_topics** (association table)
10. **question_subtopics** (association table)

---

## Application Components

### Application Factory (`app/__init__.py`)

```python
def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__, 
                template_folder='../templates',
                static_folder='../static')
    
    # Load configuration
    app.config.from_object(Config)
    
    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    
    # User loader
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(generator_bp)
    
    return app
```

**Key Points**:
- Creates fresh app instance
- Configures from Config class
- Initializes extensions (db, login_manager)
- Registers blueprints
- Returns configured app

### Configuration (`app/config.py`)

```python
class Config:
    """Base configuration"""
    
    # Flask settings
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key')
    
    # Database settings
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_NAME = os.getenv('DB_NAME', 'oqb2')
    
    SQLALCHEMY_DATABASE_URI = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}?charset=utf8mb4'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }
    
    # Application paths
    SOURCE_PATH = os.getenv('SOURCE_PATH', './Source')
    OUTPUT_PATH = os.getenv('OUTPUT_PATH', './output')
    
    # Pagination
    QUESTIONS_PER_PAGE = 20
```

**Configuration Sources**:
1. Environment variables (`.env` file)
2. Default values (fallback)

### Authentication Blueprint (`app/auth.py`)

Handles user authentication.

**Routes**:
- `GET /`: Redirect to dashboard or login
- `GET /login`: Display login form
- `POST /login`: Process login
- `GET /logout`: Logout user
- `GET /register`: Display registration form (admin only)
- `POST /register`: Create new user (admin only)

**Key Functions**:
- `login_required`: Decorator for protected routes
- `admin_required`: Decorator for admin-only routes
- `load_user()`: Flask-Login user loader

### Dashboard Blueprint (`app/dashboard.py`)

Main question browsing interface.

**Routes**:
- `GET /dashboard/`: Main dashboard
- `GET /dashboard/filter`: Filter questions (HTMX)
- `POST /dashboard/filter`: Filter questions (HTMX)
- `GET /dashboard/api/topics/<subject_id>`: Get topics for subject (JSON)
- `GET /dashboard/api/subtopics`: Get subtopics for topics (JSON)
- `GET /dashboard/api/years/<subject_id>/<source>`: Get available years (JSON)
- `GET /dashboard/files/<path:filename>`: Serve question files (protected)
- `GET /dashboard/api/asset/<asset_id>`: Get asset metadata (JSON)
- `GET /dashboard/api/asset_preview/<asset_id>`: Get asset file for preview
- `GET /dashboard/api/question/<question_id>/assets/<asset_type>`: Get specific asset

**Key Features**:
- Multi-level sorting
- Advanced filtering
- HTMX partial rendering
- Session persistence
- Protected file serving

### Admin Blueprint (`app/admin.py`)

Administrative functions.

**Routes**:
- `GET /admin/`: Admin dashboard
- `GET /admin/topics`: Topic management interface
- `POST /admin/topics/add`: Add new topic
- `POST /admin/topics/<id>/edit`: Edit topic
- `POST /admin/topics/<id>/delete`: Delete topic
- `POST /admin/subtopics/add`: Add new subtopic
- `POST /admin/subtopics/<id>/edit`: Edit subtopic
- `POST /admin/subtopics/<id>/delete`: Delete subtopic
- `GET /admin/tags`: Question tagging interface
- `POST /admin/questions/<id>/update`: Update question metadata
- `POST /admin/questions/delete`: Batch delete questions
- `POST /admin/questions/batch-update`: Batch update questions
- `GET /admin/chapters`: Chapter management
- `POST /admin/chapters/*`: Chapter CRUD operations
- `GET /admin/users`: User management
- `POST /admin/users/*`: User CRUD operations

**Key Features**:
- CRUD for topics/subtopics
- Question metadata editing
- Batch operations
- User management
- Chapter management

### Generator Blueprint (`app/generator.py`)

Word document generation.

**Routes**:
- `GET /generate/`: Generation options page
- `POST /generate/create`: Create and download document

**Key Functions**:
- `generate_document()`: Main document creation
- `add_image()`: Insert image with sizing
- `apply_spacing()`: Handle spacing logic
- `select_asset()`: Choose best asset based on preferences
- Multi-level sorting
- Smart page breaks

### Ingestor Module (`app/ingestor.py`)

File scanning and database import.

**Key Functions**:
- `ingest_command(source_path)`: Main ingestion process
- `sync_command(source_path, dry_run)`: Database sync
- `parse_filename()`: Extract metadata from filename
- `construct_qid()`: Build question identifier
- `auto_detect_qtype()`: Detect question type from metadata

**Filename Patterns**:
- PP: `SUBJ_SOURCE_YEAR_PAPER_QNO_LANG_TYPE.EXT`
- QB: `SUBJ_QB_DETAIL_QNO_LANG_TYPE.EXT`

**Process**:
1. Scan directories recursively
2. Parse filenames with regex
3. Extract metadata
4. Create/update Question records
5. Create QuestionAsset records
6. Log errors

### Utilities (`app/utils.py`)

Helper functions.

**Functions**:
- `natural_sort()`: Natural sorting (Q1, Q2, Q10)
- `apply_multi_sort()`: Multi-level sorting
- `get_asset_path()`: Resolve asset file path
- `validate_subtopic()`: Validate subtopic belongs to topic
- Additional helpers as needed

---

## API Endpoints

### Authentication Endpoints

#### POST /login
Authenticate user and create session.

**Request**:
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**Response** (200 OK):
```json
{
  "success": true,
  "redirect": "/dashboard/"
}
```

**Response** (401 Unauthorized):
```json
{
  "success": false,
  "error": "Invalid username or password"
}
```

#### GET /logout
Logout user and destroy session.

**Response**: Redirect to `/login`

### Dashboard Endpoints

#### GET /dashboard/filter
Get filtered questions (HTMX partial).

**Query Parameters**:
- `subject`: Subject ID (MATC, MAT1, etc.)
- `source`: Source type (DSE, CE, AL, QB, All)
- `years[]`: Array of years
- `topics[]`: Array of topic IDs
- `cross_topic`: Include minor topics (true/false)
- `topic_mode`: AND or OR
- `subtopics[]`: Array of subtopic IDs
- `levels[]`: Array of levels (1, 2, 3, -1 for not assigned)
- `q_type`: MC, CQ, or All
- `section`: Section filter or All
- `qid_search`: QID wildcard search
- `page`: Page number (default 1)
- `page_size`: Items per page (10, 20, 50, 100)
- `lang_pref`: Language preference (EN, CH)
- `sort`: JSON array of sort criteria

**Response**: HTML partial (question cards + pagination)

#### GET /dashboard/api/topics/<subject_id>
Get topics for a subject.

**Response** (200 OK):
```json
{
  "topics": [
    {
      "id": 1,
      "name": "Calculus",
      "subtopic_count": 5
    },
    {
      "id": 2,
      "name": "Algebra",
      "subtopic_count": 8
    }
  ]
}
```

#### GET /dashboard/api/subtopics
Get subtopics for selected topics.

**Query Parameters**:
- `topic_ids`: Comma-separated topic IDs

**Response** (200 OK):
```json
{
  "subtopics": [
    {
      "id": 1,
      "topic_id": 1,
      "name": "Integration"
    },
    {
      "id": 2,
      "topic_id": 1,
      "name": "Differentiation"
    }
  ]
}
```

#### GET /dashboard/api/years/<subject_id>/<source>
Get available years for subject and source.

**Response** (200 OK):
```json
{
  "years": [2012, 2013, ..., 2024, 2025]
}
```

#### GET /dashboard/files/<path:filename>
Serve question file (protected).

**Authentication**: Required
**Authorization**: User must have access to file's subject

**Response**: File content with appropriate MIME type

#### GET /dashboard/api/asset_preview/<asset_id>
Get asset file for preview.

**Response**: File content

### Admin Endpoints

#### POST /admin/topics/add
Add new topic.

**Request**:
```json
{
  "subject_id": "MATC",
  "name": "Statistics",
  "sort_order": 3
}
```

**Response** (200 OK):
```json
{
  "success": true,
  "topic_id": 5
}
```

#### POST /admin/questions/<id>/update
Update question metadata.

**Request**:
```json
{
  "major_topic_id": 1,
  "major_subtopic_id": 2,
  "minor_topic_ids": [3, 4],
  "subtopic_ids": [2, 5, 7],
  "level": 2,
  "q_type": "CQ",
  "section": "A",
  "description": "Requires implicit differentiation",
  "correct_percentage": 75
}
```

**Response** (200 OK):
```json
{
  "success": true,
  "message": "Question updated successfully"
}
```

**Response** (400 Bad Request):
```json
{
  "success": false,
  "error": "Major subtopic does not belong to major topic"
}
```

#### POST /admin/questions/batch-update
Batch update questions.

**Request**:
```json
{
  "question_ids": [1, 2, 3, 4, 5],
  "update_level": true,
  "level": 2,
  "update_type": true,
  "q_type": "MC",
  "update_major_topic": false,
  "update_correct_pct": true,
  "correct_percentage": 80
}
```

**Response** (200 OK):
```json
{
  "success": true,
  "count": 5,
  "message": "5 questions updated"
}
```

#### POST /admin/questions/delete
Batch delete questions.

**Request**:
```json
{
  "question_ids": [10, 11, 12]
}
```

**Response** (200 OK):
```json
{
  "success": true,
  "count": 3,
  "qids": ["MATC_DSE_2024_P1_Q1", "MATC_DSE_2024_P1_Q2", "MATC_DSE_2024_P1_Q3"]
}
```

### Generator Endpoints

#### POST /generate/create
Generate and download Word document.

**Request**:
```json
{
  "question_ids": [1, 2, 3, 4, 5],
  "sort_mode": "custom",
  "sort_config": [
    {"field": "topic", "direction": "asc"},
    {"field": "level", "direction": "asc"}
  ],
  "answer_mode": "questions_then_answers",
  "mc_before": "lines",
  "mc_before_count": 0,
  "mc_after": "lines",
  "mc_after_count": 1,
  "cq_before": "page",
  "cq_after": "page",
  "show_qid": true,
  "show_qid_answers": true,
  "show_correct_pct": true,
  "lang_pref": "EN"
}
```

**Response**: Word document file download

**File Name**: `questions_YYYYMMDD_HHMMSS.docx`

---

## Frontend Architecture

### Template Structure

**Base Template** (`templates/base.html`):
```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}OQB System{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <!-- Navigation -->
    </nav>
    
    <main class="container-fluid">
        {% block content %}{% endblock %}
    </main>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
```

**Dashboard Template** (`templates/dashboard.html`):
- Extends base.html
- Two-column layout (filters + questions)
- HTMX target for partial updates
- JavaScript for sort configuration

**Question List Partial** (`templates/partials/question_list.html`):
- Rendered by HTMX
- Question cards
- Pagination controls
- No full page layout

### HTMX Usage

**Dynamic Filtering**:
```html
<form hx-get="/dashboard/filter" 
      hx-target="#question-list" 
      hx-trigger="change">
    <select name="subject">
        <option value="MATC">MATC</option>
    </select>
    <select name="source">
        <option value="DSE">DSE</option>
    </select>
</form>

<div id="question-list">
    <!-- Partial content loaded here -->
</div>
```

**Pagination**:
```html
<a href="/dashboard/filter?page=2" 
   hx-get="/dashboard/filter?page=2"
   hx-target="#question-list">
    Page 2
</a>
```

**Benefits**:
- No page reloads
- Fast, responsive UI
- Simple implementation
- Progressive enhancement

### JavaScript

Minimal custom JavaScript:

**Sort Configuration**:
```javascript
let sortConfig = [];

function addSortLevel(field, direction) {
    sortConfig.push({field, direction});
    updateSortDisplay();
}

function clearSort() {
    sortConfig = [];
    updateSortDisplay();
}

function submitWithSort() {
    document.getElementById('sort-input').value = JSON.stringify(sortConfig);
    document.getElementById('filter-form').submit();
}
```

**Session Management**:
```javascript
// Store selected questions in sessionStorage
function updateSelection(questionId, checked) {
    let selected = JSON.parse(sessionStorage.getItem('selected') || '[]');
    if (checked) {
        selected.push(questionId);
    } else {
        selected = selected.filter(id => id !== questionId);
    }
    sessionStorage.setItem('selected', JSON.stringify(selected));
}
```

### Bootstrap Components Used

- **Navbar**: Top navigation
- **Cards**: Question display
- **Badges**: Metadata labels
- **Modals**: Previews and forms
- **Forms**: Filters and inputs
- **Pagination**: Page navigation
- **Buttons**: Actions
- **Alerts**: Flash messages
- **Dropdowns**: Multi-select
- **Collapse**: Expandable sections

---

## File Processing

### Filename Parsing

**Regex Patterns** (in `app/ingestor.py`):

```python
# Past Paper pattern
PP_PATTERN = re.compile(
    r'^(?P<subject>[A-Z]+)_'
    r'(?P<source>DSE|CE|AL)_'
    r'(?P<year>\d{4})_'
    r'(?P<paper>P\d+)_'
    r'(?P<qno>Q\d+)_'
    r'(?P<lang>EN|CH|BI)_'
    r'(?P<type>QUE|ANS|SOL)'
    r'\.(?P<ext>\w+)$',
    re.IGNORECASE
)

# Question Bank pattern
QB_PATTERN = re.compile(
    r'^(?P<subject>[A-Z]+)_QB_'
    r'(?P<detail>[^_]+)_'
    r'(?P<qno>Q\d+)_'
    r'(?P<lang>EN|CH|BI)_'
    r'(?P<type>QUE|ANS|SOL)'
    r'\.(?P<ext>\w+)$',
    re.IGNORECASE
)
```

**Parsing Process**:
1. Match filename against PP pattern
2. If no match, try QB pattern
3. If no match, log error and skip
4. Extract named groups
5. Construct QID
6. Determine file_format (IMG or DOC based on extension)
7. Return metadata dict

### QID Construction

```python
def construct_qid(metadata):
    """Construct Question ID from metadata"""
    if metadata['source'] in ['DSE', 'CE', 'AL']:
        # Past Paper QID
        return f"{metadata['subject']}_{metadata['source']}_{metadata['year']}_{metadata['paper']}_{metadata['qno']}"
    else:
        # Question Bank QID
        return f"{metadata['subject']}_QB_{metadata['detail']}_{metadata['qno']}"
```

**Examples**:
- PP: `MATC_DSE_2024_P1_Q5`
- QB: `MATC_QB_MATHSMART2024_Q1`

### Question Type Auto-Detection

```python
def auto_detect_qtype(metadata):
    """Automatically detect question type"""
    if metadata['source'] == 'DSE' and metadata['subject'] == 'MATC':
        if metadata['paper'] == 'P1':
            return 'CQ'  # Paper 1 is Conventional
        elif metadata['paper'] == 'P2':
            return 'MC'  # Paper 2 is Multiple Choice
    elif metadata['source'] == 'DSE' and metadata['subject'] in ['MAT1', 'MAT2']:
        return 'CQ'  # M1/M2 are all Conventional
    return None  # Unknown, needs manual tagging
```

### Ingestion Algorithm

```python
def ingest_command(source_path=None):
    """Main ingestion process"""
    # 1. Resolve source path
    source_path = source_path or Config.SOURCE_PATH
    
    # 2. Scan files recursively
    files = []
    for root, dirs, filenames in os.walk(source_path):
        for filename in filenames:
            filepath = os.path.join(root, filename)
            files.append(filepath)
    
    # 3. Sort naturally
    files = natsorted(files)
    
    # 4. Process each file
    for filepath in files:
        try:
            # Parse filename
            metadata = parse_filename(os.path.basename(filepath))
            if not metadata:
                log_error(f"Invalid filename: {filepath}")
                continue
            
            # Construct QID
            qid = construct_qid(metadata)
            
            # Check if question exists
            question = Question.query.filter_by(qid=qid).first()
            
            if not question:
                # Create new question
                question = Question(
                    qid=qid,
                    subject=metadata['subject'],
                    source=metadata['source'],
                    year=metadata.get('year'),
                    paper=metadata.get('paper'),
                    qno=metadata['qno'],
                    q_type=auto_detect_qtype(metadata)
                )
                db.session.add(question)
                db.session.flush()  # Get question.id
            
            # Check if asset exists
            asset = QuestionAsset.query.filter_by(
                question_id=question.id,
                asset_type=metadata['type'],
                language=metadata['lang']
            ).first()
            
            if not asset:
                # Create new asset
                asset = QuestionAsset(
                    question_id=question.id,
                    asset_type=metadata['type'],
                    file_format='IMG' if metadata['ext'].lower() in ['png', 'jpg', 'jpeg', 'gif'] else 'DOC',
                    language=metadata['lang'],
                    file_path=os.path.relpath(filepath, source_path)
                )
                db.session.add(asset)
            else:
                # Update path if changed
                asset.file_path = os.path.relpath(filepath, source_path)
            
            db.session.commit()
            
        except Exception as e:
            log_error(f"Error processing {filepath}: {str(e)}")
            db.session.rollback()
            continue
    
    print(f"Ingestion complete: {len(files)} files processed")
```

### Sync Algorithm

```python
def sync_command(source_path=None, dry_run=True):
    """Remove orphaned database records"""
    source_path = source_path or Config.SOURCE_PATH
    
    orphaned_assets = []
    orphaned_questions = []
    
    # 1. Check all assets
    assets = QuestionAsset.query.all()
    for asset in assets:
        full_path = os.path.join(source_path, asset.file_path)
        if not os.path.exists(full_path):
            orphaned_assets.append(asset)
    
    # 2. Check all questions
    questions = Question.query.all()
    for question in questions:
        if question.assets.count() == 0:
            orphaned_questions.append(question)
    
    # 3. Report
    print(f"Orphaned assets: {len(orphaned_assets)}")
    print(f"Orphaned questions: {len(orphaned_questions)}")
    
    # 4. Delete if not dry-run
    if not dry_run:
        for asset in orphaned_assets:
            db.session.delete(asset)
        for question in orphaned_questions:
            db.session.delete(question)
        db.session.commit()
        print("Orphaned records deleted")
    else:
        print("Dry run complete (no changes made)")
```

---

## Document Generation

### Generation Process

**High-Level Flow**:
1. Receive question IDs and options
2. Sort questions based on mode
3. Create Word document
4. Iterate questions based on answer mode
5. Insert images with spacing
6. Save and return file

### Core Function

```python
def generate_document(question_ids, options):
    """Generate Word document"""
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    
    # 1. Load questions
    questions = Question.query.filter(Question.id.in_(question_ids)).all()
    
    # 2. Sort questions
    if options['sort_mode'] == 'selection':
        # Preserve selection order
        question_dict = {q.id: q for q in questions}
        questions = [question_dict[qid] for qid in question_ids]
    else:
        # Custom multi-level sort
        questions = apply_multi_sort(questions, options['sort_config'])
    
    # 3. Create document
    doc = Document()
    
    # Set page size to A4
    section = doc.sections[0]
    section.page_height = Cm(29.7)
    section.page_width = Cm(21.0)
    section.top_margin = Cm(1.27)
    section.bottom_margin = Cm(1.27)
    section.left_margin = Cm(1.27)
    section.right_margin = Cm(1.27)
    
    # 4. Generate content based on answer mode
    if options['answer_mode'] == 'questions_only':
        for q in questions:
            add_question(doc, q, options)
    
    elif options['answer_mode'] == 'question_answer':
        for q in questions:
            add_question(doc, q, options)
            add_answer(doc, q, options)
    
    elif options['answer_mode'] == 'questions_then_answers':
        # Questions section
        for q in questions:
            add_question(doc, q, options)
        # New page for answers
        doc.add_page_break()
        # Answers section
        for q in questions:
            add_answer(doc, q, options)
    
    # ... other answer modes
    
    # 5. Save document
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'questions_{timestamp}.docx'
    filepath = os.path.join(Config.OUTPUT_PATH, filename)
    doc.save(filepath)
    
    return filepath
```

### Asset Selection

```python
def select_asset(question, asset_type, lang_pref):
    """Select best asset based on preferences"""
    # Define priority order
    if lang_pref == 'EN':
        lang_order = ['EN', 'BI', 'CH']
    else:
        lang_order = ['CH', 'BI', 'EN']
    
    # Try each language in order
    for lang in lang_order:
        # Prefer IMG over DOC
        asset = question.assets.filter_by(
            asset_type=asset_type,
            language=lang,
            file_format='IMG'
        ).first()
        if asset:
            return asset
        
        # Try DOC
        asset = question.assets.filter_by(
            asset_type=asset_type,
            language=lang,
            file_format='DOC'
        ).first()
        if asset:
            return asset
    
    return None  # No asset found
```

### Image Insertion

```python
def add_image(doc, asset, options):
    """Add image to document with sizing"""
    full_path = os.path.join(Config.SOURCE_PATH, asset.file_path)
    
    if not os.path.exists(full_path):
        # File missing
        para = doc.add_paragraph()
        run = para.add_run(f"[Missing file: {asset.file_path}]")
        run.italic = True
        return
    
    try:
        # Open image to get dimensions
        from PIL import Image
        img = Image.open(full_path)
        width, height = img.size
        
        # Calculate scaled size (max 6 inches width)
        max_width = Inches(6)
        if width > max_width:
            ratio = max_width / width
            scaled_height = height * ratio
        else:
            max_width = width
            scaled_height = height
        
        # Insert image
        para = doc.add_paragraph()
        run = para.add_run()
        run.add_picture(full_path, width=max_width)
        
    except Exception as e:
        # Error processing image
        para = doc.add_paragraph()
        run = para.add_run(f"[Error loading image: {str(e)}]")
        run.italic = True
```

### Spacing Logic

```python
def apply_spacing(doc, question, position, options):
    """Apply spacing before or after question"""
    # Determine if MC or CQ
    is_mc = question.q_type == 'MC'
    
    # Get spacing config
    if position == 'before':
        mode = options['mc_before'] if is_mc else options['cq_before']
        count = options.get('mc_before_count', 0) if is_mc else options.get('cq_before_count', 0)
    else:
        mode = options['mc_after'] if is_mc else options['cq_after']
        count = options.get('mc_after_count', 0) if is_mc else options.get('cq_after_count', 0)
    
    if mode == 'lines':
        # Add blank lines
        for _ in range(count):
            doc.add_paragraph()
    elif mode == 'page':
        # Add page break (check for duplicate)
        last_para = doc.paragraphs[-1] if doc.paragraphs else None
        if last_para and last_para.runs and hasattr(last_para.runs[-1], 'page_break'):
            # Already has page break, skip
            pass
        else:
            doc.add_page_break()
```

### Multi-Level Sorting

```python
def apply_multi_sort(questions, sort_config):
    """Sort questions by multiple criteria"""
    from natsort import natsorted, ns
    
    if not sort_config:
        return questions
    
    # Build sort key function
    def sort_key(question):
        keys = []
        for level in sort_config:
            field = level['field']
            direction = level['direction']
            
            if field == 'qid':
                value = question.qid
            elif field == 'year':
                value = question.year or 0
            elif field == 'level':
                value = question.level or 999
            elif field == 'topic':
                value = question.major_topic.name if question.major_topic else ''
            elif field == 'subtopic':
                value = question.major_subtopic.name if question.major_subtopic else ''
            elif field == 'source':
                value = question.source
            elif field == 'section':
                value = question.section or ''
            elif field == 'type':
                value = question.q_type or ''
            elif field == 'created':
                value = question.created_at.timestamp()
            elif field == 'correct_percentage':
                value = question.correct_percentage if question.correct_percentage is not None else 999
            else:
                value = ''
            
            # Handle reverse
            if direction == 'desc':
                if isinstance(value, str):
                    value = '~' + value  # Reverse string sort
                else:
                    value = -value  # Reverse numeric sort
            
            keys.append(value)
        
        return tuple(keys)
    
    # Sort using natsorted for natural string sorting
    return natsorted(questions, key=sort_key, alg=ns.IGNORECASE)
```

---

## Authentication & Authorization

### Flask-Login Integration

**User Loader**:
```python
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
```

**Login Required**:
```python
from flask_login import login_required

@dashboard_bp.route('/dashboard/')
@login_required
def index():
    # User must be logged in
    ...
```

**Admin Required**:
```python
from functools import wraps
from flask_login import current_user
from flask import abort

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.has_any_admin_access():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/admin/topics')
@login_required
@admin_required
def manage_topics():
    # User must be admin
    ...
```

### Subject-Based Permissions

**Check Access**:
```python
def view_question(question_id):
    question = Question.query.get_or_404(question_id)
    
    if not current_user.has_subject_access(question.subject):
        abort(403)
    
    # User has access to this subject
    ...
```

**Filter by Access**:
```python
def get_accessible_questions():
    accessible_subjects = current_user.get_accessible_subjects()
    
    questions = Question.query.filter(
        Question.subject.in_(accessible_subjects)
    ).all()
    
    return questions
```

### Password Hashing

**Set Password**:
```python
from werkzeug.security import generate_password_hash

user = User(username='john')
user.set_password('secret123')
# Stores hashed password in user.password_hash
```

**Check Password**:
```python
from werkzeug.security import check_password_hash

if user.check_password('secret123'):
    # Password correct
    ...
```

### Session Management

**Login**:
```python
from flask_login import login_user

user = User.query.filter_by(username=username).first()
if user and user.check_password(password):
    login_user(user)
    # Session created
```

**Logout**:
```python
from flask_login import logout_user

logout_user()
# Session destroyed
```

**Current User**:
```python
from flask_login import current_user

@dashboard_bp.route('/profile')
@login_required
def profile():
    username = current_user.username
    is_admin = current_user.is_super_admin
    ...
```

---

## Configuration

### Environment Variables

All configuration loaded from `.env` file via `python-dotenv`.

**Required Variables**:
```env
# Database
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=password
DB_NAME=oqb2

# Flask
SECRET_KEY=random-secret-key

# Paths
SOURCE_PATH=./Source
OUTPUT_PATH=./output
```

**Optional Variables**:
```env
# Flask environment
FLASK_ENV=development
FLASK_DEBUG=1

# Database pool
SQLALCHEMY_POOL_SIZE=10
SQLALCHEMY_MAX_OVERFLOW=20
```

### Configuration Class

Located in `app/config.py`:

```python
class Config:
    # Load from environment
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-dev-key')
    
    # Database URI
    SQLALCHEMY_DATABASE_URI = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}?charset=utf8mb4'
    
    # Engine options
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }
    
    # Paths
    SOURCE_PATH = os.getenv('SOURCE_PATH', './Source')
    OUTPUT_PATH = os.getenv('OUTPUT_PATH', './output')
```

### Development vs Production

**Development**:
```env
FLASK_ENV=development
FLASK_DEBUG=1
SECRET_KEY=dev-key
```

**Production**:
```env
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=<strong-random-key>
```

**Production Checklist**:
- Set strong SECRET_KEY
- Set FLASK_DEBUG=0
- Use production database
- Enable HTTPS
- Use production WSGI server (gunicorn)
- Set up reverse proxy (nginx)
- Configure firewall
- Set up monitoring

---

## Extension Guide

### Adding a New Subject

**1. Add to Database**:
```sql
INSERT INTO subjects (id, name) VALUES ('PHYS', 'Physics');
```

**2. Create Directory Structure**:
```
Source/PHYS/
├── PP/
│   ├── DSE/
│   ├── CE/
│   └── AL/
└── QB/
```

**3. Add Topics** (via UI or SQL):
```sql
INSERT INTO topics (subject_id, name, sort_order) VALUES
  ('PHYS', 'Mechanics', 1),
  ('PHYS', 'Electricity', 2),
  ('PHYS', 'Optics', 3);
```

**4. Update Ingestor** (if needed):
```python
# app/ingestor.py
def auto_detect_qtype(metadata):
    if metadata['subject'] == 'PHYS':
        if metadata['paper'] == 'P1':
            return 'MC'
        else:
            return 'CQ'
    ...
```

**5. Ingest Files**:
```bash
python cli.py ingest
```

### Adding a New Filter

**1. Update Dashboard Template**:
```html
<!-- templates/dashboard.html -->
<div class="mb-3">
    <label>My New Filter</label>
    <select name="new_filter" class="form-select">
        <option value="">All</option>
        <option value="A">Option A</option>
        <option value="B">Option B</option>
    </select>
</div>
```

**2. Update Dashboard Route**:
```python
# app/dashboard.py
@dashboard_bp.route('/dashboard/filter')
def filter_questions():
    new_filter = request.args.get('new_filter')
    
    query = Question.query
    
    if new_filter:
        query = query.filter(Question.new_field == new_filter)
    
    ...
```

**3. Update Model** (if new field needed):
```python
# app/models.py
class Question(db.Model):
    ...
    new_field = db.Column(db.String(50))
```

**4. Database Migration**:
```sql
ALTER TABLE questions ADD COLUMN new_field VARCHAR(50);
```

### Adding a New Sort Field

**1. Update Sort Configuration**:
```python
# app/dashboard.py
SORT_FIELDS = {
    'qid': 'QID',
    'year': 'Year',
    'level': 'Level',
    'topic': 'Topic',
    'new_field': 'My New Field',  # Add here
    ...
}
```

**2. Update Sort Logic**:
```python
# app/utils.py
def apply_multi_sort(questions, sort_config):
    def sort_key(question):
        keys = []
        for level in sort_config:
            field = level['field']
            
            if field == 'new_field':
                value = question.new_field or ''
            ...
```

**3. Update Dashboard Template**:
```html
<select name="sort_field">
    <option value="qid">QID</option>
    <option value="new_field">My New Field</option>
    ...
</select>
```

### Adding a New Answer Mode

**1. Update Generator Options**:
```html
<!-- templates/generate.html -->
<input type="radio" name="answer_mode" value="my_new_mode">
<label>My New Answer Mode</label>
```

**2. Update Generator Logic**:
```python
# app/generator.py
def generate_document(question_ids, options):
    ...
    if options['answer_mode'] == 'my_new_mode':
        # Implement new mode logic
        for q in questions:
            add_question(doc, q, options)
            # Custom logic here
    ...
```

### Adding a New API Endpoint

**1. Define Route**:
```python
# app/dashboard.py (or appropriate blueprint)
@dashboard_bp.route('/api/my_endpoint/<param>')
@login_required
def my_endpoint(param):
    # Business logic
    data = get_data(param)
    
    # Return JSON
    return jsonify({
        'success': True,
        'data': data
    })
```

**2. Add Error Handling**:
```python
@dashboard_bp.route('/api/my_endpoint/<param>')
@login_required
def my_endpoint(param):
    try:
        data = get_data(param)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
```

**3. Use in Frontend**:
```javascript
fetch('/dashboard/api/my_endpoint/123')
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log(data.data);
        } else {
            console.error(data.error);
        }
    });
```

### Adding a New Blueprint

**1. Create Blueprint File**:
```python
# app/reports.py
from flask import Blueprint, render_template
from flask_login import login_required

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')

@reports_bp.route('/')
@login_required
def index():
    return render_template('reports_index.html')
```

**2. Register Blueprint**:
```python
# app/__init__.py
def create_app():
    ...
    from app.reports import reports_bp
    app.register_blueprint(reports_bp)
    ...
```

**3. Create Template**:
```html
<!-- templates/reports_index.html -->
{% extends "base.html" %}

{% block content %}
<h1>Reports</h1>
<!-- Report content -->
{% endblock %}
```

**4. Add Navigation**:
```html
<!-- templates/base.html -->
<nav>
    ...
    <a href="{{ url_for('reports.index') }}">Reports</a>
</nav>
```

---

## Deployment

### Production WSGI Server

**Gunicorn** (Linux):
```bash
gunicorn -w 4 -b 0.0.0.0:5000 'app:create_app()'
```

**Systemd Service**:
```ini
[Unit]
Description=OQB System
After=network.target mariadb.service

[Service]
User=www-data
WorkingDirectory=/opt/oqb2
Environment="PATH=/opt/oqb2/venv/bin"
ExecStart=/opt/oqb2/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 'app:create_app()'

[Install]
WantedBy=multi-user.target
```

### Reverse Proxy

**Nginx**:
```nginx
server {
    listen 80;
    server_name oqb.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name oqb.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

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
}
```

### Docker Deployment (Optional)

**Dockerfile**:
```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_ENV=production

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:create_app()"]
```

**docker-compose.yml**:
```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "5000:5000"
    env_file:
      - .env
    depends_on:
      - db
    volumes:
      - ./Source:/app/Source
      - ./output:/app/output

  db:
    image: mariadb:10
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_PASSWORD}
      MYSQL_DATABASE: ${DB_NAME}
    volumes:
      - db_data:/var/lib/mysql

volumes:
  db_data:
```

---

## Development Workflow

### Setup Development Environment

```bash
# Clone repository
git clone <repo-url>
cd oqb2

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp env_template.txt .env
# Edit .env with your settings

# Initialize database
python init_db.py

# Run development server
python run.py
```

### Code Style

**Python**:
- Follow PEP 8
- Use 4 spaces for indentation
- Maximum line length: 100 characters
- Use docstrings for functions and classes
- Type hints recommended for new code

**HTML/Jinja2**:
- Use 2 spaces for indentation
- Keep templates DRY (use includes/macros)
- Semantic HTML5 elements

**JavaScript**:
- Use ES6+ syntax
- camelCase for variables/functions
- Semicolons required

### Testing

**Unit Tests** (TODO - not implemented):
```python
import unittest
from app import create_app, db
from app.models import User, Question

class TestUserModel(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        
    def test_password_hashing(self):
        user = User(username='test')
        user.set_password('password')
        self.assertTrue(user.check_password('password'))
        self.assertFalse(user.check_password('wrong'))
```

**Integration Tests** (TODO - not implemented):
```python
def test_login(self):
    response = self.client.post('/login', data={
        'username': 'admin',
        'password': 'admin123'
    }, follow_redirects=True)
    self.assertEqual(response.status_code, 200)
```

### Debugging

**Flask Debug Mode**:
```env
FLASK_DEBUG=1
```

**Database Queries**:
```python
# Enable query logging
import logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
```

**Breakpoints**:
```python
import pdb; pdb.set_trace()
```

### Version Control

**Branching Strategy**:
- `main`: Production-ready code
- `develop`: Integration branch
- `feature/*`: New features
- `bugfix/*`: Bug fixes
- `hotfix/*`: Urgent production fixes

**Commit Messages**:
```
<type>: <subject>

<body>

<footer>
```

Types: feat, fix, docs, style, refactor, test, chore

Example:
```
feat: Add correct percentage field to questions

- Add correct_percentage column to database
- Update question edit form
- Display percentage in dashboard
- Add to document generation options

Closes #123
```

### Release Process

1. Update version in CHANGELOG.md
2. Update version references in documentation
3. Test thoroughly (see ADMIN_GUIDE.md testing section)
4. Create release branch
5. Merge to main
6. Tag release: `git tag v2.1.0`
7. Push tags: `git push --tags`
8. Deploy to production
9. Monitor for issues

---

## Performance Optimization

### Database Query Optimization

**Use Eager Loading**:
```python
# Bad: N+1 queries
questions = Question.query.all()
for q in questions:
    print(q.major_topic.name)  # Separate query each time

# Good: Eager loading
questions = Question.query.options(
    db.joinedload(Question.major_topic)
).all()
for q in questions:
    print(q.major_topic.name)  # Already loaded
```

**Use Pagination**:
```python
# Don't load all at once
questions = Question.query.paginate(page=1, per_page=20)
```

**Index Frequently Queried Fields**:
```sql
CREATE INDEX idx_questions_subject ON questions(subject);
CREATE INDEX idx_questions_year ON questions(year);
CREATE INDEX idx_questions_level ON questions(level);
```

### Caching (Future Enhancement)

**Flask-Caching** (not implemented):
```python
from flask_caching import Cache

cache = Cache(app, config={'CACHE_TYPE': 'simple'})

@cache.cached(timeout=300)
def get_topics(subject_id):
    return Topic.query.filter_by(subject_id=subject_id).all()
```

### File Serving Optimization

**Use X-Sendfile** (production):
```python
from flask import send_file

@dashboard_bp.route('/files/<path:filename>')
def serve_file(filename):
    # Let nginx serve file
    response = make_response()
    response.headers['X-Accel-Redirect'] = f'/protected/{filename}'
    return response
```

**Nginx Configuration**:
```nginx
location /protected/ {
    internal;
    alias /path/to/Source/;
}
```

---

## Security Considerations

### Input Validation

**Validate All User Input**:
```python
from flask import request
from werkzeug.utils import secure_filename

def upload_file():
    file = request.files['file']
    filename = secure_filename(file.filename)
    # Validate extension, size, etc.
```

### SQL Injection Prevention

**Use ORM**:
```python
# Safe - parameterized
user = User.query.filter_by(username=username).first()

# Unsafe - string concatenation (DON'T DO THIS)
query = f"SELECT * FROM users WHERE username = '{username}'"
```

### XSS Prevention

**Jinja2 Auto-Escaping**:
```html
<!-- Safe - auto-escaped -->
<p>{{ user_input }}</p>

<!-- Unsafe - manual trust (use with caution) -->
<p>{{ user_input|safe }}</p>
```

### CSRF Protection

**Flask-WTF** (future enhancement):
```python
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect(app)
```

### File Upload Security

**Validate File Types**:
```python
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'docx'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
```

---

## Troubleshooting Common Development Issues

### "Module not found"

**Solution**: Activate virtual environment and install dependencies
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### "Cannot connect to database"

**Solution**: Check database is running and credentials correct
```bash
mysql -u root -p
# Enter password and test connection
```

### "Table doesn't exist"

**Solution**: Initialize database
```bash
python init_db.py
```

### "Port already in use"

**Solution**: Kill process or use different port
```bash
# Find process
lsof -i :5000  # Linux/Mac
netstat -ano | findstr :5000  # Windows

# Kill process or change port in run.py
```

---

## Future Enhancements

### Planned Features

**v2.2**:
- User profile page with password change
- Question import/export (CSV/JSON)
- Advanced statistics and reports
- Question history/versioning
- Comment system for questions

**v3.0**:
- RESTful API
- Mobile app
- Real-time collaboration
- AI-powered question tagging
- LaTeX/MathML equation support
- Question similarity detection

### Architecture Improvements

- Implement comprehensive test suite
- Add caching layer (Redis)
- Implement background tasks (Celery)
- Add API documentation (Swagger/OpenAPI)
- Implement rate limiting
- Add comprehensive logging
- Performance monitoring
- Error tracking (Sentry)

---

## Contributing

### Getting Started

1. Fork repository
2. Create feature branch
3. Make changes
4. Write/update tests
5. Update documentation
6. Submit pull request

### Code Review Checklist

- [ ] Code follows style guide
- [ ] Tests pass
- [ ] Documentation updated
- [ ] No new linter errors
- [ ] Database migrations included (if needed)
- [ ] Security considerations addressed
- [ ] Performance impact considered

---

## Support and Resources

### Documentation

- **USER_MANUAL.md**: End-user guide
- **ADMIN_GUIDE.md**: Administrator guide
- **DEVELOPER_SPEC.md**: This document
- **CHANGELOG.md**: Version history

### External Resources

- [Flask Documentation](https://flask.palletsprojects.com/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [Bootstrap 5 Documentation](https://getbootstrap.com/docs/5.3/)
- [HTMX Documentation](https://htmx.org/docs/)
- [python-docx Documentation](https://python-docx.readthedocs.io/)

---

**End of Developer Specification**

*Last Updated: February 5, 2026*  
*Online Question Bank System v2.1.0*

For user documentation, see USER_MANUAL.md  
For administrator documentation, see ADMIN_GUIDE.md

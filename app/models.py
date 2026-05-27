"""
Database models for the Online Question Bank system
"""
from app import db
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# Association table for question minor topics (many-to-many)
question_minor_topics = db.Table('question_minor_topics',
    db.Column('question_id', db.Integer, db.ForeignKey('questions.id'), primary_key=True),
    db.Column('topic_id', db.Integer, db.ForeignKey('topics.id'), primary_key=True)
)

# Association table for question subtopics (many-to-many)
question_subtopics = db.Table('question_subtopics',
    db.Column('question_id', db.Integer, db.ForeignKey('questions.id'), primary_key=True),
    db.Column('subtopic_id', db.Integer, db.ForeignKey('subtopics.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    """User model for authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)  # Legacy field, kept for compatibility
    is_super_admin = db.Column(db.Boolean, default=False, nullable=False)  # Top-level admin
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    subject_permissions = db.relationship('UserSubjectPermission', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if password matches"""
        return check_password_hash(self.password_hash, password)
    
    def has_subject_access(self, subject_id):
        """Check if user has any access (user or admin) to a subject"""
        if self.is_super_admin:
            return True
        return UserSubjectPermission.query.filter_by(
            user_id=self.id, subject_id=subject_id
        ).first() is not None
    
    def is_subject_admin(self, subject_id):
        """Check if user is admin for a specific subject"""
        if self.is_super_admin:
            return True
        perm = UserSubjectPermission.query.filter_by(
            user_id=self.id, subject_id=subject_id
        ).first()
        return perm is not None and perm.role == 'admin'
    
    def get_accessible_subjects(self):
        """Get list of subject IDs user can access"""
        if self.is_super_admin:
            return [s.id for s in Subject.query.all()]
        return [p.subject_id for p in self.subject_permissions.all()]
    
    def get_admin_subjects(self):
        """Get list of subject IDs user has admin access to"""
        if self.is_super_admin:
            return [s.id for s in Subject.query.all()]
        return [p.subject_id for p in self.subject_permissions.filter_by(role='admin').all()]
    
    def has_any_admin_access(self):
        """Check if user has admin access to any subject"""
        if self.is_super_admin:
            return True
        return self.subject_permissions.filter_by(role='admin').first() is not None

    def is_view_only(self, subject_id):
        """Check if user only has view-only access to a subject"""
        if self.is_super_admin:
            return False
        perm = UserSubjectPermission.query.filter_by(
            user_id=self.id, subject_id=subject_id
        ).first()
        return perm is not None and perm.role == 'viewer'

    def can_generate(self):
        """Check if user can generate documents (has at least 'user' role for any subject)"""
        if self.is_super_admin:
            return True
        return self.subject_permissions.filter(
            UserSubjectPermission.role.in_(['user', 'admin'])
        ).first() is not None

    def is_all_view_only(self):
        """Check if ALL of user's subject permissions are view-only (no user or admin roles)"""
        if self.is_super_admin:
            return False
        perms = self.subject_permissions.all()
        if not perms:
            return True
        return all(p.role == 'viewer' for p in perms)

    def get_subject_roles(self):
        """Get a dict mapping subject_id -> role for all permissions.
        Super admins get 'admin' for every subject."""
        if self.is_super_admin:
            return {s.id: 'admin' for s in Subject.query.all()}
        return {p.subject_id: p.role for p in self.subject_permissions.all()}

    def __repr__(self):
        return f'<User {self.username}>'


class UserSubjectPermission(db.Model):
    """User permissions for specific subjects"""
    __tablename__ = 'user_subject_permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False, default='user')  # 'viewer', 'user', or 'admin'
    
    # Unique constraint: one permission per user-subject pair
    __table_args__ = (db.UniqueConstraint('user_id', 'subject_id', name='uq_user_subject'),)
    
    def __repr__(self):
        return f'<UserSubjectPermission {self.user_id} - {self.subject_id}: {self.role}>'

class Subject(db.Model):
    """Subject model (MATC, MAT1, MAT2, ICT, etc.)"""
    __tablename__ = 'subjects'
    
    id = db.Column(db.String(10), primary_key=True)  # e.g., 'MATC'
    name = db.Column(db.String(100), nullable=False)  # e.g., 'Mathematics Compulsory'
    
    # Relationships
    topics = db.relationship('Topic', backref='subject', lazy='dynamic', cascade='all, delete-orphan')
    questions = db.relationship('Question', backref='subject_ref', lazy='dynamic')
    
    def __repr__(self):
        return f'<Subject {self.id}: {self.name}>'

class Topic(db.Model):
    """Topic model"""
    __tablename__ = 'topics'
    
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    
    # Relationships
    subtopics = db.relationship('Subtopic', backref='topic', lazy='dynamic', cascade='all, delete-orphan',
                                order_by='Subtopic.sort_order')
    major_questions = db.relationship('Question', backref='major_topic', foreign_keys='Question.major_topic_id', lazy='dynamic')
    
    def __repr__(self):
        return f'<Topic {self.name}>'

class Subtopic(db.Model):
    """Subtopic model"""
    __tablename__ = 'subtopics'
    
    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey('topics.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    hidden = db.Column(db.Boolean, default=False, nullable=False)  # Hidden subtopics (e.g. textbook chapters)
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    
    def __repr__(self):
        return f'<Subtopic {self.name}>'

class Chapter(db.Model):
    """Chapter model - for organizing questions by textbook chapters"""
    __tablename__ = 'chapters'
    
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    
    # Relationships
    subchapters = db.relationship('Subchapter', backref='chapter', lazy='dynamic', cascade='all, delete-orphan',
                                  order_by='Subchapter.sort_order')
    
    def __repr__(self):
        return f'<Chapter {self.name}>'

class Subchapter(db.Model):
    """Subchapter model - subdivisions within a chapter"""
    __tablename__ = 'subchapters'
    
    id = db.Column(db.Integer, primary_key=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey('chapters.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    hidden = db.Column(db.Boolean, default=False, nullable=False)  # Hidden subchapters
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    
    def __repr__(self):
        return f'<Subchapter {self.name}>'

class Question(db.Model):
    """Question model - represents a logical question"""
    __tablename__ = 'questions'
    
    id = db.Column(db.Integer, primary_key=True)
    qid = db.Column(db.String(100), unique=True, nullable=False, index=True)  # e.g., 'MATC_DSE_2024_P1_Q5'
    subject = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False, index=True)  # DSE, CE, AL, QB
    year = db.Column(db.Integer, nullable=True, index=True)  # NULL for QB
    paper = db.Column(db.String(10), nullable=True)  # P1, P2, etc.
    section = db.Column(db.String(50), nullable=True)  # A, B, Section I, Section II, etc.
    qno = db.Column(db.Integer, nullable=False)  # Question number (integer part of Q5)
    q_type = db.Column(db.String(10), nullable=True)  # MC, CQ, or NULL
    level = db.Column(db.Integer, nullable=True)  # 1, 2, 3, or NULL
    major_topic_id = db.Column(db.Integer, db.ForeignKey('topics.id'), nullable=True, index=True)
    major_subtopic_id = db.Column(db.Integer, db.ForeignKey('subtopics.id'), nullable=True, index=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey('chapters.id', ondelete='SET NULL'), nullable=True, index=True)
    subchapter_id = db.Column(db.Integer, db.ForeignKey('subchapters.id', ondelete='SET NULL'), nullable=True, index=True)
    description = db.Column(db.Text, nullable=True)  # Optional description for the question
    correct_percentage = db.Column(db.Integer, nullable=True)  # 0-100, NULL if unknown (public exam correct rate)
    answer = db.Column(db.Text, nullable=True)  # Answer text (alternative to ANS image)
    comment = db.Column(db.Text, nullable=True)  # Comment / notes
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    assets = db.relationship('QuestionAsset', backref='question', lazy='dynamic', cascade='all, delete-orphan')
    major_subtopic = db.relationship('Subtopic', foreign_keys=[major_subtopic_id])
    minor_topics = db.relationship('Topic', secondary=question_minor_topics, lazy='select',
                                   backref=db.backref('minor_questions', lazy='dynamic'))
    subtopics = db.relationship('Subtopic', secondary=question_subtopics, lazy='select',
                               backref=db.backref('questions', lazy='dynamic'))
    chapter = db.relationship('Chapter', foreign_keys=[chapter_id])
    subchapter = db.relationship('Subchapter', foreign_keys=[subchapter_id])
    
    def __repr__(self):
        return f'<Question {self.qid}>'

class QuestionAsset(db.Model):
    """QuestionAsset model - represents physical files for a question"""
    __tablename__ = 'question_assets'
    
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id'), nullable=False, index=True)
    asset_type = db.Column(db.Enum('QUE', 'ANS', 'SOL', name='asset_type_enum'), nullable=False)
    file_format = db.Column(db.Enum('IMG', 'DOC', 'MD', name='file_format_enum'), nullable=False)
    language = db.Column(db.Enum('EN', 'CH', 'BI', name='language_enum'), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)  # Relative path (always forward-slash separated)
    part_number = db.Column(db.Integer, nullable=False, default=1)  # For multi-image questions (1, 2, 3...)
    
    __table_args__ = (
        db.UniqueConstraint('question_id', 'asset_type', 'language', 'file_format', 'part_number',
                            name='uq_asset_identity'),
    )
    
    def __repr__(self):
        return f'<QuestionAsset {self.question_id} - {self.asset_type} ({self.language}) part {self.part_number}>'


class SavedFilter(db.Model):
    """Saved search profile for reusable filter configurations"""
    __tablename__ = 'saved_filters'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    filter_data = db.Column(db.Text, nullable=False)  # JSON blob of filter state
    is_starred = db.Column(db.Boolean, default=False, nullable=False, index=True)
    is_shared = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    user = db.relationship('User', backref=db.backref('saved_filters', lazy='dynamic'))
    
    def __repr__(self):
        return f'<SavedFilter {self.name}>'


class SavedGenerationProfile(db.Model):
    """Saved generation options preset for reusable document-generation configurations"""
    __tablename__ = 'saved_generation_profiles'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    options_data = db.Column(db.Text, nullable=False)  # JSON blob of generation options (no question_ids)
    is_starred = db.Column(db.Boolean, default=False, nullable=False, index=True)
    is_shared = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship('User', backref=db.backref('saved_generation_profiles', lazy='dynamic'))

    def __repr__(self):
        return f'<SavedGenerationProfile {self.name}>'


class SavedQuestionSet(db.Model):
    """Saved question set (subject-tied, named list of question DB IDs).

    Used by the dashboard "Set" feature: users save the current selection
    (or the result of a set-algebra operation) under a name, and reload it
    later as a chip in the set-operations modal or apply it directly via
    `/dashboard/?question_set_id=<id>`.

    The payload (`question_ids`) is a JSON list of integer Question.id values
    materialised at save time — not a formula. To change the contents, save
    again under the same name (upsert) or under a new name.
    """
    __tablename__ = 'saved_question_sets'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    question_ids = db.Column(db.Text, nullable=False)  # JSON list of int IDs
    is_starred = db.Column(db.Boolean, default=False, nullable=False, index=True)
    is_shared = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship('User', backref=db.backref('saved_question_sets', lazy='dynamic'))

    def __repr__(self):
        return f'<SavedQuestionSet {self.subject}/{self.name}>'


class FileSection(db.Model):
    """User-owned folder/section that groups generated files in My Files.

    Each user has at least one row with `is_default=True` (the auto-created
    "Latest" inbox) that cannot be deleted or renamed; new files always land
    there until the user explicitly moves them. Sections beyond the default
    are user-created and freely renameable / deletable; deleting a section
    moves its files back to the default.

    Sort behaviour for files inside the section is server-applied per
    `sort_field` + `sort_direction`; `manual` means use each file's
    `GeneratedFile.manual_position`.
    """
    __tablename__ = 'file_sections'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    sort_field = db.Column(db.String(20), default='created_at', nullable=False)  # name | created_at | completed_at | question_count | manual
    sort_direction = db.Column(db.String(4), default='desc', nullable=False)  # asc | desc
    page_size = db.Column(db.Integer, default=10, nullable=False)
    collapsed = db.Column(db.Boolean, default=False, nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'name', name='uq_section_user_name'),
    )

    user = db.relationship('User', backref=db.backref('file_sections', lazy='dynamic', cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<FileSection u={self.user_id} {self.name!r}{" [default]" if self.is_default else ""}>'


class FileShare(db.Model):
    """Per-target-user share of either a single GeneratedFile or a whole
    FileSection. Recipients see read-only rows in My Files; ownership stays
    with the sharer and the shared rows cannot be moved/renamed/deleted by
    the recipient.

    Exactly one of (file_id, section_id) is non-NULL — enforced by a CHECK
    constraint. Sharing a section transitively shares every file currently
    or later assigned to it (resolution happens at query time).
    """
    __tablename__ = 'file_shares'

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('generated_files.id', ondelete='CASCADE'), nullable=True, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey('file_sections.id', ondelete='CASCADE'), nullable=True, index=True)
    shared_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    shared_with_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.CheckConstraint(
            '((file_id IS NOT NULL) + (section_id IS NOT NULL)) = 1',
            name='ck_share_xor',
        ),
        db.UniqueConstraint('file_id', 'shared_with_user_id', name='uq_file_share_target'),
        db.UniqueConstraint('section_id', 'shared_with_user_id', name='uq_section_share_target'),
    )

    file = db.relationship('GeneratedFile', backref=db.backref('shares', lazy='dynamic', cascade='all, delete-orphan'))
    section = db.relationship('FileSection', backref=db.backref('shares', lazy='dynamic', cascade='all, delete-orphan'))
    shared_by = db.relationship('User', foreign_keys=[shared_by_user_id])
    shared_with = db.relationship('User', foreign_keys=[shared_with_user_id])

    def __repr__(self):
        kind = f'file={self.file_id}' if self.file_id else f'section={self.section_id}'
        return f'<FileShare {kind} -> u={self.shared_with_user_id}>'


class GeneratedFile(db.Model):
    """Tracks background-generated Word documents"""
    __tablename__ = 'generated_files'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    display_name = db.Column(db.String(200), nullable=False)   # User-chosen display name
    filename = db.Column(db.String(300), nullable=False)        # Actual filename on disk
    status = db.Column(db.String(20), default='pending', nullable=False)  # pending/generating/completed/failed
    error_message = db.Column(db.Text, nullable=True)
    filter_data = db.Column(db.Text, nullable=True)             # JSON - saved filter state from dashboard
    generation_options = db.Column(db.Text, nullable=True)      # JSON - answer_mode, spacing, etc.
    question_count = db.Column(db.Integer, default=0)
    section_id = db.Column(db.Integer, db.ForeignKey('file_sections.id', ondelete='SET NULL'), nullable=True, index=True)
    manual_position = db.Column(db.Integer, default=0, nullable=False)  # used when section.sort_field == 'manual'
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref=db.backref('generated_files', lazy='dynamic'))
    section = db.relationship('FileSection', foreign_keys=[section_id], backref=db.backref('files', lazy='dynamic'))

    def __repr__(self):
        return f'<GeneratedFile {self.display_name} ({self.status})>'


class SystemSetting(db.Model):
    """Key-value table for runtime-tunable system settings.

    The full source of truth for each setting's type, default, label, and
    validator lives in `app/settings.py` (the REGISTRY). This table only
    stores DB-side overrides of the .env / Config defaults — when a row
    is absent for a key, the bootstrap default applies.

    Values are JSON-encoded so we can store ints, floats, bools, and
    strings through the same column without per-type schema churn.
    """
    __tablename__ = 'system_settings'

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    updated_by_user = db.relationship('User', foreign_keys=[updated_by])

    def __repr__(self):
        return f'<SystemSetting {self.key}={self.value!r}>'

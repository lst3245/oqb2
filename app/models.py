"""
Database models for the Online Question Bank system
"""
from app import db
from flask_login import UserMixin
from datetime import datetime
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from werkzeug.security import generate_password_hash, check_password_hash

# Stored in users.password_hash for accounts that have no password (SMS JIT).
UNUSABLE_PASSWORD_PREFIX = '!'

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
    # JSON {"user": {"root", "path"}, "admin": {"root", "path"}}. Last folder
    # open in each file-browser scope. The per-user browser and every
    # OQBFileSelector share the `user` entry; `/admin/files` uses `admin`.
    file_browser_location = db.Column(db.Text, nullable=True)

    # Account state and SMS-sourced profile (docs/modules/sms-sso.md). Named
    # `active`, not `is_active`: Flask-Login reads the `is_active` property.
    active = db.Column(db.Boolean, default=True, nullable=False, server_default=db.text('1'))
    disabled_reason = db.Column(db.String(32), nullable=True)  # 'sms_deprovisioned' | 'local'
    disabled_at = db.Column(db.DateTime, nullable=True)
    # Display / audit only; never used to authorise or to link.
    display_name = db.Column(db.String(200), nullable=True)
    name_zh = db.Column(db.String(200), nullable=True)
    email = db.Column(db.String(254), nullable=True)
    staff_code = db.Column(db.String(32), nullable=True)
    # Part of the Flask-Login id ("<id>:<version>"); bumping it ends every
    # session and remember cookie of this user at their next request.
    session_version = db.Column(db.Integer, default=0, nullable=False, server_default=db.text('0'))
    # Break-glass / password-only account: never linked to an SMS identity.
    local_only = db.Column(db.Boolean, default=False, nullable=False, server_default=db.text('0'))
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_method = db.Column(db.String(16), nullable=True)

    # Relationships
    subject_permissions = db.relationship('UserSubjectPermission', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    def get_id(self):
        return f'{self.id}:{self.session_version or 0}'

    @property
    def is_active(self):
        return self.active is not False

    @property
    def is_privileged(self):
        """Super admins and local-only accounts are never linked to SMS."""
        return bool(self.is_super_admin or self.local_only)

    def bump_session_version(self):
        self.session_version = (self.session_version or 0) + 1

    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Check if password matches. Accounts without a password carry a
        hash starting with '!' and never match."""
        if not self.password_hash or self.password_hash.startswith(UNUSABLE_PASSWORD_PREFIX):
            return False
        return check_password_hash(self.password_hash, password)

    # Every helper below reads the effective level (app/authz.py): the
    # highest of the personal grant, the user's groups and SMS levels.

    def has_subject_access(self, subject_id):
        """Any access (viewer, user or admin) to a subject."""
        from app import authz
        return authz.has_level(self, subject_id, 'viewer')

    def is_subject_admin(self, subject_id):
        from app import authz
        return authz.has_level(self, subject_id, 'admin')

    def can_generate_in(self, subject_id):
        """'user' or above on this subject: may put its questions in a paper."""
        from app import authz
        return authz.has_level(self, subject_id, 'user')

    def get_accessible_subjects(self):
        """Subject ids the user can at least view."""
        from app import authz
        return authz.subjects_with_level(self, 'viewer')

    def get_admin_subjects(self):
        from app import authz
        return authz.subjects_with_level(self, 'admin')

    def get_generate_subjects(self):
        """Subject ids at 'user' or above."""
        from app import authz
        return authz.subjects_with_level(self, 'user')

    def has_any_admin_access(self):
        from app import authz
        return authz.any_level(self, 'admin')

    def is_view_only(self, subject_id):
        from app import authz
        if authz.is_super_admin(self):
            return False
        return authz.effective_level(self, subject_id) == 'viewer'

    def can_generate(self):
        """'user' or above on at least one subject."""
        from app import authz
        return authz.any_level(self, 'user')

    def is_all_view_only(self):
        """No subject above 'viewer' (also true with no access at all)."""
        from app import authz
        return not authz.any_level(self, 'user')

    def get_subject_roles(self):
        """``{subject_id: role}``; super admins get 'admin' everywhere."""
        from app import authz
        return dict(authz.effective_levels(self))

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


class UserGroup(db.Model):
    """Named set of per-subject roles. Members inherit the highest role
    across every group they belong to. A group never lowers a personal
    grant or an SMS level, and it never grants super admin.
    See docs/modules/user-groups.md."""
    __tablename__ = 'user_groups'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<UserGroup {self.name}>'


class UserGroupPermission(db.Model):
    """One role on one subject, granted by a group (not by a person)."""
    __tablename__ = 'user_group_permissions'

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('user_groups.id', ondelete='CASCADE'),
                         nullable=False)
    # No ON DELETE from subjects: delete_subject removes these rows first,
    # the same way it removes user_subject_permissions.
    subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False)  # 'viewer', 'user', or 'admin'

    __table_args__ = (db.UniqueConstraint('group_id', 'subject_id', name='uq_group_subject'),)

    def __repr__(self):
        return f'<UserGroupPermission {self.group_id} - {self.subject_id}: {self.role}>'


class UserGroupMember(db.Model):
    """Membership only. Erasing a user deletes these rows; the group stays."""
    __tablename__ = 'user_group_members'

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('user_groups.id', ondelete='CASCADE'),
                         primary_key=True, index=True)

    def __repr__(self):
        return f'<UserGroupMember {self.user_id} in {self.group_id}>'


class Subject(db.Model):
    """Subject model (MATC, MAT1, MAT2, ICT, etc.)"""
    __tablename__ = 'subjects'
    
    id = db.Column(db.String(10), primary_key=True)  # e.g., 'MATC'
    name = db.Column(db.String(100), nullable=False)  # e.g., 'Mathematics Compulsory'
    # Optional Traditional Chinese name; `name` stays the English one.
    # Displayed through app.i18n.display_name / doc_name (ADR-017).
    name_zh = db.Column(db.String(100), nullable=True)
    # Seeds the PDF-import "split into parts" checkbox. False for math-style
    # subjects (one image per numbered question). Wired in the import wizard
    # in a later phase; the column is additive and defaults off.
    split_parts_default = db.Column(db.Boolean, default=False, nullable=False)
    # Teacher-facing topic / subtopic names carry the saved-list number as
    # plain text ("01 Basic Algebra", "1.1 Law of Indices"; app/tax_numbers.py).
    # Topic on, subtopic off. Independent of the Generate-page numbering option.
    topic_number_prefix = db.Column(db.Boolean, default=True, nullable=False)
    subtopic_number_prefix = db.Column(db.Boolean, default=False, nullable=False)
    # Generate-page "spacing by marks" rules (JSON, see app/marks_spacing.py).
    # NULL = no rules; the generator falls back to the manual lines value.
    marks_spacing = db.Column(db.Text, nullable=True)
    
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
    name = db.Column(db.String(200), nullable=False)  # English (canonical)
    name_zh = db.Column(db.String(200), nullable=True)  # Traditional Chinese, optional
    hidden = db.Column(db.Boolean, default=False, nullable=False)  # Hidden topics (e.g. old syllabus)
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    # Optional one-line tagging hint shown to the auto-tag LLM next to the
    # name ("what belongs here / what does not"). Never shown to students.
    description = db.Column(db.String(300), nullable=True)

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
    name_zh = db.Column(db.String(200), nullable=True)
    hidden = db.Column(db.Boolean, default=False, nullable=False)  # Hidden subtopics (e.g. textbook chapters)
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    description = db.Column(db.String(300), nullable=True)  # auto-tag hint

    def __repr__(self):
        return f'<Subtopic {self.name}>'

class Chapter(db.Model):
    """Chapter model - for organizing questions by textbook chapters"""
    __tablename__ = 'chapters'
    
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    name_zh = db.Column(db.String(200), nullable=True)
    hidden = db.Column(db.Boolean, default=False, nullable=False)  # Hidden chapters
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    description = db.Column(db.String(300), nullable=True)  # auto-tag hint

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
    name_zh = db.Column(db.String(200), nullable=True)
    hidden = db.Column(db.Boolean, default=False, nullable=False)  # Hidden subchapters
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # For custom ordering
    description = db.Column(db.String(300), nullable=True)  # auto-tag hint
    
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
    qno = db.Column(db.Integer, nullable=False)  # Integer start of the QNO token (Q5 → 5, Q23-24 → 23)
    # Inclusive end of a range stem (`Q23-24`). NULL for standalone questions
    # and for letter/roman parts. Mutually exclusive with `part`.
    qno_end = db.Column(db.Integer, nullable=True)
    # Self-FK: a part/leaf points at its stem (or intermediate parent). NULL
    # for roots. ON DELETE RESTRICT — drop children first (see delete_questions).
    parent_id = db.Column(db.Integer, db.ForeignKey('questions.id', ondelete='RESTRICT'),
                          nullable=True, index=True)
    # Own label under the parent only (`a`, `i`, `ii`), not the full path.
    # Distinct from QuestionAsset.part_number (IMG page N of one slot).
    part = db.Column(db.String(10), nullable=True)
    part_sort = db.Column(db.Integer, nullable=True)  # letter 1–26, roman 101–110
    # Shared blocks only (`part` = `~b`): the last part segment the block owns
    # at its level (`b`, `iii`). NULL = up to the next block / end of question.
    # Ignored on every other row. See ADR-015 / hierarchy.set_block_end.
    block_end = db.Column(db.String(10), nullable=True)
    # A part that refers to the results/setup of earlier sibling parts
    # ("using your answer to (a)..."). When set, viewing/generating this leaf
    # alone also renders its earlier siblings as background (see
    # hierarchy.resolve_render_plan). Set by admins or the PDF import agent.
    needs_prev_parts = db.Column(db.Boolean, default=False, nullable=False)
    q_type = db.Column(db.String(10), nullable=True)  # MC, CQ, or NULL
    level = db.Column(db.Integer, nullable=True)  # 1, 2, 3, or NULL
    major_topic_id = db.Column(db.Integer, db.ForeignKey('topics.id'), nullable=True, index=True)
    major_subtopic_id = db.Column(db.Integer, db.ForeignKey('subtopics.id'), nullable=True, index=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey('chapters.id', ondelete='SET NULL'), nullable=True, index=True)
    subchapter_id = db.Column(db.Integer, db.ForeignKey('subchapters.id', ondelete='SET NULL'), nullable=True, index=True)
    description = db.Column(db.Text, nullable=True)  # Optional description for the question
    correct_percentage = db.Column(db.Integer, nullable=True)  # 0-100, NULL if unknown (public exam correct rate)
    # Marks printed for this leaf ("(2 marks)"). Stems / shared blocks never
    # store marks; the whole-question total is hierarchy.whole_marks(root).
    marks = db.Column(db.SmallInteger, nullable=True)
    answer = db.Column(db.Text, nullable=True)  # Answer text (alternative to ANS image)
    comment = db.Column(db.Text, nullable=True)  # Comment / notes
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Whole-question verification flag. Set manually by an admin once every
    # asset is checked and the tagging is satisfactory. Independent of the
    # per-asset QuestionAsset.check_state proofreading, but the UI soft-warns
    # when verifying a question whose assets are not all checked-ok.
    verified = db.Column(db.Boolean, default=False, nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    verified_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Relationships
    assets = db.relationship('QuestionAsset', backref='question', lazy='dynamic', cascade='all, delete-orphan')
    verified_by_user = db.relationship('User', foreign_keys=[verified_by])
    major_subtopic = db.relationship('Subtopic', foreign_keys=[major_subtopic_id])
    minor_topics = db.relationship('Topic', secondary=question_minor_topics, lazy='select',
                                   backref=db.backref('minor_questions', lazy='dynamic'))
    subtopics = db.relationship('Subtopic', secondary=question_subtopics, lazy='select',
                               backref=db.backref('questions', lazy='dynamic'))
    chapter = db.relationship('Chapter', foreign_keys=[chapter_id])
    subchapter = db.relationship('Subchapter', foreign_keys=[subchapter_id])
    parent = db.relationship(
        'Question',
        remote_side='Question.id',
        foreign_keys='Question.parent_id',
        backref=db.backref('children', lazy='select'),
    )

    def __repr__(self):
        return f'<Question {self.qid}>'

class QuestionAsset(db.Model):
    """QuestionAsset model - represents physical files for a question"""
    __tablename__ = 'question_assets'
    
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id'), nullable=False, index=True)
    # WHOLE is the unsplit original on a *root* only (PDF pass-1 / Split
    # snapshot). Dashboard, generator, viewer and Explain never read it.
    asset_type = db.Column(db.Enum('QUE', 'ANS', 'SOL', 'WHOLE', name='asset_type_enum'), nullable=False)
    file_format = db.Column(db.Enum('IMG', 'DOC', 'MD', name='file_format_enum'), nullable=False)
    # Version of the asset (formerly "language"). EN/CH/BI plus the official
    # public-exam scans ENO/CHO. See app/utils.VERSIONS.
    version = db.Column(db.Enum('EN', 'CH', 'BI', 'ENO', 'CHO', name='version_enum'), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)  # Relative path (always forward-slash separated)
    part_number = db.Column(db.Integer, nullable=False, default=1)  # For multi-image questions (1, 2, 3...)

    # AI Tools proofreading state (see app/ai_tools.py). Set when a typed
    # version is checked against an official scan via an LLM.
    #   check_state: None (never checked) / 'checking' / 'ok' / 'issues' / 'error'
    #   check_result: JSON blob {status, issues[], model, ref_version, checked_by, ...}
    #   check_raw: verbatim LLM reply from the last AI proofread/solve-check (debug)
    #   checked_at: UTC timestamp of the last check
    check_state = db.Column(db.String(20), nullable=True)
    check_result = db.Column(db.Text, nullable=True)
    check_raw = db.Column(db.Text, nullable=True)
    checked_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('question_id', 'asset_type', 'version', 'file_format', 'part_number',
                            name='uq_asset_identity'),
    )
    
    def __repr__(self):
        return f'<QuestionAsset {self.question_id} - {self.asset_type} ({self.version}) part {self.part_number}>'


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


class PromptOverride(db.Model):
    """LEGACY editable storage for AI prompt templates (one override row per
    prompt key). Superseded by :class:`PromptVariant` — kept only so existing
    rows can be migrated into the built-in variant's content on startup
    (see ``ai_prompts.ensure_seeded``). Do not write new rows.
    """
    __tablename__ = 'prompt_overrides'

    key = db.Column(db.String(80), primary_key=True)
    content = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    updated_by_user = db.relationship('User', foreign_keys=[updated_by])

    def __repr__(self):
        return f'<PromptOverride {self.key} ({len(self.content or "")} chars)>'


class PromptVariant(db.Model):
    """One named version of an AI prompt template.

    Each registry key (see ``app/ai_prompts.PROMPTS_REGISTRY``) has exactly
    one built-in variant (``is_builtin=True``, seeded at startup) plus any
    number of admin-created custom variants. ``content`` may be NULL on the
    built-in row, meaning "use the registry bootstrap default" — resetting
    the built-in nulls its content rather than deleting the row.

    Exactly one variant per key carries ``is_active=True``: the default used
    for every LLM endpoint that has no explicit
    :class:`PromptEndpointAssignment` for that key.
    """
    __tablename__ = 'prompt_variants'

    id = db.Column(db.Integer, primary_key=True)
    prompt_key = db.Column(db.String(80), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    # NULL on the built-in row = bootstrap default from PROMPTS_REGISTRY.
    content = db.Column(db.Text, nullable=True)
    is_builtin = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    updated_by_user = db.relationship('User', foreign_keys=[updated_by])

    __table_args__ = (
        db.UniqueConstraint('prompt_key', 'name', name='uq_prompt_variant_key_name'),
    )

    def __repr__(self):
        flags = ('builtin' if self.is_builtin else 'custom') + (
            ',active' if self.is_active else '')
        return f'<PromptVariant {self.prompt_key}/{self.name} ({flags})>'


class PromptEndpointAssignment(db.Model):
    """Pins one LLM endpoint to a specific prompt variant for one key.

    Endpoints without a row for a given key use that key's active variant.
    Rows are removed when the endpoint or the variant is deleted (FK
    CASCADE + explicit cleanup in the admin routes).
    """
    __tablename__ = 'prompt_endpoint_assignments'

    id = db.Column(db.Integer, primary_key=True)
    prompt_key = db.Column(db.String(80), nullable=False, index=True)
    endpoint_id = db.Column(db.Integer,
                            db.ForeignKey('llm_configs.id', ondelete='CASCADE'),
                            nullable=False)
    variant_id = db.Column(db.Integer,
                           db.ForeignKey('prompt_variants.id', ondelete='CASCADE'),
                           nullable=False)

    endpoint = db.relationship('LLMConfig', foreign_keys=[endpoint_id])
    variant = db.relationship('PromptVariant', foreign_keys=[variant_id])

    __table_args__ = (
        db.UniqueConstraint('prompt_key', 'endpoint_id',
                            name='uq_prompt_assignment_key_endpoint'),
    )


class SubjectPromptNote(db.Model):
    """Subject-admin-owned instructions layered onto an AI feature's prompt.

    One row per ``(subject_id, feature)``. ``feature`` is a short feature key
    (currently only ``'tag'`` — auto tagging); it is NOT a
    ``PROMPTS_REGISTRY`` key, because a note may touch several registry
    prompts. ``mode`` is ``'append'`` (content is added to the user turn as a
    "subject-specific instructions" block) or ``'replace'`` (content
    replaces the BODY of the feature's system prompt; the output-format
    block is always re-attached by the server so the JSON contract cannot be
    broken from here). ``examples_limit`` > 0 injects that many aggregated
    teacher-correction patterns (see :class:`TagCorrection`) into the prompt.

    Super-admin prompt variants / endpoint pins still decide the frame; this
    row only fills the ``{{subject_instructions}}`` slot or the body.
    """
    __tablename__ = 'subject_prompt_notes'

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.String(10),
                           db.ForeignKey('subjects.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    feature = db.Column(db.String(30), nullable=False, default='tag')
    mode = db.Column(db.String(10), nullable=False, default='append')  # append | replace
    content = db.Column(db.Text, nullable=True)
    examples_limit = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    updated_by_user = db.relationship('User', foreign_keys=[updated_by])

    __table_args__ = (
        db.UniqueConstraint('subject_id', 'feature',
                            name='uq_subject_prompt_note_subject_feature'),
    )

    def __repr__(self):
        return f'<SubjectPromptNote {self.subject_id}/{self.feature} ({self.mode})>'


class TagCorrection(db.Model):
    """One (field, model suggestion, teacher decision) pair recorded when a
    teacher saves tags right after an Auto Tag suggestion in the edit modal.

    ``suggested`` / ``saved`` hold DISPLAY names (comma-joined for the list
    fields), never IDs, so the report stays readable after taxonomy edits.
    ``agreed`` is True when the saved value equals the suggestion (set
    equality for list fields). Rows are the raw material for the per-subject
    disagreement report and for the aggregated correction patterns that
    :class:`SubjectPromptNote.examples_limit` feeds back into the prompt.
    """
    __tablename__ = 'tag_corrections'

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer,
                            db.ForeignKey('questions.id', ondelete='CASCADE'),
                            nullable=False, index=True)
    subject_id = db.Column(db.String(10), nullable=False, index=True)
    field = db.Column(db.String(30), nullable=False)
    suggested = db.Column(db.String(500), nullable=True)
    saved = db.Column(db.String(500), nullable=True)
    agreed = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.String(400), nullable=True)   # model's own one-line reason, if any
    model = db.Column(db.String(200), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    question = db.relationship('Question', foreign_keys=[question_id])
    user = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<TagCorrection q{self.question_id} {self.field} {"=" if self.agreed else "!="}>'

    def __repr__(self):
        return (f'<PromptEndpointAssignment {self.prompt_key} '
                f'ep={self.endpoint_id} -> var={self.variant_id}>')


class LLMConfig(db.Model):
    """A configured LLM endpoint for the AI Tools feature.

    Endpoints speak either the OpenAI-compatible Chat Completions protocol
    (``POST {base_url}/chat/completions``) or the Responses API
    (``POST {base_url}/responses``). A single client adapter in
    ``app/llm_client.py`` serves local servers (Ollama, LM Studio,
    vLLM, ...) and cloud providers (OpenAI, OpenRouter, Poe, ...).

    API-key handling is hybrid: ``api_key_enc`` holds an optional
    Fernet-encrypted key entered through the admin UI; when empty the
    client falls back to ``os.getenv(api_key_env or 'LLM_API_KEY')`` from
    the ``.env`` file. The plaintext key is NEVER stored or returned to
    the browser.
    """
    __tablename__ = 'llm_configs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    base_url = db.Column(db.String(500), nullable=False)
    model_name = db.Column(db.String(200), nullable=False)
    provider = db.Column(db.String(40), nullable=False, default='openai')
    # Fernet ciphertext of the API key (optional; blank => use .env fallback).
    api_key_enc = db.Column(db.Text, nullable=True)
    # Name of the .env var to read when no per-endpoint key is stored.
    api_key_env = db.Column(db.String(80), nullable=True)
    supports_vision = db.Column(db.Boolean, nullable=False, default=True)
    # 'local' (single-threaded box) or 'cloud' (hosted API that tolerates
    # concurrent requests). Only cloud endpoints may run batch ops in parallel.
    kind = db.Column(db.String(10), nullable=False, default='local')
    # Max in-flight requests when a batch op runs in parallel (cloud only).
    max_concurrency = db.Column(db.Integer, nullable=False, default=1)
    # Optional OpenAI/OpenRouter service tier sent as the top-level
    # ``service_tier`` request param ('' = omit / provider default; e.g.
    # 'flex' = cheaper+slower, 'priority' = faster+pricier, 'auto').
    # ``service_tier`` applies to single / interactive calls; the batch SSE
    # ops (check / generate-md / auto-tag / pdf-detect) use
    # ``service_tier_batch`` instead, so e.g. batch can run on cheap 'flex'
    # while a single question stays on the normal tier.
    service_tier = db.Column(db.String(20), nullable=False, default='')
    service_tier_batch = db.Column(db.String(20), nullable=False, default='')
    # 'chat' (Chat Completions) or 'responses' (OpenAI Responses API).
    api_protocol = db.Column(db.String(12), nullable=False, default='chat')
    # Bounding-box axis order this model answers in: 'xyxy' / 'yxyx'.
    # '' = inherit PDF_IMPORT_COORD_ORDER (llm_client.box_coord_order).
    box_coord_order = db.Column(db.String(4), nullable=False, default='')
    # Reasoning controls. Blank = inherit from LLM_REASONING_* system settings.
    # 'off' = never send reasoning params for this endpoint.
    reasoning_effort = db.Column(db.String(10), nullable=False, default='')
    reasoning_summary = db.Column(db.String(10), nullable=False, default='')
    # 0/NULL = omit max reasoning tokens from the request.
    reasoning_max_tokens = db.Column(db.Integer, nullable=True)
    # Optional provider-specific JSON merged into the request body.
    request_extra_json = db.Column(db.Text, nullable=True)
    max_output_tokens = db.Column(db.Integer, nullable=False, default=4096)
    temperature = db.Column(db.Float, nullable=False, default=0.0)
    timeout_seconds = db.Column(db.Integer, nullable=False, default=120)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<LLMConfig {self.name} ({self.model_name})>'


class SubjectRestorePoint(db.Model):
    """One subject's question tags + topic/chapter lists as they were just
    before a tag or taxonomy save (or a manual snapshot). ``payload`` is the
    JSON written by ``app/subject_snapshot.encode_state``; never edit it by
    hand. Pruned to ``subject_snapshot.KEEP_PER_SUBJECT`` per subject on every
    capture, except rows with ``pinned`` set (kept until an admin unpins them).
    See docs/modules/subject-snapshots.md and ADR-013.
    """
    __tablename__ = 'subject_restore_points'

    id = db.Column(db.Integer, primary_key=True)
    # No FK: points must survive taxonomy churn; subject delete clears them
    # explicitly (subject_snapshot.delete_for_subject).
    subject_id = db.Column(db.String(10), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'),
                        nullable=True)
    action = db.Column(db.String(30), nullable=False)
    note = db.Column(db.String(200), nullable=True)
    # Set from the Restore Points page. prune never deletes a pinned row;
    # subject delete still does (delete_for_subject).
    pinned = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    payload = db.Column(db.Text().with_variant(MEDIUMTEXT(), 'mysql', 'mariadb'),
                        nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<SubjectRestorePoint #{self.id} {self.subject_id} {self.action}>'


# ---------------------------------------------------------------------------
# SMS sign-in (docs/modules/sms-sso.md). Created by app/sso_schema.py. InnoDB
# and utf8mb4_unicode_ci are pinned: the live server has MyISAM defaults on
# some tables, and FKs need the collation of users.id / subjects.id. Indexed
# strings stay <= 191 chars (767-byte key limit on COMPACT rows).
# ---------------------------------------------------------------------------

_SSO_TABLE = {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4',
              'mysql_collate': 'utf8mb4_unicode_ci'}
_LONG_TEXT = db.Text().with_variant(MEDIUMTEXT(), 'mysql', 'mariadb')


class ExternalIdentity(db.Model):
    """A link between an SMS identity ``(issuer, sub)`` and one OQB user."""
    __tablename__ = 'external_identities'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    issuer = db.Column(db.String(191), nullable=False)
    sub = db.Column(db.String(64), nullable=False)
    link_method = db.Column(db.String(8), nullable=False)  # self | admin | jit
    linked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    linked_by_user_id = db.Column(db.Integer, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_seen_snapshot_at = db.Column(db.DateTime, nullable=True)
    # Set when a snapshot no longer lists this sub (flag for admin review).
    missing_since = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('issuer', 'sub', name='uq_extid_issuer_sub'),
        db.UniqueConstraint('issuer', 'user_id', name='uq_extid_issuer_user'),
        _SSO_TABLE,
    )


class UserSmsLevel(db.Model):
    """SMS-sourced levels, replaced per user per issuer on every handoff and
    snapshot. ``level`` holds the OQB level name (app/sms_levels.py converts).
    ``sms_subject_uuid == ''`` is the row holding ``default_level``."""
    __tablename__ = 'user_sms_levels'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False)
    issuer = db.Column(db.String(191), nullable=False)
    sms_subject_uuid = db.Column(db.String(64), nullable=False, default='', server_default='')
    sms_subject_code = db.Column(db.String(32), nullable=True)
    level = db.Column(db.String(10), nullable=False)
    source = db.Column(db.String(10), nullable=False)  # handoff | snapshot
    synced_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'issuer', 'sms_subject_uuid', name='uq_sms_level'),
        db.Index('ix_sms_level_uuid', 'sms_subject_uuid'),
        _SSO_TABLE,
    )


class SmsSubjectMap(db.Model):
    """Admin-confirmed mapping: one SMS subject uuid -> many OQB subjects."""
    __tablename__ = 'sms_subject_map'

    id = db.Column(db.Integer, primary_key=True)
    sms_uuid = db.Column(db.String(64), nullable=False)
    oqb_subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id', ondelete='CASCADE'),
                               nullable=False)
    confirmed_by_user_id = db.Column(db.Integer, nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('sms_uuid', 'oqb_subject_id', name='uq_sms_map'),
        db.Index('ix_sms_map_subject', 'oqb_subject_id'),
        _SSO_TABLE,
    )


class SmsSubjectCatalogue(db.Model):
    """SMS subjects seen in snapshots (full catalogue) or tokens."""
    __tablename__ = 'sms_subject_catalogue'

    sms_uuid = db.Column(db.String(64), primary_key=True)
    code = db.Column(db.String(32), nullable=False)
    name_en = db.Column(db.String(200), nullable=False, default='')
    name_zh = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    seen_in = db.Column(db.String(10), nullable=False)  # snapshot | token
    issuer = db.Column(db.String(191), nullable=False)
    first_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (_SSO_TABLE,)


class SmsRoster(db.Model):
    """The latest snapshot's users (for admin linking). Never authorises."""
    __tablename__ = 'sms_roster'

    id = db.Column(db.Integer, primary_key=True)
    issuer = db.Column(db.String(191), nullable=False)
    sub = db.Column(db.String(64), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    email = db.Column(db.String(254), nullable=True)
    name = db.Column(db.String(200), nullable=True)
    name_zh = db.Column(db.String(200), nullable=True)
    staff_code = db.Column(db.String(32), nullable=True)
    default_level = db.Column(db.String(10), nullable=True)
    subjects_json = db.Column(db.Text, nullable=True)
    snapshot_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('issuer', 'sub', name='uq_sms_roster'),
        _SSO_TABLE,
    )


class SmsFeedState(db.Model):
    """Last applied snapshot per issuer; read FOR UPDATE to serialise pushes."""
    __tablename__ = 'sms_feed_state'

    issuer = db.Column(db.String(191), primary_key=True)
    last_iat = db.Column(db.BigInteger, nullable=False, default=0, server_default='0')
    last_snapshot_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (_SSO_TABLE,)


class SmsSnapshot(db.Model):
    """One received snapshot (applied / held / approved / discarded / superseded)."""
    __tablename__ = 'sms_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    issuer = db.Column(db.String(191), nullable=False)
    snapshot_id = db.Column(db.String(64), nullable=False)
    jti = db.Column(db.String(64), nullable=False)
    iat = db.Column(db.BigInteger, nullable=False)
    received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(12), nullable=False)
    counts_json = db.Column(db.Text, nullable=True)
    # Held deactivations: subs the brake stopped (approve applies them).
    held_subs_json = db.Column(_LONG_TEXT, nullable=True)
    unmapped_json = db.Column(db.Text, nullable=True)
    decided_by_user_id = db.Column(db.Integer, nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('ix_sms_snapshot_issuer_iat', 'issuer', 'iat'),
        _SSO_TABLE,
    )


class SsoUsedJti(db.Model):
    """Single-use token ids, shared by every process (unique key = replay guard)."""
    __tablename__ = 'sso_used_jti'

    id = db.Column(db.Integer, primary_key=True)
    issuer = db.Column(db.String(191), nullable=False)
    jti = db.Column(db.String(64), nullable=False)
    purpose = db.Column(db.String(24), nullable=False)
    exp = db.Column(db.BigInteger, nullable=False)
    used_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('issuer', 'jti', name='uq_jti'),
        db.Index('ix_jti_used_at', 'used_at'),
        _SSO_TABLE,
    )


class SsoPendingLink(db.Model):
    """Verified claims of an unlinked handoff, kept <= 10 minutes and bound to
    the browser session by a nonce (only its sha256 is stored)."""
    __tablename__ = 'sso_pending_links'

    id = db.Column(db.Integer, primary_key=True)
    nonce_hash = db.Column(db.String(64), nullable=False, unique=True)
    issuer = db.Column(db.String(191), nullable=False)
    sub = db.Column(db.String(64), nullable=False)
    claims_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    consumed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (_SSO_TABLE,)


class SmsTrustIssuer(db.Model):
    """Trust-list entry. Normalised on load by app/sms_trust.py."""
    __tablename__ = 'sms_trust_issuers'

    id = db.Column(db.Integer, primary_key=True)
    issuer = db.Column(db.String(191), nullable=False, unique=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    enabled_until = db.Column(db.DateTime, nullable=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    allow_jit = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    accepts_feed = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    allowed_subs_json = db.Column(db.Text, nullable=True)
    level_cap = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    jwks_url = db.Column(db.String(500), nullable=True)
    jwks_auto = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    jwks_seen_kids_json = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_by_user_id = db.Column(db.Integer, nullable=True)

    keys = db.relationship('SmsTrustKey', backref='issuer_entry', lazy='select',
                           cascade='all, delete-orphan', passive_deletes=True)

    __table_args__ = (_SSO_TABLE,)


class SmsTrustKey(db.Model):
    """A pinned RSA public key (>= 2048 bits) for one issuer."""
    __tablename__ = 'sms_trust_keys'

    id = db.Column(db.Integer, primary_key=True)
    issuer_id = db.Column(db.Integer, db.ForeignKey('sms_trust_issuers.id', ondelete='CASCADE'),
                          nullable=False)
    kid = db.Column(db.String(64), nullable=False)
    public_pem = db.Column(db.Text, nullable=False)
    fingerprint = db.Column(db.String(64), nullable=False)  # sha256(DER SPKI), hex
    pinned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    pinned_by_user_id = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('issuer_id', 'kid', name='uq_trust_key'),
        _SSO_TABLE,
    )


class AuthAuditLog(db.Model):
    """Sign-in, linking, feed and trust events. Never stores a token."""
    __tablename__ = 'auth_audit_log'

    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    event = db.Column(db.String(40), nullable=False, index=True)
    outcome = db.Column(db.String(8), nullable=False)  # ok | fail | info
    reason = db.Column(db.String(32), nullable=True)
    issuer = db.Column(db.String(191), nullable=True)
    sub = db.Column(db.String(64), nullable=True)
    user_id = db.Column(db.Integer, nullable=True)  # no FK: rows outlive users
    actor_user_id = db.Column(db.Integer, nullable=True)
    ip = db.Column(db.String(45), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    is_alert = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    acknowledged_by = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.Index('ix_audit_alert', 'is_alert', 'acknowledged_at'),
        _SSO_TABLE,
    )


SSO_MODELS = (
    SmsTrustIssuer, SmsTrustKey, ExternalIdentity, UserSmsLevel, SmsSubjectMap,
    SmsSubjectCatalogue, SmsRoster, SmsFeedState, SmsSnapshot, SsoUsedJti,
    SsoPendingLink, AuthAuditLog,
)

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
    
    def __repr__(self):
        return f'<User {self.username}>'


class UserSubjectPermission(db.Model):
    """User permissions for specific subjects"""
    __tablename__ = 'user_subject_permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    subject_id = db.Column(db.String(10), db.ForeignKey('subjects.id'), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False, default='user')  # 'user' or 'admin'
    
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
    file_format = db.Column(db.Enum('IMG', 'DOC', name='file_format_enum'), nullable=False)
    language = db.Column(db.Enum('EN', 'CH', 'BI', name='language_enum'), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)  # Relative path
    
    def __repr__(self):
        return f'<QuestionAsset {self.question_id} - {self.asset_type} ({self.language})>'

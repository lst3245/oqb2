"""
Admin panel routes for managing topics and tagging questions
"""
import csv
import io
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, Response, make_response
from flask_login import login_required, current_user
from app import db
from app.models import Subject, Topic, Subtopic, Question, QuestionAsset, Chapter, Subchapter, User, UserSubjectPermission
from app.utils import admin_required, super_admin_required, get_user_admin_subjects

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/')
@login_required
@admin_required
def index():
    """Admin dashboard"""
    return render_template('admin_index.html')

# ==================== Topic Management ====================

@admin_bp.route('/topics')
@login_required
@admin_required
def topics():
    """Topic and subtopic management page"""
    # Filter subjects based on user's admin access
    subjects = get_user_admin_subjects()
    
    # Get all topics with their subtopics, ordered by sort_order
    topics_data = []
    for subject in subjects:
        subject_topics = Topic.query.filter_by(subject_id=subject.id).order_by(Topic.sort_order).all()
        topics_data.append({
            'subject': subject,
            'topics': subject_topics
        })
    
    return render_template('admin_topics.html', topics_data=topics_data)

@admin_bp.route('/topics/add', methods=['POST'])
@login_required
@admin_required
def add_topic():
    """Add a new topic"""
    subject_id = request.form.get('subject_id')
    name = request.form.get('name')
    
    if not subject_id or not name:
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Get max sort_order for this subject
    max_order = db.session.query(db.func.max(Topic.sort_order)).filter_by(subject_id=subject_id).scalar() or 0
    
    topic = Topic(subject_id=subject_id, name=name, sort_order=max_order + 1)
    db.session.add(topic)
    db.session.commit()
    
    return jsonify({'id': topic.id, 'name': topic.name, 'subject_id': topic.subject_id})

@admin_bp.route('/topics/<int:topic_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_topic(topic_id):
    """Edit a topic"""
    topic = Topic.query.get_or_404(topic_id)
    name = request.form.get('name')
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    topic.name = name
    db.session.commit()
    
    return jsonify({'id': topic.id, 'name': topic.name})

@admin_bp.route('/topics/<int:topic_id>/delete', methods=['POST', 'DELETE'])
@login_required
@admin_required
def delete_topic(topic_id):
    """Delete a topic"""
    topic = Topic.query.get_or_404(topic_id)
    db.session.delete(topic)
    db.session.commit()
    
    return jsonify({'success': True})

@admin_bp.route('/subtopics/add', methods=['POST'])
@login_required
@admin_required
def add_subtopic():
    """Add a new subtopic"""
    topic_id = request.form.get('topic_id')
    name = request.form.get('name')
    hidden = request.form.get('hidden', '0') == '1'
    
    if not topic_id or not name:
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Get max sort_order for this topic
    max_order = db.session.query(db.func.max(Subtopic.sort_order)).filter_by(topic_id=int(topic_id)).scalar() or 0
    
    subtopic = Subtopic(topic_id=int(topic_id), name=name, hidden=hidden, sort_order=max_order + 1)
    db.session.add(subtopic)
    db.session.commit()
    
    return jsonify({'id': subtopic.id, 'name': subtopic.name, 'topic_id': subtopic.topic_id, 'hidden': subtopic.hidden})

@admin_bp.route('/subtopics/<int:subtopic_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_subtopic(subtopic_id):
    """Edit a subtopic"""
    subtopic = Subtopic.query.get_or_404(subtopic_id)
    name = request.form.get('name')
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    subtopic.name = name
    
    # Handle hidden flag
    if 'hidden' in request.form:
        subtopic.hidden = request.form.get('hidden') == '1'
    
    db.session.commit()
    
    return jsonify({'id': subtopic.id, 'name': subtopic.name, 'hidden': subtopic.hidden})

@admin_bp.route('/subtopics/<int:subtopic_id>/toggle-hidden', methods=['POST'])
@login_required
@admin_required
def toggle_subtopic_hidden(subtopic_id):
    """Toggle the hidden status of a subtopic"""
    subtopic = Subtopic.query.get_or_404(subtopic_id)
    subtopic.hidden = not subtopic.hidden
    db.session.commit()
    
    return jsonify({'id': subtopic.id, 'name': subtopic.name, 'hidden': subtopic.hidden})

@admin_bp.route('/subtopics/<int:subtopic_id>/delete', methods=['POST', 'DELETE'])
@login_required
@admin_required
def delete_subtopic(subtopic_id):
    """Delete a subtopic"""
    subtopic = Subtopic.query.get_or_404(subtopic_id)
    db.session.delete(subtopic)
    db.session.commit()
    
    return jsonify({'success': True})

@admin_bp.route('/topics/reorder', methods=['POST'])
@login_required
@admin_required
def reorder_topics():
    """Reorder topics within a subject"""
    try:
        data = request.get_json()
        topic_ids = data.get('topic_ids', [])
        
        for index, topic_id in enumerate(topic_ids):
            topic = Topic.query.get(topic_id)
            if topic:
                topic.sort_order = index
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/subtopics/reorder', methods=['POST'])
@login_required
@admin_required
def reorder_subtopics():
    """Reorder subtopics within a topic"""
    try:
        data = request.get_json()
        subtopic_ids = data.get('subtopic_ids', [])
        
        for index, subtopic_id in enumerate(subtopic_ids):
            subtopic = Subtopic.query.get(subtopic_id)
            if subtopic:
                subtopic.sort_order = index
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ==================== Chapter Management ====================

@admin_bp.route('/chapters')
@login_required
@admin_required
def chapters():
    """Chapter and subchapter management page"""
    # Filter subjects based on user's admin access
    subjects = get_user_admin_subjects()

    # Get all chapters with their subchapters, grouped by subject, ordered by sort_order
    chapters_data = []
    for subject in subjects:
        subject_chapters = Chapter.query.filter_by(subject_id=subject.id).order_by(Chapter.sort_order).all()
        chapters_data.append({
            'subject': subject,
            'chapters': subject_chapters
        })

    return render_template('admin_chapters.html', chapters_data=chapters_data)

@admin_bp.route('/chapters/add', methods=['POST'])
@login_required
@admin_required
def add_chapter():
    """Add a new chapter"""
    subject_id = request.form.get('subject_id')
    name = request.form.get('name')

    if not subject_id or not name:
        return jsonify({'error': 'Missing required fields'}), 400

    # Get max sort_order for this subject
    max_order = db.session.query(db.func.max(Chapter.sort_order)).filter_by(subject_id=subject_id).scalar() or 0

    chapter = Chapter(subject_id=subject_id, name=name, sort_order=max_order + 1)
    db.session.add(chapter)
    db.session.commit()

    return jsonify({'id': chapter.id, 'name': chapter.name, 'subject_id': chapter.subject_id})

@admin_bp.route('/chapters/<int:chapter_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_chapter(chapter_id):
    """Edit a chapter"""
    chapter = Chapter.query.get_or_404(chapter_id)
    name = request.form.get('name')
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    chapter.name = name
    db.session.commit()
    
    return jsonify({'id': chapter.id, 'name': chapter.name})

@admin_bp.route('/chapters/<int:chapter_id>/delete', methods=['POST', 'DELETE'])
@login_required
@admin_required
def delete_chapter(chapter_id):
    """Delete a chapter"""
    chapter = Chapter.query.get_or_404(chapter_id)
    
    # Clear chapter_id from questions (ON DELETE SET NULL may not work in SQLite)
    Question.query.filter_by(chapter_id=chapter_id).update({'chapter_id': None, 'subchapter_id': None})
    
    db.session.delete(chapter)
    db.session.commit()
    
    return jsonify({'success': True})

@admin_bp.route('/subchapters/add', methods=['POST'])
@login_required
@admin_required
def add_subchapter():
    """Add a new subchapter"""
    chapter_id = request.form.get('chapter_id')
    name = request.form.get('name')
    hidden = request.form.get('hidden', '0') == '1'

    if not chapter_id or not name:
        return jsonify({'error': 'Missing required fields'}), 400

    # Get max sort_order for this chapter
    max_order = db.session.query(db.func.max(Subchapter.sort_order)).filter_by(chapter_id=int(chapter_id)).scalar() or 0

    subchapter = Subchapter(chapter_id=int(chapter_id), name=name, hidden=hidden, sort_order=max_order + 1)
    db.session.add(subchapter)
    db.session.commit()

    return jsonify({'id': subchapter.id, 'name': subchapter.name, 'chapter_id': subchapter.chapter_id, 'hidden': subchapter.hidden})

@admin_bp.route('/subchapters/<int:subchapter_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_subchapter(subchapter_id):
    """Edit a subchapter"""
    subchapter = Subchapter.query.get_or_404(subchapter_id)
    name = request.form.get('name')
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    subchapter.name = name
    
    # Handle hidden flag
    if 'hidden' in request.form:
        subchapter.hidden = request.form.get('hidden') == '1'
    
    db.session.commit()
    
    return jsonify({'id': subchapter.id, 'name': subchapter.name, 'hidden': subchapter.hidden})

@admin_bp.route('/subchapters/<int:subchapter_id>/toggle-hidden', methods=['POST'])
@login_required
@admin_required
def toggle_subchapter_hidden(subchapter_id):
    """Toggle the hidden status of a subchapter"""
    subchapter = Subchapter.query.get_or_404(subchapter_id)
    subchapter.hidden = not subchapter.hidden
    db.session.commit()
    
    return jsonify({'id': subchapter.id, 'name': subchapter.name, 'hidden': subchapter.hidden})

@admin_bp.route('/subchapters/<int:subchapter_id>/delete', methods=['POST', 'DELETE'])
@login_required
@admin_required
def delete_subchapter(subchapter_id):
    """Delete a subchapter"""
    subchapter = Subchapter.query.get_or_404(subchapter_id)

    # Clear subchapter_id from questions
    Question.query.filter_by(subchapter_id=subchapter_id).update({'subchapter_id': None})

    db.session.delete(subchapter)
    db.session.commit()

    return jsonify({'success': True})

@admin_bp.route('/chapters/reorder', methods=['POST'])
@login_required
@admin_required
def reorder_chapters():
    """Reorder chapters within a subject"""
    try:
        data = request.get_json()
        chapter_ids = data.get('chapter_ids', [])
        
        for index, chapter_id in enumerate(chapter_ids):
            chapter = Chapter.query.get(chapter_id)
            if chapter:
                chapter.sort_order = index
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/subchapters/reorder', methods=['POST'])
@login_required
@admin_required
def reorder_subchapters():
    """Reorder subchapters within a chapter"""
    try:
        data = request.get_json()
        subchapter_ids = data.get('subchapter_ids', [])
        
        for index, subchapter_id in enumerate(subchapter_ids):
            subchapter = Subchapter.query.get(subchapter_id)
            if subchapter:
                subchapter.sort_order = index
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ==================== Question Tagging ====================

@admin_bp.route('/questions/<int:question_id>/update', methods=['POST'])
@login_required
@admin_required
def update_question(question_id):
    """Update question metadata and tags"""
    try:
        question = Question.query.get_or_404(question_id)
        
        # Update basic fields
        if 'level' in request.form:
            level = request.form.get('level')
            question.level = int(level) if level and level != '' else None
        
        if 'q_type' in request.form:
            q_type = request.form.get('q_type')
            question.q_type = q_type if q_type and q_type != '' else None
        
        if 'section' in request.form:
            section = request.form.get('section')
            question.section = section if section and section != '' else None
        
        if 'description' in request.form:
            description = request.form.get('description')
            question.description = description if description and description.strip() != '' else None
        
        if 'answer' in request.form:
            answer = request.form.get('answer')
            question.answer = answer if answer and answer.strip() != '' else None
        
        if 'comment' in request.form:
            comment_text = request.form.get('comment')
            question.comment = comment_text if comment_text and comment_text.strip() != '' else None
        
        if 'correct_percentage' in request.form:
            correct_pct = request.form.get('correct_percentage')
            if correct_pct and correct_pct.strip() != '':
                pct_val = int(correct_pct)
                if 0 <= pct_val <= 100:
                    question.correct_percentage = pct_val
                else:
                    question.correct_percentage = None
            else:
                question.correct_percentage = None
        
        # Update major topic
        if 'major_topic_id' in request.form:
            major_topic_id = request.form.get('major_topic_id')
            new_major_topic_id = int(major_topic_id) if major_topic_id and major_topic_id != '' else None
            
            # If major topic changed, clear major subtopic (it may no longer be valid)
            if new_major_topic_id != question.major_topic_id:
                question.major_subtopic_id = None
            
            question.major_topic_id = new_major_topic_id
        
        # Update major subtopic
        if 'major_subtopic_id' in request.form:
            major_subtopic_id = request.form.get('major_subtopic_id')
            if major_subtopic_id and major_subtopic_id != '':
                subtopic = Subtopic.query.get(int(major_subtopic_id))
                # Validate that subtopic belongs to the major topic
                if subtopic and question.major_topic_id and subtopic.topic_id == question.major_topic_id:
                    question.major_subtopic_id = subtopic.id
                else:
                    question.major_subtopic_id = None
            else:
                question.major_subtopic_id = None
        
        # Update minor topics
        minor_topic_ids = request.form.getlist('minor_topic_ids')
        if minor_topic_ids:
            question.minor_topics.clear()
            for tid in minor_topic_ids:
                if tid:
                    topic = Topic.query.get(int(tid))
                    if topic:
                        question.minor_topics.append(topic)
        else:
            question.minor_topics.clear()
        
        # Update subtopics
        subtopic_ids = request.form.getlist('subtopic_ids')
        if subtopic_ids:
            question.subtopics.clear()
            for sid in subtopic_ids:
                if sid:
                    subtopic = Subtopic.query.get(int(sid))
                    if subtopic:
                        question.subtopics.append(subtopic)
        else:
            question.subtopics.clear()
        
        # Update chapter
        if 'chapter_id' in request.form:
            chapter_id = request.form.get('chapter_id')
            new_chapter_id = int(chapter_id) if chapter_id and chapter_id != '' else None
            
            # If chapter changed, clear subchapter (it may no longer be valid)
            if new_chapter_id != question.chapter_id:
                question.subchapter_id = None
            
            question.chapter_id = new_chapter_id
        
        # Update subchapter
        if 'subchapter_id' in request.form:
            subchapter_id = request.form.get('subchapter_id')
            if subchapter_id and subchapter_id != '':
                subchapter = Subchapter.query.get(int(subchapter_id))
                # Validate that subchapter belongs to the chapter
                if subchapter and question.chapter_id and subchapter.chapter_id == question.chapter_id:
                    question.subchapter_id = subchapter.id
                else:
                    question.subchapter_id = None
            else:
                question.subchapter_id = None
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'question': {
                'id': question.id,
                'qid': question.qid,
                'level': question.level,
                'q_type': question.q_type,
                'section': question.section,
                'major_topic_id': question.major_topic_id,
                'major_subtopic_id': question.major_subtopic_id,
                'chapter_id': question.chapter_id,
                'subchapter_id': question.subchapter_id,
                'description': question.description,
                'answer': question.answer,
                'comment': question.comment
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==================== Question Deletion ====================

@admin_bp.route('/questions/delete', methods=['POST'])
@login_required
@admin_required
def delete_questions():
    """Delete selected questions from database (batch delete)"""
    try:
        # Get question IDs from request
        question_ids = request.form.getlist('question_ids')
        
        if not question_ids:
            return jsonify({
                'success': False,
                'error': 'No questions selected'
            }), 400
        
        # Convert to integers
        question_ids = [int(qid) for qid in question_ids if qid.isdigit()]
        
        if not question_ids:
            return jsonify({
                'success': False,
                'error': 'Invalid question IDs'
            }), 400
        
        # Get questions to delete
        questions = Question.query.filter(Question.id.in_(question_ids)).all()
        
        if not questions:
            return jsonify({
                'success': False,
                'error': 'No questions found with the given IDs'
            }), 404
        
        deleted_count = 0
        deleted_qids = []
        
        for question in questions:
            deleted_qids.append(question.qid)
            # Assets will be cascade deleted due to model relationship
            db.session.delete(question)
            deleted_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'deleted_count': deleted_count,
            'deleted_qids': deleted_qids,
            'message': f'Successfully deleted {deleted_count} question(s)'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==================== Batch Question Update ====================

@admin_bp.route('/questions/batch-update', methods=['POST'])
@login_required
@admin_required
def batch_update_questions():
    """Batch update question metadata and tags"""
    try:
        # Get question IDs from request
        question_ids = request.form.getlist('question_ids')
        
        if not question_ids:
            return jsonify({
                'success': False,
                'error': 'No questions selected'
            }), 400
        
        # Convert to integers
        question_ids = [int(qid) for qid in question_ids if qid.isdigit()]
        
        if not question_ids:
            return jsonify({
                'success': False,
                'error': 'Invalid question IDs'
            }), 400
        
        # Get questions to update
        questions = Question.query.filter(Question.id.in_(question_ids)).all()
        
        if not questions:
            return jsonify({
                'success': False,
                'error': 'No questions found with the given IDs'
            }), 404
        
        # Determine which fields to update
        update_level = request.form.get('update_level') == '1'
        update_q_type = request.form.get('update_q_type') == '1'
        update_section = request.form.get('update_section') == '1'
        update_correct_pct = request.form.get('update_correct_pct') == '1'
        update_topics = request.form.get('update_topics') == '1'
        update_chapters = request.form.get('update_chapters') == '1'
        
        updated_count = 0
        
        for question in questions:
            # Update level if requested
            if update_level:
                level = request.form.get('level')
                question.level = int(level) if level and level != '' else None
            
            # Update question type if requested
            if update_q_type:
                q_type = request.form.get('q_type')
                question.q_type = q_type if q_type and q_type != '' else None
            
            # Update section if requested
            if update_section:
                section = request.form.get('section')
                question.section = section if section and section != '' else None
            
            # Update correct percentage if requested
            if update_correct_pct:
                correct_pct = request.form.get('correct_percentage')
                if correct_pct and correct_pct.strip() != '':
                    pct_val = int(correct_pct)
                    if 0 <= pct_val <= 100:
                        question.correct_percentage = pct_val
                    else:
                        question.correct_percentage = None
                else:
                    question.correct_percentage = None
            
            # Update topics & subtopics if requested (bundled)
            if update_topics:
                # Major topic
                major_topic_id = request.form.get('major_topic_id')
                new_major_topic_id = int(major_topic_id) if major_topic_id and major_topic_id != '' else None
                question.major_topic_id = new_major_topic_id
                
                # Major subtopic
                major_subtopic_id = request.form.get('major_subtopic_id')
                if major_subtopic_id and major_subtopic_id != '':
                    subtopic = Subtopic.query.get(int(major_subtopic_id))
                    # Validate that subtopic belongs to the major topic
                    if subtopic and new_major_topic_id and subtopic.topic_id == new_major_topic_id:
                        question.major_subtopic_id = subtopic.id
                    else:
                        question.major_subtopic_id = None
                else:
                    question.major_subtopic_id = None
                
                # Minor topics
                minor_topic_ids = request.form.getlist('minor_topic_ids')
                question.minor_topics.clear()
                for tid in minor_topic_ids:
                    if tid:
                        topic = Topic.query.get(int(tid))
                        if topic:
                            question.minor_topics.append(topic)
                
                # M2M subtopics
                subtopic_ids = request.form.getlist('subtopic_ids')
                question.subtopics.clear()
                for sid in subtopic_ids:
                    if sid:
                        subtopic = Subtopic.query.get(int(sid))
                        if subtopic:
                            question.subtopics.append(subtopic)
            
            # Update chapters if requested
            if update_chapters:
                # Chapter
                chapter_id = request.form.get('chapter_id')
                new_chapter_id = int(chapter_id) if chapter_id and chapter_id != '' else None
                question.chapter_id = new_chapter_id
                
                # Subchapter
                subchapter_id = request.form.get('subchapter_id')
                if subchapter_id and subchapter_id != '':
                    subchapter = Subchapter.query.get(int(subchapter_id))
                    # Validate that subchapter belongs to the chapter
                    if subchapter and new_chapter_id and subchapter.chapter_id == new_chapter_id:
                        question.subchapter_id = subchapter.id
                    else:
                        question.subchapter_id = None
                else:
                    question.subchapter_id = None
            
            updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_count': updated_count,
            'message': f'Successfully updated {updated_count} question(s)'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== User Management (Super Admin Only) ====================

@admin_bp.route('/users')
@login_required
@super_admin_required
def users():
    """User management page - super admin only"""
    all_users = User.query.order_by(User.username).all()
    all_subjects = Subject.query.all()
    
    # Build user data with permissions
    users_data = []
    for user in all_users:
        permissions = {}
        for perm in user.subject_permissions.all():
            permissions[perm.subject_id] = perm.role
        
        users_data.append({
            'user': user,
            'permissions': permissions
        })
    
    return render_template('admin_users.html', 
                           users_data=users_data, 
                           subjects=all_subjects)


@admin_bp.route('/users/add', methods=['POST'])
@login_required
@super_admin_required
def add_user():
    """Add a new user"""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    is_super_admin = request.form.get('is_super_admin') == '1'
    
    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400
    
    # Check if username already exists
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400
    
    # Create user
    user = User(username=username, is_super_admin=is_super_admin)
    user.set_password(password)
    
    # Also set is_admin if super_admin (for backwards compatibility)
    if is_super_admin:
        user.is_admin = True
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'id': user.id,
        'username': user.username
    })


@admin_bp.route('/users/<int:user_id>/edit', methods=['POST'])
@login_required
@super_admin_required
def edit_user(user_id):
    """Edit a user's basic info"""
    user = User.query.get_or_404(user_id)
    
    # Prevent editing your own super_admin status
    if user.id == current_user.id:
        return jsonify({'error': 'Cannot modify your own account'}), 400
    
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    is_super_admin = request.form.get('is_super_admin') == '1'
    
    if not username:
        return jsonify({'error': 'Username is required'}), 400
    
    # Check if username is taken by another user
    existing = User.query.filter_by(username=username).first()
    if existing and existing.id != user_id:
        return jsonify({'error': 'Username already exists'}), 400
    
    user.username = username
    user.is_super_admin = is_super_admin
    user.is_admin = is_super_admin  # Keep is_admin in sync for backwards compatibility
    
    if password:
        user.set_password(password)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'id': user.id,
        'username': user.username
    })


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST', 'DELETE'])
@login_required
@super_admin_required
def delete_user(user_id):
    """Delete a user"""
    user = User.query.get_or_404(user_id)
    
    # Prevent deleting yourself
    if user.id == current_user.id:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    
    db.session.delete(user)
    db.session.commit()
    
    return jsonify({'success': True})


@admin_bp.route('/users/<int:user_id>/permissions', methods=['POST'])
@login_required
@super_admin_required
def update_user_permissions(user_id):
    """Update a user's subject permissions"""
    user = User.query.get_or_404(user_id)
    
    try:
        data = request.get_json()
        permissions = data.get('permissions', {})  # {subject_id: role or null}
        
        for subject_id, role in permissions.items():
            # Find existing permission
            existing = UserSubjectPermission.query.filter_by(
                user_id=user_id, subject_id=subject_id
            ).first()
            
            if role is None or role == '':
                # Remove permission
                if existing:
                    db.session.delete(existing)
            else:
                # Add or update permission
                if existing:
                    existing.role = role
                else:
                    perm = UserSubjectPermission(
                        user_id=user_id,
                        subject_id=subject_id,
                        role=role
                    )
                    db.session.add(perm)
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/users/<int:user_id>/permissions/get')
@login_required
@super_admin_required
def get_user_permissions(user_id):
    """Get a user's subject permissions"""
    user = User.query.get_or_404(user_id)
    
    permissions = {}
    for perm in user.subject_permissions.all():
        permissions[perm.subject_id] = perm.role
    
    return jsonify({
        'user_id': user_id,
        'username': user.username,
        'is_super_admin': user.is_super_admin,
        'permissions': permissions
    })


# ==================== Export / Import ====================

@admin_bp.route('/export-import')
@login_required
@admin_required
def export_import():
    """Export/Import management page"""
    subjects = get_user_admin_subjects()
    return render_template('admin_export_import.html', subjects=subjects)


# ---------- Export Question Tags ----------

@admin_bp.route('/export/question-tags')
@login_required
@admin_required
def export_question_tags():
    """Export question tags as CSV (using nominal QID and string names)"""
    subject_id = request.args.get('subject_id')
    if not subject_id:
        flash('Please select a subject.', 'warning')
        return redirect(url_for('admin.export_import'))

    # Verify access
    subjects = get_user_admin_subjects()
    subject_ids = [s.id for s in subjects]
    if subject_id not in subject_ids:
        flash('Access denied for this subject.', 'danger')
        return redirect(url_for('admin.export_import'))

    from natsort import natsorted
    questions = natsorted(
        Question.query.filter_by(subject=subject_id).all(),
        key=lambda q: q.qid
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'qid', 'subject', 'major_topic', 'major_subtopic',
        'minor_topics', 'subtopics',
        'chapter', 'subchapter',
        'section', 'level', 'q_type', 'correct_percentage', 'description',
        'answer', 'comment'
    ])

    for q in questions:
        major_topic_name = q.major_topic.name if q.major_topic else ''
        major_subtopic_name = q.major_subtopic.name if q.major_subtopic else ''
        minor_topics_str = '; '.join(t.name for t in q.minor_topics) if q.minor_topics else ''
        subtopics_str = '; '.join(s.name for s in q.subtopics) if q.subtopics else ''
        chapter_name = q.chapter.name if q.chapter else ''
        subchapter_name = q.subchapter.name if q.subchapter else ''

        writer.writerow([
            q.qid,
            q.subject,
            major_topic_name,
            major_subtopic_name,
            minor_topics_str,
            subtopics_str,
            chapter_name,
            subchapter_name,
            q.section or '',
            q.level if q.level is not None else '',
            q.q_type or '',
            q.correct_percentage if q.correct_percentage is not None else '',
            q.description or '',
            q.answer or '',
            q.comment or ''
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=question_tags_{subject_id}.csv'
    return response


# ---------- Import Question Tags ----------

@admin_bp.route('/import/question-tags', methods=['POST'])
@login_required
@admin_required
def import_question_tags():
    """Import question tags from CSV"""
    file = request.files.get('file')
    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect(url_for('admin.export_import'))

    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)

        # Verify required columns
        required_cols = {'qid'}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            flash('CSV must contain at least a "qid" column.', 'danger')
            return redirect(url_for('admin.export_import'))

        updated = 0
        skipped = 0
        warnings = []

        # Get accessible subjects
        subjects = get_user_admin_subjects()
        subject_ids = {s.id for s in subjects}

        for row_num, row in enumerate(reader, start=2):
            qid = row.get('qid', '').strip()
            if not qid:
                skipped += 1
                warnings.append(f'Row {row_num}: empty qid, skipped.')
                continue

            question = Question.query.filter_by(qid=qid).first()
            if not question:
                skipped += 1
                warnings.append(f'Row {row_num}: question "{qid}" not found, skipped.')
                continue

            # Check subject access
            if question.subject not in subject_ids:
                skipped += 1
                warnings.append(f'Row {row_num}: no admin access to subject "{question.subject}", skipped.')
                continue

            subj_id = question.subject

            # Major topic
            major_topic_name = row.get('major_topic', '').strip()
            if major_topic_name:
                topic = Topic.query.filter_by(subject_id=subj_id, name=major_topic_name).first()
                if topic:
                    question.major_topic_id = topic.id
                else:
                    warnings.append(f'Row {row_num}: major_topic "{major_topic_name}" not found for subject {subj_id}.')
                    question.major_topic_id = None
            else:
                question.major_topic_id = None

            # Major subtopic
            major_subtopic_name = row.get('major_subtopic', '').strip()
            if major_subtopic_name and question.major_topic_id:
                subtopic = Subtopic.query.filter_by(topic_id=question.major_topic_id, name=major_subtopic_name).first()
                if subtopic:
                    question.major_subtopic_id = subtopic.id
                else:
                    warnings.append(f'Row {row_num}: major_subtopic "{major_subtopic_name}" not found under topic.')
                    question.major_subtopic_id = None
            else:
                question.major_subtopic_id = None

            # Minor topics (semicolon separated)
            minor_topics_str = row.get('minor_topics', '').strip()
            question.minor_topics.clear()
            if minor_topics_str:
                for tname in [n.strip() for n in minor_topics_str.split(';') if n.strip()]:
                    topic = Topic.query.filter_by(subject_id=subj_id, name=tname).first()
                    if topic:
                        question.minor_topics.append(topic)
                    else:
                        warnings.append(f'Row {row_num}: minor topic "{tname}" not found.')

            # Subtopics (semicolon separated)
            subtopics_str = row.get('subtopics', '').strip()
            question.subtopics.clear()
            if subtopics_str:
                for sname in [n.strip() for n in subtopics_str.split(';') if n.strip()]:
                    # Search across all topics in the subject
                    subtopic = Subtopic.query.join(Topic).filter(
                        Topic.subject_id == subj_id, Subtopic.name == sname
                    ).first()
                    if subtopic:
                        question.subtopics.append(subtopic)
                    else:
                        warnings.append(f'Row {row_num}: subtopic "{sname}" not found.')

            # Chapter
            chapter_name = row.get('chapter', '').strip()
            if chapter_name:
                chapter = Chapter.query.filter_by(subject_id=subj_id, name=chapter_name).first()
                if chapter:
                    question.chapter_id = chapter.id
                else:
                    warnings.append(f'Row {row_num}: chapter "{chapter_name}" not found.')
                    question.chapter_id = None
            else:
                question.chapter_id = None

            # Subchapter
            subchapter_name = row.get('subchapter', '').strip()
            if subchapter_name and question.chapter_id:
                subchapter = Subchapter.query.filter_by(chapter_id=question.chapter_id, name=subchapter_name).first()
                if subchapter:
                    question.subchapter_id = subchapter.id
                else:
                    warnings.append(f'Row {row_num}: subchapter "{subchapter_name}" not found.')
                    question.subchapter_id = None
            else:
                question.subchapter_id = None

            # Simple metadata fields
            section = row.get('section', '').strip()
            question.section = section if section else None

            level = row.get('level', '').strip()
            question.level = int(level) if level and level.isdigit() else None

            q_type = row.get('q_type', '').strip()
            question.q_type = q_type if q_type else None

            correct_pct = row.get('correct_percentage', '').strip()
            if correct_pct and correct_pct.isdigit():
                pct_val = int(correct_pct)
                question.correct_percentage = pct_val if 0 <= pct_val <= 100 else None
            else:
                question.correct_percentage = None

            description = row.get('description', '').strip()
            question.description = description if description else None

            # Answer text and comment
            answer = row.get('answer', '').strip()
            question.answer = answer if answer else None

            comment_text = row.get('comment', '').strip()
            question.comment = comment_text if comment_text else None

            updated += 1

        db.session.commit()

        msg = f'Import complete: {updated} question(s) updated, {skipped} skipped.'
        if warnings:
            msg += f' {len(warnings)} warning(s).'
        flash(msg, 'success' if updated > 0 else 'warning')

        # Store warnings in session for display
        if warnings:
            # Limit to first 50 warnings
            flash('Warnings:\n' + '\n'.join(warnings[:50]), 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'Import failed: {str(e)}', 'danger')

    return redirect(url_for('admin.export_import'))


# ---------- Export Topics/Subtopics ----------

@admin_bp.route('/export/topics')
@login_required
@admin_required
def export_topics():
    """Export topics and subtopics as CSV"""
    subject_id = request.args.get('subject_id')
    if not subject_id:
        flash('Please select a subject.', 'warning')
        return redirect(url_for('admin.export_import'))

    subjects = get_user_admin_subjects()
    subject_ids = [s.id for s in subjects]
    if subject_id not in subject_ids:
        flash('Access denied for this subject.', 'danger')
        return redirect(url_for('admin.export_import'))

    topics = Topic.query.filter_by(subject_id=subject_id).order_by(Topic.sort_order).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['subject_id', 'topic_name', 'topic_sort_order',
                     'subtopic_name', 'subtopic_sort_order', 'subtopic_hidden'])

    for topic in topics:
        subtopics = topic.subtopics.order_by(Subtopic.sort_order).all()
        if subtopics:
            for st in subtopics:
                writer.writerow([
                    subject_id, topic.name, topic.sort_order,
                    st.name, st.sort_order, 1 if st.hidden else 0
                ])
        else:
            writer.writerow([subject_id, topic.name, topic.sort_order, '', '', ''])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=topics_{subject_id}.csv'
    return response


# ---------- Import Topics/Subtopics ----------

@admin_bp.route('/import/topics', methods=['POST'])
@login_required
@admin_required
def import_topics():
    """Import topics and subtopics from CSV"""
    file = request.files.get('file')
    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect(url_for('admin.export_import'))

    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)

        required_cols = {'subject_id', 'topic_name'}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            flash('CSV must contain "subject_id" and "topic_name" columns.', 'danger')
            return redirect(url_for('admin.export_import'))

        subjects = get_user_admin_subjects()
        subject_ids = {s.id for s in subjects}

        topics_created = 0
        topics_updated = 0
        subtopics_created = 0
        subtopics_updated = 0
        skipped = 0
        warnings = []

        for row_num, row in enumerate(reader, start=2):
            subj_id = row.get('subject_id', '').strip()
            topic_name = row.get('topic_name', '').strip()

            if not subj_id or not topic_name:
                skipped += 1
                continue

            # Verify subject exists and we have access
            subject = Subject.query.get(subj_id)
            if not subject:
                skipped += 1
                warnings.append(f'Row {row_num}: subject "{subj_id}" not found.')
                continue
            if subj_id not in subject_ids:
                skipped += 1
                warnings.append(f'Row {row_num}: no admin access to subject "{subj_id}".')
                continue

            # Find or create topic
            topic = Topic.query.filter_by(subject_id=subj_id, name=topic_name).first()
            if not topic:
                sort_order_str = row.get('topic_sort_order', '').strip()
                sort_order = int(sort_order_str) if sort_order_str and sort_order_str.isdigit() else (
                    (db.session.query(db.func.max(Topic.sort_order)).filter_by(subject_id=subj_id).scalar() or 0) + 1
                )
                topic = Topic(subject_id=subj_id, name=topic_name, sort_order=sort_order)
                db.session.add(topic)
                db.session.flush()  # Get ID
                topics_created += 1
            else:
                # Update sort order if provided
                sort_order_str = row.get('topic_sort_order', '').strip()
                if sort_order_str and sort_order_str.isdigit():
                    topic.sort_order = int(sort_order_str)
                topics_updated += 1

            # Handle subtopic if present
            subtopic_name = row.get('subtopic_name', '').strip()
            if subtopic_name:
                subtopic = Subtopic.query.filter_by(topic_id=topic.id, name=subtopic_name).first()
                if not subtopic:
                    sort_order_str = row.get('subtopic_sort_order', '').strip()
                    sort_order = int(sort_order_str) if sort_order_str and sort_order_str.isdigit() else (
                        (db.session.query(db.func.max(Subtopic.sort_order)).filter_by(topic_id=topic.id).scalar() or 0) + 1
                    )
                    hidden = row.get('subtopic_hidden', '0').strip() == '1'
                    subtopic = Subtopic(topic_id=topic.id, name=subtopic_name, sort_order=sort_order, hidden=hidden)
                    db.session.add(subtopic)
                    subtopics_created += 1
                else:
                    sort_order_str = row.get('subtopic_sort_order', '').strip()
                    if sort_order_str and sort_order_str.isdigit():
                        subtopic.sort_order = int(sort_order_str)
                    hidden_str = row.get('subtopic_hidden', '').strip()
                    if hidden_str in ('0', '1'):
                        subtopic.hidden = hidden_str == '1'
                    subtopics_updated += 1

        db.session.commit()

        msg = (f'Topics import complete: '
               f'{topics_created} topic(s) created, {topics_updated} updated, '
               f'{subtopics_created} subtopic(s) created, {subtopics_updated} updated, '
               f'{skipped} row(s) skipped.')
        flash(msg, 'success')
        if warnings:
            flash('Warnings:\n' + '\n'.join(warnings[:50]), 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'Import failed: {str(e)}', 'danger')

    return redirect(url_for('admin.export_import'))


# ---------- Export Chapters/Subchapters ----------

@admin_bp.route('/export/chapters')
@login_required
@admin_required
def export_chapters():
    """Export chapters and subchapters as CSV"""
    subject_id = request.args.get('subject_id')
    if not subject_id:
        flash('Please select a subject.', 'warning')
        return redirect(url_for('admin.export_import'))

    subjects = get_user_admin_subjects()
    subject_ids = [s.id for s in subjects]
    if subject_id not in subject_ids:
        flash('Access denied for this subject.', 'danger')
        return redirect(url_for('admin.export_import'))

    chapters_list = Chapter.query.filter_by(subject_id=subject_id).order_by(Chapter.sort_order).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['subject_id', 'chapter_name', 'chapter_sort_order',
                     'subchapter_name', 'subchapter_sort_order', 'subchapter_hidden'])

    for chapter in chapters_list:
        subchapters = chapter.subchapters.order_by(Subchapter.sort_order).all()
        if subchapters:
            for sc in subchapters:
                writer.writerow([
                    subject_id, chapter.name, chapter.sort_order,
                    sc.name, sc.sort_order, 1 if sc.hidden else 0
                ])
        else:
            writer.writerow([subject_id, chapter.name, chapter.sort_order, '', '', ''])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=chapters_{subject_id}.csv'
    return response


# ---------- Import Chapters/Subchapters ----------

@admin_bp.route('/import/chapters', methods=['POST'])
@login_required
@admin_required
def import_chapters():
    """Import chapters and subchapters from CSV"""
    file = request.files.get('file')
    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect(url_for('admin.export_import'))

    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)

        required_cols = {'subject_id', 'chapter_name'}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            flash('CSV must contain "subject_id" and "chapter_name" columns.', 'danger')
            return redirect(url_for('admin.export_import'))

        subjects = get_user_admin_subjects()
        subject_ids = {s.id for s in subjects}

        chapters_created = 0
        chapters_updated = 0
        subchapters_created = 0
        subchapters_updated = 0
        skipped = 0
        warnings = []

        for row_num, row in enumerate(reader, start=2):
            subj_id = row.get('subject_id', '').strip()
            chapter_name = row.get('chapter_name', '').strip()

            if not subj_id or not chapter_name:
                skipped += 1
                continue

            subject = Subject.query.get(subj_id)
            if not subject:
                skipped += 1
                warnings.append(f'Row {row_num}: subject "{subj_id}" not found.')
                continue
            if subj_id not in subject_ids:
                skipped += 1
                warnings.append(f'Row {row_num}: no admin access to subject "{subj_id}".')
                continue

            # Find or create chapter
            chapter = Chapter.query.filter_by(subject_id=subj_id, name=chapter_name).first()
            if not chapter:
                sort_order_str = row.get('chapter_sort_order', '').strip()
                sort_order = int(sort_order_str) if sort_order_str and sort_order_str.isdigit() else (
                    (db.session.query(db.func.max(Chapter.sort_order)).filter_by(subject_id=subj_id).scalar() or 0) + 1
                )
                chapter = Chapter(subject_id=subj_id, name=chapter_name, sort_order=sort_order)
                db.session.add(chapter)
                db.session.flush()
                chapters_created += 1
            else:
                sort_order_str = row.get('chapter_sort_order', '').strip()
                if sort_order_str and sort_order_str.isdigit():
                    chapter.sort_order = int(sort_order_str)
                chapters_updated += 1

            # Handle subchapter if present
            subchapter_name = row.get('subchapter_name', '').strip()
            if subchapter_name:
                subchapter = Subchapter.query.filter_by(chapter_id=chapter.id, name=subchapter_name).first()
                if not subchapter:
                    sort_order_str = row.get('subchapter_sort_order', '').strip()
                    sort_order = int(sort_order_str) if sort_order_str and sort_order_str.isdigit() else (
                        (db.session.query(db.func.max(Subchapter.sort_order)).filter_by(chapter_id=chapter.id).scalar() or 0) + 1
                    )
                    hidden = row.get('subchapter_hidden', '0').strip() == '1'
                    subchapter = Subchapter(chapter_id=chapter.id, name=subchapter_name, sort_order=sort_order, hidden=hidden)
                    db.session.add(subchapter)
                    subchapters_created += 1
                else:
                    sort_order_str = row.get('subchapter_sort_order', '').strip()
                    if sort_order_str and sort_order_str.isdigit():
                        subchapter.sort_order = int(sort_order_str)
                    hidden_str = row.get('subchapter_hidden', '').strip()
                    if hidden_str in ('0', '1'):
                        subchapter.hidden = hidden_str == '1'
                    subchapters_updated += 1

        db.session.commit()

        msg = (f'Chapters import complete: '
               f'{chapters_created} chapter(s) created, {chapters_updated} updated, '
               f'{subchapters_created} subchapter(s) created, {subchapters_updated} updated, '
               f'{skipped} row(s) skipped.')
        flash(msg, 'success')
        if warnings:
            flash('Warnings:\n' + '\n'.join(warnings[:50]), 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'Import failed: {str(e)}', 'danger')

    return redirect(url_for('admin.export_import'))

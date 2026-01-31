"""
Admin panel routes for managing topics and tagging questions
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import Subject, Topic, Subtopic, Question, QuestionAsset, Chapter, Subchapter
from app.utils import admin_required

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
    subjects = Subject.query.all()
    
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
    subjects = Subject.query.all()

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
                'description': question.description
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

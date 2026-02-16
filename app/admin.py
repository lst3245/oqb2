"""
Admin panel routes for managing topics and tagging questions
"""
import csv
import io
import os
import re
import json
import shutil
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, Response, make_response, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
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
    """Delete selected questions from database (batch delete), optionally also delete files from disk"""
    try:
        # Get question IDs from request
        question_ids = request.form.getlist('question_ids')
        delete_files = request.form.get('delete_files', 'false') == 'true'
        
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
        files_deleted = 0
        source_path = current_app.config['SOURCE_PATH']
        
        for question in questions:
            deleted_qids.append(question.qid)
            
            # Optionally delete associated files from disk
            if delete_files:
                for asset in question.assets:
                    full_path = os.path.join(source_path, asset.file_path)
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                            files_deleted += 1
                        except OSError:
                            pass
            
            # Assets will be cascade deleted due to model relationship
            db.session.delete(question)
            deleted_count += 1
        
        db.session.commit()
        
        msg = f'Successfully deleted {deleted_count} question(s) from database'
        if delete_files:
            msg += f' and {files_deleted} file(s) from disk'
        
        return jsonify({
            'success': True,
            'deleted_count': deleted_count,
            'deleted_qids': deleted_qids,
            'files_deleted': files_deleted,
            'message': msg
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


# ==================== Question Management ====================

# QID validation patterns (same as ingestor)
PP_QID_PATTERN = re.compile(r'^(?P<subj>[A-Z0-9]+)_(?P<source>DSE|CE|AL)_(?P<year>\d{4})_(?P<paper>P\d+)_Q(?P<qno>\d+)$')
QB_QID_PATTERN = re.compile(r'^(?P<subj>[A-Z0-9]+)_QB_(?P<detail>[^_]+)_Q(?P<qno>\d+)$')


def validate_qid_format(qid):
    """Validate a QID matches the expected format. Returns (parsed_dict, error_msg)."""
    m = PP_QID_PATTERN.match(qid)
    if m:
        d = m.groupdict()
        return {
            'subject': d['subj'], 'source': d['source'],
            'year': int(d['year']), 'paper': d['paper'], 'qno': int(d['qno'])
        }, None
    m = QB_QID_PATTERN.match(qid)
    if m:
        d = m.groupdict()
        return {
            'subject': d['subj'], 'source': 'QB',
            'detail': d['detail'], 'qno': int(d['qno'])
        }, None
    return None, 'Invalid QID format. Expected SUBJ_SOURCE_YEAR_PAPER_QNO (e.g. MATC_DSE_2024_P1_Q5) or SUBJ_QB_DETAIL_QNO (e.g. MATC_QB_BOOK1_Q1)'


def _build_asset_file_path(question, asset):
    """Build the expected relative file path for an asset based on the question's QID components."""
    ext = asset.file_path.rsplit('.', 1)[-1] if '.' in asset.file_path else 'png'
    part_suffix = f'_{asset.part_number}' if asset.part_number > 1 else ''
    
    if question.source in ('DSE', 'CE', 'AL'):
        filename = f"{question.qid}_{asset.language}_{asset.asset_type}{part_suffix}.{ext}"
        folder = os.path.join(question.subject, 'PP', question.source,
                              str(question.year), question.paper)
    else:
        # QB
        detail = question.qid.split('_')[2]  # SUBJ_QB_DETAIL_QNO
        filename = f"{question.qid}_{asset.language}_{asset.asset_type}{part_suffix}.{ext}"
        folder = os.path.join(question.subject, 'QB', detail)
    
    return os.path.join(folder, filename)


@admin_bp.route('/questions')
@login_required
@admin_required
def questions_page():
    """Admin question management page"""
    subjects = get_user_admin_subjects()
    return render_template('admin_questions.html', subjects=subjects)


@admin_bp.route('/questions/api/list')
@login_required
@admin_required
def questions_api_list():
    """API: fetch paginated & filtered question list"""
    qid_search = request.args.get('qid_search', '').strip()
    selected_ids_str = request.args.get('selected_ids', '').strip()
    sort_field = request.args.get('sort', 'created_at')
    sort_dir = request.args.get('dir', 'desc')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    if page_size not in (10, 20, 50, 100, 200):
        page_size = 50

    admin_subjects = [s.id for s in get_user_admin_subjects()]
    query = Question.query.filter(Question.subject.in_(admin_subjects))

    if selected_ids_str:
        # Filter by specific question internal IDs (from dashboard localStorage)
        try:
            selected_ids = [int(x) for x in selected_ids_str.split(',') if x.strip()]
            if selected_ids:
                query = query.filter(Question.id.in_(selected_ids))
        except ValueError:
            pass
    elif qid_search:
        qid_pattern = qid_search
        if '*' in qid_pattern or '%' in qid_pattern:
            qid_pattern = qid_pattern.replace('*', '%')
            query = query.filter(Question.qid.ilike(qid_pattern))
        else:
            query = query.filter(Question.qid.ilike(f'%{qid_pattern}%'))

    # Sorting
    sort_col_map = {
        'qid': Question.qid,
        'subject': Question.subject,
        'source': Question.source,
        'year': Question.year,
        'paper': Question.paper,
        'qno': Question.qno,
        'q_type': Question.q_type,
        'created_at': Question.created_at,
    }
    sort_col = sort_col_map.get(sort_field, Question.created_at)
    if sort_dir == 'asc':
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    total = query.count()
    questions = query.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for q in questions:
        items.append({
            'id': q.id,
            'qid': q.qid,
            'subject': q.subject,
            'source': q.source,
            'year': q.year,
            'paper': q.paper,
            'section': q.section,
            'qno': q.qno,
            'q_type': q.q_type,
            'level': q.level,
            'created_at': q.created_at.strftime('%Y-%m-%d %H:%M') if q.created_at else '',
            'asset_count': q.assets.count(),
        })

    return jsonify({
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': (total + page_size - 1) // page_size,
    })


@admin_bp.route('/questions/<int:question_id>/details')
@login_required
@admin_required
def question_details(question_id):
    """API: get full question details (for edit modal)"""
    question = Question.query.get_or_404(question_id)

    # Check subject access
    admin_subjects = [s.id for s in get_user_admin_subjects()]
    if question.subject not in admin_subjects:
        return jsonify({'error': 'Access denied'}), 403

    return jsonify({
        'id': question.id,
        'qid': question.qid,
        'subject': question.subject,
        'source': question.source,
        'year': question.year,
        'paper': question.paper,
        'section': question.section,
        'qno': question.qno,
        'q_type': question.q_type,
        'level': question.level,
        'major_topic_id': question.major_topic_id,
        'major_subtopic_id': question.major_subtopic_id,
        'minor_topic_ids': [t.id for t in question.minor_topics],
        'subtopic_ids': [s.id for s in question.subtopics],
        'chapter_id': question.chapter_id,
        'subchapter_id': question.subchapter_id,
        'description': question.description,
        'correct_percentage': question.correct_percentage,
        'answer': question.answer,
        'comment': question.comment,
        'created_at': question.created_at.strftime('%Y-%m-%d %H:%M') if question.created_at else '',
    })


@admin_bp.route('/questions/<int:question_id>/assets')
@login_required
@admin_required
def question_assets(question_id):
    """API: get all assets for a question, grouped by language and type"""
    question = Question.query.get_or_404(question_id)
    assets = QuestionAsset.query.filter_by(question_id=question_id).order_by(
        QuestionAsset.language, QuestionAsset.asset_type, QuestionAsset.part_number
    ).all()

    result = {}
    for a in assets:
        lang = a.language
        atype = a.asset_type
        if lang not in result:
            result[lang] = {}
        if atype not in result[lang]:
            result[lang][atype] = []
        result[lang][atype].append({
            'id': a.id,
            'part_number': a.part_number,
            'file_format': a.file_format,
            'file_path': a.file_path,
            'preview_url': url_for('dashboard.get_asset_preview', asset_id=a.id),
        })

    return jsonify({'assets': result, 'qid': question.qid})


@admin_bp.route('/questions/<int:question_id>/rename', methods=['POST'])
@login_required
@admin_required
def rename_question(question_id):
    """Rename a question ID and optionally move asset files"""
    question = Question.query.get_or_404(question_id)
    data = request.get_json()
    new_qid = data.get('new_qid', '').strip()
    confirm_rename_files = data.get('confirm_rename_files', False)

    if not new_qid:
        return jsonify({'error': 'New QID is required'}), 400

    if new_qid == question.qid:
        return jsonify({'success': True, 'message': 'QID unchanged'})

    # Validate format
    parsed, err = validate_qid_format(new_qid)
    if err:
        return jsonify({'error': err}), 400

    new_subject = parsed['subject']

    # Check subject exists
    if not Subject.query.get(new_subject):
        return jsonify({'error': f'Subject {new_subject} does not exist'}), 400

    # Check subject access (superadmins can access all subjects)
    if not current_user.is_super_admin:
        admin_subjects = [s.id for s in get_user_admin_subjects()]
        if new_subject not in admin_subjects:
            return jsonify({'error': f'You do not have admin access to subject {new_subject}'}), 403

    # Check for duplicate
    existing = Question.query.filter_by(qid=new_qid).first()
    if existing and existing.id != question_id:
        return jsonify({'error': f'A question with QID {new_qid} already exists'}), 409

    old_qid = question.qid
    source_path = current_app.config['SOURCE_PATH']

    # Update the question record
    question.qid = new_qid
    question.subject = parsed['subject']
    question.source = parsed['source']
    question.year = parsed.get('year')
    question.paper = parsed.get('paper')
    question.qno = parsed['qno']

    renamed_files = []
    errors = []

    if confirm_rename_files:
        # Rename/move physical files
        for asset in question.assets.all():
            old_full = os.path.join(source_path, asset.file_path)
            new_rel = _build_asset_file_path(question, asset)
            new_full = os.path.join(source_path, new_rel)

            if os.path.exists(old_full):
                try:
                    os.makedirs(os.path.dirname(new_full), exist_ok=True)
                    shutil.move(old_full, new_full)
                    renamed_files.append({'old': asset.file_path, 'new': new_rel})
                    asset.file_path = new_rel
                except Exception as e:
                    errors.append(f'Error moving {asset.file_path}: {str(e)}')
            else:
                # File doesn't exist, just update the path in DB
                asset.file_path = new_rel

    db.session.commit()

    return jsonify({
        'success': True,
        'old_qid': old_qid,
        'new_qid': new_qid,
        'renamed_files': renamed_files,
        'errors': errors,
        'message': f'Question renamed from {old_qid} to {new_qid}'
    })


@admin_bp.route('/questions/<int:question_id>/assets/upload', methods=['POST'])
@login_required
@admin_required
def upload_question_asset(question_id):
    """Upload a new asset part for a question"""
    question = Question.query.get_or_404(question_id)

    admin_subjects = [s.id for s in get_user_admin_subjects()]
    if question.subject not in admin_subjects:
        return jsonify({'error': 'Access denied'}), 403

    language = request.form.get('language', 'EN')
    asset_type = request.form.get('asset_type', 'QUE')
    
    if language not in ('EN', 'CH', 'BI'):
        return jsonify({'error': 'Invalid language'}), 400
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        return jsonify({'error': 'Invalid asset type'}), 400

    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files provided'}), 400

    source_path = current_app.config['SOURCE_PATH']
    
    # Determine next part number
    existing_parts = QuestionAsset.query.filter_by(
        question_id=question_id, language=language, asset_type=asset_type
    ).order_by(QuestionAsset.part_number.desc()).first()
    next_part = (existing_parts.part_number + 1) if existing_parts else 1

    uploaded = []
    for f in files:
        if not f.filename:
            continue
        
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'png'
        
        # Determine file format
        if ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp'):
            file_format = 'IMG'
        elif ext in ('doc', 'docx'):
            file_format = 'DOC'
        else:
            continue  # skip unsupported

        part_suffix = f'_{next_part}' if next_part > 1 else ''
        
        if question.source in ('DSE', 'CE', 'AL'):
            filename = f"{question.qid}_{language}_{asset_type}{part_suffix}.{ext}"
            folder = os.path.join(question.subject, 'PP', question.source,
                                  str(question.year), question.paper)
        else:
            detail = question.qid.split('_')[2]
            filename = f"{question.qid}_{language}_{asset_type}{part_suffix}.{ext}"
            folder = os.path.join(question.subject, 'QB', detail)

        rel_path = os.path.join(folder, filename)
        full_path = os.path.join(source_path, rel_path)
        
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        f.save(full_path)

        asset = QuestionAsset(
            question_id=question_id,
            asset_type=asset_type,
            file_format=file_format,
            language=language,
            file_path=rel_path,
            part_number=next_part
        )
        db.session.add(asset)
        uploaded.append({
            'id': None,  # will be set after commit
            'part_number': next_part,
            'file_path': rel_path,
        })
        next_part += 1

    db.session.commit()

    return jsonify({
        'success': True,
        'uploaded_count': len(uploaded),
        'message': f'Uploaded {len(uploaded)} asset(s)'
    })


@admin_bp.route('/questions/<int:question_id>/assets/<int:asset_id>/delete', methods=['POST', 'DELETE'])
@login_required
@admin_required
def delete_question_asset(question_id, asset_id):
    """Delete a specific asset (DB record + file on disk by default)"""
    asset = QuestionAsset.query.filter_by(id=asset_id, question_id=question_id).first_or_404()
    
    file_deleted = False
    file_path = asset.file_path
    source_path = current_app.config['SOURCE_PATH']
    full_path = os.path.join(source_path, file_path)
    
    # Check if request asks to also delete from disk (default: True for individual asset)
    delete_from_disk = True
    if request.is_json:
        delete_from_disk = request.get_json().get('delete_from_disk', True)
    
    if delete_from_disk and os.path.exists(full_path):
        try:
            os.remove(full_path)
            file_deleted = True
        except OSError as e:
            # Continue with DB deletion even if file removal fails
            pass
    
    db.session.delete(asset)
    db.session.commit()

    msg = 'Asset deleted from database'
    if file_deleted:
        msg += ' and disk'
    elif delete_from_disk:
        msg += ' (file was not found on disk)'
    
    return jsonify({'success': True, 'message': msg})


@admin_bp.route('/questions/<int:question_id>/assets/reorder', methods=['POST'])
@login_required
@admin_required
def reorder_question_assets(question_id):
    """Reorder asset parts for a given language and type, renaming files on disk"""
    data = request.get_json()
    language = data.get('language')
    asset_type = data.get('asset_type')
    asset_ids = data.get('asset_ids', [])  # ordered list of asset IDs

    if not language or not asset_type or not asset_ids:
        return jsonify({'error': 'Missing required fields'}), 400

    question = Question.query.get_or_404(question_id)
    source_path = current_app.config['SOURCE_PATH']
    
    # Gather assets and plan renames
    assets_to_reorder = []
    for idx, aid in enumerate(asset_ids, start=1):
        asset = QuestionAsset.query.filter_by(
            id=aid, question_id=question_id,
            language=language, asset_type=asset_type
        ).first()
        if asset:
            old_full_path = os.path.join(source_path, asset.file_path)
            old_part = asset.part_number
            asset.part_number = idx  # Update part number in DB
            new_rel_path = _build_asset_file_path(question, asset)
            new_full_path = os.path.join(source_path, new_rel_path)
            assets_to_reorder.append({
                'asset': asset,
                'old_full_path': old_full_path,
                'new_full_path': new_full_path,
                'new_rel_path': new_rel_path,
                'old_part': old_part,
                'new_part': idx,
            })
    
    # Rename files on disk using temp names to avoid conflicts (e.g. swapping part 1 and 2)
    rename_errors = []
    temp_renames = []
    for item in assets_to_reorder:
        if item['old_full_path'] != item['new_full_path'] and os.path.exists(item['old_full_path']):
            temp_path = item['old_full_path'] + '.tmp_reorder'
            try:
                os.rename(item['old_full_path'], temp_path)
                temp_renames.append((temp_path, item))
            except OSError as e:
                rename_errors.append(f"Failed to rename {os.path.basename(item['old_full_path'])}: {e}")
    
    # Now move from temp to final paths
    files_renamed = 0
    for temp_path, item in temp_renames:
        try:
            os.makedirs(os.path.dirname(item['new_full_path']), exist_ok=True)
            os.rename(temp_path, item['new_full_path'])
            item['asset'].file_path = item['new_rel_path']
            files_renamed += 1
        except OSError as e:
            # Try to restore original
            try:
                os.rename(temp_path, item['old_full_path'])
            except OSError:
                pass
            rename_errors.append(f"Failed to rename to {os.path.basename(item['new_full_path'])}: {e}")

    db.session.commit()
    
    result = {'success': True, 'files_renamed': files_renamed}
    if rename_errors:
        result['warnings'] = rename_errors
    return jsonify(result)


@admin_bp.route('/questions/create', methods=['POST'])
@login_required
@admin_required
def create_question():
    """Create a new question"""
    data = request.get_json()
    
    subject = data.get('subject', '').strip()
    source = data.get('source', '').strip()
    year = data.get('year')
    paper = data.get('paper', '').strip()
    qno = data.get('qno')
    detail = data.get('detail', '').strip()  # For QB source

    if not subject or not source or not qno:
        return jsonify({'error': 'Subject, source, and question number are required'}), 400

    # Check subject exists
    if not Subject.query.get(subject):
        return jsonify({'error': f'Subject {subject} does not exist'}), 400

    # Check subject access (superadmins can access all subjects)
    if not current_user.is_super_admin:
        admin_subjects = [s.id for s in get_user_admin_subjects()]
        if subject not in admin_subjects:
            return jsonify({'error': f'You do not have admin access to subject {subject}'}), 403

    # Build QID
    qno_int = int(qno)
    if source in ('DSE', 'CE', 'AL'):
        if not year or not paper:
            return jsonify({'error': 'Year and paper are required for PP questions'}), 400
        year_int = int(year)
        qid = f"{subject}_{source}_{year_int}_{paper}_Q{qno_int}"
    elif source == 'QB':
        if not detail:
            return jsonify({'error': 'Detail/book name is required for QB questions'}), 400
        if '_' in detail:
            return jsonify({'error': 'Detail/book name cannot contain underscores'}), 400
        qid = f"{subject}_QB_{detail}_Q{qno_int}"
        year_int = None
        paper = None
    else:
        return jsonify({'error': 'Invalid source type'}), 400

    # Validate format
    parsed, err = validate_qid_format(qid)
    if err:
        return jsonify({'error': err}), 400

    # Check duplicate
    if Question.query.filter_by(qid=qid).first():
        return jsonify({'error': f'Question {qid} already exists'}), 409

    question = Question(
        qid=qid,
        subject=subject,
        source=source,
        year=year_int if source != 'QB' else None,
        paper=paper if source != 'QB' else None,
        qno=qno_int,
    )
    db.session.add(question)
    db.session.commit()

    return jsonify({
        'success': True,
        'question': {
            'id': question.id,
            'qid': question.qid,
        },
        'message': f'Question {qid} created successfully'
    })


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

    # Which fields did the user choose to import?
    selected_fields = set(request.form.getlist('import_fields'))
    # If none were submitted (e.g. old form without checkboxes), import everything
    all_importable = {
        'major_topic', 'major_subtopic', 'minor_topics', 'subtopics',
        'chapter', 'subchapter', 'section', 'level', 'q_type',
        'correct_percentage', 'description', 'answer', 'comment'
    }
    if not selected_fields:
        selected_fields = all_importable

    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)

        # Verify required columns
        required_cols = {'qid'}
        csv_columns = set(reader.fieldnames or [])
        if not required_cols.issubset(csv_columns):
            flash('CSV must contain at least a "qid" column.', 'danger')
            return redirect(url_for('admin.export_import'))

        # Only import fields that are both selected AND present in the CSV
        fields_to_import = selected_fields & csv_columns

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
            if 'major_topic' in fields_to_import:
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
            if 'major_subtopic' in fields_to_import:
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
            if 'minor_topics' in fields_to_import:
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
            if 'subtopics' in fields_to_import:
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
            if 'chapter' in fields_to_import:
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
            if 'subchapter' in fields_to_import:
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
            if 'section' in fields_to_import:
                section = row.get('section', '').strip()
                question.section = section if section else None

            if 'level' in fields_to_import:
                level = row.get('level', '').strip()
                question.level = int(level) if level and level.isdigit() else None

            if 'q_type' in fields_to_import:
                q_type = row.get('q_type', '').strip()
                question.q_type = q_type if q_type else None

            if 'correct_percentage' in fields_to_import:
                correct_pct = row.get('correct_percentage', '').strip()
                if correct_pct and correct_pct.isdigit():
                    pct_val = int(correct_pct)
                    question.correct_percentage = pct_val if 0 <= pct_val <= 100 else None
                else:
                    question.correct_percentage = None

            if 'description' in fields_to_import:
                description = row.get('description', '').strip()
                question.description = description if description else None

            # Answer text and comment
            if 'answer' in fields_to_import:
                answer = row.get('answer', '').strip()
                question.answer = answer if answer else None

            if 'comment' in fields_to_import:
                comment_text = row.get('comment', '').strip()
                question.comment = comment_text if comment_text else None

            updated += 1

        db.session.commit()

        imported_fields_str = ', '.join(sorted(fields_to_import)) if fields_to_import else 'none'
        msg = f'Import complete: {updated} question(s) updated, {skipped} skipped. Fields imported: {imported_fields_str}.'
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
    writer.writerow(['subject_id', 'topic_name', 'subtopic_name', 'subtopic_hidden'])

    for topic in topics:
        subtopics = topic.subtopics.order_by(Subtopic.sort_order).all()
        if subtopics:
            for st in subtopics:
                writer.writerow([
                    subject_id, topic.name,
                    st.name, 1 if st.hidden else 0
                ])
        else:
            writer.writerow([subject_id, topic.name, '', ''])

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

        # Track sort_order from row position
        topic_order = {}        # (subj_id, topic_name) -> sort_order
        topic_counter = {}      # subj_id -> next sort_order
        subtopic_counter = {}   # topic_id -> next sort_order

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

            # Determine topic sort_order from row position
            topic_key = (subj_id, topic_name)
            if topic_key not in topic_order:
                if subj_id not in topic_counter:
                    topic_counter[subj_id] = 1
                topic_order[topic_key] = topic_counter[subj_id]
                topic_counter[subj_id] += 1

            # Find or create topic
            topic = Topic.query.filter_by(subject_id=subj_id, name=topic_name).first()
            if not topic:
                topic = Topic(subject_id=subj_id, name=topic_name, sort_order=topic_order[topic_key])
                db.session.add(topic)
                db.session.flush()  # Get ID
                topics_created += 1
            else:
                topic.sort_order = topic_order[topic_key]
                topics_updated += 1

            # Handle subtopic if present
            subtopic_name = row.get('subtopic_name', '').strip()
            if subtopic_name:
                # Determine subtopic sort_order from row position
                if topic.id not in subtopic_counter:
                    subtopic_counter[topic.id] = 1

                subtopic = Subtopic.query.filter_by(topic_id=topic.id, name=subtopic_name).first()
                if not subtopic:
                    hidden = row.get('subtopic_hidden', '0').strip() == '1'
                    subtopic = Subtopic(
                        topic_id=topic.id, name=subtopic_name,
                        sort_order=subtopic_counter[topic.id], hidden=hidden
                    )
                    db.session.add(subtopic)
                    subtopics_created += 1
                else:
                    subtopic.sort_order = subtopic_counter[topic.id]
                    hidden_str = row.get('subtopic_hidden', '').strip()
                    if hidden_str in ('0', '1'):
                        subtopic.hidden = hidden_str == '1'
                    subtopics_updated += 1

                subtopic_counter[topic.id] += 1

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
    writer.writerow(['subject_id', 'chapter_name', 'subchapter_name', 'subchapter_hidden'])

    for chapter in chapters_list:
        subchapters = chapter.subchapters.order_by(Subchapter.sort_order).all()
        if subchapters:
            for sc in subchapters:
                writer.writerow([
                    subject_id, chapter.name,
                    sc.name, 1 if sc.hidden else 0
                ])
        else:
            writer.writerow([subject_id, chapter.name, '', ''])

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

        # Track sort_order from row position
        chapter_order = {}        # (subj_id, chapter_name) -> sort_order
        chapter_counter = {}      # subj_id -> next sort_order
        subchapter_counter = {}   # chapter_id -> next sort_order

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

            # Determine chapter sort_order from row position
            chapter_key = (subj_id, chapter_name)
            if chapter_key not in chapter_order:
                if subj_id not in chapter_counter:
                    chapter_counter[subj_id] = 1
                chapter_order[chapter_key] = chapter_counter[subj_id]
                chapter_counter[subj_id] += 1

            # Find or create chapter
            chapter = Chapter.query.filter_by(subject_id=subj_id, name=chapter_name).first()
            if not chapter:
                chapter = Chapter(subject_id=subj_id, name=chapter_name, sort_order=chapter_order[chapter_key])
                db.session.add(chapter)
                db.session.flush()
                chapters_created += 1
            else:
                chapter.sort_order = chapter_order[chapter_key]
                chapters_updated += 1

            # Handle subchapter if present
            subchapter_name = row.get('subchapter_name', '').strip()
            if subchapter_name:
                # Determine subchapter sort_order from row position
                if chapter.id not in subchapter_counter:
                    subchapter_counter[chapter.id] = 1

                subchapter = Subchapter.query.filter_by(chapter_id=chapter.id, name=subchapter_name).first()
                if not subchapter:
                    hidden = row.get('subchapter_hidden', '0').strip() == '1'
                    subchapter = Subchapter(
                        chapter_id=chapter.id, name=subchapter_name,
                        sort_order=subchapter_counter[chapter.id], hidden=hidden
                    )
                    db.session.add(subchapter)
                    subchapters_created += 1
                else:
                    subchapter.sort_order = subchapter_counter[chapter.id]
                    hidden_str = row.get('subchapter_hidden', '').strip()
                    if hidden_str in ('0', '1'):
                        subchapter.hidden = hidden_str == '1'
                    subchapters_updated += 1

                subchapter_counter[chapter.id] += 1

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


# ==================== Ingestion (Admin) ====================

@admin_bp.route('/ingestion')
@login_required
@admin_required
def ingestion():
    """Ingestion management page"""
    subjects = get_user_admin_subjects()
    source_path = current_app.config['SOURCE_PATH']
    return render_template('admin_ingestion.html', subjects=subjects, source_path=source_path)


@admin_bp.route('/ingestion/preview')
@login_required
@admin_required
def ingestion_preview():
    """Preview source directory contents for a subject"""
    from app.ingestor import preview_source_directory
    
    subject_id = request.args.get('subject_id')
    if not subject_id:
        return jsonify({'error': 'No subject selected'}), 400
    
    # Verify admin access to this subject
    admin_subjects = get_user_admin_subjects()
    admin_subject_ids = [s.id for s in admin_subjects]
    if subject_id not in admin_subject_ids:
        return jsonify({'error': 'Access denied for this subject'}), 403
    
    source_path = current_app.config['SOURCE_PATH']
    subject_path = os.path.join(source_path, subject_id)
    
    if not os.path.exists(subject_path):
        return jsonify({
            'error': f'Source folder not found: {subject_id}/',
            'folders': [],
            'total_files': 0,
            'parseable_files': 0,
            'skipped_files': 0,
            'subject_path': subject_path
        })
    
    preview = preview_source_directory(subject_path)
    preview['subject_path'] = subject_path
    return jsonify(preview)


@admin_bp.route('/ingestion/start')
@login_required
@admin_required
def ingestion_start():
    """Start ingestion via SSE stream"""
    from app.ingestor import scan_directory_stream
    
    subject_id = request.args.get('subject_id')
    if not subject_id:
        def error_gen_no_subject():
            yield f"data: {json.dumps({'type': 'error', 'message': 'No subject selected'})}\n\n"
        return Response(error_gen_no_subject(), mimetype='text/event-stream')
    
    # Verify admin access to this subject
    admin_subjects = get_user_admin_subjects()
    admin_subject_ids = [s.id for s in admin_subjects]
    if subject_id not in admin_subject_ids:
        def error_gen_denied():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Access denied for this subject'})}\n\n"
        return Response(error_gen_denied(), mimetype='text/event-stream')
    
    source_path = current_app.config['SOURCE_PATH']
    subject_path = os.path.join(source_path, subject_id)
    app = current_app._get_current_object()
    
    def generate():
        with app.app_context():
            try:
                for event in scan_directory_stream(subject_path, base_path=source_path):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Unexpected error: {str(e)}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message': 'Ingestion failed due to error.'})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ==================== Database Health (Super Admin) ====================

@admin_bp.route('/health')
@login_required
@super_admin_required
def health():
    """Database health check page - super admin only"""
    source_path = current_app.config['SOURCE_PATH']
    return render_template('admin_health.html', source_path=source_path)


@admin_bp.route('/health/stats')
@login_required
@super_admin_required
def health_stats():
    """Get database statistics as JSON"""
    from app.ingestor import get_database_stats
    
    source_path = current_app.config['SOURCE_PATH']
    stats = get_database_stats(source_path)
    return jsonify(stats)


@admin_bp.route('/health/sync')
@login_required
@super_admin_required
def health_sync():
    """Run database sync via SSE stream"""
    from app.ingestor import sync_database_stream
    
    mode = request.args.get('mode', 'dry_run')
    dry_run = (mode != 'delete')
    source_path = current_app.config['SOURCE_PATH']
    app = current_app._get_current_object()
    
    def generate():
        with app.app_context():
            try:
                for event in sync_database_stream(source_path, dry_run=dry_run):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Unexpected error: {str(e)}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message': 'Sync failed due to error.'})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ==================== File Browser (Super Admin Only) ====================

def _resolve_source_path():
    """Get the resolved source path, using abspath instead of realpath to avoid UNC issues on Windows."""
    return os.path.abspath(current_app.config['SOURCE_PATH'])


def _safe_join(base, *paths):
    """Safely join paths ensuring result stays within base directory."""
    base = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base, *paths))
    # Use os.path.normcase for case-insensitive comparison on Windows
    if not os.path.normcase(target).startswith(os.path.normcase(base)):
        return None
    return target


def _get_dir_info(full_path, source_path):
    """Get directory listing info for a given path."""
    # Use abspath consistently to avoid mount mismatch issues
    full_path = os.path.abspath(full_path)
    source_path = os.path.abspath(source_path)
    rel_path = os.path.relpath(full_path, source_path).replace('\\', '/')
    if rel_path == '.':
        rel_path = ''

    items = []
    try:
        entries = sorted(os.listdir(full_path), key=lambda x: (not os.path.isdir(os.path.join(full_path, x)), x.lower()))
    except PermissionError:
        entries = []

    for entry in entries:
        entry_path = os.path.join(full_path, entry)
        is_dir = os.path.isdir(entry_path)
        stat = os.stat(entry_path)
        items.append({
            'name': entry,
            'is_dir': is_dir,
            'size': stat.st_size if not is_dir else None,
            'modified': stat.st_mtime,
        })

    return {
        'current_path': rel_path,
        'items': items,
    }


@admin_bp.route('/files')
@login_required
@super_admin_required
def files():
    """File browser page - super admin only"""
    source_path = current_app.config['SOURCE_PATH']
    return render_template('admin_files.html', source_path=source_path)


@admin_bp.route('/files/list')
@login_required
@super_admin_required
def files_list():
    """List files and directories in a path (JSON API)"""
    source_path = _resolve_source_path()
    rel_path = request.args.get('path', '').strip('/')

    if rel_path:
        full_path = _safe_join(source_path, rel_path)
    else:
        full_path = source_path

    if not full_path or not os.path.isdir(full_path):
        return jsonify({'error': 'Directory not found or access denied'}), 404

    info = _get_dir_info(full_path, source_path)
    return jsonify(info)


@admin_bp.route('/files/download')
@login_required
@super_admin_required
def files_download():
    """Download a single file"""
    source_path = _resolve_source_path()
    rel_path = request.args.get('path', '').strip('/')

    if not rel_path:
        return jsonify({'error': 'No file specified'}), 400

    full_path = _safe_join(source_path, rel_path)
    if not full_path or not os.path.isfile(full_path):
        return jsonify({'error': 'File not found or access denied'}), 404

    return send_file(full_path, as_attachment=True)


@admin_bp.route('/files/upload', methods=['POST'])
@login_required
@super_admin_required
def files_upload():
    """Upload one or more files to a directory"""
    source_path = _resolve_source_path()
    rel_path = request.form.get('path', '').strip('/')

    if rel_path:
        target_dir = _safe_join(source_path, rel_path)
    else:
        target_dir = source_path

    if not target_dir or not os.path.isdir(target_dir):
        return jsonify({'error': 'Target directory not found or access denied'}), 404

    uploaded_files = request.files.getlist('files')
    if not uploaded_files:
        return jsonify({'error': 'No files provided'}), 400

    uploaded = []
    errors = []
    for f in uploaded_files:
        if not f.filename:
            continue
        filename = secure_filename(f.filename)
        if not filename:
            errors.append(f'Invalid filename: {f.filename}')
            continue
        dest = os.path.join(target_dir, filename)
        try:
            f.save(dest)
            uploaded.append(filename)
        except Exception as e:
            errors.append(f'{filename}: {str(e)}')

    return jsonify({
        'success': True,
        'uploaded': uploaded,
        'errors': errors,
        'message': f'Uploaded {len(uploaded)} file(s)' + (f', {len(errors)} error(s)' if errors else '')
    })


@admin_bp.route('/files/rename', methods=['POST'])
@login_required
@super_admin_required
def files_rename():
    """Rename a file or directory"""
    source_path = _resolve_source_path()
    data = request.get_json()
    old_path = data.get('path', '').strip('/')
    new_name = data.get('new_name', '').strip()

    if not old_path or not new_name:
        return jsonify({'error': 'Path and new name are required'}), 400

    # Validate new_name doesn't contain path separators
    if '/' in new_name or '\\' in new_name:
        return jsonify({'error': 'New name cannot contain path separators'}), 400

    full_path = _safe_join(source_path, old_path)
    if not full_path or not os.path.exists(full_path):
        return jsonify({'error': 'File or directory not found'}), 404

    parent_dir = os.path.dirname(full_path)
    new_full_path = os.path.join(parent_dir, new_name)

    # Ensure new path is also within source
    new_full_path_abs = os.path.abspath(new_full_path)
    if not os.path.normcase(new_full_path_abs).startswith(os.path.normcase(source_path)):
        return jsonify({'error': 'Access denied'}), 403

    if os.path.exists(new_full_path):
        return jsonify({'error': f'A file or directory named "{new_name}" already exists'}), 409

    try:
        os.rename(full_path, new_full_path)
        return jsonify({'success': True, 'new_name': new_name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/files/delete', methods=['POST'])
@login_required
@super_admin_required
def files_delete():
    """Delete one or more files or directories"""
    source_path = _resolve_source_path()
    data = request.get_json()
    paths = data.get('paths', [])

    if not paths:
        return jsonify({'error': 'No paths specified'}), 400

    deleted = []
    errors = []
    for rel_path in paths:
        rel_path = rel_path.strip('/')
        if not rel_path:
            errors.append('Cannot delete root directory')
            continue

        full_path = _safe_join(source_path, rel_path)
        if not full_path or not os.path.exists(full_path):
            errors.append(f'{rel_path}: not found')
            continue

        # Extra safety: don't allow deleting the source root
        if os.path.normcase(os.path.abspath(full_path)) == os.path.normcase(source_path):
            errors.append(f'{rel_path}: cannot delete source root')
            continue

        try:
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)
            deleted.append(rel_path)
        except Exception as e:
            errors.append(f'{rel_path}: {str(e)}')

    return jsonify({
        'success': True,
        'deleted': deleted,
        'errors': errors,
        'message': f'Deleted {len(deleted)} item(s)' + (f', {len(errors)} error(s)' if errors else '')
    })


@admin_bp.route('/files/mkdir', methods=['POST'])
@login_required
@super_admin_required
def files_mkdir():
    """Create a new directory"""
    source_path = _resolve_source_path()
    data = request.get_json()
    parent_path = data.get('path', '').strip('/')
    dir_name = data.get('name', '').strip()

    if not dir_name:
        return jsonify({'error': 'Directory name is required'}), 400

    if '/' in dir_name or '\\' in dir_name:
        return jsonify({'error': 'Directory name cannot contain path separators'}), 400

    if parent_path:
        parent_dir = _safe_join(source_path, parent_path)
    else:
        parent_dir = source_path

    if not parent_dir or not os.path.isdir(parent_dir):
        return jsonify({'error': 'Parent directory not found'}), 404

    new_dir = os.path.join(parent_dir, dir_name)
    new_dir_abs = os.path.abspath(new_dir)
    if not os.path.normcase(new_dir_abs).startswith(os.path.normcase(source_path)):
        return jsonify({'error': 'Access denied'}), 403

    if os.path.exists(new_dir):
        return jsonify({'error': f'"{dir_name}" already exists'}), 409

    try:
        os.makedirs(new_dir)
        return jsonify({'success': True, 'name': dir_name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

"""
Admin panel routes for managing topics and tagging questions
"""
import csv
import io
import os
import re
import json
import shutil
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, Response, make_response, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app import db
from app import word_com
from app.models import Subject, Topic, Subtopic, Question, QuestionAsset, Chapter, Subchapter, User, UserSubjectPermission
from app.utils import admin_required, super_admin_required, get_user_admin_subjects, VERSIONS, VERSION_LABELS
from app import md_render

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


@admin_bp.route('/questions/batch_delete_assets', methods=['POST'])
@login_required
@admin_required
def batch_delete_assets():
    """Batch-delete a filtered subset of assets for the selected questions.

    Filters are applied as an AND of three independent IN(...) clauses:
        file_format IN formats   (any non-empty subset of IMG/MD/DOC)
        version     IN versions  (any non-empty subset of EN/CH/BI/ENO/CHO)
        asset_type  IN atypes    (any non-empty subset of QUE/ANS/SOL)

    The selected-question scope is further intersected with the caller's
    admin subjects so a subject-admin can't accidentally touch questions
    in a subject they don't own.

    Each removed asset also triggers the same DOC-thumbnail lifecycle hooks
    as the single-asset delete route so cached PNGs stay consistent.
    """
    try:
        question_ids = request.form.getlist('question_ids')
        formats = [f for f in request.form.getlist('formats') if f in ('IMG', 'MD', 'DOC')]
        # Accept both the new `versions` param and the legacy `langs` param.
        _raw_versions = request.form.getlist('versions') or request.form.getlist('langs')
        versions = [v for v in _raw_versions if v in VERSIONS]
        atypes = [t for t in request.form.getlist('atypes') if t in ('QUE', 'ANS', 'SOL')]
        delete_files = request.form.get('delete_files', 'true') == 'true'

        if not question_ids:
            return jsonify({'success': False, 'error': 'No questions selected'}), 400
        if not formats:
            return jsonify({'success': False, 'error': 'Pick at least one format (IMG / MD / DOC)'}), 400
        if not versions:
            return jsonify({'success': False, 'error': 'Pick at least one version (' + ' / '.join(VERSIONS) + ')'}), 400
        if not atypes:
            return jsonify({'success': False, 'error': 'Pick at least one asset type (QUE / ANS / SOL)'}), 400

        question_ids = [int(qid) for qid in question_ids if qid.isdigit()]
        if not question_ids:
            return jsonify({'success': False, 'error': 'Invalid question IDs'}), 400

        # Restrict to questions whose subject the caller can admin.
        admin_subjects = [s.id for s in get_user_admin_subjects()]
        accessible_qids = [
            q.id for q in Question.query
                .filter(Question.id.in_(question_ids))
                .filter(Question.subject.in_(admin_subjects))
                .all()
        ]
        if not accessible_qids:
            return jsonify({'success': False, 'error': 'No accessible questions in selection'}), 403

        assets = (
            QuestionAsset.query
            .filter(QuestionAsset.question_id.in_(accessible_qids))
            .filter(QuestionAsset.file_format.in_(formats))
            .filter(QuestionAsset.version.in_(versions))
            .filter(QuestionAsset.asset_type.in_(atypes))
            .all()
        )

        if not assets:
            return jsonify({
                'success': True,
                'deleted_count': 0,
                'files_deleted': 0,
                'questions_touched': 0,
                'message': 'No matching assets found',
            })

        source_path = current_app.config['SOURCE_PATH']
        files_deleted = 0
        files_missing = 0
        deleted_count = 0
        touched_qids = set()

        # Snapshot the per-asset metadata we need for the thumbnail-lifecycle hooks,
        # because we lose the rows after db.session.delete + commit.
        doc_asset_ids_deleted: list[int] = []
        img_slots_deleted: list[tuple[int, str, str]] = []  # (question_id, asset_type, version)

        for asset in assets:
            touched_qids.add(asset.question_id)

            if delete_files:
                full_path = os.path.join(source_path, asset.file_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                        files_deleted += 1
                    except OSError:
                        # Continue with DB delete even if filesystem remove fails.
                        pass
                else:
                    files_missing += 1

            if asset.file_format == 'MD':
                md_render.invalidate(asset.id)
            elif asset.file_format == 'DOC':
                doc_asset_ids_deleted.append(asset.id)
            elif asset.file_format == 'IMG':
                img_slots_deleted.append((asset.question_id, asset.asset_type, asset.version))

            db.session.delete(asset)
            deleted_count += 1

        db.session.commit()

        # Post-commit thumbnail lifecycle (mirrors delete_question_asset).
        from app import doc_thumbnails
        for asset_id in doc_asset_ids_deleted:
            doc_thumbnails.on_doc_asset_deleted(asset_id)
        if img_slots_deleted:
            class _Stub:
                pass
            for qid, atype, ver in img_slots_deleted:
                stub = _Stub()
                stub.file_format = 'IMG'
                stub.question_id = qid
                stub.asset_type = atype
                stub.version = ver
                doc_thumbnails.on_img_asset_deleted(stub)

        msg = f'Deleted {deleted_count} asset(s) across {len(touched_qids)} question(s)'
        if delete_files:
            msg += f' ({files_deleted} file(s) removed from disk'
            if files_missing:
                msg += f', {files_missing} already missing'
            msg += ')'
        else:
            msg += ' (files on disk left intact)'

        return jsonify({
            'success': True,
            'deleted_count': deleted_count,
            'files_deleted': files_deleted,
            'files_missing': files_missing,
            'questions_touched': len(touched_qids),
            'message': msg,
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

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
PP_QID_PATTERN = re.compile(r'^(?P<subj>[A-Z0-9]+)_(?P<source>DSE|CE|AL)_(?P<year>\d{4})_(?P<paper>P[A-Za-z0-9]+)_Q(?P<qno>\d+)$')
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
    return None, 'Invalid QID format. Expected SUBJ_SOURCE_YEAR_PAPER_QNO (e.g. MATC_DSE_2024_P1_Q5 or MATC_DSE_2024_P2A_Q1) or SUBJ_QB_DETAIL_QNO (e.g. MATC_QB_BOOK1_Q1)'


def _extract_qb_detail(qid):
    """Extract the 'detail' component from a QB-style QID (SUBJ_QB_DETAIL_QNO).
    Uses the QB regex for robust parsing, falls back to string split."""
    m = QB_QID_PATTERN.match(qid)
    if m:
        return m.group('detail')
    parts = qid.split('_')
    return parts[2] if len(parts) >= 4 else 'UNKNOWN'


def _build_asset_file_path(question, asset):
    """Build the expected relative file path for an asset based on the question's QID components.
    Always returns forward-slash separated paths for cross-platform consistency."""
    ext = asset.file_path.rsplit('.', 1)[-1] if '.' in asset.file_path else 'png'
    part_suffix = f'_{asset.part_number}' if asset.part_number > 1 else ''
    
    if question.source in ('DSE', 'CE', 'AL'):
        filename = f"{question.qid}_{asset.version}_{asset.asset_type}{part_suffix}.{ext}"
        folder = '/'.join([question.subject, 'PP', question.source,
                           str(question.year), question.paper])
    else:
        # QB
        detail = _extract_qb_detail(question.qid)
        filename = f"{question.qid}_{asset.version}_{asset.asset_type}{part_suffix}.{ext}"
        folder = '/'.join([question.subject, 'QB', detail])
    
    return f"{folder}/{filename}"


@admin_bp.route('/questions')
@login_required
@admin_required
def questions_page():
    """Admin question management page"""
    subjects = get_user_admin_subjects()
    return render_template(
        'admin_questions.html',
        subjects=subjects,
        md_max_size_bytes=current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024),
        ai_tools_enabled=current_app.config.get('AI_TOOLS_ENABLED', True),
    )


@admin_bp.route('/questions/api/list')
@login_required
@admin_required
def questions_api_list():
    """API: fetch paginated & filtered question list"""
    qid_search = request.args.get('qid_search', '').strip()
    selected_ids_str = request.args.get('selected_ids', '').strip()
    # Explicit QID-string list filter (comma-separated). Used by the DB-Health
    # anomaly modal to jump straight to a specific set of questions.
    qids_str = request.args.get('qids', '').strip()
    sort_field = request.args.get('sort', 'created_at')
    sort_dir = request.args.get('dir', 'desc')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    if page_size not in (10, 20, 50, 100, 200):
        page_size = 50

    admin_subjects = [s.id for s in get_user_admin_subjects()]
    query = Question.query.filter(Question.subject.in_(admin_subjects))

    preserve_selection_order = False
    selected_ids = []
    qid_order_list = []

    if qids_str:
        # Filter by explicit QID string list (anomaly view, etc.)
        qid_order_list = [q.strip() for q in qids_str.split(',') if q.strip()]
        if qid_order_list:
            query = query.filter(Question.qid.in_(qid_order_list))
            # Preserve the supplied order unless user picked an explicit sort
            if sort_field in ('selection_order', 'created_at'):
                preserve_selection_order = True
    elif selected_ids_str:
        # Filter by specific question internal IDs (from dashboard localStorage)
        try:
            selected_ids = [int(x) for x in selected_ids_str.split(',') if x.strip()]
            if selected_ids:
                query = query.filter(Question.id.in_(selected_ids))
                # Default to preserving selection order (dashboard order)
                if sort_field == 'selection_order' or sort_field == 'created_at':
                    preserve_selection_order = True
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
    if preserve_selection_order and qid_order_list:
        # Preserve order of the supplied QID list
        from sqlalchemy import case
        whens = [(qid, idx) for idx, qid in enumerate(qid_order_list)]
        ordering = case(*whens, value=Question.qid, else_=len(qid_order_list))
        query = query.order_by(ordering)
    elif preserve_selection_order and selected_ids:
        # Use CASE expression to preserve the original selection order from dashboard
        from sqlalchemy import case
        whens = [(qid, idx) for idx, qid in enumerate(selected_ids)]
        ordering = case(*whens, value=Question.id, else_=len(selected_ids))
        query = query.order_by(ordering)
    elif sort_field == 'qid':
        # Natural sort for QID: sort by component fields (subject, source, year, paper, qno)
        if sort_dir == 'asc':
            query = query.order_by(
                Question.subject.asc(), Question.source.asc(),
                Question.year.asc(), Question.paper.asc(), Question.qno.asc()
            )
        else:
            query = query.order_by(
                Question.subject.desc(), Question.source.desc(),
                Question.year.desc(), Question.paper.desc(), Question.qno.desc()
            )
    else:
        sort_col_map = {
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
    """API: get all assets for a question, grouped by version and type"""
    question = Question.query.get_or_404(question_id)
    assets = QuestionAsset.query.filter_by(question_id=question_id).order_by(
        QuestionAsset.version, QuestionAsset.asset_type, QuestionAsset.part_number
    ).all()

    result = {}
    for a in assets:
        ver = a.version
        atype = a.asset_type
        if ver not in result:
            result[ver] = {}
        if atype not in result[ver]:
            result[ver][atype] = []
        check_result = None
        if a.check_result:
            try:
                check_result = json.loads(a.check_result)
            except (ValueError, TypeError):
                check_result = {'status': a.check_state, 'raw': a.check_result}
        result[ver][atype].append({
            'id': a.id,
            'part_number': a.part_number,
            'file_format': a.file_format,
            'file_path': a.file_path,
            'preview_url': url_for('dashboard.get_asset_preview', asset_id=a.id),
            'check_state': a.check_state,
            'check_result': check_result,
            'checked_at': a.checked_at.isoformat() if a.checked_at else None,
        })

    return jsonify({'assets': result, 'qid': question.qid})


@admin_bp.route('/questions/<int:question_id>/assets/check-state', methods=['POST'])
@login_required
@admin_required
def set_asset_check_state(question_id):
    """Manually set / clear the proofread check state for one
    (version, asset_type) slot — drives the editable status bar in the
    edit-question modal.

    Body JSON: {version, asset_type, state, note?, severity?}
      state ∈ 'ok' | 'issues' | 'error' | 'clear'  ('clear' → unchecked)

    Writes check_state/check_result/checked_at to EVERY asset row in the slot
    (mirrors how the AI proofread writes the typed IMG parts). Subject-admin
    scoped.
    """
    question = Question.query.get_or_404(question_id)
    if not current_user.is_super_admin:
        admin_subject_ids = [s.id for s in get_user_admin_subjects()]
        if question.subject not in admin_subject_ids:
            return jsonify({'error': 'You do not have admin access to this subject.'}), 403

    data = request.get_json(silent=True) or {}
    version = (data.get('version') or '').strip().upper()
    asset_type = (data.get('asset_type') or '').strip().upper()
    state = (data.get('state') or '').strip().lower()
    note = (data.get('note') or '').strip()
    severity = (data.get('severity') or 'minor').strip().lower()

    if version not in set(VERSIONS):
        return jsonify({'error': 'version must be one of ' + '/'.join(VERSIONS)}), 400
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        return jsonify({'error': 'asset_type must be QUE / ANS / SOL'}), 400
    if state not in ('ok', 'issues', 'error', 'clear'):
        return jsonify({'error': "state must be ok / issues / error / clear"}), 400
    if severity not in ('minor', 'major', 'critical'):
        severity = 'minor'

    slot_assets = QuestionAsset.query.filter_by(
        question_id=question_id, version=version, asset_type=asset_type
    ).all()
    if not slot_assets:
        return jsonify({'error': f'No {version}/{asset_type} assets to mark.'}), 404

    if state == 'clear':
        new_state = None
        encoded = None
        checked_at = None
    else:
        new_state = state
        result = {'status': state, 'issues': [], 'checked_by': 'manual',
                  'editor': current_user.username}
        if state == 'issues':
            result['issues'] = [{
                'severity': severity,
                'location': '',
                'description': note or 'Marked as having issues (manual).',
            }]
        elif state == 'error' and note:
            result['raw'] = note
        elif note:
            result['note'] = note
        encoded = json.dumps(result, ensure_ascii=False)
        checked_at = datetime.utcnow()

    try:
        for a in slot_assets:
            a.check_state = new_state
            a.check_result = encoded
            a.checked_at = checked_at
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('Manual check-state write failed for q%s %s/%s',
                         question_id, version, asset_type)
        return jsonify({'error': f'Save failed: {e}'}), 500

    return jsonify({
        'success': True,
        'version': version,
        'asset_type': asset_type,
        'check_state': new_state,
        'checked_at': checked_at.isoformat() if checked_at else None,
    })


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

    version = request.form.get('version') or request.form.get('language', 'EN')
    asset_type = request.form.get('asset_type', 'QUE')
    
    if version not in VERSIONS:
        return jsonify({'error': 'Invalid version'}), 400
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        return jsonify({'error': 'Invalid asset type'}), 400

    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files provided'}), 400

    source_path = current_app.config['SOURCE_PATH']
    md_max = current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024)

    # Determine next image part number — only IMG assets are multi-part.
    # MD and DOC are always part_number=1 (single-slot), so they must not
    # consume an image part index.
    last_img_part = QuestionAsset.query.filter_by(
        question_id=question_id, version=version,
        asset_type=asset_type, file_format='IMG'
    ).order_by(QuestionAsset.part_number.desc()).first()
    next_img_part = (last_img_part.part_number + 1) if last_img_part else 1

    uploaded = []
    errors = []
    for f in files:
        if not f.filename:
            continue

        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'png'

        # Determine file format
        if ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp'):
            file_format = 'IMG'
        elif ext in ('doc', 'docx'):
            file_format = 'DOC'
        elif ext in ('md', 'markdown'):
            file_format = 'MD'
        else:
            errors.append(f'{f.filename}: unsupported extension')
            continue

        if file_format == 'MD':
            # MD is single-slot: reject if an MD asset already exists.
            existing_md = QuestionAsset.query.filter_by(
                question_id=question_id, version=version,
                asset_type=asset_type, file_format='MD'
            ).first()
            if existing_md:
                errors.append(
                    f'{f.filename}: a Markdown asset already exists for {asset_type}/{version}. '
                    f'Edit or delete it first.'
                )
                continue

            # Enforce size cap (read into memory; MD files are small text).
            data = f.read()
            if len(data) > md_max:
                errors.append(
                    f'{f.filename}: exceeds MD_MAX_SIZE_BYTES ({md_max} bytes)'
                )
                continue
            # Normalise extension to .md for consistency.
            ext = 'md'
            filename = f"{question.qid}_{version}_{asset_type}.{ext}"
            part_to_use = 1

        elif file_format == 'DOC':
            # DOC is single-slot too: only one Word document per (version, atype).
            existing_doc = QuestionAsset.query.filter_by(
                question_id=question_id, version=version,
                asset_type=asset_type, file_format='DOC'
            ).first()
            if existing_doc:
                errors.append(
                    f'{f.filename}: a Word document already exists for {asset_type}/{version}. '
                    f'Delete it first to replace.'
                )
                continue
            filename = f"{question.qid}_{version}_{asset_type}.{ext}"
            part_to_use = 1

        else:  # IMG — multi-part allowed
            part_suffix = f'_{next_img_part}' if next_img_part > 1 else ''
            filename = f"{question.qid}_{version}_{asset_type}{part_suffix}.{ext}"
            part_to_use = next_img_part

        if question.source in ('DSE', 'CE', 'AL'):
            folder = '/'.join([question.subject, 'PP', question.source,
                               str(question.year), question.paper])
        else:
            detail = _extract_qb_detail(question.qid)
            folder = '/'.join([question.subject, 'QB', detail])

        rel_path = f"{folder}/{filename}"
        full_path = os.path.join(source_path, *rel_path.split('/'))

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        if file_format == 'MD':
            with open(full_path, 'wb') as out:
                out.write(data)
        else:
            f.save(full_path)

        asset = QuestionAsset(
            question_id=question_id,
            asset_type=asset_type,
            file_format=file_format,
            version=version,
            file_path=rel_path,
            part_number=part_to_use
        )
        db.session.add(asset)
        uploaded.append({
            'asset': asset,  # bound to session; id populated on flush
            'part_number': part_to_use,
            'file_path': rel_path,
            'file_format': file_format,
        })
        if file_format == 'IMG':
            next_img_part += 1

    db.session.commit()

    # Post-commit DOC thumbnail lifecycle:
    #   * IMG just uploaded into a slot → delete any stale DOC thumbnail.
    #   * DOC just uploaded into a slot → schedule async thumbnail render
    #     (only if no IMG wins the same slot).
    from app import doc_thumbnails
    for u in uploaded:
        a = u['asset']
        if a.file_format == 'IMG':
            doc_thumbnails.on_img_asset_created(a)
        elif a.file_format == 'DOC':
            doc_thumbnails.on_doc_asset_created(a)

    return jsonify({
        'success': True,
        'uploaded_count': len(uploaded),
        'errors': errors,
        'message': f'Uploaded {len(uploaded)} asset(s)' + (
            f', skipped {len(errors)}' if errors else ''
        )
    })


# ==================== Markdown asset edit/create endpoints ====================
#
# MD assets are single-part text files. These three endpoints (content/save/create)
# back the in-browser live editor (modal + fullscreen). They are gated on the
# admin subject-access check just like upload.

def _require_md_admin(question):
    """Helper: ensure current_user can admin the question's subject. Returns
    a (jsonify, status) tuple to return on failure, or None if allowed."""
    if current_user.is_super_admin:
        return None
    admin_subjects = [s.id for s in get_user_admin_subjects()]
    if question.subject not in admin_subjects:
        return jsonify({'error': 'Access denied'}), 403
    return None


@admin_bp.route('/questions/<int:question_id>/assets/<int:asset_id>/md/content', methods=['GET'])
@login_required
@admin_required
def get_md_asset_content(question_id, asset_id):
    """Return raw .md text + mtime for the editor to load."""
    question = Question.query.get_or_404(question_id)
    denial = _require_md_admin(question)
    if denial:
        return denial

    asset = QuestionAsset.query.filter_by(id=asset_id, question_id=question_id).first_or_404()
    if asset.file_format != 'MD':
        return jsonify({'error': 'Asset is not Markdown'}), 400

    source_path = current_app.config['SOURCE_PATH']
    full_path = os.path.join(source_path, *asset.file_path.split('/'))

    # Stat BEFORE read so the returned mtime_ns matches the returned content.
    # If we stat after reading, a concurrent write between read and stat would
    # return a NEW mtime alongside the OLD content; the next save's optimistic
    # mtime check would then accept a stale-write and silently clobber the
    # newer disk state.
    try:
        mtime_ns = os.stat(full_path).st_mtime_ns
    except OSError:
        return jsonify({'error': 'File not found on disk'}), 404

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        return jsonify({'error': f'Read failed: {e}'}), 500
    except UnicodeDecodeError as e:
        return jsonify({
            'error': f'File is not valid UTF-8 ({e.reason} at byte {e.start}). '
                     f'Check the source file encoding.'
        }), 400

    return jsonify({
        'asset_id': asset.id,
        'qid': question.qid,
        'version': asset.version,
        'asset_type': asset.asset_type,
        'file_path': asset.file_path,
        'mtime_ns': mtime_ns,
        'content': content,
        'max_size': current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024),
    })


@admin_bp.route('/questions/<int:question_id>/assets/<int:asset_id>/md/save', methods=['POST'])
@login_required
@admin_required
def save_md_asset_content(question_id, asset_id):
    """Overwrite an MD asset's contents. Optimistic conflict check on mtime_ns."""
    question = Question.query.get_or_404(question_id)
    denial = _require_md_admin(question)
    if denial:
        return denial

    asset = QuestionAsset.query.filter_by(id=asset_id, question_id=question_id).first_or_404()
    if asset.file_format != 'MD':
        return jsonify({'error': 'Asset is not Markdown'}), 400

    data = request.get_json(silent=True) or {}
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'content is required'}), 400
    if not isinstance(content, str):
        return jsonify({'error': 'content must be a string'}), 400

    md_max = current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024)
    payload = content.encode('utf-8')
    if len(payload) > md_max:
        return jsonify({
            'error': f'Content exceeds MD_MAX_SIZE_BYTES ({md_max} bytes); got {len(payload)}'
        }), 413

    source_path = current_app.config['SOURCE_PATH']
    full_path = os.path.join(source_path, *asset.file_path.split('/'))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    # Optimistic concurrency: if the caller passes expected_mtime_ns and the
    # file on disk has changed since, reject with 409 unless `force` is set.
    expected = data.get('expected_mtime_ns')
    force = bool(data.get('force'))
    if expected is not None and not force and os.path.exists(full_path):
        try:
            current = os.stat(full_path).st_mtime_ns
            if int(expected) != current:
                return jsonify({
                    'error': 'File changed on disk since you opened it. '
                             'Reload to see the latest content, or pass force=true to overwrite.',
                    'current_mtime_ns': current,
                }), 409
        except (OSError, ValueError):
            pass

    try:
        with open(full_path, 'wb') as f:
            f.write(payload)
    except OSError as e:
        return jsonify({'error': f'Write failed: {e}'}), 500

    md_render.invalidate(asset.id)

    return jsonify({
        'success': True,
        'asset_id': asset.id,
        'mtime_ns': os.stat(full_path).st_mtime_ns,
        'size_bytes': len(payload),
    })


@admin_bp.route('/questions/<int:question_id>/assets/md/create', methods=['POST'])
@login_required
@admin_required
def create_md_asset(question_id):
    """Create a new MD asset for (question, version, asset_type) from editor content.

    Rejects if an MD asset already exists in that slot (MD is single-part).
    """
    question = Question.query.get_or_404(question_id)
    denial = _require_md_admin(question)
    if denial:
        return denial

    data = request.get_json(silent=True) or {}
    version = data.get('version') or data.get('language', 'EN')
    asset_type = data.get('asset_type', 'QUE')
    content = data.get('content', '')

    if version not in VERSIONS:
        return jsonify({'error': 'Invalid version'}), 400
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        return jsonify({'error': 'Invalid asset_type'}), 400
    if not isinstance(content, str):
        return jsonify({'error': 'content must be a string'}), 400

    md_max = current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024)
    payload = content.encode('utf-8')
    if len(payload) > md_max:
        return jsonify({
            'error': f'Content exceeds MD_MAX_SIZE_BYTES ({md_max} bytes); got {len(payload)}'
        }), 413

    existing = QuestionAsset.query.filter_by(
        question_id=question_id, version=version,
        asset_type=asset_type, file_format='MD'
    ).first()
    if existing:
        return jsonify({
            'error': f'A Markdown asset already exists for {asset_type}/{version}. '
                     f'Edit or delete it first.',
            'existing_asset_id': existing.id,
        }), 409

    filename = f"{question.qid}_{version}_{asset_type}.md"
    if question.source in ('DSE', 'CE', 'AL'):
        folder = '/'.join([question.subject, 'PP', question.source,
                           str(question.year), question.paper])
    else:
        detail = _extract_qb_detail(question.qid)
        folder = '/'.join([question.subject, 'QB', detail])
    rel_path = f"{folder}/{filename}"

    source_path = current_app.config['SOURCE_PATH']
    full_path = os.path.join(source_path, *rel_path.split('/'))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    try:
        with open(full_path, 'wb') as f:
            f.write(payload)
    except OSError as e:
        return jsonify({'error': f'Write failed: {e}'}), 500

    asset = QuestionAsset(
        question_id=question_id,
        asset_type=asset_type,
        file_format='MD',
        version=version,
        file_path=rel_path,
        part_number=1,
    )
    db.session.add(asset)
    db.session.commit()

    return jsonify({
        'success': True,
        'asset_id': asset.id,
        'file_path': rel_path,
        'mtime_ns': os.stat(full_path).st_mtime_ns,
        'size_bytes': len(payload),
    })


@admin_bp.route('/questions/<int:question_id>/assets/<int:asset_id>/md/edit', methods=['GET'])
@login_required
@admin_required
def edit_md_asset_page(question_id, asset_id):
    """Fullscreen Markdown editor page (templates/admin_md_editor.html)."""
    question = Question.query.get_or_404(question_id)
    denial = _require_md_admin(question)
    if denial:
        return denial

    asset = QuestionAsset.query.filter_by(id=asset_id, question_id=question_id).first_or_404()
    if asset.file_format != 'MD':
        flash('That asset is not a Markdown file.', 'warning')
        return redirect(url_for('admin.questions_page'))

    return render_template(
        'admin_md_editor.html',
        question=question,
        asset=asset,
        md_max=current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024),
    )


@admin_bp.route('/questions/<int:question_id>/assets/md/new', methods=['GET'])
@login_required
@admin_required
def new_md_asset_page(question_id):
    """Fullscreen Markdown editor in create mode.

    Query params: version=EN|CH|BI|ENO|CHO (legacy: language), asset_type=QUE|ANS|SOL.
    """
    question = Question.query.get_or_404(question_id)
    denial = _require_md_admin(question)
    if denial:
        return denial

    version = request.args.get('version') or request.args.get('language', 'EN')
    asset_type = request.args.get('asset_type', 'QUE')
    if version not in VERSIONS:
        version = 'EN'
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        asset_type = 'QUE'

    return render_template(
        'admin_md_editor.html',
        question=question,
        asset=None,
        create_version=version,
        create_asset_type=asset_type,
        md_max=current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024),
    )


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

    if asset.file_format == 'MD':
        md_render.invalidate(asset.id)

    # Capture the slot info before we lose the asset row — we need it for the
    # DOC thumbnail lifecycle below.
    deleted_format = asset.file_format
    deleted_asset_id = asset.id
    deleted_question_id = asset.question_id
    deleted_asset_type = asset.asset_type
    deleted_version = asset.version

    db.session.delete(asset)
    db.session.commit()

    # Post-commit DOC thumbnail lifecycle:
    #   * DOC deleted → drop its cached PNG.
    #   * IMG deleted → if a DOC is still in the slot and no IMG remains,
    #     that DOC becomes visible again — schedule its thumbnail render.
    from app import doc_thumbnails
    if deleted_format == 'DOC':
        doc_thumbnails.on_doc_asset_deleted(deleted_asset_id)
    elif deleted_format == 'IMG':
        # Re-evaluate the slot: pass a lightweight stand-in with the relevant fields.
        class _Stub:
            pass
        stub = _Stub()
        stub.file_format = 'IMG'
        stub.question_id = deleted_question_id
        stub.asset_type = deleted_asset_type
        stub.version = deleted_version
        doc_thumbnails.on_img_asset_deleted(stub)

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
    """Reorder asset parts for a given version and type, renaming files on disk"""
    data = request.get_json()
    version = data.get('version') or data.get('language')
    asset_type = data.get('asset_type')
    asset_ids = data.get('asset_ids', [])  # ordered list of asset IDs

    if not version or not asset_type or not asset_ids:
        return jsonify({'error': 'Missing required fields'}), 400

    question = Question.query.get_or_404(question_id)
    source_path = current_app.config['SOURCE_PATH']

    # Gather assets and record old state. Reordering only applies to IMG
    # assets (MD and DOC are single-slot, always part_number=1); silently
    # ignoring non-IMG IDs here prevents a buggy / hand-crafted client from
    # re-numbering MD/DOC and violating the single-part invariant.
    assets_to_reorder = []
    skipped = 0
    for idx, aid in enumerate(asset_ids, start=1):
        asset = QuestionAsset.query.filter_by(
            id=aid, question_id=question_id,
            version=version, asset_type=asset_type
        ).first()
        if not asset:
            continue
        if asset.file_format != 'IMG':
            skipped += 1
            continue
        old_full_path = os.path.join(source_path, asset.file_path)
        old_part = asset.part_number
        assets_to_reorder.append({
            'asset': asset,
            'old_full_path': old_full_path,
            'old_part': old_part,
            'new_part': idx,
        })

    if skipped and not assets_to_reorder:
        return jsonify({
            'error': 'Reorder only supports IMG parts; MD and DOC are single-slot.'
        }), 400

    # Two-phase DB update to avoid UniqueConstraint violation when swapping part numbers.
    # Phase 1: set all part_numbers to temporary negative values
    for item in assets_to_reorder:
        item['asset'].part_number = -item['new_part']
    db.session.flush()
    # Phase 2: set to the real positive values
    for item in assets_to_reorder:
        item['asset'].part_number = item['new_part']
    db.session.flush()

    # Now compute new file paths based on updated part_numbers
    for item in assets_to_reorder:
        new_rel_path = _build_asset_file_path(question, item['asset'])
        new_full_path = os.path.join(source_path, new_rel_path)
        item['new_rel_path'] = new_rel_path
        item['new_full_path'] = new_full_path

    # Rename files on disk using temp names to avoid conflicts (e.g. swapping part 1 and 2)
    rename_errors = []
    temp_renames = []
    for item in assets_to_reorder:
        # Normalise both paths with os.path.normpath for reliable comparison on Windows
        old_norm = os.path.normpath(item['old_full_path'])
        new_norm = os.path.normpath(item['new_full_path'])
        if old_norm != new_norm and os.path.exists(old_norm):
            temp_path = old_norm + '.tmp_reorder'
            try:
                os.rename(old_norm, temp_path)
                temp_renames.append((temp_path, item))
            except OSError as e:
                rename_errors.append(f"Failed to rename {os.path.basename(old_norm)}: {e}")

    # Now move from temp to final paths
    files_renamed = 0
    for temp_path, item in temp_renames:
        final_path = os.path.normpath(item['new_full_path'])
        try:
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            os.rename(temp_path, final_path)
            item['asset'].file_path = item['new_rel_path']
            files_renamed += 1
        except OSError as e:
            # Try to restore original
            try:
                os.rename(temp_path, os.path.normpath(item['old_full_path']))
            except OSError:
                pass
            rename_errors.append(f"Failed to rename to {os.path.basename(final_path)}: {e}")

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
    """Export question tags as CSV (using nominal QID and string names).

    Two modes:
    * ``question_ids`` (optional, csv of DB IDs) — export only those specific
      questions (filtered to subjects the caller can admin).  ``subject_id`` is
      ignored in this mode.
    * ``subject_id`` — original mode: export all questions for the subject.
    """
    from natsort import natsorted

    admin_subjects = get_user_admin_subjects()
    admin_subject_ids = [s.id for s in admin_subjects]

    raw_qids = request.args.get('question_ids', '').strip()
    if raw_qids:
        # Selection-based export — no subject filter required
        try:
            db_ids = [int(s) for s in raw_qids.split(',') if s.strip()]
        except ValueError:
            flash('Invalid question_ids parameter.', 'danger')
            return redirect(url_for('admin.export_import'))

        qs = Question.query.filter(Question.id.in_(db_ids)).all()
        if not current_user.is_super_admin:
            qs = [q for q in qs if q.subject in admin_subject_ids]

        questions = natsorted(qs, key=lambda q: q.qid)
        filename = f'question_tags_selected_{len(questions)}.csv'
    else:
        # Subject-based export (original behaviour)
        subject_id = request.args.get('subject_id')
        if not subject_id:
            flash('Please select a subject.', 'warning')
            return redirect(url_for('admin.export_import'))

        if subject_id not in admin_subject_ids:
            flash('Access denied for this subject.', 'danger')
            return redirect(url_for('admin.export_import'))

        questions = natsorted(
            Question.query.filter_by(subject=subject_id).all(),
            key=lambda q: q.qid
        )
        filename = f'question_tags_{subject_id}.csv'

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
        writer.writerow([
            q.qid,
            q.subject,
            q.major_topic.name if q.major_topic else '',
            q.major_subtopic.name if q.major_subtopic else '',
            '; '.join(t.name for t in q.minor_topics) if q.minor_topics else '',
            '; '.join(s.name for s in q.subtopics) if q.subtopics else '',
            q.chapter.name if q.chapter else '',
            q.subchapter.name if q.subchapter else '',
            q.section or '',
            q.level if q.level is not None else '',
            q.q_type or '',
            q.correct_percentage if q.correct_percentage is not None else '',
            q.description or '',
            q.answer or '',
            q.comment or '',
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
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


# ==================== PDF Batch Import (Admin) ====================
#
# Rasterise uploaded DSE question/solution PDFs, ask a vision LLM for a tight
# bounding box per question per page, then crop + create Question/QuestionAsset
# rows. Two modes (automatic, auto-then-review) share one detect->commit
# pipeline. Backed by app/pdf_import.py; reuses app/batch_image_gen.py for the
# atomic asset write. See .cursor/rules/pdf-import.mdc.

def _pdf_vision_endpoints():
    """Enabled, vision-capable LLM endpoints, in display order."""
    from app.models import LLMConfig
    return (LLMConfig.query
            .filter_by(enabled=True, supports_vision=True)
            .order_by(LLMConfig.sort_order, LLMConfig.name).all())


def _pdf_sse_error(message):
    """Return an SSE Response that emits a single error + done (so the
    EventSource client surfaces the failure and stops cleanly)."""
    def gen():
        yield f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'message': 'Aborted.'})}\n\n"
    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _pdf_load_token_meta(token):
    """Load a staging token's meta.json and enforce admin access to its
    subject. Returns ``(meta, error_message)``."""
    from app import pdf_import
    try:
        meta = pdf_import.load_meta(token)
    except (ValueError, OSError):
        return None, 'Staging session not found or expired. Please re-upload.'
    subject = meta.get('subject')
    if not current_user.is_super_admin:
        admin_ids = [s.id for s in get_user_admin_subjects()]
        if subject not in admin_ids:
            return None, f'You do not have admin access to subject {subject}.'
    return meta, None


@admin_bp.route('/pdf-import')
@login_required
@admin_required
def pdf_import_page():
    """PDF Batch Import management page."""
    subjects = get_user_admin_subjects()
    endpoints = [{'id': c.id, 'name': c.name, 'model_name': c.model_name}
                 for c in _pdf_vision_endpoints()]
    return render_template(
        'admin_pdf_import.html',
        subjects=subjects,
        versions=VERSIONS,
        version_labels=VERSION_LABELS,
        endpoints=endpoints,
        ai_enabled=bool(current_app.config.get('AI_TOOLS_ENABLED', True)),
        raster_width=int(current_app.config.get('PDF_IMPORT_RASTER_WIDTH', 1700)),
    )


@admin_bp.route('/pdf-import/stage', methods=['POST'])
@login_required
@admin_required
def pdf_import_stage():
    """Upload + rasterise the QUE/SOL PDFs for a paper. Returns the staging
    token and per-kind page lists."""
    from app import pdf_import

    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return jsonify({'error': 'AI features are disabled.'}), 400

    paper = request.form.get('paper', '')
    meta, err = pdf_import.parse_paper_prefix(paper)
    if err:
        return jsonify({'error': err}), 400

    subject = meta['subject']
    if not Subject.query.get(subject):
        return jsonify({'error': f'Subject {subject} does not exist.'}), 400
    if not current_user.is_super_admin:
        admin_ids = [s.id for s in get_user_admin_subjects()]
        if subject not in admin_ids:
            return jsonify({'error': f'You do not have admin access to subject {subject}.'}), 403

    version = (request.form.get('version') or '').strip().upper()
    if version not in VERSIONS:
        return jsonify({'error': 'version must be one of ' + '/'.join(VERSIONS)}), 400
    meta['version'] = version

    que_file = request.files.get('que_pdf')
    sol_file = request.files.get('sol_pdf')

    def _is_pdf(fs):
        return fs is not None and fs.filename and fs.filename.lower().endswith('.pdf')

    has_que = _is_pdf(que_file)
    has_sol = _is_pdf(sol_file)
    if not has_que and not has_sol:
        return jsonify({'error': 'Upload at least one PDF (a question PDF and/or a solution PDF).'}), 400

    raster_width = int(current_app.config.get('PDF_IMPORT_RASTER_WIDTH', 1700))
    try:
        token, saved_meta = pdf_import.stage(
            que_file if has_que else None,
            sol_file if has_sol else None,
            meta, raster_width)
    except Exception as e:
        current_app.logger.exception('PDF import staging failed')
        return jsonify({'error': f'Could not process PDF: {e}'}), 500

    def _pages(kind):
        info = saved_meta.get(kind)
        if not info:
            return []
        return [{'index': p['index'], 'width': p['width'], 'height': p['height']}
                for p in info['pages']]

    return jsonify({
        'token': token,
        'subject': subject, 'source': meta['source'],
        'year': meta['year'], 'paper': meta['paper'], 'version': version,
        'que': {'filename': (que_file.filename if has_que else None), 'pages': _pages('que')},
        'sol': {'filename': (sol_file.filename if has_sol else None), 'pages': _pages('sol')},
    })


@admin_bp.route('/pdf-import/page/<token>/<kind>/<int:page>.png')
@login_required
@admin_required
def pdf_import_page_image(token, kind, page):
    """Serve a staged page PNG for the review overlay."""
    from app import pdf_import
    if kind not in ('que', 'sol'):
        return abort(404)
    _meta, err = _pdf_load_token_meta(token)
    if err:
        return abort(404)
    try:
        path = pdf_import.page_png_path(token, kind, page)
    except ValueError:
        return abort(404)
    if not os.path.isfile(path):
        return abort(404)
    resp = send_file(path, mimetype='image/png', conditional=True)
    resp.headers['Cache-Control'] = 'private, max-age=600'
    return resp


@admin_bp.route('/pdf-import/detect')
@login_required
@admin_required
def pdf_import_detect():
    """SSE: detect question regions on every staged page (QUE then SOL)."""
    from app import pdf_import
    from app.models import LLMConfig

    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return _pdf_sse_error('AI features are disabled.')

    token = request.args.get('token', '')
    meta, err = _pdf_load_token_meta(token)
    if err:
        return _pdf_sse_error(err)

    try:
        endpoint_id = int(request.args.get('endpoint_id', '0'))
    except (ValueError, TypeError):
        endpoint_id = 0
    cfg = LLMConfig.query.get(endpoint_id)
    if cfg is None or not cfg.enabled:
        return _pdf_sse_error('Selected LLM endpoint not found or disabled.')
    if not cfg.supports_vision:
        return _pdf_sse_error('The selected endpoint is not vision-capable.')

    debug = request.args.get('debug', '0').strip().lower() in ('1', 'true', 'yes', 'on')

    app = current_app._get_current_object()
    job_id, cancel = pdf_import.new_job()

    def generate():
        with app.app_context():
            yield f"data: {json.dumps({'type': 'job', 'job_id': job_id})}\n\n"
            try:
                from app.models import LLMConfig as _Cfg
                live_cfg = _Cfg.query.get(endpoint_id)
                image_max_dim = int(app.config.get('LLM_IMAGE_MAX_DIM', 1600))
                for ev in pdf_import.iter_detect(app, cancel, token, live_cfg,
                                                 image_max_dim, debug=debug):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as e:
                current_app.logger.exception('PDF import detect stream aborted')
                yield f"data: {json.dumps({'type': 'error', 'message': f'Aborted: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message': 'Aborted.'})}\n\n"
            finally:
                pdf_import.finish_job(job_id)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@admin_bp.route('/pdf-import/redo-page', methods=['POST'])
@login_required
@admin_required
def pdf_import_redo_page():
    """Re-run detection for a single page (review mode). Returns fresh boxes."""
    from app import pdf_import
    from app.models import LLMConfig

    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return jsonify({'error': 'AI features are disabled.'}), 400

    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    kind = (data.get('kind') or '').strip()
    meta, err = _pdf_load_token_meta(token)
    if err:
        return jsonify({'error': err}), 404
    if kind not in ('que', 'sol') or not meta.get(kind):
        return jsonify({'error': 'invalid kind'}), 400
    try:
        index = int(data.get('index'))
    except (ValueError, TypeError):
        return jsonify({'error': 'invalid page index'}), 400

    cfg = LLMConfig.query.get(data.get('endpoint_id') or 0)
    if cfg is None or not cfg.enabled or not cfg.supports_vision:
        return jsonify({'error': 'Selected LLM endpoint is unavailable or not vision-capable.'}), 400

    image_max_dim = int(current_app.config.get('LLM_IMAGE_MAX_DIM', 1600))
    debug = bool(data.get('debug'))
    try:
        boxes, raw = pdf_import.detect_single_page(cfg, token, kind, index, image_max_dim)
    except Exception as e:
        current_app.logger.exception('PDF import redo-page failed')
        return jsonify({'error': f'Detection failed: {e}'}), 502
    out = {'boxes': boxes}
    if debug:
        current_app.logger.info('pdf-import raw redo (%s page %s):\n%s', kind, index + 1, raw)
        out['raw'] = (raw or '')[:6000]
    return jsonify(out)


@admin_bp.route('/pdf-import/plan', methods=['POST'])
@login_required
@admin_required
def pdf_import_save_plan():
    """Persist the (edited) plan before commit. Body: {token, plan:{que,sol}}."""
    from app import pdf_import

    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    _meta, err = _pdf_load_token_meta(token)
    if err:
        return jsonify({'error': err}), 404

    raw = data.get('plan') or {}
    clean = {'que': [], 'sol': []}
    for kind in ('que', 'sol'):
        for item in (raw.get(kind) or []):
            if not isinstance(item, dict):
                continue
            box = item.get('box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                box = [float(v) for v in box]
                page = int(item.get('page', 0))
            except (ValueError, TypeError):
                continue
            qno_raw = item.get('qno')
            qno = None
            if qno_raw is not None and str(qno_raw).strip() != '':
                m = re.search(r'\d+', str(qno_raw))
                if m:
                    qno = int(m.group(0))
            clean[kind].append({'page': page, 'qno': qno, 'box': box})
    try:
        pdf_import.save_plan(token, clean)
    except (ValueError, OSError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'success': True,
                    'counts': {'que': len(clean['que']), 'sol': len(clean['sol'])}})


@admin_bp.route('/pdf-import/commit')
@login_required
@admin_required
def pdf_import_commit():
    """SSE: crop every question region in plan.json and create the
    Question / QuestionAsset rows."""
    from app import pdf_import

    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return _pdf_sse_error('AI features are disabled.')

    token = request.args.get('token', '')
    meta, err = _pdf_load_token_meta(token)
    if err:
        return _pdf_sse_error(err)

    version = (request.args.get('version') or meta.get('version') or '').strip().upper()
    if version not in VERSIONS:
        return _pdf_sse_error('version must be one of ' + '/'.join(VERSIONS))
    overwrite = request.args.get('overwrite', '0') in ('1', 'true', 'yes')

    app = current_app._get_current_object()
    job_id, cancel = pdf_import.new_job()

    def generate():
        with app.app_context():
            yield f"data: {json.dumps({'type': 'job', 'job_id': job_id})}\n\n"
            try:
                plan = pdf_import.load_plan(token)
                source_path = app.config['SOURCE_PATH']
                for ev in pdf_import.iter_commit(app, cancel, token, plan,
                                                 version, overwrite, source_path):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as e:
                current_app.logger.exception('PDF import commit stream aborted')
                yield f"data: {json.dumps({'type': 'error', 'message': f'Aborted: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message': 'Aborted.'})}\n\n"
            finally:
                pdf_import.finish_job(job_id)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@admin_bp.route('/pdf-import/cancel', methods=['POST'])
@login_required
@admin_required
def pdf_import_cancel():
    """Signal a running detect/commit job to stop."""
    from app import pdf_import
    data = request.get_json(silent=True) or {}
    job_id = (data.get('job_id') or '').strip()
    if not job_id:
        return jsonify({'error': 'job_id is required'}), 400
    known = pdf_import.cancel_job(job_id)
    return jsonify({'success': True, 'known': known})


@admin_bp.route('/pdf-import/discard', methods=['POST'])
@login_required
@admin_required
def pdf_import_discard():
    """Delete a staging dir (its uploaded PDFs + rendered page PNGs)."""
    from app import pdf_import
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    _meta, err = _pdf_load_token_meta(token)
    if err:
        # Already gone / inaccessible — treat as success for idempotency.
        return jsonify({'success': True, 'removed': False})
    try:
        removed = pdf_import.discard(token)
    except ValueError:
        return jsonify({'success': True, 'removed': False})
    return jsonify({'success': True, 'removed': removed})


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


@admin_bp.route('/health/untracked')
@login_required
@super_admin_required
def health_untracked():
    """Find files on disk that are not tracked in the database (reverse orphan check)"""
    from app.ingestor import find_untracked_files
    source_path = current_app.config['SOURCE_PATH']
    untracked = find_untracked_files(source_path)
    return jsonify({
        'count': len(untracked),
        'files': untracked[:500]  # Cap for UI
    })


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


# ==================== DOC Thumbnail Backfill (Super Admin Only) ====================

@admin_bp.route('/health/doc-thumbnails/backfill')
@login_required
@super_admin_required
def doc_thumbnail_backfill():
    """
    Walk every DOC asset and (re)render its first-page PNG thumbnail
    when no IMG eclipses its slot. Streams progress via SSE.

    Use cases:
      * One-time priming after upgrading to a build that introduces DOC
        thumbnails (existing DOC assets predate the per-upload hook).
      * Recovering from a thumbnail directory that was wiped manually.
      * After tweaking DOC_THUMBNAIL_WIDTH (renders are cached by asset_id
        only, not by width — wipe and backfill to apply a new width).
    """
    from app import doc_thumbnails

    if not word_com.IS_AVAILABLE:
        return jsonify({
            'error': 'Word COM unavailable on this server — DOC thumbnails cannot be rendered.'
        }), 400

    force = request.args.get('force', '0') in ('1', 'true', 'yes')
    app = current_app._get_current_object()

    def generate():
        with app.app_context():
            from app.models import QuestionAsset
            try:
                doc_assets = QuestionAsset.query.filter_by(file_format='DOC').all()
                total = len(doc_assets)
                yield f"data: {json.dumps({'type': 'info', 'message': f'Scanning {total} DOC asset(s)...'})}\n\n"

                rendered = 0
                skipped_img = 0
                skipped_existing = 0
                failed = 0

                for i, a in enumerate(doc_assets, start=1):
                    base_msg = f'[{i}/{total}] {a.file_path}'

                    # Skip if an IMG wins the same slot (thumbnail would be
                    # invisible to the resolver anyway).
                    other_img = QuestionAsset.query.filter_by(
                        question_id=a.question_id, asset_type=a.asset_type,
                        version=a.version, file_format='IMG'
                    ).first()
                    if other_img:
                        skipped_img += 1
                        yield f"data: {json.dumps({'type': 'skip', 'message': f'{base_msg} - skipped (IMG wins slot)', 'current': i, 'total': total})}\n\n"
                        continue

                    # Skip if a cached PNG already exists and force=False.
                    if not force and doc_thumbnails.thumbnail_exists(a.id):
                        skipped_existing += 1
                        yield f"data: {json.dumps({'type': 'skip', 'message': f'{base_msg} - already cached', 'current': i, 'total': total})}\n\n"
                        continue

                    ok = doc_thumbnails.render_doc_thumbnail_sync(app, a.id)
                    if ok:
                        rendered += 1
                        yield f"data: {json.dumps({'type': 'success', 'message': f'{base_msg} - rendered', 'current': i, 'total': total})}\n\n"
                    else:
                        failed += 1
                        yield f"data: {json.dumps({'type': 'error', 'message': f'{base_msg} - render failed (see server log)', 'current': i, 'total': total})}\n\n"

                summary = (
                    f'Done. Rendered: {rendered}, '
                    f'skipped (IMG wins): {skipped_img}, '
                    f'skipped (already cached): {skipped_existing}, '
                    f'failed: {failed}.'
                )
                yield f"data: {json.dumps({'type': 'done', 'message': summary, 'current': total, 'total': total, 'stats': {'rendered': rendered, 'skipped_img': skipped_img, 'skipped_existing': skipped_existing, 'failed': failed}})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Unexpected error: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message': 'Backfill aborted.'})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@admin_bp.route('/questions/<int:question_id>/assets/<int:asset_id>/rerender-thumb', methods=['POST'])
@login_required
@admin_required
def rerender_doc_thumbnail(question_id, asset_id):
    """
    Force a re-render of a single DOC asset thumbnail. Used by the per-preview
    "Re-render" button in dashboard cards, the preview modal, and the
    viewer.

    Permission: subject-admin of the question (or super admin). The cost is
    real (one Word session, 1–3 s) so we gate this to admins.

    The endpoint returns immediately after scheduling — the frontend then
    polls `/dashboard/api/doc_thumbnail/<asset_id>.png` to swap in the new
    image when it's ready. See `window.oqbPollDocThumbnails`.
    """
    asset = QuestionAsset.query.filter_by(
        id=asset_id, question_id=question_id, file_format='DOC'
    ).first_or_404()

    # Subject-admin permission check.
    question = Question.query.get(question_id)
    if question is None:
        return jsonify({'error': 'Question not found'}), 404
    if not current_user.is_super_admin:
        admin_subjects = [s.id for s in get_user_admin_subjects()]
        if question.subject not in admin_subjects:
            return jsonify({'error': 'Access denied — not an admin for this subject'}), 403

    from app import doc_thumbnails as _doc_thumbnails

    scheduled = _doc_thumbnails.force_rerender(asset_id)
    if not scheduled:
        return jsonify({
            'success': False,
            'asset_id': asset_id,
            'error': 'Word COM not available on this server — re-render skipped.',
        }), 503

    return jsonify({
        'success': True,
        'asset_id': asset_id,
        'message': 'Re-render scheduled. Poll the thumbnail URL for the new PNG.',
    })


@admin_bp.route('/health/doc-thumbnails/clear', methods=['POST'])
@login_required
@super_admin_required
def doc_thumbnail_clear():
    """
    Delete every cached DOC thumbnail PNG from disk.

    The next time a card / modal / viewer resolves to a DOC asset, the lazy
    `ensure_thumbnail` path will re-schedule a render automatically. Use
    this when you want to free disk space, recover from a partial render,
    or apply a new `DOC_THUMBNAIL_WIDTH` without forcing an immediate
    re-render of every file.
    """
    thumb_dir = current_app.config.get('DOC_THUMBNAIL_PATH')
    if not thumb_dir or not os.path.isdir(thumb_dir):
        return jsonify({'success': True, 'deleted': 0, 'message': 'No thumbnail directory present.'})

    deleted = 0
    errors = []
    for entry in os.listdir(thumb_dir):
        if not entry.lower().endswith('.png'):
            continue
        full = os.path.join(thumb_dir, entry)
        try:
            os.remove(full)
            deleted += 1
        except OSError as e:
            errors.append(f'{entry}: {e}')

    return jsonify({
        'success': True,
        'deleted': deleted,
        'errors': errors,
        'message': f'Deleted {deleted} thumbnail file(s).' + (
            f' {len(errors)} error(s) — see response.' if errors else ''
        ),
    })


# ==================== Batch IMG Generation from DOC/MD ====================
#
# Bulk-render Word/Markdown source files into IMG assets, replacing any
# existing IMGs in the same slot. Powered by Word COM (DOC) and pandoc +
# Word COM (MD). Streams progress via SSE.

@admin_bp.route('/questions/batch-generate-images')
@login_required
@admin_required
def batch_generate_images():
    """
    SSE stream that renders DOC/MD source assets into PNG IMGs in bulk.

    Query params:
      * `question_ids` (comma list, required) — DB ids of Question rows.
      * `types`        (comma list, default `QUE`) — any of QUE / ANS / SOL.
      * `versions`     (comma list, default all; legacy alias `langs`) — restrict versions.
      * `sources`      (comma list, default `DOC,MD`) — source formats.
      * `stitch`       (`1`|`0`, default 1) — stitch multi-page output into
                       one tall PNG vs. one PNG per source page.
      * `overwrite`    (`1`|`0`, default 0) — replace IMG even when one
                       already exists for the slot.
      * `width`        (int) — render width in px (default from settings).
      * `transparent`  (`1`|`0`) — alpha mask (default from settings).

    Permission: subject-admin or super-admin. Each question is checked
    individually; non-admin'd questions are silently dropped.

    Streamed events:
      * `info`    — bookkeeping messages.
      * `skip`    — slot skipped (no source / IMG exists w/o overwrite).
      * `success` — slot processed successfully.
      * `error`   — render or DB failure for one slot; loop continues.
      * `done`    — final summary with stats.
    """
    if not word_com.IS_AVAILABLE:
        return jsonify({
            'error': 'Word COM unavailable on this server — batch image generation requires Windows + Microsoft Word + pywin32.'
        }), 400

    # Parse + validate query params.
    raw_qids = request.args.get('question_ids', '').strip()
    if not raw_qids:
        return jsonify({'error': 'question_ids is required'}), 400
    try:
        question_ids = [int(s) for s in raw_qids.split(',') if s.strip()]
    except ValueError:
        return jsonify({'error': 'question_ids must be a comma-separated list of integers'}), 400
    if not question_ids:
        return jsonify({'error': 'question_ids resolved to empty list'}), 400

    def _csv_set(name, default):
        raw = request.args.get(name, '').strip()
        if not raw:
            return set(default)
        return {s.strip().upper() for s in raw.split(',') if s.strip()}

    types = _csv_set('types', ['QUE']) & {'QUE', 'ANS', 'SOL'}
    # Accept the new `versions` param, falling back to the legacy `langs`.
    _ver_raw = request.args.get('versions', '').strip() or request.args.get('langs', '').strip()
    if _ver_raw:
        versions = {s.strip().upper() for s in _ver_raw.split(',') if s.strip()} & set(VERSIONS)
    else:
        versions = set(VERSIONS)
    sources = _csv_set('sources', ['DOC', 'MD']) & {'DOC', 'MD'}

    if not types:
        return jsonify({'error': 'types must include at least one of QUE / ANS / SOL'}), 400
    if not sources:
        return jsonify({'error': 'sources must include at least one of DOC / MD'}), 400

    stitch = request.args.get('stitch', '1') in ('1', 'true', 'yes')
    overwrite = request.args.get('overwrite', '0') in ('1', 'true', 'yes')
    transparent_raw = request.args.get('transparent')
    width_raw = request.args.get('width')
    symmetric_raw = request.args.get('symmetric_horizontal')

    # Permission filter: scope question_ids to those the caller can admin.
    admin_subject_ids = [s.id for s in get_user_admin_subjects()]
    qs = Question.query.filter(Question.id.in_(question_ids)).all()
    if not current_user.is_super_admin:
        qs = [q for q in qs if q.subject in admin_subject_ids]
    if not qs:
        return jsonify({
            'error': 'No questions you have admin access to in the selection.'
        }), 403

    app = current_app._get_current_object()

    def generate():
        from app import batch_image_gen

        with app.app_context():
            # Resolve defaults from Settings after entering app context so
            # any DB-overridden values are honoured.
            width = int(width_raw) if width_raw else int(
                app.config.get('BATCH_IMG_DEFAULT_WIDTH', app.config.get('DOC_THUMBNAIL_WIDTH', 1000))
            )
            transparent = (
                bool(int(transparent_raw)) if transparent_raw in ('0', '1') else
                bool(app.config.get('THUMBNAIL_TRANSPARENT', False))
            )
            symmetric = (
                bool(int(symmetric_raw)) if symmetric_raw in ('0', '1') else
                bool(app.config.get('THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP', False))
            )
            whiteness = int(app.config.get('THUMBNAIL_WHITENESS_THRESHOLD', 250))
            padding = int(app.config.get('THUMBNAIL_BOTTOM_PADDING_PX', 24))
            source_path = app.config['SOURCE_PATH']
            lock_timeout = float(app.config.get('WORD_COM_LOCK_TIMEOUT', 600))

            # Build the work list before opening Word so we know the total
            # for progress reporting up front. Each slot is a tuple
            # (question, asset_type, version).
            work = []
            for question in qs:
                for atype in types:
                    for ver in versions:
                        work.append((question, atype, ver))
            total = len(work)

            yield f"data: {json.dumps({'type': 'info', 'message': f'Scanning {len(qs)} question(s) — {total} slots to consider...'})}\n\n"

            rendered = 0
            skipped_no_src = 0
            skipped_has_img = 0
            failed = 0
            current = 0

            try:
                with word_com.word_session(lock_timeout=lock_timeout) as word_app:
                    for question, atype, ver in work:
                        current += 1
                        slot_label = f'{question.qid} / {atype} / {ver}'

                        # 1. Find a usable source.
                        src_asset = batch_image_gen.find_best_source(
                            question, atype, ver,
                            allow_doc=('DOC' in sources),
                            allow_md=('MD' in sources),
                        )
                        if src_asset is None:
                            skipped_no_src += 1
                            yield f"data: {json.dumps({'type': 'skip', 'message': f'{slot_label} — no DOC/MD source', 'current': current, 'total': total})}\n\n"
                            continue

                        # 2. Honor overwrite=0.
                        if not overwrite and batch_image_gen.slot_has_img(
                            question.id, atype, ver
                        ):
                            skipped_has_img += 1
                            yield f"data: {json.dumps({'type': 'skip', 'message': f'{slot_label} — IMG exists (overwrite off)', 'current': current, 'total': total})}\n\n"
                            continue

                        # 3. Render.
                        src_abs = os.path.join(source_path, *src_asset.file_path.split('/'))
                        if not os.path.isfile(src_abs):
                            failed += 1
                            yield f"data: {json.dumps({'type': 'error', 'message': f'{slot_label} — source file missing on disk', 'current': current, 'total': total})}\n\n"
                            continue

                        try:
                            if src_asset.file_format == 'DOC':
                                pages = batch_image_gen.render_doc_to_pages(
                                    word_app, src_abs, width, transparent,
                                    whiteness, padding, symmetric,
                                )
                            else:  # MD
                                pages = batch_image_gen.render_md_to_pages(
                                    word_app, src_abs, width, transparent,
                                    whiteness, padding, symmetric,
                                )
                        except Exception as e:
                            failed += 1
                            logger.exception('Render failed for %s', slot_label)
                            yield f"data: {json.dumps({'type': 'error', 'message': f'{slot_label} — render failed: {e}', 'current': current, 'total': total})}\n\n"
                            continue

                        if not pages:
                            failed += 1
                            yield f"data: {json.dumps({'type': 'error', 'message': f'{slot_label} — render produced 0 pages', 'current': current, 'total': total})}\n\n"
                            continue

                        # 4. Persist (delete existing IMG + write new files + DB rows).
                        try:
                            summary = batch_image_gen.replace_img_assets(
                                question, atype, ver, pages, stitch, source_path,
                            )
                        except Exception as e:
                            failed += 1
                            logger.exception('replace_img_assets failed for %s', slot_label)
                            yield f"data: {json.dumps({'type': 'error', 'message': f'{slot_label} — db/disk write failed: {e}', 'current': current, 'total': total})}\n\n"
                            continue

                        rendered += 1
                        wrote = summary['wrote']
                        deleted = summary['deleted']
                        yield (
                            f"data: {json.dumps({'type': 'success', 'message': f'{slot_label} — wrote {wrote} IMG part(s), replaced {deleted}', 'current': current, 'total': total})}\n\n"
                        )

                summary_msg = (
                    f'Done. Rendered: {rendered}, '
                    f'skipped (no source): {skipped_no_src}, '
                    f'skipped (IMG exists): {skipped_has_img}, '
                    f'failed: {failed}.'
                )
                done_event = {
                    'type': 'done',
                    'message': summary_msg,
                    'current': total,
                    'total': total,
                    'stats': {
                        'rendered': rendered,
                        'skipped_no_src': skipped_no_src,
                        'skipped_has_img': skipped_has_img,
                        'failed': failed,
                    },
                }
                yield f"data: {json.dumps(done_event)}\n\n"
            except Exception as e:
                logger.exception('Batch image generation aborted')
                yield f"data: {json.dumps({'type': 'error', 'message': f'Aborted: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message': 'Batch generation aborted.'})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@admin_bp.route('/questions/batch-mcq-ans')
@login_required
@admin_required
def batch_mcq_ans():
    """
    SSE stream: assign MCQ ANS images for selected questions from the bundled
    resources/mcq_answer_img/ pool.

    Reads ``question.answer`` (one of A / B / C / D), copies the matching PNG
    into the canonical ANS slot on disk, and upserts the QuestionAsset row.
    Questions whose ``q_type`` is not ``'MC'`` or whose ``answer`` is not a
    single letter A–D are silently skipped.

    Query params:
      * ``question_ids`` (csv, required)
      * ``versions``     (csv, default ``EN``; legacy alias ``langs``) — any of EN/CH/BI/ENO/CHO
      * ``overwrite``    (``1``|``0``, default ``0``) — replace existing IMG ANS
    """
    raw_qids = request.args.get('question_ids', '').strip()
    if not raw_qids:
        return jsonify({'error': 'question_ids is required'}), 400
    try:
        question_ids = [int(s) for s in raw_qids.split(',') if s.strip()]
    except ValueError:
        return jsonify({'error': 'question_ids must be integers'}), 400
    if not question_ids:
        return jsonify({'error': 'question_ids is empty'}), 400

    raw_versions = (request.args.get('versions', '').strip()
                    or request.args.get('langs', '').strip() or 'EN')
    versions = sorted({s.strip().upper() for s in raw_versions.split(',') if s.strip()} & set(VERSIONS))
    if not versions:
        return jsonify({'error': 'versions must include at least one of ' + ' / '.join(VERSIONS)}), 400

    overwrite = request.args.get('overwrite', '0') in ('1', 'true', 'yes')

    admin_subject_ids = [s.id for s in get_user_admin_subjects()]
    qs = Question.query.filter(Question.id.in_(question_ids)).all()
    if not current_user.is_super_admin:
        qs = [q for q in qs if q.subject in admin_subject_ids]
    if not qs:
        return jsonify({'error': 'No questions you have admin access to in the selection.'}), 403

    app = current_app._get_current_object()

    def generate():
        with app.app_context():
            source_path = app.config['SOURCE_PATH']
            # resources/mcq_answer_img/ lives one level above the app package
            resources_dir = os.path.normpath(
                os.path.join(os.path.dirname(app.root_path), 'resources', 'mcq_answer_img')
            )
            valid_answers = {'A', 'B', 'C', 'D'}
            total_slots = len(qs) * len(versions)
            done_count = 0
            n_success = 0
            n_skipped = 0
            n_error = 0

            yield (
                f"data: {json.dumps({'type': 'info', 'message': f'Processing {len(qs)} question(s) × {len(versions)} version(s) = {total_slots} slot(s). overwrite={overwrite}.'})}\n\n"
            )

            for q in qs:
                # Must be an MC question
                if q.q_type != 'MC':
                    n_skipped += len(versions)
                    done_count += len(versions)
                    yield f"data: {json.dumps({'type': 'skip', 'message': f'{q.qid}: q_type={q.q_type!r} (not MC) — skipped.', 'current': done_count, 'total': total_slots})}\n\n"
                    continue

                # answer must be A/B/C/D
                answer = (q.answer or '').strip().upper()
                if answer not in valid_answers:
                    n_skipped += len(versions)
                    done_count += len(versions)
                    yield f"data: {json.dumps({'type': 'skip', 'message': f'{q.qid}: answer={answer!r} is not A/B/C/D — skipped.', 'current': done_count, 'total': total_slots})}\n\n"
                    continue

                # Locate source image
                src_img = os.path.join(resources_dir, f'{answer}.png')
                if not os.path.isfile(src_img):
                    n_error += len(versions)
                    done_count += len(versions)
                    yield f"data: {json.dumps({'type': 'error', 'message': f'{q.qid}: {answer}.png not found in resources/mcq_answer_img/ — skipped.', 'current': done_count, 'total': total_slots})}\n\n"
                    continue

                for ver in versions:
                    done_count += 1

                    # Build canonical relative path (mirrors _build_asset_file_path)
                    filename = f'{q.qid}_{ver}_ANS.png'
                    if q.source in ('DSE', 'CE', 'AL'):
                        folder = f'{q.subject}/PP/{q.source}/{q.year}/{q.paper}'
                    else:
                        detail = _extract_qb_detail(q.qid)
                        folder = f'{q.subject}/QB/{detail}'
                    rel_path = f'{folder}/{filename}'

                    # Check for an existing IMG ANS asset at part 1
                    existing = QuestionAsset.query.filter_by(
                        question_id=q.id,
                        asset_type='ANS',
                        version=ver,
                        file_format='IMG',
                        part_number=1,
                    ).first()

                    if existing and not overwrite:
                        n_skipped += 1
                        yield f"data: {json.dumps({'type': 'skip', 'message': f'{q.qid} [{ver}]: IMG ANS already exists (overwrite=off) — skipped.', 'current': done_count, 'total': total_slots})}\n\n"
                        continue

                    # Copy image onto disk
                    try:
                        dest_full = os.path.normpath(os.path.join(source_path, *rel_path.split('/')))
                        os.makedirs(os.path.dirname(dest_full), exist_ok=True)
                        shutil.copy2(src_img, dest_full)
                    except Exception as exc:
                        n_error += 1
                        yield f"data: {json.dumps({'type': 'error', 'message': f'{q.qid} [{ver}]: file copy failed: {exc}', 'current': done_count, 'total': total_slots})}\n\n"
                        continue

                    # Upsert DB record
                    try:
                        if existing:
                            existing.file_path = rel_path
                        else:
                            db.session.add(QuestionAsset(
                                question_id=q.id,
                                asset_type='ANS',
                                version=ver,
                                file_format='IMG',
                                part_number=1,
                                file_path=rel_path,
                            ))
                        db.session.commit()
                        n_success += 1
                        yield f"data: {json.dumps({'type': 'success', 'message': f'{q.qid} [{ver}]: ANS set to {answer}.png → {rel_path}', 'current': done_count, 'total': total_slots})}\n\n"
                    except Exception as exc:
                        db.session.rollback()
                        n_error += 1
                        yield f"data: {json.dumps({'type': 'error', 'message': f'{q.qid} [{ver}]: DB update failed: {exc}', 'current': done_count, 'total': total_slots})}\n\n"

            yield (
                f"data: {json.dumps({'type': 'done', 'message': f'Done. {n_success} set, {n_skipped} skipped, {n_error} error(s).', 'success': n_success, 'skipped': n_skipped, 'errors': n_error, 'current': total_slots, 'total': total_slots})}\n\n"
            )

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ==================== AI Tools (LLM image checking / MD generation) ====================
#
# Batch operations that call a configured LLM endpoint per question slot and
# stream a live SSE log. Mirrors the batch_generate_images pattern: gather
# work in request context, then stream inside a pushed app context. A
# job_id-based cancel flag (app/ai_tools._AI_CANCEL) allows a real
# server-side Stop. Subject-admins may run it on their own subjects.

def _ai_tools_guard():
    """Shared pre-flight for AI Tools routes. Returns (error_response, code)
    on failure, else None."""
    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return jsonify({'error': 'AI Tools are disabled (see System Settings).'}), 400
    return None


def _ai_parse_qs():
    """Parse + permission-scope question_ids. Returns (qs, error, code)."""
    raw_qids = request.args.get('question_ids', '').strip()
    if not raw_qids:
        return None, jsonify({'error': 'question_ids is required'}), 400
    try:
        question_ids = [int(s) for s in raw_qids.split(',') if s.strip()]
    except ValueError:
        return None, jsonify({'error': 'question_ids must be integers'}), 400
    if not question_ids:
        return None, jsonify({'error': 'question_ids resolved to empty list'}), 400

    admin_subject_ids = [s.id for s in get_user_admin_subjects()]
    qs = Question.query.filter(Question.id.in_(question_ids)).all()
    if not current_user.is_super_admin:
        qs = [q for q in qs if q.subject in admin_subject_ids]
    if not qs:
        return None, jsonify({'error': 'No questions you have admin access to in the selection.'}), 403
    return qs, None, None


def _ai_load_endpoint():
    """Load + validate the requested LLM endpoint. Returns (cfg, error, code)."""
    from app.models import LLMConfig
    raw = request.args.get('endpoint_id', '').strip()
    if not raw:
        return None, jsonify({'error': 'endpoint_id is required'}), 400
    try:
        cfg = LLMConfig.query.get(int(raw))
    except ValueError:
        return None, jsonify({'error': 'endpoint_id must be an integer'}), 400
    if not cfg or not cfg.enabled:
        return None, jsonify({'error': 'Endpoint not found or disabled'}), 404
    if not cfg.supports_vision:
        return None, jsonify({'error': f'Endpoint "{cfg.name}" is not vision-capable; image operations require a vision model.'}), 400
    return cfg, None, None


def _ai_csv_versions(name):
    raw = request.args.get(name, '').strip().upper()
    return raw if raw in set(VERSIONS) else None


def _ai_atypes():
    raw = request.args.get('atypes', '').strip()
    if not raw:
        return {'QUE'}
    return {s.strip().upper() for s in raw.split(',') if s.strip()} & {'QUE', 'ANS', 'SOL'}


@admin_bp.route('/questions/ai/endpoints')
@login_required
@admin_required
def ai_endpoints():
    """List enabled LLM endpoints for the AI Tools modal dropdown (admins)."""
    from app.models import LLMConfig
    rows = (LLMConfig.query.filter_by(enabled=True)
            .order_by(LLMConfig.sort_order, LLMConfig.name).all())
    return jsonify({'endpoints': [
        {'id': c.id, 'name': c.name, 'model_name': c.model_name,
         'supports_vision': bool(c.supports_vision)}
        for c in rows
    ]})


@admin_bp.route('/questions/ai/cancel', methods=['POST'])
@login_required
@admin_required
def ai_cancel():
    """Signal a running AI Tools job to stop."""
    from app import ai_tools
    data = request.get_json(silent=True) or {}
    job_id = (data.get('job_id') or '').strip()
    if not job_id:
        return jsonify({'error': 'job_id is required'}), 400
    known = ai_tools.cancel_job(job_id)
    return jsonify({'success': True, 'known': known})


def _ai_stream(work_iter_factory):
    """Wrap an ai_tools generator factory into an SSE Response: emit a
    `job` event with the job_id, stream the events, and clean up the job."""
    from app import ai_tools
    app = current_app._get_current_object()
    job_id, cancel = ai_tools.new_job()

    def generate():
        with app.app_context():
            yield f"data: {json.dumps({'type': 'job', 'job_id': job_id})}\n\n"
            try:
                for ev in work_iter_factory(app, cancel):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as e:  # pragma: no cover
                current_app.logger.exception('AI Tools stream aborted')
                yield f"data: {json.dumps({'type': 'error', 'message': f'Aborted: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'message': 'Aborted.'})}\n\n"
            finally:
                ai_tools.finish_job(job_id)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@admin_bp.route('/questions/ai/check')
@login_required
@admin_required
def ai_check():
    """SSE: proofread typed-version images against an official reference.

    Query params: question_ids (csv), endpoint_id, typed_version, ref_version,
    atypes (csv of QUE/ANS/SOL), recheck (1/0).
    """
    guard = _ai_tools_guard()
    if guard:
        return guard
    qs, err, code = _ai_parse_qs()
    if err:
        return err, code
    cfg, err, code = _ai_load_endpoint()
    if err:
        return err, code

    typed_version = _ai_csv_versions('typed_version')
    ref_version = _ai_csv_versions('ref_version')
    if not typed_version or not ref_version:
        return jsonify({'error': 'typed_version and ref_version must each be one of ' + '/'.join(VERSIONS)}), 400
    if typed_version == ref_version:
        return jsonify({'error': 'typed_version and ref_version must differ'}), 400
    atypes = _ai_atypes()
    if not atypes:
        return jsonify({'error': 'atypes must include at least one of QUE/ANS/SOL'}), 400
    recheck = request.args.get('recheck', '0') in ('1', 'true', 'yes')

    def factory(app, cancel):
        from app import ai_tools
        image_max_dim = int(app.config.get('LLM_IMAGE_MAX_DIM', 1600))
        source_path = app.config['SOURCE_PATH']
        from app.models import LLMConfig
        live_cfg = LLMConfig.query.get(cfg.id)
        return ai_tools.iter_check(qs, typed_version, ref_version, atypes,
                                   recheck, live_cfg, image_max_dim, source_path, cancel)

    return _ai_stream(factory)


@admin_bp.route('/questions/ai/generate-md')
@login_required
@admin_required
def ai_generate_md():
    """SSE: transcribe source-version images into Markdown assets.

    Query params: question_ids (csv), endpoint_id, source_version,
    target_version, atypes (csv), overwrite (1/0), embed_image (1/0).
    """
    guard = _ai_tools_guard()
    if guard:
        return guard
    qs, err, code = _ai_parse_qs()
    if err:
        return err, code
    cfg, err, code = _ai_load_endpoint()
    if err:
        return err, code

    source_version = _ai_csv_versions('source_version')
    target_version = _ai_csv_versions('target_version')
    if not source_version or not target_version:
        return jsonify({'error': 'source_version and target_version must each be one of ' + '/'.join(VERSIONS)}), 400
    atypes = _ai_atypes()
    if not atypes:
        return jsonify({'error': 'atypes must include at least one of QUE/ANS/SOL'}), 400
    overwrite = request.args.get('overwrite', '0') in ('1', 'true', 'yes')
    embed_image = request.args.get('embed_image', '1') in ('1', 'true', 'yes')

    def factory(app, cancel):
        from app import ai_tools
        image_max_dim = int(app.config.get('LLM_IMAGE_MAX_DIM', 1600))
        md_max_bytes = int(app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024))
        source_path = app.config['SOURCE_PATH']
        from app.models import LLMConfig
        live_cfg = LLMConfig.query.get(cfg.id)
        return ai_tools.iter_generate_md(qs, source_version, target_version, atypes,
                                         overwrite, embed_image, live_cfg, image_max_dim,
                                         md_max_bytes, source_path, cancel)

    return _ai_stream(factory)


@admin_bp.route('/questions/<int:question_id>/assets/ai/generate-md', methods=['POST'])
@login_required
@admin_required
def ai_generate_md_slot(question_id):
    """Synchronously generate one Markdown asset for a single
    (question, asset_type, version) slot from that version's images — drives
    the per-slot "Generate with AI" button in the edit-question modal.

    Body JSON: {version, asset_type, endpoint_id, embed_image?, overwrite?}.
    Source and target version are the same (the slot's own version).
    """
    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return jsonify({'error': 'AI Tools are disabled (see System Settings).'}), 400

    question = Question.query.get_or_404(question_id)
    if not current_user.is_super_admin:
        admin_subject_ids = [s.id for s in get_user_admin_subjects()]
        if question.subject not in admin_subject_ids:
            return jsonify({'error': 'You do not have admin access to this subject.'}), 403

    data = request.get_json(silent=True) or {}
    version = (data.get('version') or '').strip().upper()
    asset_type = (data.get('asset_type') or '').strip().upper()
    if version not in set(VERSIONS):
        return jsonify({'error': 'version must be one of ' + '/'.join(VERSIONS)}), 400
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        return jsonify({'error': 'asset_type must be QUE / ANS / SOL'}), 400

    from app.models import LLMConfig
    try:
        cfg = LLMConfig.query.get(int(data.get('endpoint_id')))
    except (TypeError, ValueError):
        return jsonify({'error': 'endpoint_id is required'}), 400
    if not cfg or not cfg.enabled:
        return jsonify({'error': 'Endpoint not found or disabled'}), 404
    if not cfg.supports_vision:
        return jsonify({'error': f'Endpoint "{cfg.name}" is not vision-capable.'}), 400

    embed_image = bool(data.get('embed_image', True))
    overwrite = bool(data.get('overwrite', False))

    from app import ai_tools
    res = ai_tools.generate_md_slot(
        question, asset_type, version, version,
        embed_image=embed_image, overwrite=overwrite, config=cfg,
        image_max_dim=int(current_app.config.get('LLM_IMAGE_MAX_DIM', 1600)),
        md_max_bytes=int(current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024)),
        source_path=current_app.config['SOURCE_PATH'],
    )
    status = res.get('status')
    http = 200 if status in ('created', 'updated') else (409 if status == 'skip' else 502)
    return jsonify({
        'success': status in ('created', 'updated'),
        'status': status,
        'message': res.get('message', ''),
        'asset_id': res.get('asset_id'),
    }), http


# ==================== System Settings (Super Admin Only) ====================
#
# DB-backed runtime tunables. The full registry lives in `app/settings.py`;
# these routes are the thin HTTP surface around it. All write paths require
# super-admin because settings affect global behaviour.

@admin_bp.route('/settings')
@login_required
@super_admin_required
def settings_page():
    """Render the system settings admin page."""
    return render_template('admin_settings.html')


@admin_bp.route('/settings/data')
@login_required
@super_admin_required
def settings_data():
    """Return the full registry + current values as JSON for the UI."""
    from app import settings as system_settings
    return jsonify(system_settings.as_dict())


@admin_bp.route('/settings/save', methods=['POST'])
@login_required
@super_admin_required
def settings_save():
    """
    Accept a JSON body `{key: value, ...}` and apply each entry. Per-key
    validation errors are reported in the response (200 OK either way) so
    a partial save can complete even if one field is bad.

    Response shape:
        {
          'saved':   ['KEY_A', ...],
          'errors':  {'KEY_B': 'must be >= 1', ...},
          'values':  {'KEY_A': <parsed value>, ...},
        }
    """
    from app import settings as system_settings

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'JSON object body required'}), 400

    saved = []
    errors = {}
    values = {}
    for key, raw in payload.items():
        if key not in system_settings.REGISTRY:
            errors[key] = 'unknown setting key'
            continue
        try:
            parsed = system_settings.set_value(key, raw, user_id=current_user.id)
            saved.append(key)
            values[key] = parsed
        except (ValueError, KeyError) as e:
            errors[key] = str(e)
        except Exception as e:  # pragma: no cover — surface DB errors gracefully
            errors[key] = f'unexpected error: {e}'

    return jsonify({'saved': saved, 'errors': errors, 'values': values})


@admin_bp.route('/settings/reset/<key>', methods=['POST'])
@login_required
@super_admin_required
def settings_reset(key):
    """Drop the DB override for `key` and restore the bootstrap default.

    Returns the restored value so the UI can update its display without a
    refresh.
    """
    from app import settings as system_settings

    if key not in system_settings.REGISTRY:
        return jsonify({'error': 'Unknown setting key'}), 404

    try:
        default = system_settings.reset(key, user_id=current_user.id)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'key': key, 'value': default, 'has_override': False})


# ==================== LLM Endpoints (AI Tools, Super Admin Only) ====================
#
# CRUD for the named OpenAI-compatible endpoints used by the AI Tools
# feature. Plaintext API keys are NEVER returned to the browser — the data
# endpoint reports only whether a key is stored / falls back to .env.

def _serialize_llm_config(c, *, include_secret=False):
    """JSON-friendly LLMConfig. The API key is masked unless explicitly
    requested (never exposed via HTTP)."""
    from app.llm_client import resolve_api_key
    has_stored = bool(c.api_key_enc)
    # Whether a usable key resolves at all (stored or via .env fallback).
    try:
        resolves = bool(resolve_api_key(c))
    except Exception:
        resolves = has_stored
    data = {
        'id': c.id,
        'name': c.name,
        'base_url': c.base_url,
        'model_name': c.model_name,
        'provider': c.provider,
        'api_key_env': c.api_key_env or '',
        'has_stored_key': has_stored,
        'key_resolves': resolves,
        'supports_vision': bool(c.supports_vision),
        'max_output_tokens': c.max_output_tokens,
        'temperature': c.temperature,
        'timeout_seconds': c.timeout_seconds,
        'enabled': bool(c.enabled),
        'sort_order': c.sort_order,
    }
    return data


@admin_bp.route('/llm-endpoints')
@login_required
@super_admin_required
def llm_endpoints_page():
    """Render the LLM endpoint management page (AI Tools config)."""
    return render_template('admin_llm_endpoints.html')


@admin_bp.route('/llm-endpoints/data')
@login_required
@super_admin_required
def llm_endpoints_data():
    """List all configured endpoints (keys masked)."""
    from app.models import LLMConfig
    rows = LLMConfig.query.order_by(LLMConfig.sort_order, LLMConfig.name).all()
    return jsonify({'endpoints': [_serialize_llm_config(c) for c in rows]})


@admin_bp.route('/llm-endpoints/save', methods=['POST'])
@login_required
@super_admin_required
def llm_endpoints_save():
    """Create or update an endpoint. Body JSON includes an optional
    `api_key` (blank = keep existing; `clear_key:true` = remove stored key,
    fall back to .env)."""
    from app.models import LLMConfig
    from app.llm_client import encrypt_key

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    base_url = (data.get('base_url') or '').strip()
    model_name = (data.get('model_name') or '').strip()
    if not name or not base_url or not model_name:
        return jsonify({'error': 'name, base_url and model_name are required'}), 400

    cid = data.get('id')
    if cid:
        cfg = LLMConfig.query.get(cid)
        if not cfg:
            return jsonify({'error': 'Endpoint not found'}), 404
    else:
        cfg = None

    # Unique-name guard. Run this BEFORE adding any new row to the session so
    # the query's autoflush doesn't try to INSERT a half-built (name=NULL) row.
    clash = LLMConfig.query.filter(LLMConfig.name == name)
    if cid:
        clash = clash.filter(LLMConfig.id != cid)
    if clash.first():
        return jsonify({'error': f'An endpoint named "{name}" already exists'}), 409

    if cfg is None:
        cfg = LLMConfig()
        db.session.add(cfg)

    cfg.name = name
    cfg.base_url = base_url
    cfg.model_name = model_name
    cfg.provider = (data.get('provider') or 'openai').strip() or 'openai'
    cfg.api_key_env = (data.get('api_key_env') or '').strip() or None
    cfg.supports_vision = bool(data.get('supports_vision', True))

    def _int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _float(v, default):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    cfg.max_output_tokens = max(1, _int(data.get('max_output_tokens'), 4096))
    cfg.temperature = _float(data.get('temperature'), 0.0)
    cfg.timeout_seconds = max(5, _int(data.get('timeout_seconds'), 120))
    cfg.enabled = bool(data.get('enabled', True))
    cfg.sort_order = _int(data.get('sort_order'), 0)

    # Key handling: explicit clear, or set-if-provided, else keep existing.
    if data.get('clear_key'):
        cfg.api_key_enc = None
    else:
        new_key = (data.get('api_key') or '').strip()
        if new_key:
            cfg.api_key_enc = encrypt_key(new_key)

    db.session.commit()
    return jsonify({'success': True, 'endpoint': _serialize_llm_config(cfg)})


@admin_bp.route('/llm-endpoints/<int:cid>/delete', methods=['POST'])
@login_required
@super_admin_required
def llm_endpoints_delete(cid):
    from app.models import LLMConfig
    cfg = LLMConfig.query.get_or_404(cid)
    db.session.delete(cfg)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/llm-endpoints/<int:cid>/duplicate', methods=['POST'])
@login_required
@super_admin_required
def llm_endpoints_duplicate(cid):
    """Create a copy of an endpoint with a unique auto-generated name.
    The API key is NOT copied — the duplicate starts without a stored key
    and falls back to the .env variable just like a freshly created endpoint.
    """
    from app.models import LLMConfig
    src = LLMConfig.query.get_or_404(cid)

    # Build a unique name: "Copy of <name>", "Copy 2 of <name>", …
    base_name = f'Copy of {src.name}'
    candidate = base_name
    counter = 2
    while LLMConfig.query.filter_by(name=candidate).first():
        candidate = f'Copy {counter} of {src.name}'
        counter += 1

    copy = LLMConfig(
        name=candidate,
        base_url=src.base_url,
        model_name=src.model_name,
        provider=src.provider,
        api_key_env=src.api_key_env,
        supports_vision=src.supports_vision,
        max_output_tokens=src.max_output_tokens,
        temperature=src.temperature,
        timeout_seconds=src.timeout_seconds,
        enabled=src.enabled,
        sort_order=src.sort_order,
    )
    db.session.add(copy)
    db.session.commit()
    return jsonify({'success': True, 'endpoint': _serialize_llm_config(copy)})


@admin_bp.route('/llm-endpoints/<int:cid>/test', methods=['POST'])
@login_required
@super_admin_required
def llm_endpoints_test(cid):
    """Send a trivial prompt to validate connectivity / auth / model."""
    from app.models import LLMConfig
    from app.llm_client import test_endpoint
    cfg = LLMConfig.query.get_or_404(cid)
    ok, message = test_endpoint(cfg)
    return jsonify({'success': ok, 'message': message})


@admin_bp.route('/llm-endpoints/<int:cid>/chat', methods=['POST'])
@login_required
@super_admin_required
def llm_endpoints_chat(cid):
    """Direct chat with an LLM endpoint — NO system prompt, NO injected
    context, NO guardrails. Powers the "Chat" console on the LLM Endpoints
    management page so super-admins can probe a model in its raw form to
    verify behaviour, debug formatting, or check reasoning quality.

    Body JSON: ``{turns: [{role, content}, ...]}`` — the full conversation.
    The server passes it straight through to ``llm_client.chat_messages_stream``
    with no other messages prepended.

    Response is ``text/event-stream`` (SSE). Same event shape as the dashboard
    Explain endpoint: ``preamble``, ``delta``, ``done``, ``error``. Streaming
    keeps the response flowing through reverse proxies (no idle timeout) and
    lets the user watch the model think in real time.
    """
    from app.models import LLMConfig
    from app import llm_client, md_render, ai_prompts

    cfg = LLMConfig.query.get_or_404(cid)
    if not cfg.enabled:
        return jsonify({'error': 'This endpoint is disabled.'}), 400

    data = request.get_json(silent=True) or {}
    raw_turns = data.get('turns') or []

    messages = []
    for t in raw_turns[-40:]:
        if not isinstance(t, dict):
            continue
        role = t.get('role')
        content = t.get('content')
        if role in ('user', 'assistant', 'system') and isinstance(content, str) and content.strip():
            messages.append({'role': role, 'content': content[:16000]})

    if not messages:
        return jsonify({'error': 'No messages to send.'}), 400

    chat_timeout = int(current_app.config.get('LLM_CHAT_TIMEOUT_SECONDS') or 0) or None
    app = current_app._get_current_object()
    cfg_id = cfg.id
    cfg_name = cfg.name
    cfg_model = cfg.model_name

    def generate():
        with app.app_context():
            yield ': stream-start\n\n'
            yield 'data: ' + json.dumps({'type': 'preamble'}) + '\n\n'

            full_text_parts: list[str] = []
            full_reasoning_parts: list[str] = []
            final_finish_reason = None

            try:
                # Re-fetch the config inside the app context the generator owns
                # so the SQLAlchemy session is local to this thread.
                cfg_local = LLMConfig.query.get(cfg_id)
                if cfg_local is None:
                    yield 'data: ' + json.dumps({
                        'type': 'error',
                        'message': 'Endpoint was deleted.',
                    }) + '\n\n'
                    return

                for evt in llm_client.chat_messages_stream(
                        cfg_local, messages, timeout=chat_timeout):
                    etype = evt.get('type')
                    if etype == 'delta':
                        c = evt.get('content') or ''
                        r = evt.get('reasoning') or ''
                        if c:
                            full_text_parts.append(c)
                        if r:
                            full_reasoning_parts.append(r)
                        out = {'type': 'delta'}
                        if c:
                            out['content'] = c
                        if r:
                            out['reasoning'] = r
                        yield 'data: ' + json.dumps(out) + '\n\n'
                    elif etype == 'done':
                        final_finish_reason = evt.get('finish_reason')
                        if not full_text_parts and evt.get('text'):
                            full_text_parts.append(evt['text'])
                        if not full_reasoning_parts and evt.get('reasoning'):
                            full_reasoning_parts.append(evt['reasoning'])
            except llm_client.LLMError as e:
                yield 'data: ' + json.dumps({
                    'type': 'error',
                    'message': f'LLM error: {e}',
                }) + '\n\n'
                return
            except Exception as e:
                app.logger.exception('Unhandled error streaming chat for cid=%s', cfg_id)
                yield 'data: ' + json.dumps({
                    'type': 'error',
                    'message': f'Server error: {type(e).__name__}: {e}',
                }) + '\n\n'
                return

            full_text = ''.join(full_text_parts)
            full_reasoning = ''.join(full_reasoning_parts)
            reply = full_text.strip() or full_reasoning.strip()
            if not reply:
                yield 'data: ' + json.dumps({
                    'type': 'error',
                    'message': f'The model returned an empty reply '
                               f'(finish_reason={final_finish_reason}).',
                }) + '\n\n'
                return

            try:
                reply_norm = ai_prompts.normalize_inline_math(reply)
                reply_html = md_render.render_text(reply_norm)
            except Exception as e:
                app.logger.exception('Failed to render chat reply for cid=%s', cfg_id)
                yield 'data: ' + json.dumps({
                    'type': 'error',
                    'message': f'Failed to render reply: {type(e).__name__}: {e}',
                }) + '\n\n'
                return

            yield 'data: ' + json.dumps({
                'type': 'done',
                'reply': reply_norm,
                'reply_html': reply_html,
                'model': cfg_model,
                'endpoint': cfg_name,
            }) + '\n\n'

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })


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


def _unique_copy_name(dest_dir, name):
    """Return a filename that does not yet exist inside dest_dir.

    Tries: <name>, <stem>_copy<ext>, <stem>_copy2<ext>, … until a free slot is found.
    Works for both files (with extensions) and directories (no extension).
    """
    if not os.path.exists(os.path.join(dest_dir, name)):
        return name
    base, ext = os.path.splitext(name)
    candidate = f'{base}_copy{ext}'
    if not os.path.exists(os.path.join(dest_dir, candidate)):
        return candidate
    n = 2
    while True:
        candidate = f'{base}_copy{n}{ext}'
        if not os.path.exists(os.path.join(dest_dir, candidate)):
            return candidate
        n += 1


@admin_bp.route('/files/copy', methods=['POST'])
@login_required
@super_admin_required
def files_copy():
    """Copy one or more files/directories into a destination directory."""
    source_path = _resolve_source_path()
    data = request.get_json()
    sources = data.get('sources', [])
    dest_dir = data.get('dest_dir', '').strip('/')

    if not sources:
        return jsonify({'error': 'No sources specified'}), 400

    dest_full = _safe_join(source_path, dest_dir) if dest_dir else source_path
    if not dest_full or not os.path.isdir(dest_full):
        return jsonify({'error': 'Destination directory not found or access denied'}), 404

    copied = []
    errors = []
    for rel_path in sources:
        rel_path = rel_path.strip('/')
        if not rel_path:
            errors.append('Cannot copy root directory')
            continue

        src_full = _safe_join(source_path, rel_path)
        if not src_full or not os.path.exists(src_full):
            errors.append(f'{rel_path}: not found')
            continue

        # Prevent copying a directory into itself or a subdirectory of itself
        if os.path.isdir(src_full):
            src_abs = os.path.normcase(os.path.abspath(src_full))
            dest_abs = os.path.normcase(os.path.abspath(dest_full))
            if dest_abs == src_abs or dest_abs.startswith(src_abs + os.sep):
                errors.append(f'{rel_path}: cannot copy a folder into itself')
                continue

        dest_name = _unique_copy_name(dest_full, os.path.basename(src_full))
        dest_item = os.path.join(dest_full, dest_name)

        try:
            if os.path.isdir(src_full):
                shutil.copytree(src_full, dest_item)
            else:
                shutil.copy2(src_full, dest_item)
            copied.append({'original': rel_path, 'new_name': dest_name})
        except Exception as e:
            errors.append(f'{rel_path}: {str(e)}')

    return jsonify({
        'success': True,
        'copied': copied,
        'errors': errors,
        'message': f'Copied {len(copied)} item(s)' + (f', {len(errors)} error(s)' if errors else ''),
    })

"""
Dashboard routes for question browsing and filtering
"""
from flask import Blueprint, render_template, request, jsonify, session, current_app, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_, and_
from app import db
from app.models import Question, QuestionAsset, Topic, Subtopic, Subject
from app.utils import natural_sort
import os

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.route('/')
@login_required
def index():
    """Main dashboard page"""
    subjects = Subject.query.all()
    return render_template('dashboard.html', subjects=subjects)

@dashboard_bp.route('/filter', methods=['POST', 'GET'])
@login_required
def filter_questions():
    """Filter questions based on criteria"""
    
    # Get filter parameters
    subject = request.args.get('subject') or request.form.get('subject')
    source_type = request.args.get('source_type') or request.form.get('source_type')
    years = request.args.getlist('years') or request.form.getlist('years')
    section = request.args.get('section') or request.form.get('section')
    topics = request.args.getlist('topics') or request.form.getlist('topics')
    subtopics = request.args.getlist('subtopics') or request.form.getlist('subtopics')
    is_crosstopic = request.args.get('is_crosstopic') or request.form.get('is_crosstopic')
    levels = request.args.getlist('levels') or request.form.getlist('levels')
    q_type = request.args.get('q_type') or request.form.get('q_type')
    page = int(request.args.get('page', 1))
    
    # Store in session for pagination
    session['filter_params'] = {
        'subject': subject,
        'source_type': source_type,
        'years': years,
        'section': section,
        'topics': topics,
        'subtopics': subtopics,
        'is_crosstopic': is_crosstopic,
        'levels': levels,
        'q_type': q_type
    }
    
    # Build query
    query = Question.query
    
    # Filter by subject
    if subject:
        query = query.filter(Question.subject == subject)
    
    # Filter by source type
    if source_type:
        if source_type in ['DSE', 'CE', 'AL']:
            query = query.filter(Question.source == source_type)
        elif source_type == 'QB':
            query = query.filter(Question.source == 'QB')
    
    # Filter by years (for PP only)
    if years:
        year_ints = [int(y) for y in years if y.isdigit()]
        if year_ints:
            query = query.filter(Question.year.in_(year_ints))
    
    # Filter by section
    if section and section != 'all':
        query = query.filter(Question.section == section)
    
    # Filter by topics
    if topics:
        topic_ids = [int(t) for t in topics if t.isdigit()]
        if topic_ids:
            if is_crosstopic:
                # Include questions with selected topics as major OR minor
                query = query.filter(
                    or_(
                        Question.major_topic_id.in_(topic_ids),
                        Question.minor_topics.any(Topic.id.in_(topic_ids))
                    )
                )
            else:
                # Only major topic
                query = query.filter(Question.major_topic_id.in_(topic_ids))
    
    # Filter by subtopics
    if subtopics:
        subtopic_ids = [int(s) for s in subtopics if s.isdigit()]
        if subtopic_ids:
            query = query.filter(Question.subtopics.any(Subtopic.id.in_(subtopic_ids)))
    
    # Filter by levels
    if levels:
        level_ints = [int(l) for l in levels if l.isdigit()]
        if level_ints:
            query = query.filter(Question.level.in_(level_ints))
    
    # Filter by question type
    if q_type and q_type != 'all':
        query = query.filter(Question.q_type == q_type)
    
    # Get all matching questions for natural sorting
    all_questions = query.all()
    
    # Natural sort by qid
    sorted_questions = natural_sort(all_questions, key_func=lambda q: q.qid)
    
    # Paginate
    per_page = current_app.config['QUESTIONS_PER_PAGE']
    total = len(sorted_questions)
    start = (page - 1) * per_page
    end = start + per_page
    questions = sorted_questions[start:end]
    
    total_pages = (total + per_page - 1) // per_page
    
    # Prepare question data with assets
    question_data = []
    for q in questions:
        # Get QUE asset (prefer EN image)
        que_asset = QuestionAsset.query.filter_by(
            question_id=q.id,
            asset_type='QUE'
        ).filter(
            QuestionAsset.language.in_(['EN', 'CH', 'BI'])
        ).order_by(
            QuestionAsset.language.desc()  # EN > CH > BI
        ).first()
        
        # Check for ANS and SOL
        has_ans = QuestionAsset.query.filter_by(
            question_id=q.id,
            asset_type='ANS'
        ).first() is not None
        
        has_sol = QuestionAsset.query.filter_by(
            question_id=q.id,
            asset_type='SOL'
        ).first() is not None
        
        question_data.append({
            'id': q.id,
            'qid': q.qid,
            'source': q.source,
            'year': q.year,
            'paper': q.paper,
            'section': q.section,
            'qno': q.qno,
            'level': q.level,
            'q_type': q.q_type,
            'subject': q.subject,
            'major_topic': q.major_topic.name if q.major_topic else 'N/A',
            'major_topic_id': q.major_topic_id,
            'minor_topic_ids': [t.id for t in q.minor_topics],
            'subtopic_ids': [s.id for s in q.subtopics],
            'description': q.description,
            'que_asset_id': que_asset.id if que_asset else None,
            'has_ans': has_ans,
            'has_sol': has_sol
        })
    
    # Get all question IDs for selection purposes
    all_question_ids = [q.id for q in sorted_questions]
    
    # If AJAX request, return JSON
    if request.headers.get('HX-Request'):
        return render_template('partials/question_list.html', 
                             questions=question_data,
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             all_question_ids=all_question_ids)
    
    # Otherwise return full page
    subjects = Subject.query.all()
    return render_template('dashboard.html', 
                         subjects=subjects,
                         questions=question_data,
                         page=page,
                         total_pages=total_pages,
                         total=total,
                         all_question_ids=all_question_ids)

@dashboard_bp.route('/api/topics/<subject_id>')
@login_required
def get_topics(subject_id):
    """Get topics for a subject"""
    topics = Topic.query.filter_by(subject_id=subject_id).all()
    return jsonify([{'id': t.id, 'name': t.name} for t in topics])

@dashboard_bp.route('/api/subtopics')
@login_required
def get_subtopics():
    """Get subtopics for selected topics"""
    topic_ids = request.args.get('topic_ids', '').split(',')
    topic_ids = [int(tid) for tid in topic_ids if tid.isdigit()]
    
    if not topic_ids:
        return jsonify([])
    
    subtopics = Subtopic.query.filter(Subtopic.topic_id.in_(topic_ids)).all()
    return jsonify([{'id': s.id, 'name': s.name, 'topic_id': s.topic_id} for s in subtopics])

@dashboard_bp.route('/api/years/<subject_id>/<source>')
@login_required
def get_years(subject_id, source):
    """Get available years for a subject and source"""
    years = db.session.query(Question.year)\
        .filter(Question.subject == subject_id)\
        .filter(Question.source == source)\
        .filter(Question.year.isnot(None))\
        .distinct()\
        .order_by(Question.year.desc())\
        .all()
    
    return jsonify([y[0] for y in years])

@dashboard_bp.route('/files/<path:filepath>')
@login_required
def serve_file(filepath):
    """Serve question asset files"""
    source_path = current_app.config['SOURCE_PATH']
    full_path = os.path.join(source_path, filepath)
    
    if not os.path.exists(full_path):
        return "File not found", 404
    
    return send_file(full_path)

@dashboard_bp.route('/api/asset/<int:asset_id>')
@login_required
def get_asset(asset_id):
    """Get asset information"""
    asset = QuestionAsset.query.get_or_404(asset_id)
    source_path = current_app.config['SOURCE_PATH']
    file_url = f"/dashboard/files/{asset.file_path}"
    
    return jsonify({
        'id': asset.id,
        'type': asset.asset_type,
        'format': asset.file_format,
        'language': asset.language,
        'url': file_url
    })

@dashboard_bp.route('/api/asset_preview/<int:asset_id>')
@login_required
def get_asset_preview(asset_id):
    """Get asset file for preview"""
    asset = QuestionAsset.query.get_or_404(asset_id)
    source_path = current_app.config['SOURCE_PATH']
    full_path = os.path.join(source_path, asset.file_path)
    
    if not os.path.exists(full_path):
        return "File not found", 404
    
    return send_file(full_path)

@dashboard_bp.route('/api/question/<int:question_id>/assets/<asset_type>')
@login_required
def get_question_asset(question_id, asset_type):
    """Get specific asset type for a question"""
    asset = QuestionAsset.query.filter_by(
        question_id=question_id,
        asset_type=asset_type
    ).order_by(
        QuestionAsset.language.desc()  # Prefer EN
    ).first()
    
    if not asset:
        return jsonify({'error': 'Asset not found'}), 404
    
    file_url = f"/dashboard/files/{asset.file_path}"
    
    return jsonify({
        'id': asset.id,
        'type': asset.asset_type,
        'format': asset.file_format,
        'language': asset.language,
        'url': file_url
    })

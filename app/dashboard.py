"""
Dashboard routes for question browsing and filtering
"""
from flask import Blueprint, render_template, request, jsonify, session, current_app, send_file, abort
from flask_login import login_required, current_user
from sqlalchemy import or_, and_, case
from app import db
from app.models import Question, QuestionAsset, Topic, Subtopic, Subject, Chapter, Subchapter
from app.utils import natural_sort, apply_multi_sort, get_user_accessible_subjects
from app import md_render
import os
import re
import json

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.route('/')
@login_required
def index():
    """Main dashboard page"""
    # Filter subjects based on user's access permissions
    subjects = get_user_accessible_subjects()
    
    if not subjects:
        # User has no subject access
        return render_template('dashboard.html', subjects=[], sort_config=[], no_access=True)
    
    # Get sort config from session or use default
    sort_config = session.get('sort_config', [{"field": "qid", "direction": "asc"}])
    subject_roles = current_user.get_subject_roles()
    return render_template('dashboard.html', subjects=subjects, sort_config=sort_config,
                         subject_roles=subject_roles)

@dashboard_bp.route('/filter', methods=['POST', 'GET'])
@login_required
def filter_questions():
    """Filter questions based on criteria"""
    
    # Get filter parameters
    subject = request.args.get('subject') or request.form.get('subject')
    
    # Check subject access permission
    if subject and not current_user.has_subject_access(subject):
        return jsonify({'error': 'Access denied to this subject'}), 403
    source_type = request.args.get('source_type') or request.form.get('source_type')
    years = request.args.getlist('years') or request.form.getlist('years')
    section = request.args.get('section') or request.form.get('section')
    topics = request.args.getlist('topics') or request.form.getlist('topics')
    topic_mode = request.args.get('topic_mode') or request.form.get('topic_mode') or 'OR'  # AND or OR
    subtopics = request.args.getlist('subtopics') or request.form.getlist('subtopics')
    subtopic_mode = request.args.get('subtopic_mode') or request.form.get('subtopic_mode') or 'OR'  # AND or OR
    is_crosstopic = request.args.get('is_crosstopic') or request.form.get('is_crosstopic')
    is_crosssubtopic = request.args.get('is_crosssubtopic') or request.form.get('is_crosssubtopic')
    chapters = request.args.getlist('chapters') or request.form.getlist('chapters')
    subchapters = request.args.getlist('subchapters') or request.form.getlist('subchapters')
    levels = request.args.getlist('levels') or request.form.getlist('levels')
    q_type = request.args.get('q_type') or request.form.get('q_type')
    qid_search = request.args.get('qid_search') or request.form.get('qid_search')  # Direct QID search
    qid_strict = (request.args.get('qid_strict') or request.form.get('qid_strict')) in ('on', 'true', '1', True)
    # Explicit QID list filter (comma-separated). When provided, overrides all other filters.
    # Used by the Admin → DB Health anomaly view to jump to a specific set of questions.
    qids_raw = request.args.get('qids') or request.form.get('qids')
    qid_list = [q.strip() for q in qids_raw.split(',') if q.strip()] if qids_raw else []
    # Explicit DB id list filter (comma-separated integer Question.id). When provided,
    # overrides all other filters. Used by the dashboard "Show Selected Only" feature
    # so the user can paginate through their full selection regardless of any topic /
    # level / subject filters configured in the sidebar. Access is still scoped to the
    # subjects the user has permission to view.
    ids_raw = request.args.get('ids') or request.form.get('ids')
    id_list = []
    if ids_raw:
        for tok in ids_raw.split(','):
            tok = tok.strip()
            if not tok:
                continue
            try:
                id_list.append(int(tok))
            except ValueError:
                continue
    page = int(request.args.get('page', 1))
    page_size = request.args.get('page_size') or request.form.get('page_size')
    preview_language = request.args.get('preview_language') or request.form.get('preview_language') or 'EN'
    
    # Handle page size - default to config value or 20
    try:
        page_size = int(page_size) if page_size else None
    except (ValueError, TypeError):
        page_size = None
    
    # Limit page_size to reasonable values
    if page_size and page_size not in [10, 20, 50, 100]:
        page_size = 20
    
    # Get sort configuration - supports multi-level sorting
    # Format: [{"field": "qid", "direction": "asc"}, {"field": "year", "direction": "desc"}]
    sort_config_str = request.args.get('sort_config') or request.form.get('sort_config')
    if sort_config_str:
        try:
            sort_config = json.loads(sort_config_str)
        except json.JSONDecodeError:
            sort_config = [{"field": "qid", "direction": "asc"}]
    else:
        sort_config = [{"field": "qid", "direction": "asc"}]
    
    # Store in session for pagination
    session['filter_params'] = {
        'subject': subject,
        'source_type': source_type,
        'years': years,
        'section': section,
        'topics': topics,
        'topic_mode': topic_mode,
        'subtopics': subtopics,
        'subtopic_mode': subtopic_mode,
        'is_crosstopic': is_crosstopic,
        'is_crosssubtopic': is_crosssubtopic,
        'chapters': chapters,
        'subchapters': subchapters,
        'levels': levels,
        'q_type': q_type,
        'qid_search': qid_search,
        'qid_strict': qid_strict,
        'qids': qid_list,
        'ids': id_list,
    }
    session['sort_config'] = sort_config
    
    # Build query
    query = Question.query

    # Explicit QID list - if provided, filter by exact QID matches and ignore all other filters.
    # This is used by the admin DB-health "view anomaly" buttons to jump to a specific set
    # without losing visibility behind subject/source/topic filters. Access is still scoped
    # to subjects the user has permission to view.
    if qid_list:
        accessible_subjects = [s.id for s in get_user_accessible_subjects()]
        query = query.filter(
            Question.qid.in_(qid_list),
            Question.subject.in_(accessible_subjects)
        )
        # Null out other filter variables so the remaining filter blocks become no-ops
        subject = None
        source_type = None
        years = []
        section = None
        topics = []
        subtopics = []
        chapters = []
        subchapters = []
        levels = []
        q_type = None
        qid_search = None
    # Explicit DB id list - same override semantics as `qids` but keyed on Question.id.
    # Drives the "Show Selected Only" toggle: the frontend submits the user's full
    # `selectedQuestions` set so all selections paginate cleanly, regardless of the
    # sidebar filter. Subject access is still enforced.
    elif id_list:
        accessible_subjects = [s.id for s in get_user_accessible_subjects()]
        query = query.filter(
            Question.id.in_(id_list),
            Question.subject.in_(accessible_subjects)
        )
        subject = None
        source_type = None
        years = []
        section = None
        topics = []
        subtopics = []
        chapters = []
        subchapters = []
        levels = []
        q_type = None
        qid_search = None
    # Direct QID search - if provided, search by QID directly
    elif qid_search and qid_search.strip():
        if qid_strict:
            # Strict mode: user controls the pattern with * as wildcard
            qid_pattern = qid_search.strip()
            if '*' in qid_pattern or '%' in qid_pattern:
                qid_pattern = qid_pattern.replace('*', '%')
                query = query.filter(Question.qid.ilike(qid_pattern))
            else:
                query = query.filter(Question.qid.ilike(f'%{qid_pattern}%'))
        else:
            # Loose mode: normalize input — keep only alphanumeric chars as tokens,
            # then match any QID that contains all tokens in order (case-insensitive).
            # e.g. "2025 q1" → LIKE '%2025%Q1%'
            raw = qid_search.strip().upper()
            tokens = re.split(r'[^A-Z0-9]+', raw)
            tokens = [t for t in tokens if t]
            if tokens:
                # Build a single LIKE pattern: %TOKEN1%TOKEN2%...%
                qid_pattern = '%' + '%'.join(tokens) + '%'
                query = query.filter(Question.qid.ilike(qid_pattern))

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
            if topic_mode == 'AND' and len(topic_ids) > 1:
                # AND mode: question must have ALL selected topics
                # This only makes sense when checking both major and minor topics
                # (since a question can only have one major topic)
                # For each topic, the question must have it as major OR as one of its minor topics
                for tid in topic_ids:
                    query = query.filter(
                        or_(
                            Question.major_topic_id == tid,
                            Question.minor_topics.any(Topic.id == tid)
                        )
                    )
            else:
                # OR mode (default): question matches ANY of the selected topics
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
            if subtopic_mode == 'AND' and len(subtopic_ids) > 1:
                # AND mode: question must have ALL selected subtopics
                # For each subtopic, the question must have it as major OR as one of its M2M subtopics
                for sid in subtopic_ids:
                    query = query.filter(
                        or_(
                            Question.major_subtopic_id == sid,
                            Question.subtopics.any(Subtopic.id == sid)
                        )
                    )
            else:
                # OR mode (default): question matches ANY of the selected subtopics
                if is_crosssubtopic:
                    # Include questions with selected subtopics as major OR M2M
                    query = query.filter(
                        or_(
                            Question.major_subtopic_id.in_(subtopic_ids),
                            Question.subtopics.any(Subtopic.id.in_(subtopic_ids))
                        )
                    )
                else:
                    # Only major subtopic
                    query = query.filter(Question.major_subtopic_id.in_(subtopic_ids))
    
    # Filter by chapters
    if chapters:
        chapter_ids = [int(c) for c in chapters if c.isdigit()]
        if chapter_ids:
            query = query.filter(Question.chapter_id.in_(chapter_ids))
    
    # Filter by subchapters
    if subchapters:
        subchapter_ids = [int(sc) for sc in subchapters if sc.isdigit()]
        if subchapter_ids:
            query = query.filter(Question.subchapter_id.in_(subchapter_ids))
    
    # Filter by levels
    if levels:
        level_ints = [int(l) for l in levels if l.isdigit()]
        include_null = 'null' in levels
        
        if level_ints and include_null:
            # Include both specific levels and NULL
            query = query.filter(or_(Question.level.in_(level_ints), Question.level.is_(None)))
        elif level_ints:
            # Only specific levels
            query = query.filter(Question.level.in_(level_ints))
        elif include_null:
            # Only NULL levels
            query = query.filter(Question.level.is_(None))
    
    # Filter by question type
    if q_type and q_type != 'all':
        query = query.filter(Question.q_type == q_type)
    
    # Get all matching questions for sorting
    all_questions = query.all()
    
    # Apply multi-level sorting
    sorted_questions = apply_multi_sort(all_questions, sort_config)
    
    # Paginate
    per_page = page_size if page_size else current_app.config.get('QUESTIONS_PER_PAGE', 20)
    total = len(sorted_questions)
    start = (page - 1) * per_page
    end = start + per_page
    questions = sorted_questions[start:end]
    
    total_pages = (total + per_page - 1) // per_page
    
    # Prepare question data with assets
    # Build language ordering: preferred > BI > other
    if preview_language == 'CH':
        lang_order = case(
            (QuestionAsset.language == 'CH', 1),
            (QuestionAsset.language == 'BI', 2),
            (QuestionAsset.language == 'EN', 3),
            else_=4
        )
    else:  # EN (default)
        lang_order = case(
            (QuestionAsset.language == 'EN', 1),
            (QuestionAsset.language == 'BI', 2),
            (QuestionAsset.language == 'CH', 3),
            else_=4
        )
    
    question_data = []
    for q in questions:
        # Get all QUE assets with language preference ordering, ordered by part_number
        que_assets = QuestionAsset.query.filter_by(
            question_id=q.id,
            asset_type='QUE'
        ).filter(
            QuestionAsset.language.in_(['EN', 'CH', 'BI'])
        ).order_by(
            lang_order,  # Preferred > BI > Other
            QuestionAsset.part_number  # Then by part number
        ).all()

        # Pick the best language group: take the language of the first result
        # then filter to only that language so all parts share the same language.
        # Within that language pick the best format (IMG > MD > DOC) so the
        # dashboard card knows which preview mode to render.
        que_asset_ids = []
        preview_mode = None  # 'image' | 'html' | 'download' | None
        preview_format = None  # 'IMG' | 'MD' | 'DOC'
        if que_assets:
            best_lang = que_assets[0].language
            same_lang = [a for a in que_assets if a.language == best_lang]
            # IMG=0, MD=1, DOC=2 (mirror dashboard._PREVIEW_FORMAT_ORDER)
            fmt_rank = {'IMG': 0, 'MD': 1, 'DOC': 2}
            same_lang.sort(key=lambda a: (fmt_rank.get(a.file_format, 99),
                                          a.part_number))
            best_fmt = same_lang[0].file_format
            selected = [a for a in same_lang if a.file_format == best_fmt]
            selected.sort(key=lambda a: a.part_number)
            preview_format = best_fmt
            if best_fmt == 'IMG':
                preview_mode = 'image'
                que_asset_ids = [a.id for a in selected]
            elif best_fmt == 'MD':
                preview_mode = 'html'
            else:
                preview_mode = 'download'
        
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
            'major_subtopic': q.major_subtopic.name if q.major_subtopic else None,
            'major_subtopic_id': q.major_subtopic_id,
            'minor_topic_ids': [t.id for t in q.minor_topics],
            'minor_topics': [t.name for t in q.minor_topics],
            'subtopic_ids': [s.id for s in q.subtopics],
            'subtopics': [s.name for s in q.subtopics],
            'chapter': q.chapter.name if q.chapter else None,
            'chapter_id': q.chapter_id,
            'subchapter': q.subchapter.name if q.subchapter else None,
            'subchapter_id': q.subchapter_id,
            'description': q.description,
            'correct_percentage': q.correct_percentage,
            'que_asset_id': que_asset_ids[0] if que_asset_ids else None,
            'que_asset_ids': que_asset_ids,
            'preview_mode': preview_mode,
            'preview_format': preview_format,
            'has_ans': has_ans,
            'has_sol': has_sol,
            'answer': q.answer,
            'comment': q.comment,
            'has_answer_text': bool(q.answer),
            'has_comment': bool(q.comment)
        })
    
    # Get all question IDs for selection purposes
    all_question_ids = [q.id for q in sorted_questions]
    
    # Get subjects user has admin access to for showing edit buttons
    admin_subjects = current_user.get_admin_subjects()
    
    # If AJAX request, return JSON
    if request.headers.get('HX-Request'):
        return render_template('partials/question_list.html', 
                             questions=question_data,
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             all_question_ids=all_question_ids,
                             sort_config=sort_config,
                             admin_subjects=admin_subjects)
    
    # Otherwise return full page
    subjects = get_user_accessible_subjects()
    return render_template('dashboard.html', 
                         subjects=subjects,
                         questions=question_data,
                         page=page,
                         total_pages=total_pages,
                         total=total,
                         all_question_ids=all_question_ids,
                         sort_config=sort_config,
                         admin_subjects=admin_subjects)

@dashboard_bp.route('/api/topics/<subject_id>')
@login_required
def get_topics(subject_id):
    """Get topics for a subject"""
    # Check subject access
    if not current_user.has_subject_access(subject_id):
        return jsonify([])
    
    topics = Topic.query.filter_by(subject_id=subject_id).order_by(Topic.sort_order).all()
    return jsonify([{'id': t.id, 'name': t.name} for t in topics])

@dashboard_bp.route('/api/subtopics')
@login_required
def get_subtopics():
    """Get subtopics for selected topics
    
    Query params:
        topic_ids: comma-separated topic IDs
        include_hidden: if '1', include hidden subtopics (for admin edit modes)
        q_type: 'all', 'MC', or 'CQ' - filter question counts by type
    """
    topic_ids = request.args.get('topic_ids', '').split(',')
    topic_ids = [int(tid) for tid in topic_ids if tid.isdigit()]
    include_hidden = request.args.get('include_hidden', '0') == '1'
    q_type = request.args.get('q_type', 'all')
    
    if not topic_ids:
        return jsonify([])
    
    query = Subtopic.query.filter(Subtopic.topic_id.in_(topic_ids))
    
    # Filter hidden subtopics unless explicitly included
    if not include_hidden:
        query = query.filter(Subtopic.hidden == False)
    
    subtopics = query.order_by(Subtopic.sort_order).all()
    
    # Build question count for each subtopic
    # Count questions where subtopic is major_subtopic OR in M2M relationship
    result = []
    for s in subtopics:
        # Base query for questions linked to this subtopic
        q_query = Question.query.filter(
            or_(
                Question.major_subtopic_id == s.id,
                Question.subtopics.any(Subtopic.id == s.id)
            )
        )
        # Filter by question type if not 'all'
        if q_type and q_type != 'all':
            q_query = q_query.filter(Question.q_type == q_type)
        
        count = q_query.count()
        result.append({
            'id': s.id, 
            'name': s.name, 
            'topic_id': s.topic_id, 
            'hidden': s.hidden,
            'count': count
        })
    
    return jsonify(result)

@dashboard_bp.route('/api/chapters/<subject_id>')
@login_required
def get_chapters(subject_id):
    """Get chapters for a subject"""
    # Check subject access
    if not current_user.has_subject_access(subject_id):
        return jsonify([])
    
    chapters_list = Chapter.query.filter_by(subject_id=subject_id).order_by(Chapter.sort_order).all()
    return jsonify([{'id': c.id, 'name': c.name} for c in chapters_list])

@dashboard_bp.route('/api/subchapters')
@login_required
def get_subchapters():
    """Get subchapters for selected chapters
    
    Query params:
        chapter_ids: comma-separated chapter IDs
        include_hidden: if '1', include hidden subchapters (for admin edit modes)
    """
    chapter_ids = request.args.get('chapter_ids', '').split(',')
    chapter_ids = [int(cid) for cid in chapter_ids if cid.isdigit()]
    include_hidden = request.args.get('include_hidden', '0') == '1'
    
    if not chapter_ids:
        return jsonify([])
    
    query = Subchapter.query.filter(Subchapter.chapter_id.in_(chapter_ids))
    
    # Filter hidden subchapters unless explicitly included
    if not include_hidden:
        query = query.filter(Subchapter.hidden == False)
    
    subchapters_list = query.order_by(Subchapter.sort_order).all()
    return jsonify([{'id': sc.id, 'name': sc.name, 'chapter_id': sc.chapter_id, 'hidden': sc.hidden} for sc in subchapters_list])

@dashboard_bp.route('/api/years/<subject_id>/<source>')
@login_required
def get_years(subject_id, source):
    """Get available years for a subject and source"""
    # Check subject access
    if not current_user.has_subject_access(subject_id):
        return jsonify([])
    
    years = db.session.query(Question.year)\
        .filter(Question.subject == subject_id)\
        .filter(Question.source == source)\
        .filter(Question.year.isnot(None))\
        .distinct()\
        .order_by(Question.year.desc())\
        .all()

    return jsonify([y[0] for y in years])

@dashboard_bp.route('/api/sections/<subject_id>/<source>')
@login_required
def get_sections(subject_id, source):
    """Get available sections for a subject and source"""
    # Check subject access
    if not current_user.has_subject_access(subject_id):
        return jsonify([])
    
    sections = db.session.query(Question.section)\
        .filter(Question.subject == subject_id)\
        .filter(Question.source == source)\
        .filter(Question.section.isnot(None))\
        .distinct()\
        .order_by(Question.section)\
        .all()

    return jsonify([s[0] for s in sections])

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
    """Get all asset parts for a question and type, ordered by part_number"""
    assets = QuestionAsset.query.filter_by(
        question_id=question_id,
        asset_type=asset_type
    ).order_by(
        QuestionAsset.language.desc(),  # Prefer EN
        QuestionAsset.part_number
    ).all()
    
    if not assets:
        return jsonify({'error': 'Asset not found'}), 404
    
    # Pick the best language group (language of first result after ordering)
    best_lang = assets[0].language
    selected = [a for a in assets if a.language == best_lang]
    
    parts = []
    for asset in selected:
        parts.append({
            'id': asset.id,
            'type': asset.asset_type,
            'format': asset.file_format,
            'language': asset.language,
            'part_number': asset.part_number,
            'url': f"/dashboard/files/{asset.file_path}"
        })
    
    return jsonify({
        'parts': parts,
        # Keep backward-compat single-asset fields from the first part
        'id': parts[0]['id'],
        'type': parts[0]['type'],
        'format': parts[0]['format'],
        'language': parts[0]['language'],
        'url': parts[0]['url']
    })


# Format preference for the unified preview resolver.
# Lower number = preferred. IMG first (rich rendering), then MD (rendered HTML),
# then DOC (download-only). Mirrored in app/generator.py for generation order.
_PREVIEW_FORMAT_ORDER = {'IMG': 0, 'MD': 1, 'DOC': 2}


def _resolve_preview_assets(question_id, asset_type, language_pref):
    """
    Pick the best asset group for a question's asset_type.

    Order: language preference (preferred -> BI -> other), then format
    (IMG > MD > DOC). All parts in the winning (language, format) group are
    returned in part_number order.
    """
    assets = QuestionAsset.query.filter_by(
        question_id=question_id, asset_type=asset_type
    ).all()
    if not assets:
        return None, None, []

    def lang_rank(a):
        if a.language == language_pref:
            return 0
        if a.language == 'BI':
            return 1
        return 2

    def fmt_rank(a):
        return _PREVIEW_FORMAT_ORDER.get(a.file_format, 99)

    assets.sort(key=lambda a: (lang_rank(a), fmt_rank(a), a.part_number))
    best = assets[0]
    selected = [a for a in assets
                if a.language == best.language and a.file_format == best.file_format]
    selected.sort(key=lambda a: a.part_number)
    return best.language, best.file_format, selected


@dashboard_bp.route('/api/question/<int:question_id>/preview/<asset_type>')
@login_required
def get_question_preview(question_id, asset_type):
    """
    Unified preview resolver. Returns one of three shapes:

      {mode: 'image',    format: 'IMG', language, parts: [{url, part_number, id}]}
      {mode: 'html',     format: 'MD',  language, html, asset_id, url}
      {mode: 'download', format: 'DOC', language, url, filename, asset_id}

    Plus `{error: ...}` with 404 when no matching asset exists.
    """
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        return jsonify({'error': 'Invalid asset_type'}), 400

    lang_pref = (request.args.get('lang') or 'EN').upper()
    if lang_pref not in ('EN', 'CH', 'BI'):
        lang_pref = 'EN'

    language, file_format, selected = _resolve_preview_assets(
        question_id, asset_type, lang_pref
    )
    if not selected:
        return jsonify({'error': 'Asset not found'}), 404

    source_path = current_app.config['SOURCE_PATH']

    if file_format == 'IMG':
        return jsonify({
            'mode': 'image',
            'format': 'IMG',
            'language': language,
            'parts': [{
                'id': a.id,
                'part_number': a.part_number,
                'url': f"/dashboard/files/{a.file_path}",
            } for a in selected],
        })

    if file_format == 'MD':
        asset = selected[0]
        abs_path = os.path.join(source_path, *asset.file_path.split('/'))
        html = md_render.render_file(asset.id, abs_path)
        return jsonify({
            'mode': 'html',
            'format': 'MD',
            'language': language,
            'asset_id': asset.id,
            'html': html,
            'url': f"/dashboard/files/{asset.file_path}",
        })

    # DOC fallback: download-only (browsers cannot inline-render .docx).
    asset = selected[0]
    return jsonify({
        'mode': 'download',
        'format': 'DOC',
        'language': language,
        'asset_id': asset.id,
        'url': f"/dashboard/files/{asset.file_path}",
        'filename': asset.file_path.rsplit('/', 1)[-1],
    })

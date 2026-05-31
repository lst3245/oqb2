"""
Dashboard routes for question browsing and filtering
"""
from flask import Blueprint, render_template, request, jsonify, session, current_app, send_file, abort, url_for, Response
from flask_login import login_required, current_user
from sqlalchemy import or_, and_, case
from app import db
from app.models import Question, QuestionAsset, Topic, Subtopic, Subject, Chapter, Subchapter
from app.utils import (natural_sort, apply_multi_sort, get_user_accessible_subjects,
                       enumerate_sort_groups, GROUPING_FIELDS,
                       parse_version_priority, VERSIONS, DEFAULT_VERSION_PRIORITY)
from app import md_render
import os
import re
import json

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


def _build_filtered_query(params):
    """
    Build the question query from a normalized filter-params dict (same shape as
    ``session['filter_params']`` plus ``qid_strict``). Returns a SQLAlchemy query
    (before ``.all()``). Shared by ``filter_questions`` and the sort-groups API so
    both see the exact same result set.
    """
    subject = params.get('subject')
    source_type = params.get('source_type')
    years = params.get('years') or []
    section = params.get('section')
    topics = params.get('topics') or []
    topic_mode = params.get('topic_mode') or 'OR'
    subtopics = params.get('subtopics') or []
    subtopic_mode = params.get('subtopic_mode') or 'OR'
    is_crosstopic = params.get('is_crosstopic')
    is_crosssubtopic = params.get('is_crosssubtopic')
    chapters = params.get('chapters') or []
    subchapters = params.get('subchapters') or []
    levels = params.get('levels') or []
    q_type = params.get('q_type')
    qid_search = params.get('qid_search')
    qid_strict = params.get('qid_strict')
    qid_list = params.get('qids') or []
    id_list = params.get('ids') or []

    query = Question.query

    # Explicit QID list - if provided, filter by exact QID matches and ignore all other filters.
    if qid_list:
        accessible_subjects = [s.id for s in get_user_accessible_subjects()]
        query = query.filter(
            Question.qid.in_(qid_list),
            Question.subject.in_(accessible_subjects)
        )
        subject = source_type = section = q_type = qid_search = None
        years = topics = subtopics = chapters = subchapters = levels = []
    # Explicit DB id list - same override semantics as `qids` but keyed on Question.id.
    elif id_list:
        accessible_subjects = [s.id for s in get_user_accessible_subjects()]
        query = query.filter(
            Question.id.in_(id_list),
            Question.subject.in_(accessible_subjects)
        )
        subject = source_type = section = q_type = qid_search = None
        years = topics = subtopics = chapters = subchapters = levels = []
    # Direct QID search - if provided, search by QID directly
    elif qid_search and qid_search.strip():
        if qid_strict:
            qid_pattern = qid_search.strip()
            if '*' in qid_pattern or '%' in qid_pattern:
                qid_pattern = qid_pattern.replace('*', '%')
                query = query.filter(Question.qid.ilike(qid_pattern))
            else:
                query = query.filter(Question.qid.ilike(f'%{qid_pattern}%'))
        else:
            raw = qid_search.strip().upper()
            tokens = re.split(r'[^A-Z0-9]+', raw)
            tokens = [t for t in tokens if t]
            if tokens:
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
        year_ints = [int(y) for y in years if str(y).isdigit()]
        if year_ints:
            query = query.filter(Question.year.in_(year_ints))

    # Filter by section
    if section and section != 'all':
        query = query.filter(Question.section == section)

    # Filter by topics
    if topics:
        topic_ids = [int(t) for t in topics if str(t).isdigit()]
        if topic_ids:
            if topic_mode == 'AND' and len(topic_ids) > 1:
                for tid in topic_ids:
                    query = query.filter(
                        or_(
                            Question.major_topic_id == tid,
                            Question.minor_topics.any(Topic.id == tid)
                        )
                    )
            else:
                if is_crosstopic:
                    query = query.filter(
                        or_(
                            Question.major_topic_id.in_(topic_ids),
                            Question.minor_topics.any(Topic.id.in_(topic_ids))
                        )
                    )
                else:
                    query = query.filter(Question.major_topic_id.in_(topic_ids))

    # Filter by subtopics
    if subtopics:
        subtopic_ids = [int(s) for s in subtopics if str(s).isdigit()]
        if subtopic_ids:
            if subtopic_mode == 'AND' and len(subtopic_ids) > 1:
                for sid in subtopic_ids:
                    query = query.filter(
                        or_(
                            Question.major_subtopic_id == sid,
                            Question.subtopics.any(Subtopic.id == sid)
                        )
                    )
            else:
                if is_crosssubtopic:
                    query = query.filter(
                        or_(
                            Question.major_subtopic_id.in_(subtopic_ids),
                            Question.subtopics.any(Subtopic.id.in_(subtopic_ids))
                        )
                    )
                else:
                    query = query.filter(Question.major_subtopic_id.in_(subtopic_ids))

    # Filter by chapters
    if chapters:
        chapter_ids = [int(c) for c in chapters if str(c).isdigit()]
        if chapter_ids:
            query = query.filter(Question.chapter_id.in_(chapter_ids))

    # Filter by subchapters
    if subchapters:
        subchapter_ids = [int(sc) for sc in subchapters if str(sc).isdigit()]
        if subchapter_ids:
            query = query.filter(Question.subchapter_id.in_(subchapter_ids))

    # Filter by levels
    if levels:
        level_ints = [int(l) for l in levels if str(l).isdigit()]
        include_null = 'null' in levels
        if level_ints and include_null:
            query = query.filter(or_(Question.level.in_(level_ints), Question.level.is_(None)))
        elif level_ints:
            query = query.filter(Question.level.in_(level_ints))
        elif include_null:
            query = query.filter(Question.level.is_(None))

    # Filter by question type
    if q_type and q_type != 'all':
        query = query.filter(Question.q_type == q_type)

    return query

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
    # Ordered version priority (highest first). Accepts the new
    # `version_priority` comma list; falls back to the legacy single-value
    # `preview_language` for older clients / saved state.
    version_priority = parse_version_priority(
        request.args.get('version_priority') or request.form.get('version_priority'),
        legacy_preferred=(request.args.get('preview_language') or request.form.get('preview_language')),
    )
    
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

    # Optional manual block ordering (drives Topic/Subtopic/Chapter/Subchapter
    # block drag-reorder). Kept separate from sort_config; ignored by
    # apply_multi_sort unless its `fields` match the active grouping fields.
    sort_group_order_str = request.args.get('sort_group_order') or request.form.get('sort_group_order')
    sort_group_order = None
    if sort_group_order_str:
        try:
            sort_group_order = json.loads(sort_group_order_str)
        except (json.JSONDecodeError, TypeError):
            sort_group_order = None

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
    session['sort_group_order'] = sort_group_order

    # Build query (shared with the sort-groups API via _build_filtered_query)
    query = _build_filtered_query(session['filter_params'])

    # Get all matching questions for sorting
    all_questions = query.all()
    
    # Apply multi-level sorting (with optional manual block ordering)
    sorted_questions = apply_multi_sort(all_questions, sort_config, group_order=sort_group_order)
    
    # Paginate
    per_page = page_size if page_size else current_app.config.get('QUESTIONS_PER_PAGE', 20)
    total = len(sorted_questions)
    start = (page - 1) * per_page
    end = start + per_page
    questions = sorted_questions[start:end]
    
    total_pages = (total + per_page - 1) // per_page
    
    # Prepare question data with assets
    # Build version ordering from the priority list (highest priority first).
    version_order = case(
        *[(QuestionAsset.version == v, i) for i, v in enumerate(version_priority)],
        else_=len(version_priority),
    )
    
    question_data = []
    for q in questions:
        # Get all QUE assets with version preference ordering, ordered by part_number
        que_assets = QuestionAsset.query.filter_by(
            question_id=q.id,
            asset_type='QUE'
        ).filter(
            QuestionAsset.version.in_(VERSIONS)
        ).order_by(
            version_order,  # By version priority
            QuestionAsset.part_number  # Then by part number
        ).all()

        # Pick the best version group: take the version of the first result
        # then filter to only that version so all parts share the same version.
        # Within that version pick the best format (IMG > MD > DOC) so the
        # dashboard card knows which preview mode to render.
        que_asset_ids = []
        preview_mode = None  # 'image' | 'html' | 'thumbnail' | 'download' | None
        preview_format = None  # 'IMG' | 'MD' | 'DOC'
        preview_version = None  # the resolved winning version code
        preview_doc_asset_id = None  # set when preview_mode in ('thumbnail', 'download')
        preview_doc_filename = None
        preview_doc_file_path = None
        if que_assets:
            best_lang = que_assets[0].version
            preview_version = best_lang
            same_lang = [a for a in que_assets if a.version == best_lang]
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
                # DOC: prefer a server-rendered first-page PNG thumbnail when
                # cached on disk; otherwise schedule a render in the
                # background and fall back to the download stub for now.
                # The thumbnail will appear on the user's next refresh.
                doc_asset = selected[0]
                preview_doc_asset_id = doc_asset.id
                preview_doc_file_path = doc_asset.file_path
                preview_doc_filename = doc_asset.file_path.rsplit('/', 1)[-1]
                from app import doc_thumbnails as _doc_thumbnails
                if _doc_thumbnails.ensure_thumbnail(doc_asset.id):
                    preview_mode = 'thumbnail'
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
            'preview_version': preview_version,
            'preview_doc_asset_id': preview_doc_asset_id,
            'preview_doc_filename': preview_doc_filename,
            'preview_doc_file_path': preview_doc_file_path,
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

@dashboard_bp.route('/api/sort-groups', methods=['POST'])
@login_required
def get_sort_groups():
    """
    Enumerate the grouping-blocks (Topic / Subtopic / Chapter / Subchapter
    combinations) for the current filter, so the frontend can present them for
    manual drag-reordering.

    Accepts the same filter fields as ``/filter`` plus ``group_fields`` (JSON
    array or comma list). Returns ``{group_fields, blocks: [{key, labels, count}]}``
    in default natural-name order.
    """
    # Parse requested grouping fields
    gf_raw = request.form.get('group_fields') or request.args.get('group_fields') or ''
    group_fields = []
    if gf_raw:
        try:
            parsed = json.loads(gf_raw)
            if isinstance(parsed, list):
                group_fields = [f for f in parsed if f in GROUPING_FIELDS]
        except (json.JSONDecodeError, TypeError):
            group_fields = [f.strip() for f in gf_raw.split(',') if f.strip() in GROUPING_FIELDS]
    if not group_fields:
        return jsonify({'group_fields': [], 'blocks': []})

    # Subject access check (mirrors filter_questions)
    subject = request.form.get('subject') or request.args.get('subject')
    if subject and not current_user.has_subject_access(subject):
        return jsonify({'error': 'Access denied to this subject'}), 403

    # Build normalized filter params (same shape _build_filtered_query expects)
    qids_raw = request.form.get('qids') or request.args.get('qids')
    qid_list = [q.strip() for q in qids_raw.split(',') if q.strip()] if qids_raw else []
    ids_raw = request.form.get('ids') or request.args.get('ids')
    id_list = []
    if ids_raw:
        for tok in ids_raw.split(','):
            tok = tok.strip()
            if tok:
                try:
                    id_list.append(int(tok))
                except ValueError:
                    continue

    params = {
        'subject': subject,
        'source_type': request.form.get('source_type') or request.args.get('source_type'),
        'years': request.form.getlist('years') or request.args.getlist('years'),
        'section': request.form.get('section') or request.args.get('section'),
        'topics': request.form.getlist('topics') or request.args.getlist('topics'),
        'topic_mode': request.form.get('topic_mode') or request.args.get('topic_mode') or 'OR',
        'subtopics': request.form.getlist('subtopics') or request.args.getlist('subtopics'),
        'subtopic_mode': request.form.get('subtopic_mode') or request.args.get('subtopic_mode') or 'OR',
        'is_crosstopic': request.form.get('is_crosstopic') or request.args.get('is_crosstopic'),
        'is_crosssubtopic': request.form.get('is_crosssubtopic') or request.args.get('is_crosssubtopic'),
        'chapters': request.form.getlist('chapters') or request.args.getlist('chapters'),
        'subchapters': request.form.getlist('subchapters') or request.args.getlist('subchapters'),
        'levels': request.form.getlist('levels') or request.args.getlist('levels'),
        'q_type': request.form.get('q_type') or request.args.get('q_type'),
        'qid_search': request.form.get('qid_search') or request.args.get('qid_search'),
        'qid_strict': (request.form.get('qid_strict') or request.args.get('qid_strict')) in ('on', 'true', '1', True),
        'qids': qid_list,
        'ids': id_list,
    }

    query = _build_filtered_query(params)
    questions = query.all()
    blocks = enumerate_sort_groups(questions, group_fields)
    return jsonify({'group_fields': group_fields, 'blocks': blocks})


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
        'version': asset.version,
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
        QuestionAsset.version.desc(),  # Prefer EN
        QuestionAsset.part_number
    ).all()
    
    if not assets:
        return jsonify({'error': 'Asset not found'}), 404
    
    # Pick the best version group (version of first result after ordering)
    best_ver = assets[0].version
    selected = [a for a in assets if a.version == best_ver]
    
    parts = []
    for asset in selected:
        parts.append({
            'id': asset.id,
            'type': asset.asset_type,
            'format': asset.file_format,
            'version': asset.version,
            'part_number': asset.part_number,
            'url': f"/dashboard/files/{asset.file_path}"
        })
    
    return jsonify({
        'parts': parts,
        # Keep backward-compat single-asset fields from the first part
        'id': parts[0]['id'],
        'type': parts[0]['type'],
        'format': parts[0]['format'],
        'version': parts[0]['version'],
        'url': parts[0]['url']
    })


# Format preference for the unified preview resolver.
# Lower number = preferred. IMG first (rich rendering), then MD (rendered HTML),
# then DOC (download-only). Mirrored in app/generator.py for generation order.
_PREVIEW_FORMAT_ORDER = {'IMG': 0, 'MD': 1, 'DOC': 2}


def _resolve_preview_assets(question_id, asset_type, version_priority,
                            force_format=None):
    """
    Pick the best asset group for a question's asset_type.

    Order: version priority (the ordered list, highest priority first), then
    format (IMG > MD > DOC). All parts in the winning (version, format) group
    are returned in part_number order.

    ``force_format`` (IMG/MD/DOC) restricts the candidates to that format only
    — used by the edit-question modal's per-slot MD preview card so it renders
    the MD even when a higher-priority IMG exists for the same slot.
    """
    assets = QuestionAsset.query.filter_by(
        question_id=question_id, asset_type=asset_type
    ).all()
    if force_format:
        assets = [a for a in assets if a.file_format == force_format]
    if not assets:
        return None, None, []

    ver_rank_map = {v: i for i, v in enumerate(version_priority)}

    def ver_rank(a):
        return ver_rank_map.get(a.version, len(version_priority))

    def fmt_rank(a):
        return _PREVIEW_FORMAT_ORDER.get(a.file_format, 99)

    assets.sort(key=lambda a: (ver_rank(a), fmt_rank(a), a.part_number))
    best = assets[0]
    selected = [a for a in assets
                if a.version == best.version and a.file_format == best.file_format]
    selected.sort(key=lambda a: a.part_number)
    return best.version, best.file_format, selected


@dashboard_bp.route('/api/question/<int:question_id>/preview/<asset_type>')
@login_required
def get_question_preview(question_id, asset_type):
    """
    Unified preview resolver. Returns one of four shapes:

      {mode: 'image',     format: 'IMG', version, parts: [{url, part_number, id}]}
      {mode: 'html',      format: 'MD',  version, html, asset_id, url}
      {mode: 'thumbnail', format: 'DOC', version, thumbnail_url, download_url, filename, asset_id}
      {mode: 'download',  format: 'DOC', version, url, filename, asset_id}

    DOC assets prefer 'thumbnail' mode when a cached PNG exists on disk (the
    server-rendered first page); 'download' is the fallback when the
    thumbnail isn't ready yet or Word COM is unavailable.

    Accepts `?version_priority=EN,CH,BI,ENO,CHO` (ordered, highest first).
    Legacy `?lang=EN` is still honoured as a fallback preferred version.

    Plus `{error: ...}` with 404 when no matching asset exists.
    """
    if asset_type not in ('QUE', 'ANS', 'SOL'):
        return jsonify({'error': 'Invalid asset_type'}), 400

    version_priority = parse_version_priority(
        request.args.get('version_priority'),
        legacy_preferred=request.args.get('lang'),
    )

    force_format = (request.args.get('format') or '').strip().upper() or None
    if force_format and force_format not in ('IMG', 'MD', 'DOC'):
        force_format = None

    version, file_format, selected = _resolve_preview_assets(
        question_id, asset_type, version_priority, force_format=force_format
    )
    if not selected:
        return jsonify({'error': 'Asset not found'}), 404

    source_path = current_app.config['SOURCE_PATH']

    if file_format == 'IMG':
        return jsonify({
            'mode': 'image',
            'format': 'IMG',
            'version': version,
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
            'version': version,
            'asset_id': asset.id,
            'html': html,
            'url': f"/dashboard/files/{asset.file_path}",
        })

    # DOC: prefer a server-rendered first-page PNG thumbnail when present;
    # otherwise schedule one in the background and fall back to download-only.
    asset = selected[0]
    from app import doc_thumbnails
    filename = asset.file_path.rsplit('/', 1)[-1]
    download_url = f"/dashboard/files/{asset.file_path}"
    if doc_thumbnails.ensure_thumbnail(asset.id):
        return jsonify({
            'mode': 'thumbnail',
            'format': 'DOC',
            'version': version,
            'asset_id': asset.id,
            'question_id': asset.question_id,
            'thumbnail_url': url_for('dashboard.doc_thumbnail', asset_id=asset.id),
            'download_url': download_url,
            'filename': filename,
        })
    return jsonify({
        'mode': 'download',
        'format': 'DOC',
        'version': version,
        'asset_id': asset.id,
        'question_id': asset.question_id,
        'url': download_url,
        'filename': filename,
    })


# ==================== Explain (AI tutor chat) ====================

def _default_explain_endpoint():
    """The endpoint used by the Explain tutor.

    When ``EXPLAIN_DEFAULT_LLM`` names a specific enabled endpoint that
    endpoint is used.  Otherwise falls back to the first enabled,
    vision-capable endpoint ordered by sort_order then name.
    Returns ``None`` when nothing is configured.
    """
    from app.models import LLMConfig
    from flask import current_app
    preferred = (current_app.config.get('EXPLAIN_DEFAULT_LLM') or '').strip()
    if preferred:
        cfg = LLMConfig.query.filter_by(name=preferred, enabled=True).first()
        if cfg:
            return cfg
    return (LLMConfig.query
            .filter_by(enabled=True, supports_vision=True)
            .order_by(LLMConfig.sort_order, LLMConfig.name)
            .first())


def _explain_slot_context(question_id, asset_type, version_priority,
                          source_path, image_max_dim):
    """Gather context for one asset slot for the Explain prompt.

    Prefers IMG (sent to the vision model); falls back to the slot's Markdown
    text when no image exists. Returns ``(image_blocks, md_text)`` where
    ``image_blocks`` is a list of ``(b64, mime)`` pairs.
    """
    from app import llm_client
    _v, _f, imgs = _resolve_preview_assets(
        question_id, asset_type, version_priority, force_format='IMG')
    blocks = []
    for a in imgs:
        abs_path = os.path.join(source_path, *a.file_path.split('/'))
        try:
            blocks.append(llm_client.prepare_image(abs_path, image_max_dim))
        except Exception:
            current_app.logger.warning('explain: image prep failed for %s', a.file_path)
    if blocks:
        return blocks, ''
    # No image — fall back to Markdown text so text-only questions still work.
    _v, _f, mds = _resolve_preview_assets(
        question_id, asset_type, version_priority, force_format='MD')
    for a in mds:
        abs_path = os.path.join(source_path, *a.file_path.split('/'))
        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                return [], f.read()
        except OSError:
            continue
    return [], ''


@dashboard_bp.route('/api/question/<int:question_id>/explain', methods=['POST'])
@login_required
def explain_question(question_id):
    """AI tutor chat for a single question — SSE-streamed.

    Sends the QUESTION image(s) (and the SOLUTION image(s) when present) to the
    default vision LLM and streams an explanation back as Server-Sent Events.
    Streaming keeps the response flowing through reverse proxies (no idle
    timeout) and lets the user watch the model think in real time.

    Body JSON: ``{turns: [{role, content}], version_priority?}``. ``turns`` is
    the conversation AFTER the (server-built) initial image turn; empty for
    the first request — the server rebuilds turn 1 (system prompt + labelled
    QUE/SOL context) on every call so images aren't re-uploaded by the client.

    Response is ``text/event-stream``; each event is one ``data: {...}\\n\\n``
    line of JSON:

    * ``{type: 'preamble'}`` — emitted immediately so proxies see bytes.
    * ``{type: 'delta', content?: str, reasoning?: str}`` — incremental tokens.
    * ``{type: 'done', reply, reply_html, model, endpoint, has_solution}`` —
      final, with the rendered Markdown HTML.
    * ``{type: 'error', message}`` — fatal error mid-stream.
    """
    # Permission / prep work happens here, in request context.
    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return jsonify({'error': 'AI features are disabled.'}), 400

    question = Question.query.get_or_404(question_id)
    if not current_user.is_super_admin and not current_user.has_subject_access(question.subject):
        return jsonify({'error': 'You do not have access to this question.'}), 403

    cfg = _default_explain_endpoint()
    if not cfg:
        return jsonify({'error': 'No vision-capable LLM endpoint is configured. '
                                 'Ask an administrator to add one under Admin → LLM Endpoints.'}), 400

    data = request.get_json(silent=True) or {}

    turns = []
    for t in (data.get('turns') or [])[-20:]:
        if not isinstance(t, dict):
            continue
        role = t.get('role')
        content = t.get('content')
        if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
            turns.append({'role': role, 'content': content[:8000]})

    version_priority = parse_version_priority(data.get('version_priority'))
    source_path = current_app.config['SOURCE_PATH']
    image_max_dim = int(current_app.config.get('LLM_IMAGE_MAX_DIM', 1600))
    chat_timeout = int(current_app.config.get('LLM_CHAT_TIMEOUT_SECONDS') or 0) or None

    que_imgs, que_text = _explain_slot_context(
        question_id, 'QUE', version_priority, source_path, image_max_dim)
    sol_imgs, sol_text = _explain_slot_context(
        question_id, 'SOL', version_priority, source_path, image_max_dim)

    if not (que_imgs or que_text):
        return jsonify({'error': 'This question has no image or Markdown to explain.'}), 400

    from app import ai_prompts, llm_client

    parts = []
    if que_imgs:
        parts.append({'type': 'text', 'text': 'QUESTION image(s):'})
        parts += [llm_client._image_block(b, m) for (b, m) in que_imgs]
    elif que_text:
        parts.append({'type': 'text', 'text': 'QUESTION (Markdown):\n' + que_text[:6000]})
    if sol_imgs:
        parts.append({'type': 'text', 'text': 'Official SOLUTION image(s):'})
        parts += [llm_client._image_block(b, m) for (b, m) in sol_imgs]
    elif sol_text:
        parts.append({'type': 'text', 'text': 'Official SOLUTION (Markdown):\n' + sol_text[:6000]})
    parts.append({'type': 'text', 'text': ai_prompts.EXPLAIN_INITIAL_USER})

    messages = [
        {'role': 'system', 'content': ai_prompts.EXPLAIN_SYSTEM},
        {'role': 'user', 'content': parts},
    ]
    messages.extend(turns)

    has_solution = bool(sol_imgs or sol_text)
    app = current_app._get_current_object()

    def generate():
        # Push an app context so DB / config access inside the generator
        # (md_render, normalize_inline_math, llm_client) keep working after
        # the request context tears down.
        with app.app_context():
            # Initial padding helps some proxies recognise this as a stream
            # and start forwarding bytes without buffering.
            yield ': stream-start\n\n'
            yield 'data: ' + json.dumps({'type': 'preamble'}) + '\n\n'

            full_text_parts: list[str] = []
            full_reasoning_parts: list[str] = []
            final_finish_reason = None

            try:
                for evt in llm_client.chat_messages_stream(
                        cfg, messages, timeout=chat_timeout):
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
                        # Some servers emit the full text only at done.
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
                app.logger.exception('Unhandled error streaming explain for q=%s', question_id)
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
                               f'(finish_reason={final_finish_reason}). '
                               'The endpoint may not be vision-capable or hit its token limit.',
                }) + '\n\n'
                return

            try:
                reply_norm = ai_prompts.normalize_inline_math(reply)
                reply_html = md_render.render_text(reply_norm)
            except Exception as e:
                app.logger.exception('Failed to render explain reply for q=%s', question_id)
                yield 'data: ' + json.dumps({
                    'type': 'error',
                    'message': f'Failed to render reply: {type(e).__name__}: {e}',
                }) + '\n\n'
                return

            yield 'data: ' + json.dumps({
                'type': 'done',
                'reply': reply_norm,
                'reply_html': reply_html,
                'model': cfg.model_name,
                'endpoint': cfg.name,
                'has_solution': has_solution,
            }) + '\n\n'

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',  # Tells nginx to not buffer the stream.
        'Connection': 'keep-alive',
    })


@dashboard_bp.route('/api/doc_thumbnail/<int:asset_id>.png')
@login_required
def doc_thumbnail(asset_id):
    """
    Serve the cached first-page PNG thumbnail for a DOC asset.

    Permission: any logged-in user who has access to the question's subject.
    Returns 404 when the cached PNG isn't on disk (the frontend falls back to
    the existing download stub).
    """
    asset = QuestionAsset.query.get_or_404(asset_id)
    if asset.file_format != 'DOC':
        return abort(404)

    # Subject permission check (mirrors /dashboard/files/<path>).
    question = Question.query.get(asset.question_id)
    if question is not None and not current_user.is_super_admin:
        if not current_user.has_subject_access(question.subject):
            return abort(403)

    from app import doc_thumbnails
    path = doc_thumbnails.thumbnail_path(asset_id)
    if not os.path.isfile(path):
        return abort(404)

    # `conditional=True` (the default for send_file) lets Flask emit
    # Last-Modified + ETag and return 304 when the browser already has the
    # current file. We pair it with `Cache-Control: no-cache` so the
    # browser revalidates every fetch — this is what makes the per-preview
    # "Re-render" button visibly update the thumbnail without a hard
    # refresh. Revalidation is cheap (a small 304 response) when nothing
    # has changed, so the dashboard stays fast.
    response = send_file(path, mimetype='image/png', conditional=True)
    response.headers['Cache-Control'] = 'private, no-cache, must-revalidate'
    return response

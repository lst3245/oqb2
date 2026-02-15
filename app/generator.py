"""
Word document generation module
"""
from flask import Blueprint, render_template, request, send_file, current_app, flash, redirect, url_for, session, jsonify
from flask_login import login_required, current_user
from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from PIL import Image
import os
import re
import json
import threading
from datetime import datetime
from app import db
from app.models import Question, QuestionAsset, GeneratedFile
from app.utils import natural_sort, apply_multi_sort, SORT_FIELDS

generator_bp = Blueprint('generator', __name__, url_prefix='/generate')

@generator_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    """Generation options page"""
    # Accept question IDs from POST form data (preferred) or GET query params (fallback)
    filter_data = ''
    generation_options = {}
    
    if request.method == 'POST':
        question_ids = request.form.getlist('question_ids')
        filter_data = request.form.get('filter_data', '')
        # Store in session so page refreshes still work
        if question_ids:
            session['generator_question_ids'] = question_ids
            session['generator_filter_data'] = filter_data
            sort_config_str = request.form.get('sort_config')
            if sort_config_str:
                try:
                    session['sort_config'] = json.loads(sort_config_str)
                except json.JSONDecodeError:
                    pass
    else:
        question_ids = request.args.getlist('question_ids')
        if not question_ids:
            # Fallback to session (e.g. page refresh)
            question_ids = session.get('generator_question_ids', [])
        filter_data = session.get('generator_filter_data', '')
    
    # Check if regenerating from a saved file
    regen_file_id = request.args.get('regen_file_id') or request.form.get('regen_file_id')
    regen_display_name = ''
    if regen_file_id:
        try:
            gen_file = GeneratedFile.query.get(int(regen_file_id))
            if gen_file and (gen_file.user_id == current_user.id or current_user.is_super_admin):
                # Load generation options
                if gen_file.generation_options:
                    try:
                        generation_options = json.loads(gen_file.generation_options)
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Load question IDs from saved options
                saved_qids = generation_options.get('question_ids', [])
                if saved_qids and not question_ids:
                    question_ids = [str(qid) for qid in saved_qids]
                    session['generator_question_ids'] = question_ids
                # Load filter data
                if gen_file.filter_data and not filter_data:
                    filter_data = gen_file.filter_data
                    session['generator_filter_data'] = filter_data
                # Load sort config
                saved_sort = generation_options.get('sort_config')
                if saved_sort:
                    try:
                        session['sort_config'] = json.loads(saved_sort) if isinstance(saved_sort, str) else saved_sort
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Get display name for pre-filling
                if gen_file.display_name:
                    regen_display_name = re.sub(r'_\d{8}_\d{6}$', '', gen_file.display_name)
        except (ValueError, TypeError):
            pass
    
    if not question_ids:
        flash('No questions selected', 'warning')
        return redirect(url_for('dashboard.index'))
    
    # Get questions - preserve the selection order by using a dict
    questions_dict = {str(q.id): q for q in Question.query.filter(Question.id.in_(question_ids)).all()}
    questions = [questions_dict[qid] for qid in question_ids if qid in questions_dict]
    
    # Get sort config from session (from dashboard)
    sort_config = session.get('sort_config', [{"field": "qid", "direction": "asc"}])
    
    # Get available sort fields for the UI
    sort_fields = [{"value": key, "label": info["label"]} for key, info in SORT_FIELDS.items()]
    
    return render_template('generate.html', 
                          questions=questions,
                          question_ids=question_ids,
                          sort_config=sort_config,
                          sort_fields=sort_fields,
                          filter_data=filter_data,
                          generation_options=generation_options,
                          regen_display_name=regen_display_name)

@generator_bp.route('/viewer', methods=['GET', 'POST'])
@login_required
def viewer():
    """Full-screen viewer/presentation mode page"""
    # Accept question IDs from POST form data (preferred) or GET query params (fallback)
    if request.method == 'POST':
        question_ids = request.form.getlist('question_ids')
        sort_config_str = request.form.get('sort_config')
        # Store in session so page refreshes still work
        if question_ids:
            session['viewer_question_ids'] = question_ids
        if sort_config_str:
            try:
                sort_config = json.loads(sort_config_str)
                session['viewer_sort_config'] = sort_config
            except json.JSONDecodeError:
                sort_config = session.get('sort_config', [{"field": "qid", "direction": "asc"}])
        else:
            sort_config = session.get('sort_config', [{"field": "qid", "direction": "asc"}])
    else:
        question_ids = request.args.getlist('question_ids')
        if not question_ids:
            # Fallback to session (e.g. page refresh)
            question_ids = session.get('viewer_question_ids', [])
        sort_config_str = request.args.get('sort_config')
        if sort_config_str:
            try:
                sort_config = json.loads(sort_config_str)
            except json.JSONDecodeError:
                sort_config = session.get('sort_config', [{"field": "qid", "direction": "asc"}])
        else:
            sort_config = session.get('viewer_sort_config', 
                         session.get('sort_config', [{"field": "qid", "direction": "asc"}]))
    
    if not question_ids:
        flash('No questions selected', 'warning')
        return redirect(url_for('dashboard.index'))
    
    # Get questions - preserve the selection order by using a dict
    questions_dict = {str(q.id): q for q in Question.query.filter(Question.id.in_(question_ids)).all()}
    questions_list = [questions_dict[qid] for qid in question_ids if qid in questions_dict]
    
    # Apply sort if custom sort mode
    questions_list = apply_multi_sort(questions_list, sort_config)
    
    # Prepare question data with assets
    questions_data = []
    for q in questions_list:
        # Get all assets for this question
        assets = QuestionAsset.query.filter_by(question_id=q.id).all()
        
        # Group assets by type
        que_assets = [a for a in assets if a.asset_type == 'QUE']
        ans_assets = [a for a in assets if a.asset_type == 'ANS']
        sol_assets = [a for a in assets if a.asset_type == 'SOL']
        
        questions_data.append({
            'id': q.id,
            'qid': q.qid,
            'year': q.year,
            'level': q.level,
            'q_type': q.q_type,
            'has_que': len(que_assets) > 0,
            'has_ans': len(ans_assets) > 0,
            'has_sol': len(sol_assets) > 0,
            'answer': q.answer,
            'comment': q.comment,
            'has_answer_text': bool(q.answer),
            'has_comment': bool(q.comment)
        })
    
    return render_template('viewer.html', 
                          questions=questions_data,
                          question_ids=question_ids)

@generator_bp.route('/api/viewer_asset/<int:question_id>/<asset_type>')
@login_required
def get_viewer_asset(question_id, asset_type):
    """Get asset URL for viewer mode with language preference"""
    preferred_language = request.args.get('lang', 'EN')
    
    # Get all assets for this question and type
    assets = QuestionAsset.query.filter_by(
        question_id=question_id,
        asset_type=asset_type
    ).all()
    
    if not assets:
        # If SOL not found, try ANS and vice versa
        fallback_type = 'ANS' if asset_type == 'SOL' else 'SOL' if asset_type == 'ANS' else None
        if fallback_type:
            assets = QuestionAsset.query.filter_by(
                question_id=question_id,
                asset_type=fallback_type
            ).all()
            if assets:
                asset_type = fallback_type  # Update to indicate the fallback
    
    if not assets:
        return jsonify({'error': 'Asset not found', 'asset_type': asset_type}), 404
    
    # Sort by language preference and format
    def lang_order(asset):
        if asset.language == preferred_language:
            return 0
        elif asset.language == 'BI':
            return 1
        else:
            return 2
    
    def format_order(asset):
        return 0 if asset.file_format == 'IMG' else 1
    
    sorted_assets = sorted(assets, key=lambda a: (format_order(a), lang_order(a)))
    asset = sorted_assets[0]
    
    file_url = f"/dashboard/files/{asset.file_path}"
    
    return jsonify({
        'id': asset.id,
        'type': asset.asset_type,
        'format': asset.file_format,
        'language': asset.language,
        'url': file_url
    })

@generator_bp.route('/create', methods=['POST'])
@login_required
def create_document():
    """Start background generation of Word document from selected questions"""
    
    # Get parameters
    question_ids = request.form.getlist('question_ids')
    sort_mode = request.form.get('sort_mode', 'custom')
    sort_config_str = request.form.get('sort_config', '')
    answer_mode = request.form.get('answer_mode', 'QUE_ONLY')
    display_name = request.form.get('display_name', '').strip()
    filter_data = request.form.get('filter_data', '')
    
    # MC spacing settings
    mc_before_mode = request.form.get('mc_before_mode', 'lines')
    mc_before_lines = int(request.form.get('mc_before_lines', 0))
    mc_after_mode = request.form.get('mc_after_mode', 'lines')
    mc_after_lines = int(request.form.get('mc_after_lines', 1))
    
    # CQ spacing settings
    cq_before_mode = request.form.get('cq_before_mode', 'page')
    cq_before_lines = int(request.form.get('cq_before_lines', 0))
    cq_after_mode = request.form.get('cq_after_mode', 'page')
    cq_after_lines = int(request.form.get('cq_after_lines', 0))
    
    # Show QID options
    show_qid = request.form.get('show_qid') == 'on'
    show_qid_answer = request.form.get('show_qid_answer') == 'on'
    show_correct_pct = request.form.get('show_correct_pct') == 'on'
    show_seq_no = request.form.get('show_seq_no') == 'on'
    show_page_no = request.form.get('show_page_no') == 'on'
    
    preferred_language = request.form.get('preferred_language', 'EN')
    answer_preference = request.form.get('answer_preference', 'image_first')
    
    if not question_ids:
        return jsonify({'error': 'No questions selected'}), 400
    
    # Build generation options for storage
    generation_options = {
        'sort_mode': sort_mode,
        'sort_config': sort_config_str,
        'answer_mode': answer_mode,
        'mc_before_mode': mc_before_mode, 'mc_before_lines': mc_before_lines,
        'mc_after_mode': mc_after_mode, 'mc_after_lines': mc_after_lines,
        'cq_before_mode': cq_before_mode, 'cq_before_lines': cq_before_lines,
        'cq_after_mode': cq_after_mode, 'cq_after_lines': cq_after_lines,
        'show_qid': show_qid, 'show_qid_answer': show_qid_answer,
        'show_correct_pct': show_correct_pct,
        'show_seq_no': show_seq_no, 'show_page_no': show_page_no,
        'preferred_language': preferred_language,
        'answer_preference': answer_preference,
        'question_ids': question_ids,
    }
    
    # Create filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if not display_name:
        display_name = f'questions_{timestamp}'
        filename = f'{display_name}.docx'
    else:
        filename = f'{display_name}_{timestamp}.docx'
    # Sanitize filename
    filename = "".join(c for c in filename if c.isalnum() or c in '._- ').strip()
    if not filename.endswith('.docx'):
        filename += '.docx'
    
    # Create GeneratedFile record
    gen_file = GeneratedFile(
        user_id=current_user.id,
        display_name=display_name,
        filename=filename,
        status='pending',
        filter_data=filter_data if filter_data else None,
        generation_options=json.dumps(generation_options),
        question_count=len(question_ids),
    )
    db.session.add(gen_file)
    db.session.commit()
    gen_file_id = gen_file.id
    
    # Build spacing config
    spacing_config = {
        'mc': {
            'before_mode': mc_before_mode, 'before_lines': mc_before_lines,
            'after_mode': mc_after_mode, 'after_lines': mc_after_lines
        },
        'cq': {
            'before_mode': cq_before_mode, 'before_lines': cq_before_lines,
            'after_mode': cq_after_mode, 'after_lines': cq_after_lines
        }
    }
    
    # Spawn background thread
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_generate_in_background,
        args=(app, gen_file_id, question_ids, sort_mode, sort_config_str,
              answer_mode, spacing_config, show_qid, show_qid_answer,
              preferred_language, show_correct_pct, answer_preference,
              show_seq_no, show_page_no, filename)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({'id': gen_file_id, 'status': 'pending', 'filename': filename})


def _generate_in_background(app, gen_file_id, question_ids, sort_mode, sort_config_str,
                            answer_mode, spacing_config, show_qid, show_qid_answer,
                            preferred_language, show_correct_pct, answer_preference,
                            show_seq_no, show_page_no, filename):
    """Background thread function to generate the Word document"""
    with app.app_context():
        gen_file = GeneratedFile.query.get(gen_file_id)
        if not gen_file:
            return
        
        gen_file.status = 'generating'
        db.session.commit()
        
        try:
            # Get questions
            questions_dict = {str(q.id): q for q in Question.query.filter(Question.id.in_(question_ids)).all()}
            
            if not questions_dict:
                gen_file.status = 'failed'
                gen_file.error_message = 'No valid questions found'
                db.session.commit()
                return
            
            # Sort questions
            if sort_mode == 'selection':
                questions = [questions_dict[qid] for qid in question_ids if qid in questions_dict]
            else:
                try:
                    sort_config = json.loads(sort_config_str) if sort_config_str else [{"field": "qid", "direction": "asc"}]
                except json.JSONDecodeError:
                    sort_config = [{"field": "qid", "direction": "asc"}]
                questions = list(questions_dict.values())
                questions = apply_multi_sort(questions, sort_config)
            
            # Create document
            doc = create_word_document(
                questions, answer_mode, spacing_config,
                show_qid, show_qid_answer, preferred_language,
                show_correct_pct, answer_preference,
                show_seq_no, show_page_no
            )
            
            # Save document
            output_path = app.config['OUTPUT_PATH']
            filepath = os.path.join(output_path, filename)
            doc.save(filepath)
            
            gen_file.status = 'completed'
            gen_file.completed_at = datetime.utcnow()
            db.session.commit()
            
        except Exception as e:
            gen_file.status = 'failed'
            gen_file.error_message = str(e)
            db.session.commit()


@generator_bp.route('/status/<int:file_id>')
@login_required
def generation_status(file_id):
    """Check generation status (for polling)"""
    gen_file = GeneratedFile.query.get_or_404(file_id)
    
    # Only owner or super admin
    if gen_file.user_id != current_user.id and not current_user.is_super_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    return jsonify({
        'id': gen_file.id,
        'status': gen_file.status,
        'error_message': gen_file.error_message,
        'display_name': gen_file.display_name,
        'filename': gen_file.filename,
    })


@generator_bp.route('/download/<int:file_id>')
@login_required
def download_file(file_id):
    """Download a completed generated file"""
    gen_file = GeneratedFile.query.get_or_404(file_id)
    
    # Only owner or super admin
    if gen_file.user_id != current_user.id and not current_user.is_super_admin:
        flash('Access denied', 'danger')
        return redirect(url_for('user.files'))
    
    if gen_file.status != 'completed':
        flash('File is not ready for download', 'warning')
        return redirect(url_for('user.files'))
    
    output_path = current_app.config['OUTPUT_PATH']
    filepath = os.path.join(output_path, gen_file.filename)
    
    if not os.path.exists(filepath):
        flash('File not found on disk', 'danger')
        return redirect(url_for('user.files'))
    
    return send_file(
        filepath,
        as_attachment=True,
        download_name=gen_file.filename,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )

def get_question_spacing_config(question, spacing_config):
    """
    Get spacing settings for a question based on its type (MC or CQ)
    Returns dict with before_mode, before_lines, after_mode, after_lines
    """
    q_type = question.q_type.upper() if question.q_type else 'CQ'  # Default to CQ if not set
    
    if q_type == 'MC':
        return spacing_config['mc']
    else:
        # CQ or any other type uses CQ settings
        return spacing_config['cq']


def add_before_spacing(doc, spacing, last_had_page_break, is_first):
    """
    Add spacing before a question based on settings.
    Returns whether we effectively have a page break before this question.
    
    Smart logic: If previous question added page break, don't add another one
    even if "start from new page" is selected.
    """
    if is_first:
        # First question - no spacing before
        return False
    
    if spacing['before_mode'] == 'page':
        # Want to start from new page
        if not last_had_page_break:
            # Previous question didn't add page break, so we need one
            doc.add_page_break()
        # Either way, we're now at a new page
        return True
    else:
        # Skip lines before
        for _ in range(spacing['before_lines']):
            doc.add_paragraph()
        return False


def add_after_spacing(doc, spacing):
    """
    Add spacing after a question based on settings.
    Returns whether we added a page break.
    """
    if spacing['after_mode'] == 'page':
        doc.add_page_break()
        return True
    else:
        # Skip lines after
        for _ in range(spacing['after_lines']):
            doc.add_paragraph()
        return False


def _add_page_numbers(doc):
    """Add auto-generated page numbers at the bottom centre of every page."""
    for section in doc.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Build the PAGE field XML: renders as the current page number
        run = paragraph.add_run()
        fldChar_begin = OxmlElement('w:fldChar')
        fldChar_begin.set(qn('w:fldCharType'), 'begin')
        run._r.append(fldChar_begin)
        
        instrText = OxmlElement('w:instrText')
        instrText.set(qn('xml:space'), 'preserve')
        instrText.text = ' PAGE '
        run._r.append(instrText)
        
        fldChar_end = OxmlElement('w:fldChar')
        fldChar_end.set(qn('w:fldCharType'), 'end')
        run._r.append(fldChar_end)
        
        run.font.size = Pt(10)


def create_word_document(questions, answer_mode, spacing_config, show_qid, show_qid_answer, preferred_language='EN', show_correct_pct=False, answer_preference='image_first', show_seq_no=False, show_page_no=False):
    """
    Create Word document with questions
    
    Args:
        questions: List of Question objects
        answer_mode: One of QUE_ONLY, QUE_ANS, QUE_SOL, QUE_THEN_ANS, QUE_THEN_SOL
        spacing_config: Dict with mc and cq sub-dicts containing before_mode, before_lines, after_mode, after_lines
        show_qid: Show question ID for questions
        show_qid_answer: Show question ID for answers/solutions
        preferred_language: 'EN' or 'CH' - order: preferred > BI > other
        show_correct_pct: Show correct percentage with question ID (format: "QID [X%]")
        answer_preference: 'image_first' or 'text_first' - for ANS content, prefer image or text
        show_seq_no: Show sequential question number (1. 2. 3. ...)
        show_page_no: Show page numbers at bottom centre
    """
    doc = Document()
    
    # Set page size to A4
    section = doc.sections[0]
    section.page_height = Cm(29.7)  # A4 height
    section.page_width = Cm(21.0)   # A4 width
    
    # Set narrow margins (0.5 inches = 1.27 cm)
    section.top_margin = Cm(1.27)
    section.bottom_margin = Cm(1.27)
    section.left_margin = Cm(1.27)
    section.right_margin = Cm(1.27)
    
    # Add page numbers if requested
    if show_page_no:
        _add_page_numbers(doc)
    
    source_path = current_app.config['SOURCE_PATH']
    
    # Track if last question ended with a page break
    last_had_page_break = False
    
    # Answer modes:
    # QUE_ONLY - questions only
    # QUE_ANS - question followed by answer
    # QUE_SOL - question followed by solution
    # QUE_THEN_ANS - all questions first, then all answers
    # QUE_THEN_SOL - all questions first, then all solutions
    
    if answer_mode == 'QUE_THEN_ANS':
        # Add all questions first
        for i, question in enumerate(questions):
            spacing = get_question_spacing_config(question, spacing_config)
            
            # Add before spacing
            add_before_spacing(doc, spacing, last_had_page_break, i == 0)
            
            # Add question content
            seq_no = i + 1 if show_seq_no else None
            add_question_content_to_doc(doc, question, 'QUE', show_qid, source_path, preferred_language, show_correct_pct, seq_no=seq_no)
            
            # Add after spacing
            last_had_page_break = add_after_spacing(doc, spacing)
        
        # Then add all answers - always start on new page
        doc.add_page_break()
        heading = doc.add_paragraph()
        heading_run = heading.add_run('ANSWERS')
        heading_run.bold = True
        heading_run.font.size = Pt(16)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph()
        last_had_page_break = False
        
        for i, question in enumerate(questions):
            spacing = get_question_spacing_config(question, spacing_config)
            add_before_spacing(doc, spacing, last_had_page_break, i == 0)
            seq_no = i + 1 if show_seq_no else None
            add_question_content_to_doc(doc, question, 'ANS', show_qid_answer, source_path, preferred_language, show_correct_pct, answer_preference, seq_no=seq_no)
            last_had_page_break = add_after_spacing(doc, spacing)
    
    elif answer_mode == 'QUE_THEN_SOL':
        # Add all questions first
        for i, question in enumerate(questions):
            spacing = get_question_spacing_config(question, spacing_config)
            add_before_spacing(doc, spacing, last_had_page_break, i == 0)
            seq_no = i + 1 if show_seq_no else None
            add_question_content_to_doc(doc, question, 'QUE', show_qid, source_path, preferred_language, show_correct_pct, seq_no=seq_no)
            last_had_page_break = add_after_spacing(doc, spacing)
        
        # Then add all solutions - always start on new page
        doc.add_page_break()
        heading = doc.add_paragraph()
        heading_run = heading.add_run('SOLUTIONS')
        heading_run.bold = True
        heading_run.font.size = Pt(16)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph()
        last_had_page_break = False
        
        for i, question in enumerate(questions):
            spacing = get_question_spacing_config(question, spacing_config)
            add_before_spacing(doc, spacing, last_had_page_break, i == 0)
            seq_no = i + 1 if show_seq_no else None
            add_question_content_to_doc(doc, question, 'SOL', show_qid_answer, source_path, preferred_language, show_correct_pct, seq_no=seq_no)
            last_had_page_break = add_after_spacing(doc, spacing)
    
    else:
        # Add questions with optional answers/solutions
        for i, question in enumerate(questions):
            spacing = get_question_spacing_config(question, spacing_config)
            
            # Add before spacing
            add_before_spacing(doc, spacing, last_had_page_break, i == 0)
            
            # Add question content
            seq_no = i + 1 if show_seq_no else None
            add_question_content_to_doc(doc, question, 'QUE', show_qid, source_path, preferred_language, show_correct_pct, seq_no=seq_no)
            
            # Add answer/solution if requested (no extra spacing between Q and A/S)
            if answer_mode == 'QUE_ANS':
                add_question_content_to_doc(doc, question, 'ANS', show_qid_answer, source_path, preferred_language, show_correct_pct, answer_preference)
            elif answer_mode == 'QUE_SOL':
                add_question_content_to_doc(doc, question, 'SOL', show_qid_answer, source_path, preferred_language, show_correct_pct)
            
            # Add after spacing
            last_had_page_break = add_after_spacing(doc, spacing)
    
    return doc

def add_question_content_to_doc(doc, question, asset_type, show_qid, source_path, preferred_language='EN', show_correct_pct=False, answer_preference='image_first', seq_no=None):
    """
    Add a question (or answer/solution) content to the document.
    Spacing is handled separately by add_before_spacing and add_after_spacing.
    
    Args:
        preferred_language: 'EN' or 'CH' - order: preferred > BI > other
        show_correct_pct: Show correct percentage (format: "QID [X%]" or just "[X%]" if no QID)
                          Only shown for QUE type, not for ANS or SOL
        answer_preference: 'image_first' or 'text_first' - for ANS content, prefer image or text
        seq_no: Sequential question number (int) or None to skip
    """
    # Build heading: "{seq_no}. {QID} [{pct}%]" — each part optional
    has_seq = seq_no is not None
    has_qid = show_qid
    has_pct = show_correct_pct and asset_type == 'QUE' and question.correct_percentage is not None
    
    if has_seq or has_qid or has_pct:
        heading = doc.add_paragraph()
        parts = []
        
        if has_seq:
            parts.append(f"{seq_no}.")
        
        if has_qid:
            parts.append(question.qid)
        
        if has_pct:
            parts.append(f"[{question.correct_percentage}%]")
        
        heading_text = " ".join(parts)
        
        if heading_text:
            heading_run = heading.add_run(heading_text)
            heading_run.bold = True
            heading_run.font.size = Pt(12)
    
    # For ANS type, handle answer_preference (text_first vs image_first)
    if asset_type == 'ANS' and answer_preference == 'text_first':
        # Text first: use answer text if available, fall back to image
        if question.answer:
            para = doc.add_paragraph()
            run = para.add_run(question.answer)
            run.font.size = Pt(11)
            return
        # Fall through to image if no text
    
    # Get all available assets for this question and type
    assets = QuestionAsset.query.filter_by(
        question_id=question.id,
        asset_type=asset_type
    ).all()
    
    if not assets:
        # No image asset found
        if asset_type == 'ANS' and answer_preference == 'image_first' and question.answer:
            # Image first but no image available — fall back to answer text
            para = doc.add_paragraph()
            run = para.add_run(question.answer)
            run.font.size = Pt(11)
            return
        # No asset found, add placeholder
        para = doc.add_paragraph()
        run = para.add_run(f'[{asset_type} not available for {question.qid}]')
        run.italic = True
        return
    
    # Sort assets by language preference and format
    # Language order: preferred > BI > other
    def lang_order(asset):
        if asset.language == preferred_language:
            return 0
        elif asset.language == 'BI':
            return 1
        else:
            return 2
    
    # Format order: IMG before DOC
    def format_order(asset):
        return 0 if asset.file_format == 'IMG' else 1
    
    # Sort by format first (IMG preferred), then by language
    sorted_assets = sorted(assets, key=lambda a: (format_order(a), lang_order(a)))
    asset = sorted_assets[0] if sorted_assets else None
    
    if not asset:
        # No asset found, add placeholder
        para = doc.add_paragraph()
        run = para.add_run(f'[{asset_type} not available for {question.qid}]')
        run.italic = True
        return
    
    # Get file path
    file_path = os.path.join(source_path, asset.file_path)
    
    if not os.path.exists(file_path):
        para = doc.add_paragraph()
        run = para.add_run(f'[File not found: {asset.file_path}]')
        run.italic = True
        return
    
    # Add image to document
    if asset.file_format == 'IMG':
        try:
            # Open image to get dimensions
            img = Image.open(file_path)
            img_width, img_height = img.size
            
            # Calculate size for document
            # Max width: 6 inches (to fit in A4 with margins)
            max_width_inches = 6.0
            max_width_pixels = max_width_inches * 96  # Assuming 96 DPI
            
            if img_width > max_width_pixels:
                # Resize to fit
                scale = max_width_pixels / img_width
                doc_width = Inches(max_width_inches)
            else:
                # Use actual size
                doc_width = Inches(img_width / 96)
            
            # Add picture
            doc.add_picture(file_path, width=doc_width)
            
        except Exception as e:
            para = doc.add_paragraph()
            run = para.add_run(f'[Error loading image: {str(e)}]')
            run.italic = True
    
    elif asset.file_format == 'DOC':
        # For Word files, just add a placeholder for now
        # Full merging with docxcompose can be added later
        para = doc.add_paragraph()
        run = para.add_run(f'[Word document: {asset.file_path}]')
        run.italic = True

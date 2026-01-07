"""
Word document generation module
"""
from flask import Blueprint, render_template, request, send_file, current_app, flash, redirect, url_for, session
from flask_login import login_required
from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from PIL import Image
import os
import json
from datetime import datetime
from app import db
from app.models import Question, QuestionAsset
from app.utils import natural_sort, apply_multi_sort, SORT_FIELDS

generator_bp = Blueprint('generator', __name__, url_prefix='/generate')

@generator_bp.route('/', methods=['GET'])
@login_required
def index():
    """Generation options page"""
    # Get selected question IDs from query params (order preserved)
    question_ids = request.args.getlist('question_ids')
    
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
                          sort_fields=sort_fields)

@generator_bp.route('/create', methods=['POST'])
@login_required
def create_document():
    """Generate Word document from selected questions"""
    
    # Get parameters
    question_ids = request.form.getlist('question_ids')
    sort_mode = request.form.get('sort_mode', 'custom')  # 'selection' or 'custom'
    sort_config_str = request.form.get('sort_config', '')
    answer_mode = request.form.get('answer_mode', 'QUE_ONLY')
    skip_lines = int(request.form.get('skip_lines', 1))
    new_page_per_question = request.form.get('new_page_per_question') == 'on'
    show_qid = request.form.get('show_qid') == 'on'
    
    if not question_ids:
        flash('No questions selected', 'warning')
        return redirect(url_for('dashboard.index'))
    
    # Get questions - preserve the selection order using dict
    questions_dict = {str(q.id): q for q in Question.query.filter(Question.id.in_(question_ids)).all()}
    
    if not questions_dict:
        flash('No valid questions found', 'danger')
        return redirect(url_for('dashboard.index'))
    
    # Sort questions based on mode
    if sort_mode == 'selection':
        # Preserve selection order from the form
        questions = [questions_dict[qid] for qid in question_ids if qid in questions_dict]
    else:
        # Apply custom sort config
        try:
            sort_config = json.loads(sort_config_str) if sort_config_str else [{"field": "qid", "direction": "asc"}]
        except json.JSONDecodeError:
            sort_config = [{"field": "qid", "direction": "asc"}]
        
        questions = list(questions_dict.values())
        questions = apply_multi_sort(questions, sort_config)
    
    # Create document
    try:
        doc = create_word_document(
            questions, 
            answer_mode, 
            skip_lines, 
            new_page_per_question, 
            show_qid
        )
        
        # Save document
        output_path = current_app.config['OUTPUT_PATH']
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'questions_{timestamp}.docx'
        filepath = os.path.join(output_path, filename)
        
        doc.save(filepath)
        
        # Send file
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        
    except Exception as e:
        flash(f'Error generating document: {str(e)}', 'danger')
        return redirect(url_for('dashboard.index'))

def create_word_document(questions, answer_mode, skip_lines, new_page_per_question, show_qid):
    """
    Create Word document with questions
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
    
    source_path = current_app.config['SOURCE_PATH']
    
    # Answer modes:
    # QUE_ONLY - questions only
    # QUE_ANS - question followed by answer
    # QUE_SOL - question followed by solution
    # QUE_THEN_ANS - all questions first, then all answers
    # QUE_THEN_SOL - all questions first, then all solutions
    
    if answer_mode == 'QUE_THEN_ANS':
        # Add all questions first
        for i, question in enumerate(questions):
            if i > 0 and new_page_per_question:
                doc.add_page_break()
            
            add_question_to_doc(doc, question, 'QUE', show_qid, skip_lines, source_path)
        
        # Then add all answers
        doc.add_page_break()
        heading = doc.add_paragraph()
        heading_run = heading.add_run('ANSWERS')
        heading_run.bold = True
        heading_run.font.size = Pt(16)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph()
        
        for i, question in enumerate(questions):
            if i > 0 and new_page_per_question:
                doc.add_page_break()
            
            add_question_to_doc(doc, question, 'ANS', show_qid, skip_lines, source_path)
    
    elif answer_mode == 'QUE_THEN_SOL':
        # Add all questions first
        for i, question in enumerate(questions):
            if i > 0 and new_page_per_question:
                doc.add_page_break()
            
            add_question_to_doc(doc, question, 'QUE', show_qid, skip_lines, source_path)
        
        # Then add all solutions
        doc.add_page_break()
        heading = doc.add_paragraph()
        heading_run = heading.add_run('SOLUTIONS')
        heading_run.bold = True
        heading_run.font.size = Pt(16)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph()
        
        for i, question in enumerate(questions):
            if i > 0 and new_page_per_question:
                doc.add_page_break()
            
            add_question_to_doc(doc, question, 'SOL', show_qid, skip_lines, source_path)
    
    else:
        # Add questions with optional answers/solutions
        for i, question in enumerate(questions):
            if i > 0 and new_page_per_question:
                doc.add_page_break()
            
            # Add question
            add_question_to_doc(doc, question, 'QUE', show_qid, skip_lines, source_path)
            
            # Add answer/solution if requested
            if answer_mode == 'QUE_ANS':
                add_question_to_doc(doc, question, 'ANS', False, skip_lines, source_path)
            elif answer_mode == 'QUE_SOL':
                add_question_to_doc(doc, question, 'SOL', False, skip_lines, source_path)
    
    return doc

def add_question_to_doc(doc, question, asset_type, show_qid, skip_lines, source_path):
    """
    Add a question (or answer/solution) to the document
    """
    # Add QID as heading if requested
    if show_qid:
        heading = doc.add_paragraph()
        heading_run = heading.add_run(question.qid)
        heading_run.bold = True
        heading_run.font.size = Pt(12)
    
    # Get asset (prefer EN, then CH, then BI; prefer IMG over DOC)
    asset = QuestionAsset.query.filter_by(
        question_id=question.id,
        asset_type=asset_type
    ).order_by(
        QuestionAsset.file_format.asc(),  # IMG before DOC
        QuestionAsset.language.desc()      # EN > CH > BI
    ).first()
    
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
    
    # Add spacing
    for _ in range(skip_lines):
        doc.add_paragraph()

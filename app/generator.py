"""
Word document generation module
"""
from flask import Blueprint, render_template, request, send_file, current_app, flash, redirect, url_for
from flask_login import login_required
from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from PIL import Image
import os
from datetime import datetime
from app import db
from app.models import Question, QuestionAsset
from app.utils import natural_sort

generator_bp = Blueprint('generator', __name__, url_prefix='/generate')

@generator_bp.route('/', methods=['GET'])
@login_required
def index():
    """Generation options page"""
    # Get selected question IDs from query params
    question_ids = request.args.getlist('question_ids')
    
    if not question_ids:
        flash('No questions selected', 'warning')
        return redirect(url_for('dashboard.index'))
    
    # Get questions
    questions = Question.query.filter(Question.id.in_(question_ids)).all()
    
    return render_template('generate.html', questions=questions, question_ids=question_ids)

@generator_bp.route('/create', methods=['POST'])
@login_required
def create_document():
    """Generate Word document from selected questions"""
    
    # Get parameters
    question_ids = request.form.getlist('question_ids')
    sort_by = request.form.get('sort_by', 'qid')
    answer_mode = request.form.get('answer_mode', 'QUE_ONLY')
    skip_lines = int(request.form.get('skip_lines', 1))
    new_page_per_question = request.form.get('new_page_per_question') == 'on'
    show_qid = request.form.get('show_qid') == 'on'
    
    if not question_ids:
        flash('No questions selected', 'warning')
        return redirect(url_for('dashboard.index'))
    
    # Get questions
    questions = Question.query.filter(Question.id.in_(question_ids)).all()
    
    if not questions:
        flash('No valid questions found', 'danger')
        return redirect(url_for('dashboard.index'))
    
    # Sort questions
    if sort_by == 'level':
        questions.sort(key=lambda q: (q.level, q.qid))
    elif sort_by == 'year':
        questions.sort(key=lambda q: (q.year if q.year else 0, q.qid))
    elif sort_by == 'topic':
        questions.sort(key=lambda q: (q.major_topic.name if q.major_topic else 'ZZZ', q.qid))
    else:  # qid - natural sort
        questions = natural_sort(questions, key_func=lambda q: q.qid)
    
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

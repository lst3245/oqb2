"""
File scanner/ingestor module for importing questions from file system
"""
import os
import re
import logging
from flask import current_app
from app import db
from app.models import Question, QuestionAsset, Subject

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Regex patterns for parsing filenames
# PP format: MATC_DSE_2025_P2_Q5_EN_QUE.png
PP_PATTERN = re.compile(
    r'^(?P<subj>\w+)_(?P<source>DSE|CE|AL)_(?P<year>\d+)_(?P<paper>P\d+)_(?P<qno>Q\d+)_(?P<lang>EN|CH|BI)_(?P<type>QUE|ANS|SOL)\.(?P<ext>\w+)$'
)

# QB format: MATC_QB_MATHSMART2024_Q1_EN_QUE.png
QB_PATTERN = re.compile(
    r'^(?P<subj>\w+)_(?P<source>QB)_(?P<detail>[^_]+)_(?P<qno>Q\d+)_(?P<lang>EN|CH|BI)_(?P<type>QUE|ANS|SOL)\.(?P<ext>\w+)$'
)

def parse_filename(filename):
    """
    Parse filename using regex patterns
    Returns dict with parsed components or None if no match
    """
    # Try PP pattern first
    match = PP_PATTERN.match(filename)
    if match:
        return match.groupdict()
    
    # Try QB pattern
    match = QB_PATTERN.match(filename)
    if match:
        return match.groupdict()
    
    return None

def construct_qid(parsed):
    """
    Construct question ID from parsed filename
    Example: MATC_DSE_2025_P2_Q5
    """
    subj = parsed['subj']
    source = parsed['source']
    
    if source in ['DSE', 'CE', 'AL']:
        year = parsed['year']
        paper = parsed['paper']
        qno = parsed['qno']
        return f"{subj}_{source}_{year}_{paper}_{qno}"
    else:  # QB
        detail = parsed['detail']
        qno = parsed['qno']
        return f"{subj}_{source}_{detail}_{qno}"

def parse_qno(qno_str):
    """Extract integer from question number like Q5 -> 5"""
    return int(qno_str[1:]) if qno_str.startswith('Q') else int(qno_str)

def extract_folder_metadata(file_path, source_path):
    """
    Extract metadata from folder structure
    For PP: /MATC/PP/DSE/2024/P1/
    For QB: /MATC/QB/Detail/
    """
    # Get relative path from source
    rel_path = os.path.relpath(file_path, source_path)
    parts = rel_path.split(os.sep)
    
    metadata = {}
    
    if len(parts) >= 2:
        metadata['subject'] = parts[0]
        
        if len(parts) >= 3 and parts[1] == 'PP':
            # PP structure
            if len(parts) >= 4:
                metadata['source'] = parts[2]  # DSE/CE/AL
            if len(parts) >= 5:
                metadata['year'] = parts[3]
            if len(parts) >= 6:
                metadata['paper'] = parts[4]
        elif len(parts) >= 3 and parts[1] == 'QB':
            # QB structure
            metadata['source'] = 'QB'
            if len(parts) >= 4:
                metadata['detail'] = parts[2]
    
    return metadata

def determine_file_format(ext):
    """Determine file format from extension"""
    ext_lower = ext.lower()
    if ext_lower in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
        return 'IMG'
    elif ext_lower in ['doc', 'docx']:
        return 'DOC'
    return None

def upsert_question(qid, parsed, folder_meta):
    """
    Insert or update question in database
    Returns question object
    """
    question = Question.query.filter_by(qid=qid).first()
    
    if not question:
        # Create new question
        question = Question(qid=qid)
        
        # Set subject
        question.subject = parsed['subj']
        
        # Set source
        question.source = parsed['source']
        
        # Set year (PP only)
        if parsed['source'] in ['DSE', 'CE', 'AL']:
            question.year = int(parsed['year'])
            question.paper = parsed['paper']
        else:
            question.year = None
            question.paper = None
        
        # Set question number
        question.qno = parse_qno(parsed['qno'])
        
        # Set defaults
        question.q_type = 'CQ'
        question.level = 1
        question.section = None
        
        db.session.add(question)
        logger.info(f"Created new question: {qid}")
    
    return question

def upsert_asset(question, parsed, file_path, source_path):
    """
    Insert or update question asset in database
    """
    asset_type = parsed['type']  # QUE, ANS, SOL
    language = parsed['lang']  # EN, CH, BI
    file_format = determine_file_format(parsed['ext'])
    
    if not file_format:
        logger.warning(f"Unknown file format: {parsed['ext']} for {file_path}")
        return
    
    # Get relative path from source
    rel_path = os.path.relpath(file_path, source_path)
    
    # Check if asset already exists
    asset = QuestionAsset.query.filter_by(
        question_id=question.id,
        asset_type=asset_type,
        language=language,
        file_format=file_format
    ).first()
    
    if asset:
        # Update path
        asset.file_path = rel_path
        logger.info(f"Updated asset: {rel_path}")
    else:
        # Create new asset
        asset = QuestionAsset(
            question_id=question.id,
            asset_type=asset_type,
            file_format=file_format,
            language=language,
            file_path=rel_path
        )
        db.session.add(asset)
        logger.info(f"Created new asset: {rel_path}")

def scan_directory(source_path):
    """
    Walk through source directory and ingest all question files
    """
    if not os.path.exists(source_path):
        logger.error(f"Source path does not exist: {source_path}")
        return
    
    file_count = 0
    skipped_count = 0
    error_log = []
    
    logger.info(f"Starting scan of: {source_path}")
    
    for root, dirs, files in os.walk(source_path):
        for filename in files:
            file_path = os.path.join(root, filename)
            
            # Parse filename
            parsed = parse_filename(filename)
            
            if not parsed:
                skipped_count += 1
                error_log.append(f"Could not parse: {file_path}")
                continue
            
            try:
                # Extract folder metadata
                folder_meta = extract_folder_metadata(file_path, source_path)
                
                # Construct question ID
                qid = construct_qid(parsed)
                
                # Upsert question
                question = upsert_question(qid, parsed, folder_meta)
                
                # Commit to get question.id
                db.session.commit()
                
                # Upsert asset
                upsert_asset(question, parsed, file_path, source_path)
                
                # Commit asset
                db.session.commit()
                
                file_count += 1
                
            except Exception as e:
                db.session.rollback()
                error_msg = f"Error processing {file_path}: {str(e)}"
                logger.error(error_msg)
                error_log.append(error_msg)
    
    logger.info(f"Scan complete. Processed: {file_count}, Skipped: {skipped_count}")
    
    # Write error log
    if error_log:
        with open('ingest_errors.log', 'w', encoding='utf-8') as f:
            f.write('\n'.join(error_log))
        logger.info(f"Wrote {len(error_log)} errors to ingest_errors.log")
    
    return file_count, skipped_count

def ingest_command(source_path=None):
    """
    CLI command to run ingestion
    """
    from app import create_app
    app = create_app()
    
    with app.app_context():
        if not source_path:
            source_path = app.config['SOURCE_PATH']
        
        logger.info(f"Ingesting from: {source_path}")
        file_count, skipped_count = scan_directory(source_path)
        logger.info(f"Ingestion complete: {file_count} files processed, {skipped_count} skipped")

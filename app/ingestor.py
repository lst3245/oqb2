"""
File scanner/ingestor module for importing questions from file system
"""
import os
import re
import logging
from flask import current_app
from natsort import natsorted
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

def determine_question_type(subject, source, paper):
    """
    Determine question type based on subject, source, and paper.
    
    Rules:
    - MATC DSE P1: CQ (Conventional Question)
    - MATC DSE P2: MC (Multiple Choice)
    - MAT1 DSE: CQ
    - MAT2 DSE: CQ
    - All others (other subjects, QB, CE, AL): NULL
    """
    if source == 'DSE':
        if subject == 'MATC':
            if paper == 'P1':
                return 'CQ'
            elif paper == 'P2':
                return 'MC'
        elif subject in ['MAT1', 'MAT2']:
            return 'CQ'
    
    # For all other cases (other subjects, QB, CE, AL), return NULL
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
        
        # Set question type based on subject/source/paper rules
        question.q_type = determine_question_type(parsed['subj'], parsed['source'], parsed.get('paper'))
        
        # Level is always NULL on ingestion (to be tagged manually)
        question.level = None
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
        # Sort files naturally for consistent ordering (Q1, Q2, Q10 not Q1, Q10, Q2)
        for filename in natsorted(files):
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

def sync_database(source_path, dry_run=False):
    """
    Sync database with filesystem - remove orphaned records where files no longer exist.
    
    Args:
        source_path: Base path where question files are stored
        dry_run: If True, only report what would be deleted without actually deleting
    
    Returns:
        Tuple of (deleted_assets, deleted_questions, orphaned_assets_list, orphaned_questions_list)
    """
    if not os.path.exists(source_path):
        logger.error(f"Source path does not exist: {source_path}")
        return 0, 0, [], []
    
    logger.info(f"Starting sync check against: {source_path}")
    if dry_run:
        logger.info("DRY RUN - no changes will be made")
    
    orphaned_assets = []
    orphaned_questions = []
    
    # Check all assets
    all_assets = QuestionAsset.query.all()
    logger.info(f"Checking {len(all_assets)} assets...")
    
    for asset in all_assets:
        full_path = os.path.join(source_path, asset.file_path)
        if not os.path.exists(full_path):
            orphaned_assets.append({
                'id': asset.id,
                'question_id': asset.question_id,
                'file_path': asset.file_path,
                'asset_type': asset.asset_type
            })
            logger.warning(f"Orphaned asset: {asset.file_path} (ID: {asset.id})")
    
    # Delete orphaned assets
    deleted_assets = 0
    if not dry_run:
        for orphan in orphaned_assets:
            asset = QuestionAsset.query.get(orphan['id'])
            if asset:
                db.session.delete(asset)
                deleted_assets += 1
        db.session.commit()
    else:
        deleted_assets = len(orphaned_assets)
    
    # Check for questions with no assets remaining
    all_questions = Question.query.all()
    for question in all_questions:
        if question.assets.count() == 0:
            orphaned_questions.append({
                'id': question.id,
                'qid': question.qid
            })
            logger.warning(f"Question with no assets: {question.qid} (ID: {question.id})")
    
    # Delete orphaned questions
    deleted_questions = 0
    if not dry_run:
        for orphan in orphaned_questions:
            question = Question.query.get(orphan['id'])
            if question:
                db.session.delete(question)
                deleted_questions += 1
        db.session.commit()
    else:
        deleted_questions = len(orphaned_questions)
    
    if dry_run:
        logger.info(f"Sync check complete. Would delete: {deleted_assets} assets, {deleted_questions} questions")
    else:
        logger.info(f"Sync complete. Deleted: {deleted_assets} assets, {deleted_questions} questions")
    
    return deleted_assets, deleted_questions, orphaned_assets, orphaned_questions

def sync_command(source_path=None, dry_run=True):
    """
    CLI command to run database sync
    """
    from app import create_app
    app = create_app()
    
    with app.app_context():
        if not source_path:
            source_path = app.config['SOURCE_PATH']
        
        logger.info(f"Syncing database against: {source_path}")
        deleted_assets, deleted_questions, orphaned_assets, orphaned_questions = sync_database(source_path, dry_run)
        
        if dry_run:
            logger.info(f"DRY RUN complete: Would delete {deleted_assets} assets, {deleted_questions} questions")
            if orphaned_assets:
                logger.info("Orphaned assets:")
                for asset in orphaned_assets:
                    logger.info(f"  - {asset['file_path']}")
            if orphaned_questions:
                logger.info("Orphaned questions (no assets):")
                for q in orphaned_questions:
                    logger.info(f"  - {q['qid']}")
        else:
            logger.info(f"Sync complete: Deleted {deleted_assets} assets, {deleted_questions} questions")

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

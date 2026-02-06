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


# ==================== Streaming Variants (for UI) ====================

def _count_files(source_path):
    """Quick count of total files in a directory tree."""
    total = 0
    for root, dirs, files in os.walk(source_path):
        total += len(files)
    return total


def preview_source_directory(source_path):
    """
    Build a tree summary of the source directory for preview display.
    Returns dict with folder structure and file counts.
    """
    if not os.path.exists(source_path):
        return {'error': f'Source path does not exist: {source_path}', 'folders': [], 'total_files': 0}
    
    folders = []
    total_files = 0
    parseable_files = 0
    
    for root, dirs, files in os.walk(source_path):
        if not files:
            continue
        rel_dir = os.path.relpath(root, source_path)
        if rel_dir == '.':
            rel_dir = ''
        
        file_count = len(files)
        total_files += file_count
        
        # Count parseable files
        parse_count = sum(1 for f in files if parse_filename(f) is not None)
        parseable_files += parse_count
        
        folders.append({
            'path': rel_dir,
            'total': file_count,
            'parseable': parse_count,
            'skipped': file_count - parse_count
        })
    
    # Sort folders naturally
    folders = natsorted(folders, key=lambda f: f['path'])
    
    return {
        'folders': folders,
        'total_files': total_files,
        'parseable_files': parseable_files,
        'skipped_files': total_files - parseable_files
    }


def scan_directory_stream(source_path, base_path=None):
    """
    Generator variant of scan_directory() that yields progress events.
    Each yield is a dict: {type, message, current, total, ...}
    
    Args:
        source_path: Directory to walk/scan (e.g. SOURCE_PATH/MATC for a single subject)
        base_path: Root path used for computing relative file paths stored in DB.
                   If None, defaults to source_path (same behaviour as original scan_directory).
                   When scanning a subject subfolder, pass the parent SOURCE_PATH here
                   so that stored paths include the subject prefix (e.g. MATC/PP/...).
    
    Types: info, success, skip, error, progress, done
    """
    if base_path is None:
        base_path = source_path
    
    if not os.path.exists(source_path):
        yield {'type': 'error', 'message': f'Source path does not exist: {source_path}'}
        return
    
    # First pass: count total files for progress tracking
    total_files = _count_files(source_path)
    yield {'type': 'info', 'message': f'Scanning {source_path}...', 'total': total_files}
    yield {'type': 'info', 'message': f'Found {total_files} total files to process'}
    
    file_count = 0
    skipped_count = 0
    error_count = 0
    new_questions = 0
    new_assets = 0
    updated_assets = 0
    current = 0
    
    for root, dirs, files in os.walk(source_path):
        rel_dir = os.path.relpath(root, source_path)
        if files:
            yield {'type': 'info', 'message': f'Entering directory: {rel_dir} ({len(files)} files)'}
        
        for filename in natsorted(files):
            current += 1
            file_path = os.path.join(root, filename)
            
            # Parse filename
            parsed = parse_filename(filename)
            
            if not parsed:
                skipped_count += 1
                yield {
                    'type': 'skip',
                    'message': f'Skipped (unparseable): {filename}',
                    'current': current,
                    'total': total_files
                }
                continue
            
            try:
                # Extract folder metadata (use base_path so paths include subject prefix)
                folder_meta = extract_folder_metadata(file_path, base_path)
                
                # Construct question ID
                qid = construct_qid(parsed)
                
                # Check if question already exists
                existing_q = Question.query.filter_by(qid=qid).first()
                
                # Upsert question
                question = upsert_question(qid, parsed, folder_meta)
                db.session.commit()
                
                if not existing_q:
                    new_questions += 1
                
                # Check if asset already exists
                asset_type = parsed['type']
                language = parsed['lang']
                file_format = determine_file_format(parsed['ext'])
                existing_asset = None
                if file_format:
                    existing_asset = QuestionAsset.query.filter_by(
                        question_id=question.id,
                        asset_type=asset_type,
                        language=language,
                        file_format=file_format
                    ).first()
                
                # Upsert asset (use base_path so stored rel_path includes subject prefix)
                upsert_asset(question, parsed, file_path, base_path)
                db.session.commit()
                
                if existing_asset:
                    updated_assets += 1
                else:
                    new_assets += 1
                
                file_count += 1
                
                action = 'Updated' if existing_q else 'Created'
                yield {
                    'type': 'success',
                    'message': f'{action}: {qid} [{parsed["type"]}_{parsed["lang"]}]',
                    'current': current,
                    'total': total_files
                }
                
            except Exception as e:
                db.session.rollback()
                error_count += 1
                yield {
                    'type': 'error',
                    'message': f'Error processing {filename}: {str(e)}',
                    'current': current,
                    'total': total_files
                }
    
    # Final summary
    yield {
        'type': 'done',
        'message': (
            f'Ingestion complete! '
            f'Processed: {file_count}, Skipped: {skipped_count}, Errors: {error_count}. '
            f'New questions: {new_questions}, New assets: {new_assets}, Updated assets: {updated_assets}.'
        ),
        'current': total_files,
        'total': total_files,
        'stats': {
            'processed': file_count,
            'skipped': skipped_count,
            'errors': error_count,
            'new_questions': new_questions,
            'new_assets': new_assets,
            'updated_assets': updated_assets
        }
    }


def sync_database_stream(source_path, dry_run=True):
    """
    Generator variant of sync_database() that yields progress events.
    Each yield is a dict: {type, message, ...}
    """
    if not os.path.exists(source_path):
        yield {'type': 'error', 'message': f'Source path does not exist: {source_path}'}
        return
    
    mode_label = 'DRY RUN' if dry_run else 'DELETE MODE'
    yield {'type': 'info', 'message': f'Starting sync check ({mode_label}) against: {source_path}'}
    
    # Check all assets
    all_assets = QuestionAsset.query.all()
    total_assets = len(all_assets)
    yield {'type': 'info', 'message': f'Checking {total_assets} assets for orphaned files...'}
    
    orphaned_assets = []
    for i, asset in enumerate(all_assets):
        full_path = os.path.join(source_path, asset.file_path)
        if not os.path.exists(full_path):
            orphaned_assets.append(asset)
            yield {
                'type': 'warning',
                'message': f'Orphaned asset: {asset.file_path} (Asset ID: {asset.id}, Question ID: {asset.question_id})',
                'current': i + 1,
                'total': total_assets
            }
        
        # Yield progress every 50 assets
        if (i + 1) % 50 == 0:
            yield {
                'type': 'progress',
                'message': f'Checked {i + 1}/{total_assets} assets...',
                'current': i + 1,
                'total': total_assets
            }
    
    yield {'type': 'info', 'message': f'Found {len(orphaned_assets)} orphaned assets'}
    
    # Delete orphaned assets if not dry run
    deleted_assets = 0
    if not dry_run and orphaned_assets:
        yield {'type': 'info', 'message': 'Deleting orphaned assets...'}
        for asset in orphaned_assets:
            try:
                db.session.delete(asset)
                deleted_assets += 1
            except Exception as e:
                yield {'type': 'error', 'message': f'Error deleting asset {asset.id}: {str(e)}'}
        db.session.commit()
        yield {'type': 'success', 'message': f'Deleted {deleted_assets} orphaned assets'}
    
    # Check for questions with no assets
    yield {'type': 'info', 'message': 'Checking for questions with no assets...'}
    all_questions = Question.query.all()
    total_questions = len(all_questions)
    
    orphaned_questions = []
    for i, question in enumerate(all_questions):
        if question.assets.count() == 0:
            orphaned_questions.append(question)
            yield {
                'type': 'warning',
                'message': f'Question with no assets: {question.qid} (ID: {question.id})',
                'current': i + 1,
                'total': total_questions
            }
        
        if (i + 1) % 100 == 0:
            yield {
                'type': 'progress',
                'message': f'Checked {i + 1}/{total_questions} questions...',
                'current': i + 1,
                'total': total_questions
            }
    
    yield {'type': 'info', 'message': f'Found {len(orphaned_questions)} questions with no assets'}
    
    # Delete orphaned questions if not dry run
    deleted_questions = 0
    if not dry_run and orphaned_questions:
        yield {'type': 'info', 'message': 'Deleting orphaned questions...'}
        for question in orphaned_questions:
            try:
                db.session.delete(question)
                deleted_questions += 1
            except Exception as e:
                yield {'type': 'error', 'message': f'Error deleting question {question.qid}: {str(e)}'}
        db.session.commit()
        yield {'type': 'success', 'message': f'Deleted {deleted_questions} orphaned questions'}
    
    # Summary
    if dry_run:
        summary = f'Sync check complete (DRY RUN). Would delete: {len(orphaned_assets)} assets, {len(orphaned_questions)} questions'
    else:
        summary = f'Sync complete. Deleted: {deleted_assets} assets, {deleted_questions} questions'
    
    yield {
        'type': 'done',
        'message': summary,
        'stats': {
            'orphaned_assets': len(orphaned_assets),
            'orphaned_questions': len(orphaned_questions),
            'deleted_assets': deleted_assets if not dry_run else 0,
            'deleted_questions': deleted_questions if not dry_run else 0,
            'dry_run': dry_run
        }
    }


def get_database_stats(source_path=None):
    """
    Gather database health statistics (fast, database-only queries).
    File existence checks are left to the sync operation to avoid slow network I/O.
    Returns a dict with various counts and anomaly information.
    """
    from sqlalchemy import func
    
    stats = {}
    
    # Total counts
    stats['total_questions'] = Question.query.count()
    stats['total_assets'] = QuestionAsset.query.count()
    stats['total_subjects'] = Subject.query.count()
    
    # Per-subject breakdown
    subject_stats = []
    subjects = Subject.query.all()
    for subj in subjects:
        q_count = Question.query.filter_by(subject=subj.id).count()
        a_count = db.session.query(func.count(QuestionAsset.id)).join(Question).filter(Question.subject == subj.id).scalar()
        subject_stats.append({
            'id': subj.id,
            'name': subj.name,
            'questions': q_count,
            'assets': a_count
        })
    stats['subjects'] = subject_stats
    
    # Cap for QID lists returned in anomaly details
    LIST_CAP = 500
    
    # Untagged questions (no major topic)
    untagged_q = Question.query.filter(Question.major_topic_id == None).all()
    stats['untagged_questions'] = len(untagged_q)
    stats['untagged_questions_list'] = [q.qid for q in untagged_q[:LIST_CAP]]
    
    # Questions with no major subtopic
    no_subtopic_q = Question.query.filter(Question.major_subtopic_id == None).all()
    stats['questions_no_subtopic'] = len(no_subtopic_q)
    stats['questions_no_subtopic_list'] = [q.qid for q in no_subtopic_q[:LIST_CAP]]
    
    # Questions with no assets (use a subquery for efficiency)
    questions_with_assets = db.session.query(QuestionAsset.question_id).distinct().subquery()
    no_asset_questions = Question.query.filter(
        ~Question.id.in_(db.session.query(questions_with_assets))
    ).all()
    stats['questions_no_assets'] = len(no_asset_questions)
    stats['questions_no_assets_list'] = [q.qid for q in no_asset_questions[:LIST_CAP]]
    
    # Note: File existence check is intentionally skipped here (too slow for network drives).
    # Use the "Orphaned Records Sync" to perform filesystem checks.
    stats['assets_missing_files'] = None
    
    # Duplicate QID check (should not happen with unique constraint, but just in case)
    dup_qids = db.session.query(Question.qid, func.count(Question.id)).group_by(Question.qid).having(func.count(Question.id) > 1).all()
    stats['duplicate_qids'] = [{'qid': qid, 'count': count} for qid, count in dup_qids]
    
    # Questions without q_type
    no_type_q = Question.query.filter(Question.q_type == None).all()
    stats['questions_no_type'] = len(no_type_q)
    stats['questions_no_type_list'] = [q.qid for q in no_type_q[:LIST_CAP]]
    
    # Questions without level
    no_level_q = Question.query.filter(Question.level == None).all()
    stats['questions_no_level'] = len(no_level_q)
    stats['questions_no_level_list'] = [q.qid for q in no_level_q[:LIST_CAP]]
    
    return stats
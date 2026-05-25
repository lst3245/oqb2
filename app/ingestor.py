"""
File scanner/ingestor module for importing questions from file system
"""
import os
import re
import logging
from datetime import datetime, timedelta
from flask import current_app
from natsort import natsorted
from app import db
from app.models import Question, QuestionAsset, Subject

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Regex patterns for parsing filenames
# PP format: MATC_DSE_2025_P2_Q5_EN_QUE.png  or  MATC_DSE_2025_P2_Q5_EN_QUE_2.png (multi-part)
PP_PATTERN = re.compile(
    r'^(?P<subj>\w+)_(?P<source>DSE|CE|AL)_(?P<year>\d+)_(?P<paper>P[A-Za-z0-9]+)_(?P<qno>Q\d+)_(?P<lang>EN|CH|BI)_(?P<type>QUE|ANS|SOL)(?:_(?P<part>\d+))?\.(?P<ext>\w+)$'
)

# QB format: MATC_QB_MATHSMART2024_Q1_EN_QUE.png  or  ..._QUE_2.png (multi-part)
QB_PATTERN = re.compile(
    r'^(?P<subj>\w+)_(?P<source>QB)_(?P<detail>[^_]+)_(?P<qno>Q\d+)_(?P<lang>EN|CH|BI)_(?P<type>QUE|ANS|SOL)(?:_(?P<part>\d+))?\.(?P<ext>\w+)$'
)

def parse_filename(filename):
    """
    Parse filename using regex patterns
    Returns dict with parsed components or None if no match.
    The 'part' key will be an int (default 1 when not present in filename).
    """
    # Try PP pattern first
    match = PP_PATTERN.match(filename)
    if not match:
        # Try QB pattern
        match = QB_PATTERN.match(filename)
    
    if match:
        parsed = match.groupdict()
        # Convert optional part number to int, default 1
        parsed['part'] = int(parsed['part']) if parsed.get('part') else 1
        return parsed
    
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
    elif ext_lower in ['md', 'markdown']:
        return 'MD'
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
    Insert or update question asset in database.

    Returns:
        (asset, was_created) tuple where `was_created` is True for a brand-new
        row and False if an existing row was updated. Returns (None, False)
        if the file couldn't be ingested (unknown format / unsupported case).
        Callers use the flag to trigger downstream side-effects (e.g. DOC
        thumbnail rendering on first import).
    """
    asset_type = parsed['type']  # QUE, ANS, SOL
    language = parsed['lang']  # EN, CH, BI
    file_format = determine_file_format(parsed['ext'])
    part_number = parsed.get('part', 1)
    
    if not file_format:
        logger.warning(f"Unknown file format: {parsed['ext']} for {file_path}")
        return None, False

    # MD assets are self-contained — multi-part is not supported. Skip silently-but-loudly.
    if file_format == 'MD' and part_number != 1:
        logger.warning(
            f"Markdown asset with part_number={part_number} is not supported "
            f"(MD is single-part only): {file_path}"
        )
        return None, False
    
    # Get relative path from source, normalised to forward slashes for cross-platform consistency
    rel_path = os.path.relpath(file_path, source_path).replace('\\', '/')
    
    # Check if asset already exists (uniqueness includes part_number)
    asset = QuestionAsset.query.filter_by(
        question_id=question.id,
        asset_type=asset_type,
        language=language,
        file_format=file_format,
        part_number=part_number
    ).first()
    
    if asset:
        # Update path
        asset.file_path = rel_path
        logger.info(f"Updated asset: {rel_path}")
        return asset, False
    else:
        # Create new asset
        asset = QuestionAsset(
            question_id=question.id,
            asset_type=asset_type,
            file_format=file_format,
            language=language,
            file_path=rel_path,
            part_number=part_number
        )
        db.session.add(asset)
        logger.info(f"Created new asset: {rel_path}")
        return asset, True

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
                ingested_asset, was_created = upsert_asset(question, parsed, file_path, source_path)

                # Commit asset
                db.session.commit()

                # DOC thumbnail lifecycle on first ingest (best effort — skipped
                # silently if no Flask app context, e.g. early-CLI use).
                if ingested_asset is not None and was_created:
                    try:
                        from app import doc_thumbnails
                        if ingested_asset.file_format == 'DOC':
                            doc_thumbnails.on_doc_asset_created(ingested_asset)
                        elif ingested_asset.file_format == 'IMG':
                            doc_thumbnails.on_img_asset_created(ingested_asset)
                    except Exception as _e:
                        logger.warning(f'Thumbnail lifecycle skipped for {file_path}: {_e}')

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
        # Collect DOC asset IDs ahead of deletion so we can drop their cached
        # thumbnails after the DB commit succeeds.
        doc_ids_to_drop = []
        for orphan in orphaned_assets:
            asset = QuestionAsset.query.get(orphan['id'])
            if asset:
                if asset.file_format == 'DOC':
                    doc_ids_to_drop.append(asset.id)
                db.session.delete(asset)
                deleted_assets += 1
        db.session.commit()

        if doc_ids_to_drop:
            try:
                from app import doc_thumbnails
                for aid in doc_ids_to_drop:
                    doc_thumbnails.on_doc_asset_deleted(aid)
            except Exception as _e:
                logger.warning(f'Thumbnail cleanup skipped during sync: {_e}')
    else:
        deleted_assets = len(orphaned_assets)
    
    # Check for questions with no assets remaining
    # Grace period: skip questions created within the last 24 hours (may be mid-upload via admin)
    grace_cutoff = datetime.utcnow() - timedelta(hours=24)
    all_questions = Question.query.all()
    for question in all_questions:
        if question.assets.count() == 0:
            if question.created_at > grace_cutoff:
                logger.info(f"Skipping recently created question (grace period): {question.qid} (ID: {question.id})")
                continue
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
                part_number = parsed.get('part', 1)
                existing_asset = None
                if file_format:
                    existing_asset = QuestionAsset.query.filter_by(
                        question_id=question.id,
                        asset_type=asset_type,
                        language=language,
                        file_format=file_format,
                        part_number=part_number
                    ).first()
                
                # Upsert asset (use base_path so stored rel_path includes subject prefix)
                ingested_asset, was_created = upsert_asset(question, parsed, file_path, base_path)
                db.session.commit()

                # DOC thumbnail lifecycle on first ingest:
                #   * New DOC asset → schedule thumbnail render (unless IMG wins the slot).
                #   * New IMG asset → drop any stale DOC thumbnail in the same slot.
                if ingested_asset is not None and was_created:
                    try:
                        from app import doc_thumbnails
                        if ingested_asset.file_format == 'DOC':
                            doc_thumbnails.on_doc_asset_created(ingested_asset)
                        elif ingested_asset.file_format == 'IMG':
                            doc_thumbnails.on_img_asset_created(ingested_asset)
                    except Exception as _e:
                        logger.warning(f'Thumbnail lifecycle skipped for {file_path}: {_e}')

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
        # Track DOC asset IDs so we can drop their cached thumbnails after commit.
        doc_ids_to_drop = []
        for asset in orphaned_assets:
            try:
                if asset.file_format == 'DOC':
                    doc_ids_to_drop.append(asset.id)
                db.session.delete(asset)
                deleted_assets += 1
            except Exception as e:
                yield {'type': 'error', 'message': f'Error deleting asset {asset.id}: {str(e)}'}
        db.session.commit()
        yield {'type': 'success', 'message': f'Deleted {deleted_assets} orphaned assets'}

        if doc_ids_to_drop:
            try:
                from app import doc_thumbnails
                for aid in doc_ids_to_drop:
                    doc_thumbnails.on_doc_asset_deleted(aid)
            except Exception as _e:
                yield {'type': 'warning', 'message': f'Thumbnail cleanup skipped: {_e}'}
    
    # Check for questions with no assets
    # Grace period: skip questions created within the last 24 hours (may be mid-upload via admin)
    grace_cutoff = datetime.utcnow() - timedelta(hours=24)
    yield {'type': 'info', 'message': 'Checking for questions with no assets (skipping < 24h old)...'}
    all_questions = Question.query.all()
    total_questions = len(all_questions)
    skipped_grace = 0
    
    orphaned_questions = []
    for i, question in enumerate(all_questions):
        if question.assets.count() == 0:
            if question.created_at > grace_cutoff:
                skipped_grace += 1
                yield {
                    'type': 'info',
                    'message': f'Skipping recently created question (grace period): {question.qid}',
                    'current': i + 1,
                    'total': total_questions
                }
                continue
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
    
    yield {'type': 'info', 'message': f'Found {len(orphaned_questions)} orphaned questions with no assets' +
           (f' (skipped {skipped_grace} recently created)' if skipped_grace else '')}
    
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
            'skipped_grace': skipped_grace,
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
    
    # Questions with no assets — split into "recent" (< 24h, grace period) and "stale"
    grace_cutoff = datetime.utcnow() - timedelta(hours=24)
    questions_with_assets = db.session.query(QuestionAsset.question_id).distinct().subquery()
    no_asset_questions = Question.query.filter(
        ~Question.id.in_(db.session.query(questions_with_assets))
    ).all()
    stale_no_assets = [q for q in no_asset_questions if q.created_at <= grace_cutoff]
    recent_no_assets = [q for q in no_asset_questions if q.created_at > grace_cutoff]
    stats['questions_no_assets'] = len(stale_no_assets)
    stats['questions_no_assets_list'] = [q.qid for q in stale_no_assets[:LIST_CAP]]
    stats['questions_no_assets_recent'] = len(recent_no_assets)
    stats['questions_no_assets_recent_list'] = [q.qid for q in recent_no_assets[:LIST_CAP]]
    
    # Note: File existence check is intentionally skipped here (too slow for network drives).
    # Use the "Orphaned Records Sync" to perform filesystem checks.
    stats['assets_missing_files'] = None
    
    # Duplicate QID check (should not happen with unique constraint, but just in case)
    dup_qids = db.session.query(Question.qid, func.count(Question.id)).group_by(Question.qid).having(func.count(Question.id) > 1).all()
    stats['duplicate_qids'] = [{'qid': qid, 'count': count} for qid, count in dup_qids]
    
    # Duplicate asset check — same (question_id, asset_type, language, file_format, part_number) > 1
    dup_assets = db.session.query(
        QuestionAsset.question_id, QuestionAsset.asset_type,
        QuestionAsset.language, QuestionAsset.file_format,
        QuestionAsset.part_number, func.count(QuestionAsset.id).label('cnt')
    ).group_by(
        QuestionAsset.question_id, QuestionAsset.asset_type,
        QuestionAsset.language, QuestionAsset.file_format,
        QuestionAsset.part_number
    ).having(func.count(QuestionAsset.id) > 1).all()
    dup_asset_details = []
    for row in dup_assets:
        q = Question.query.get(row.question_id)
        dup_asset_details.append(
            f"{q.qid if q else '?'}:{row.asset_type}_{row.language}_P{row.part_number} (×{row.cnt})"
        )
    stats['duplicate_assets'] = len(dup_assets)
    stats['duplicate_assets_list'] = dup_asset_details[:LIST_CAP]
    
    # File-path consistency check — compare stored file_path against expected path
    # Import _build_asset_file_path lazily to avoid circular imports
    path_mismatches = []
    try:
        from app.admin import _build_asset_file_path
        # Only check a reasonable number to keep this fast
        assets_sample = QuestionAsset.query.limit(5000).all()
        for asset in assets_sample:
            question = asset.question
            if not question:
                continue
            try:
                expected = _build_asset_file_path(question, asset)
                stored = asset.file_path.replace('\\', '/')
                if stored != expected:
                    path_mismatches.append(f"{question.qid}: stored={stored} expected={expected}")
            except Exception:
                pass  # Skip if path can't be computed (e.g. missing fields)
    except ImportError:
        pass
    stats['path_mismatches'] = len(path_mismatches)
    stats['path_mismatches_list'] = path_mismatches[:LIST_CAP]
    
    # Questions without q_type
    no_type_q = Question.query.filter(Question.q_type == None).all()
    stats['questions_no_type'] = len(no_type_q)
    stats['questions_no_type_list'] = [q.qid for q in no_type_q[:LIST_CAP]]
    
    # Questions without level
    no_level_q = Question.query.filter(Question.level == None).all()
    stats['questions_no_level'] = len(no_level_q)
    stats['questions_no_level_list'] = [q.qid for q in no_level_q[:LIST_CAP]]
    
    return stats


def find_untracked_files(source_path):
    """
    Scan filesystem for parseable question asset files that have no matching DB record.
    Returns a list of dicts with file info for un-tracked files.
    This is the 'reverse orphan' check — files on disk not in the database.
    """
    if not os.path.exists(source_path):
        return []

    # Build a set of all known file_paths (normalised to forward slashes)
    known_paths = set()
    for asset in QuestionAsset.query.with_entities(QuestionAsset.file_path).all():
        known_paths.add(asset.file_path.replace('\\', '/'))

    untracked = []
    for root, dirs, files in os.walk(source_path):
        for filename in files:
            parsed = parse_filename(filename)
            if not parsed:
                continue  # Not a recognised question file
            file_path = os.path.join(root, filename)
            rel_path = os.path.relpath(file_path, source_path).replace('\\', '/')
            if rel_path not in known_paths:
                qid = construct_qid(parsed)
                untracked.append({
                    'file_path': rel_path,
                    'qid': qid,
                    'filename': filename,
                })
    return untracked
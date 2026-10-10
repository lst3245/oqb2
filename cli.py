"""
CLI commands for the Online Question Bank system
"""
import os
import shutil

import click
from app.ingestor import ingest_command, sync_command

@click.group()
def cli():
    """Online Question Bank CLI"""
    pass

@cli.command()
@click.option('--source-path', default=None, help='Path to source files directory')
def ingest(source_path):
    """Ingest questions from file system into database"""
    click.echo('Starting ingestion...')
    ingest_command(source_path)
    click.echo('Ingestion complete!')

@cli.command()
@click.option('--source-path', default=None, help='Path to source files directory')
@click.option('--dry-run/--no-dry-run', default=True, help='Preview changes without deleting (default: dry-run)')
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
def sync(source_path, dry_run, force):
    """
    Sync database with filesystem - remove orphaned records.
    
    By default, runs in dry-run mode (preview only).
    Use --no-dry-run to actually delete orphaned records.
    """
    if dry_run:
        click.echo('Starting sync check (DRY RUN - no changes will be made)...')
        sync_command(source_path, dry_run=True)
        click.echo('\nDry run complete. Use --no-dry-run to actually delete orphaned records.')
    else:
        if not force:
            click.echo('WARNING: This will permanently delete orphaned records from the database.')
            if not click.confirm('Do you want to continue?'):
                click.echo('Sync cancelled.')
                return
        click.echo('Starting sync (DELETE mode)...')
        sync_command(source_path, dry_run=False)
        click.echo('Sync complete!')

@cli.command('migrate-storage')
@click.option('--dry-run/--no-dry-run', default=True,
              help='Preview the moves without touching disk (default: dry-run).')
@click.option('--old-pdf-source', default=None,
              help='Legacy flat Source_PDF folder to archive '
                   '(default: <dirname(SOURCE_PATH)>/Source_PDF).')
@click.option('--old-thumbnails', default=None,
              help='Legacy DOC thumbnail cache to relocate '
                   '(default: <OUTPUT_PATH>/.doc_thumbnails).')
def migrate_storage(dry_run, old_pdf_source, old_thumbnails):
    """Migrate an existing deployment into the unified Storage tree.

    Performs, in order:

    \b
      1. Create the Storage tree + one Shared/<SUBJECT_ID> folder per subject.
      2. Move the legacy DOC thumbnail cache  -> System/doc_thumbnails.
      3. Move each generated document (+ PDF sibling) -> User/<name>/generated.
      4. Move the legacy flat Source_PDF folder -> Shared/_archive.

    Defaults to a DRY RUN (prints the plan). Re-run with --no-dry-run to apply.
    Safe to run repeatedly: anything already in place is skipped.
    """
    from app import create_app, storage
    from app.models import GeneratedFile, Subject
    from app.generator import _pdf_sibling_filename

    app = create_app()
    with app.app_context():
        tag = 'DRY RUN — no changes' if dry_run else 'APPLYING changes'
        click.echo(f'=== migrate-storage ({tag}) ===\n')

        stats = {'dirs': 0, 'thumbs': 0, 'generated': 0, 'archived': 0,
                 'skipped': 0, 'errors': 0}

        def _ensure_dir(path):
            if os.path.isdir(path):
                return
            click.echo(f'  + mkdir  {path}')
            stats['dirs'] += 1
            if not dry_run:
                os.makedirs(path, exist_ok=True)

        def _move(src, dst):
            """Move src -> dst (file or dir). Skips if dst exists or src missing."""
            if not os.path.exists(src):
                return False
            if os.path.exists(dst):
                click.echo(f'  ~ skip   {src}  (target exists)')
                stats['skipped'] += 1
                return False
            click.echo(f'  > move   {src}\n           -> {dst}')
            if not dry_run:
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                except OSError as e:
                    click.echo(f'    ! error: {e}', err=True)
                    stats['errors'] += 1
                    return False
            return True

        # --- 1. Storage tree + per-subject Shared folders -----------------
        click.echo('[1/4] Storage tree + per-subject Shared folders')
        for path in (storage.storage_path(), storage.shared_path(),
                     storage.system_path(), storage.user_path(),
                     os.path.join(storage.system_path(), 'doc_thumbnails')):
            _ensure_dir(path)
        for subj in Subject.query.order_by(Subject.id).all():
            sd = storage.shared_subject_dir(subj.id)
            if sd:
                _ensure_dir(sd)
        click.echo('')

        # --- 2. DOC thumbnail cache --------------------------------------
        click.echo('[2/4] DOC thumbnail cache')
        old_thumb = old_thumbnails or os.path.join(
            app.config['OUTPUT_PATH'], '.doc_thumbnails')
        new_thumb = os.path.join(storage.system_path(), 'doc_thumbnails')
        old_thumb = os.path.abspath(old_thumb)
        if os.path.isdir(old_thumb) and os.path.normcase(old_thumb) != os.path.normcase(new_thumb):
            _ensure_dir(new_thumb)
            for name in os.listdir(old_thumb):
                if _move(os.path.join(old_thumb, name), os.path.join(new_thumb, name)):
                    stats['thumbs'] += 1
        else:
            click.echo('  (nothing to move — already at System/doc_thumbnails or absent)')
        click.echo('')

        # --- 3. Generated documents --------------------------------------
        click.echo('[3/4] Generated documents -> User/<name>/generated')
        legacy_out = os.path.abspath(app.config['OUTPUT_PATH'])
        for gf in GeneratedFile.query.all():
            owner = getattr(gf, 'user', None)
            if owner is None or not gf.filename:
                continue
            dest_dir = storage.user_generated_dir(owner)
            if os.path.normcase(dest_dir) == os.path.normcase(legacy_out):
                continue
            names = [gf.filename]
            sib = _pdf_sibling_filename(gf.filename)
            if sib:
                names.append(sib)
            for name in names:
                src = os.path.join(legacy_out, name)
                if _move(src, os.path.join(dest_dir, name)):
                    stats['generated'] += 1
        click.echo('')

        # --- 4. Legacy flat Source_PDF -> Shared/_archive ----------------
        click.echo('[4/4] Legacy Source_PDF -> Shared/_archive')
        old_pdf = old_pdf_source or os.path.join(
            os.path.dirname(app.config['SOURCE_PATH']), 'Source_PDF')
        old_pdf = os.path.abspath(old_pdf)
        archive = storage.shared_archive_dir()
        if os.path.isdir(old_pdf) and os.path.normcase(old_pdf) != os.path.normcase(storage.shared_path()):
            _ensure_dir(archive)
            for name in os.listdir(old_pdf):
                if _move(os.path.join(old_pdf, name), os.path.join(archive, name)):
                    stats['archived'] += 1
        else:
            click.echo(f'  (no legacy Source_PDF at {old_pdf})')
        click.echo('')

        click.echo('=== Summary ===')
        click.echo(f'  dirs created     : {stats["dirs"]}')
        click.echo(f'  thumbnails moved : {stats["thumbs"]}')
        click.echo(f'  generated moved  : {stats["generated"]}')
        click.echo(f'  archived items   : {stats["archived"]}')
        click.echo(f'  skipped          : {stats["skipped"]}')
        click.echo(f'  errors           : {stats["errors"]}')
        if dry_run:
            click.echo('\nDry run complete. Re-run with --no-dry-run to apply.')
        else:
            click.echo('\nMigration complete.')


@cli.command('set-block-end')
@click.argument('block_qid', required=False)
@click.argument('end_part', required=False)
@click.option('--from-csv', 'csv_path', default=None, type=click.Path(exists=True, dir_okay=False),
              help='CSV of block_qid,end_part rows (blank end_part clears the end).')
@click.option('--dry-run/--no-dry-run', default=True,
              help='Print the parts that would move without writing (default: dry-run).')
def set_block_end_cmd(block_qid, end_part, csv_path, dry_run):
    """Set the last part a shared block (ADR-015) owns, e.g.

    \b
      python cli.py set-block-end ICT_DSE_2017_P2D_Q4~b b
      python cli.py set-block-end --from-csv ends.csv
      python cli.py set-block-end ICT_DSE_2017_P2D_Q4~b none   (clear)

    Parts after the end move from the block to its lettered parent. Defaults
    to a DRY RUN; re-run with --no-dry-run to apply. Idempotent.
    """
    from app.hierarchy import (HierarchyError, normalize_block_end,
                               parse_block_end_rows, set_block_end)
    if csv_path:
        if block_qid or end_part:
            raise click.UsageError('Give either BLOCK_QID END_PART or --from-csv, not both.')
        with open(csv_path, newline='', encoding='utf-8-sig') as fh:
            rows = parse_block_end_rows(fh)
    elif block_qid and end_part:
        rows = [(block_qid.strip(), normalize_block_end(end_part))]
    else:
        raise click.UsageError('Give BLOCK_QID END_PART (END_PART "none" clears) or --from-csv.')

    from app import create_app, db
    from app.models import Question

    app = create_app()
    with app.app_context():
        tag = 'DRY RUN — no changes' if dry_run else 'APPLYING changes'
        click.echo(f'=== set-block-end ({tag}) ===\n')
        stats = {'changed': 0, 'unchanged': 0, 'moves': 0, 'errors': 0}
        for qid, end in rows:
            block = Question.query.filter_by(qid=qid).first()
            if block is None:
                click.echo(f'! {qid}: not found', err=True)
                stats['errors'] += 1
                continue
            try:
                res = set_block_end(block, end, dry_run=dry_run)
            except HierarchyError as e:
                db.session.rollback()
                click.echo(f'! {qid}: {e}', err=True)
                stats['errors'] += 1
                continue
            if not res['changed']:
                click.echo(f'= {qid}: already ends after {res["end"] or "(next block / end)"}')
                stats['unchanged'] += 1
                continue
            click.echo(f'* {qid}: end {res["old_end"] or "none"} -> {res["end"] or "none"}')
            for m in res['moves']:
                click.echo(f'    move {m["qid"]}: {m["from_qid"]} -> {m["to_qid"]}')
            stats['changed'] += 1
            stats['moves'] += len(res['moves'])
            if not dry_run:
                db.session.commit()
        click.echo(f'\nchanged {stats["changed"]}, unchanged {stats["unchanged"]}, '
                   f'parts moved {stats["moves"]}, errors {stats["errors"]}')
        if dry_run:
            db.session.rollback()
            click.echo('Dry run complete. Re-run with --no-dry-run to apply.')


if __name__ == '__main__':
    cli()

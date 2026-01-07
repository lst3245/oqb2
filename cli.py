"""
CLI commands for the Online Question Bank system
"""
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

if __name__ == '__main__':
    cli()

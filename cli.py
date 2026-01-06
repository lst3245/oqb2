"""
CLI commands for the Online Question Bank system
"""
import click
from app.ingestor import ingest_command

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

if __name__ == '__main__':
    cli()

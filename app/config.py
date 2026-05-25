"""
Configuration settings for the Flask application
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Base configuration class"""
    
    # Flask settings
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Database settings
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_NAME = os.getenv('DB_NAME', 'oqb2')
    
    # SQLAlchemy settings
    SQLALCHEMY_DATABASE_URI = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}?charset=utf8mb4'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }
    
    # Application paths
    SOURCE_PATH = os.getenv('SOURCE_PATH', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Source'))
    OUTPUT_PATH = os.getenv('OUTPUT_PATH', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output'))
    
    # Pagination
    QUESTIONS_PER_PAGE = 20

    # Markdown source format
    # Path to pandoc binary used for MD -> docx during generation.
    # Defaults to looking up `pandoc` on PATH.
    PANDOC_PATH = os.getenv('PANDOC_PATH', 'pandoc')
    # Hard cap for an individual .md asset (base64-embedded images can balloon size).
    MD_MAX_SIZE_BYTES = int(os.getenv('MD_MAX_SIZE_BYTES', str(5 * 1024 * 1024)))

    # DOC source format + PDF output (Word COM automation, Windows-only).
    # Per-job watchdog: kill WINWORD.EXE if a single Word call exceeds this.
    WORD_COM_TIMEOUT = int(os.getenv('WORD_COM_TIMEOUT', '300'))
    # Bounded wait for the global Word COM lock when another generation is running.
    WORD_COM_LOCK_TIMEOUT = int(os.getenv('WORD_COM_LOCK_TIMEOUT', '600'))
    # Where to store cached DOC asset thumbnails (PNG, keyed by asset_id).
    DOC_THUMBNAIL_PATH = os.getenv(
        'DOC_THUMBNAIL_PATH',
        os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', '.doc_thumbnails')
    )
    # Thumbnail render width in pixels (~A4 page width at 96 DPI).
    DOC_THUMBNAIL_WIDTH = int(os.getenv('DOC_THUMBNAIL_WIDTH', '1000'))

    # Thumbnail post-processing — runtime-tunable via the Admin → System
    # Settings page; the values below are .env bootstrap defaults only.
    THUMBNAIL_TRANSPARENT = os.getenv('THUMBNAIL_TRANSPARENT', '0').strip().lower() in ('1', 'true', 'yes', 'on')
    THUMBNAIL_WHITENESS_THRESHOLD = int(os.getenv('THUMBNAIL_WHITENESS_THRESHOLD', '250'))
    THUMBNAIL_BOTTOM_PADDING_PX = int(os.getenv('THUMBNAIL_BOTTOM_PADDING_PX', '24'))
    THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP = os.getenv('THUMBNAIL_SYMMETRIC_HORIZONTAL_CROP', '0').strip().lower() in ('1', 'true', 'yes', 'on')

    # Batch IMG generation defaults — runtime-tunable via System Settings.
    BATCH_IMG_DEFAULT_WIDTH = int(os.getenv('BATCH_IMG_DEFAULT_WIDTH', '1500'))
    BATCH_IMG_DEFAULT_STITCH = os.getenv('BATCH_IMG_DEFAULT_STITCH', '1').strip().lower() in ('1', 'true', 'yes', 'on')

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

    # AI Tools (LLM proofreading / markdown generation).
    # Global fallback API key used when a configured endpoint has no key of
    # its own (hybrid model — see app/llm_client.py). Stays in .env only.
    LLM_API_KEY = os.getenv('LLM_API_KEY', '')
    # Optional dedicated secret for encrypting UI-entered endpoint keys at
    # rest (Fernet). When blank, a key is derived from SECRET_KEY instead.
    LLM_KEY_SECRET = os.getenv('LLM_KEY_SECRET', '')
    # Long edge (px) to downscale images to before sending to the LLM —
    # runtime-tunable via System Settings.
    LLM_IMAGE_MAX_DIM = int(os.getenv('LLM_IMAGE_MAX_DIM', '1600'))
    # Master on/off switch for the AI Tools admin feature.
    AI_TOOLS_ENABLED = os.getenv('AI_TOOLS_ENABLED', '1').strip().lower() in ('1', 'true', 'yes', 'on')
    # Name of the LLM endpoint to use for the dashboard Explain tutor chat.
    # Empty string = auto-select the first enabled, vision-capable endpoint
    # by sort_order then name (original behaviour).
    EXPLAIN_DEFAULT_LLM = os.getenv('EXPLAIN_DEFAULT_LLM', '')
    # Per-feature timeout override for interactive LLM chat (the dashboard
    # Explain tutor and the LLM Endpoints chat console). Reasoning models
    # can take several minutes thinking before they emit any visible output,
    # so this defaults much higher than the per-endpoint `timeout_seconds`
    # (which is fine for fast batch ops like proofreading). Set to 0 to fall
    # back to the endpoint's own timeout.
    LLM_CHAT_TIMEOUT_SECONDS = int(os.getenv('LLM_CHAT_TIMEOUT_SECONDS', '600'))

    # PDF Batch Import — width (px) to rasterise uploaded PDF pages to. The
    # high-res page PNGs are what per-question crops are cut from (the image
    # sent to the LLM is downscaled separately to LLM_IMAGE_MAX_DIM), so a
    # larger value yields sharper crops at the cost of disk/CPU.
    PDF_IMPORT_RASTER_WIDTH = int(os.getenv('PDF_IMPORT_RASTER_WIDTH', '1700'))
    # PDF Batch Import — how the vision model orders bounding-box coordinates.
    # 'xyxy' = [x1,y1,x2,y2] (Qwen and most models); 'yxyx' = [y1,x1,y2,x2]
    # (Gemma / Gemini / PaliGemma family). Range (0..1 vs 0..1000 vs pixels) is
    # auto-detected; only the axis ORDER is ambiguous, so it's configurable.
    PDF_IMPORT_COORD_ORDER = os.getenv('PDF_IMPORT_COORD_ORDER', 'xyxy').strip().lower()
    # PDF Batch Import — whether the "Auto-deskew scans" checkbox starts ticked.
    # Deskew straightens skewed/rotated scanned pages during staging (NumPy).
    PDF_IMPORT_DESKEW_DEFAULT = os.getenv('PDF_IMPORT_DESKEW_DEFAULT', '1').strip().lower() in ('1', 'true', 'yes', 'on')
    # PDF Batch Import — default detection method pre-selected in Setup:
    # 'llm' (model draws boxes), 'refine' (LLM boxes + CV edge-snap), or
    # 'segment' (LLM start-anchors + CV projection segmentation).
    PDF_IMPORT_DEFAULT_METHOD = os.getenv('PDF_IMPORT_DEFAULT_METHOD', 'llm').strip().lower()

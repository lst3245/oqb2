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
    # SOURCE_PATH holds the question-bank assets (images / DOC / MD). It is the
    # canonical, read-mostly library and is unchanged by the storage refactor.
    SOURCE_PATH = os.getenv('SOURCE_PATH', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Source'))

    # ---- Unified Storage tree (Shared / System / User) -------------------
    # STORAGE_PATH is the parent of three sibling roots. Each child is
    # overridable independently via its own env var, but by default they all
    # live under STORAGE_PATH so a deployment only needs to set STORAGE_PATH
    # (e.g. STORAGE_PATH=Q:\Storage beside SOURCE_PATH=Q:\Source).
    #   Shared  — per-subject shared files (replaces the old flat Source_PDF);
    #             role-gated subfolder per subject.
    #   System  — server-internal caches/temp: DOC thumbnails, PDF import +
    #             Toolbox staging.
    #   User    — per-user home folders, each with a `generated/` subfolder for
    #             that user's generated documents.
    STORAGE_PATH = os.getenv('STORAGE_PATH', os.path.join(os.path.dirname(SOURCE_PATH), 'Storage'))
    SHARED_PATH = os.getenv('SHARED_PATH', os.path.join(STORAGE_PATH, 'Shared'))
    SYSTEM_PATH = os.getenv('SYSTEM_PATH', os.path.join(STORAGE_PATH, 'System'))
    USER_PATH = os.getenv('USER_PATH', os.path.join(STORAGE_PATH, 'User'))

    # OUTPUT_PATH is now a LEGACY base, kept for backward compatibility: it is
    # the fallback location for generated files created before the per-user
    # relocation, and still the default if STORAGE_PATH is not configured.
    OUTPUT_PATH = os.getenv('OUTPUT_PATH', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output'))

    # Server-side PDF library the PDF Batch Import / Toolbox tools pick from.
    # Now defaults to the Shared tree (per-subject subfolders). A legacy
    # `Source_PDF` deployment keeps working by setting PDF_SOURCE_PATH in .env.
    PDF_SOURCE_PATH = os.getenv('PDF_SOURCE_PATH', SHARED_PATH)
    
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
    # Defaults under the System tree so caches/temp live together, separate
    # from generated documents and shared files.
    DOC_THUMBNAIL_PATH = os.getenv(
        'DOC_THUMBNAIL_PATH',
        os.path.join(SYSTEM_PATH, 'doc_thumbnails')
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
    # Per-feature default LLM endpoints (by endpoint name). Empty = auto-pick
    # the first enabled vision-capable endpoint by sort_order, name. These
    # are pre-selected in the corresponding UI dropdowns and used as the
    # server-side fallback when no `endpoint_id` is supplied.
    AUTOTAG_DEFAULT_LLM = os.getenv('AUTOTAG_DEFAULT_LLM', '')
    MD_DEFAULT_LLM = os.getenv('MD_DEFAULT_LLM', '')
    CHECK_DEFAULT_LLM = os.getenv('CHECK_DEFAULT_LLM', '')
    PDF_IMPORT_DEFAULT_LLM = os.getenv('PDF_IMPORT_DEFAULT_LLM', '')
    # Per-feature timeout override for interactive LLM chat (the dashboard
    # Explain tutor and the LLM Endpoints chat console). Reasoning models
    # can take several minutes thinking before they emit any visible output,
    # so this defaults much higher than the per-endpoint `timeout_seconds`
    # (which is fine for fast batch ops like proofreading). Set to 0 to fall
    # back to the endpoint's own timeout.
    LLM_CHAT_TIMEOUT_SECONDS = int(os.getenv('LLM_CHAT_TIMEOUT_SECONDS', '600'))
    # Default reasoning effort for endpoints that inherit ('' = inherit).
    # 'off' = omit reasoning params; 'low'/'medium'/'high' enable thinking.
    LLM_REASONING_EFFORT_DEFAULT = os.getenv('LLM_REASONING_EFFORT_DEFAULT', 'off').strip().lower()
    # Default reasoning summary for Responses API ('auto' or 'none').
    LLM_REASONING_SUMMARY_DEFAULT = os.getenv('LLM_REASONING_SUMMARY_DEFAULT', 'auto').strip().lower()

    # PDF Batch Import — width (px) to rasterise uploaded PDF pages to. The
    # high-res page PNGs are what per-question crops are cut from (the image
    # sent to the LLM is downscaled separately to LLM_IMAGE_MAX_DIM), so a
    # larger value yields sharper crops at the cost of disk/CPU.
    PDF_IMPORT_RASTER_WIDTH = int(os.getenv('PDF_IMPORT_RASTER_WIDTH', '1700'))
    # PDF Batch Import — how many pages to rasterise concurrently during the
    # "Load PDF" staging step. Page rendering (PyMuPDF) + image filters (deskew
    # etc.) are CPU-bound and independent per page, so this fans them across
    # cores; capped by the machine's CPU count at runtime. 1 = sequential.
    PDF_IMPORT_RASTER_WORKERS = int(os.getenv('PDF_IMPORT_RASTER_WORKERS', '4'))
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
    # PDF Batch Import — safety margin (% of page) added around every detected
    # box before the final crop. Larger = safer against clipping content; the
    # white-trim still removes excess blank space afterwards, so content stays
    # the focus.
    PDF_IMPORT_CROP_PAD_PCT = float(os.getenv('PDF_IMPORT_CROP_PAD_PCT', '0.6'))
    # PDF Batch Import — "refine" method: how far (% of page) to expand each
    # LLM box into a search window before snapping back to content. Larger
    # recovers more chopped text / nearby figures / marks but risks merging
    # adjacent questions.
    PDF_IMPORT_REFINE_GROW_PCT = float(os.getenv('PDF_IMPORT_REFINE_GROW_PCT', '3.5'))
    # PDF Batch Import — "refine"/"segment" methods: padding (% of page) kept
    # around the detected content edges.
    PDF_IMPORT_ASSIST_PAD_PCT = float(os.getenv('PDF_IMPORT_ASSIST_PAD_PCT', '0.6'))
    # PDF Batch Import — whether the "Trim whitespace" checkbox starts ticked.
    # When trimming is on, each imported crop is tightened to its non-white
    # content (drops blank answer space / loose margins). When off, the crop
    # respects the selected bounding box exactly (only the crop safety margin
    # is applied). Users can toggle it per run.
    PDF_IMPORT_TRIM_WHITE_DEFAULT = os.getenv('PDF_IMPORT_TRIM_WHITE_DEFAULT', '0').strip().lower() in ('1', 'true', 'yes', 'on')
    # PDF Batch Import — whether the "Uniform width per side" checkbox starts
    # ticked. Users can toggle it per run.
    PDF_IMPORT_UNIFORM_WIDTH_DEFAULT = os.getenv('PDF_IMPORT_UNIFORM_WIDTH_DEFAULT', '1').strip().lower() in ('1', 'true', 'yes', 'on')

    # PDF Toolbox — width (px) used to rasterise pages for the preview thumbnails
    # and any raster-only processing in the working set.
    TOOLBOX_RASTER_WIDTH = int(os.getenv('TOOLBOX_RASTER_WIDTH', '1700'))
    # PDF Toolbox — width (px) of rasterised pages on EXPORT (only pages that
    # carry a raster-only op are rasterised; vector pages stay lossless).
    # Used only as a fallback for pages staged before the per-page DPI existed.
    TOOLBOX_EXPORT_WIDTH = int(os.getenv('TOOLBOX_EXPORT_WIDTH', '2200'))
    # PDF Toolbox — default processing/export resolution in DPI, chosen per
    # batch in step 2. DPI is page-size independent, so it gives a predictable
    # quality for both A4 and A3 (e.g. 200 DPI → A4 ≈ 1654 px wide, A3 ≈ 2339
    # px wide). 150 = draft/screen, 200 = normal (recommended), 300 = print.
    TOOLBOX_DEFAULT_DPI = int(os.getenv('TOOLBOX_DEFAULT_DPI', '200'))
    # PDF Toolbox — subfolder (under PDF_SOURCE_PATH) where "Save to server"
    # writes assembled PDFs/ZIPs so Batch PDF Import can pick them.
    TOOLBOX_SAVE_SUBDIR = os.getenv('TOOLBOX_SAVE_SUBDIR', 'Saved')
    # PDF Toolbox — Tesseract executable for the Find & Mark OCR engine.
    # Blank = auto-detect (common Windows install paths, then PATH).
    TESSERACT_CMD = os.getenv('TESSERACT_CMD', '')
    # PDF Toolbox — DPI used to rasterise pages for OCR word extraction in
    # Find & Mark (higher = better recognition of small print, slower).
    TOOLBOX_OCR_DPI = int(os.getenv('TOOLBOX_OCR_DPI', '300'))
    # PDF Toolbox — number of pages OCR'd / extracted concurrently in Find &
    # Mark when "Parallel" is on (Tesseract runs as a subprocess, so threads
    # scale across CPU cores). Capped by the CPU count at runtime.
    TOOLBOX_OCR_WORKERS = int(os.getenv('TOOLBOX_OCR_WORKERS', '4'))
    # PDF Toolbox — when a page's upright OCR pass finds little text, retry at
    # 90/180/270° and keep the best (handles sideways scans). Costs up to 3
    # extra OCR passes on sparse/rotated pages only.
    TOOLBOX_OCR_AUTO_ORIENT = os.getenv(
        'TOOLBOX_OCR_AUTO_ORIENT', '1').strip().lower() in ('1', 'true', 'yes', 'on')

    # Markup — longest edge (world units) for resolution-normalized image imports.
    MARKUP_NORMALIZED_MAX_DIM = int(os.getenv('MARKUP_NORMALIZED_MAX_DIM', '2400'))

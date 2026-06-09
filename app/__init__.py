"""
Flask application factory
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    """Create and configure the Flask application"""
    # Get the parent directory (project root) since this file is in app/
    import os
    basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    
    # Create Flask app with correct template and static folders
    app = Flask(__name__,
                template_folder=os.path.join(basedir, 'templates'),
                static_folder=os.path.join(basedir, 'static'))
    
    # Load configuration
    from app.config import Config
    app.config.from_object(Config)
    
    # Initialize extensions with app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    
    # User loader for Flask-Login
    from app.models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # Register blueprints
    from app.auth import auth_bp
    from app.dashboard import dashboard_bp
    from app.admin import admin_bp
    from app.generator import generator_bp
    from app.user import user_bp
    from app.toolbox import toolbox_bp
    from app.pwa import pwa_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(generator_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(toolbox_bp)
    app.register_blueprint(pwa_bp)

    # Expose the canonical asset-version list to every template (including
    # viewer.html, which does not extend base.html). Templates build their
    # version UIs from these instead of re-hardcoding EN/CH/BI/ENO/CHO.
    @app.context_processor
    def _inject_versions():
        from app.utils import VERSIONS, VERSION_LABELS, DEFAULT_VERSION_PRIORITY
        return {
            'OQB_VERSIONS': VERSIONS,
            'OQB_VERSION_LABELS': VERSION_LABELS,
            'OQB_DEFAULT_VERSION_PRIORITY': DEFAULT_VERSION_PRIORITY,
        }
    
    # Create output directory if it doesn't exist
    os.makedirs(app.config['OUTPUT_PATH'], exist_ok=True)
    # DOC thumbnail cache directory
    if app.config.get('DOC_THUMBNAIL_PATH'):
        os.makedirs(app.config['DOC_THUMBNAIL_PATH'], exist_ok=True)
    
    # Startup cleanup: mark any stale 'generating' files as failed
    with app.app_context():
        try:
            from app.models import GeneratedFile
            stale = GeneratedFile.query.filter(GeneratedFile.status.in_(['pending', 'generating'])).all()
            for gf in stale:
                gf.status = 'failed'
                gf.error_message = 'Server restarted during generation'
            if stale:
                db.session.commit()
        except Exception:
            db.session.rollback()  # Table may not exist yet (pre-migration)

    # Auto-create the system_settings + prompt_overrides tables if missing
    # so admins running an upgraded build don't have to re-run init_db.py
    # just for these. Other tables predate this feature and are already
    # present.
    with app.app_context():
        try:
            from app.models import SystemSetting, PromptOverride
            SystemSetting.__table__.create(db.engine, checkfirst=True)
            PromptOverride.__table__.create(db.engine, checkfirst=True)
        except Exception:
            pass  # broken DB connection / pre-init; settings will fall back to .env

    # Auto-create the My Files sections + shares tables and patch
    # generated_files with the new columns. Same idempotent upgrade pattern
    # as the system_settings block above — missing prerequisites are
    # tolerated so a fresh deploy is still bootstrapped by init_db.py.
    with app.app_context():
        try:
            from app.models import FileSection, FileShare
            FileSection.__table__.create(db.engine, checkfirst=True)
            FileShare.__table__.create(db.engine, checkfirst=True)
        except Exception:
            pass

        # Add section_id / manual_position to generated_files if absent
        # (MariaDB / MySQL syntax — INFORMATION_SCHEMA lookup keeps this
        # idempotent and avoids needing Alembic for a single column add).
        try:
            from sqlalchemy import text
            with db.engine.begin() as conn:
                existing = {row[0] for row in conn.execute(text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'generated_files'"
                ))}
                if 'section_id' not in existing:
                    conn.execute(text(
                        "ALTER TABLE generated_files ADD COLUMN section_id INT NULL, "
                        "ADD INDEX ix_generated_files_section_id (section_id), "
                        "ADD CONSTRAINT fk_generated_files_section "
                        "FOREIGN KEY (section_id) REFERENCES file_sections(id) ON DELETE SET NULL"
                    ))
                if 'manual_position' not in existing:
                    conn.execute(text(
                        "ALTER TABLE generated_files ADD COLUMN manual_position INT NOT NULL DEFAULT 0"
                    ))
        except Exception:
            pass  # pre-init DB / non-MySQL backend; init_db.py will handle creation

        # Rename question_assets.language -> version and widen the enum to
        # include ENO / CHO. Same idempotent INFORMATION_SCHEMA pattern as
        # above so existing deployments upgrade without running
        # migrate_versions.py by hand. CHANGE COLUMN carries the unique index
        # over to the new column name automatically.
        try:
            from sqlalchemy import text
            with db.engine.begin() as conn:
                row = conn.execute(text(
                    "SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'question_assets' "
                    "AND COLUMN_NAME = 'language'"
                )).first()
                if row is not None:
                    conn.execute(text(
                        "ALTER TABLE question_assets CHANGE COLUMN language version "
                        "ENUM('EN','CH','BI','ENO','CHO') NOT NULL"
                    ))
                else:
                    vrow = conn.execute(text(
                        "SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'question_assets' "
                        "AND COLUMN_NAME = 'version'"
                    )).first()
                    if vrow is not None and "'ENO'" not in (vrow[0] or '').upper():
                        conn.execute(text(
                            "ALTER TABLE question_assets MODIFY COLUMN version "
                            "ENUM('EN','CH','BI','ENO','CHO') NOT NULL"
                        ))
        except Exception:
            pass  # pre-init DB / non-MySQL backend; migrate_versions.py covers it

        # AI Tools: create the llm_configs table and add the per-asset check
        # columns (check_state / check_result / checked_at) if absent. Same
        # idempotent pattern so existing deployments upgrade automatically.
        try:
            from app.models import LLMConfig
            LLMConfig.__table__.create(db.engine, checkfirst=True)
        except Exception:
            pass

        # Parallel batch ops: add llm_configs.kind / max_concurrency if absent,
        # then back-fill a 'cloud' classification (with a sane concurrency) for
        # well-known hosted API hosts so existing endpoints light up parallel
        # mode automatically. Admins can override per-endpoint afterwards.
        try:
            from sqlalchemy import text
            _CLOUD_HOSTS = (
                'api.openai.com', 'openrouter.ai', 'api.poe.com',
                'generativelanguage.googleapis.com', 'api.anthropic.com',
                'api.groq.com', 'api.together.xyz', 'api.deepseek.com',
                'api.mistral.ai', 'api.x.ai',
            )
            with db.engine.begin() as conn:
                cols = {row[0] for row in conn.execute(text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'llm_configs'"
                ))}
                added = False
                if 'kind' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN kind VARCHAR(10) "
                        "NOT NULL DEFAULT 'local'"
                    ))
                    added = True
                if 'max_concurrency' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN max_concurrency INT "
                        "NOT NULL DEFAULT 1"
                    ))
                    added = True
                if 'service_tier' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN service_tier "
                        "VARCHAR(20) NOT NULL DEFAULT ''"
                    ))
                if 'service_tier_batch' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN service_tier_batch "
                        "VARCHAR(20) NOT NULL DEFAULT ''"
                    ))
                if 'api_protocol' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN api_protocol "
                        "VARCHAR(12) NOT NULL DEFAULT 'chat'"
                    ))
                if 'reasoning_effort' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN reasoning_effort "
                        "VARCHAR(10) NOT NULL DEFAULT ''"
                    ))
                if 'reasoning_summary' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN reasoning_summary "
                        "VARCHAR(10) NOT NULL DEFAULT ''"
                    ))
                if 'reasoning_max_tokens' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN reasoning_max_tokens "
                        "INT NULL"
                    ))
                if 'request_extra_json' not in cols:
                    conn.execute(text(
                        "ALTER TABLE llm_configs ADD COLUMN request_extra_json "
                        "TEXT NULL"
                    ))
                if added:
                    for row in conn.execute(text(
                        "SELECT id, base_url FROM llm_configs"
                    )):
                        base = (row[1] or '').lower()
                        if any(h in base for h in _CLOUD_HOSTS):
                            conn.execute(text(
                                "UPDATE llm_configs SET kind = 'cloud', "
                                "max_concurrency = 4 WHERE id = :id"
                            ), {'id': row[0]})
        except Exception:
            pass  # pre-init DB / non-MySQL backend; init_db.py will handle creation

        try:
            from sqlalchemy import text
            with db.engine.begin() as conn:
                existing = {row[0] for row in conn.execute(text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'question_assets'"
                ))}
                if 'check_state' not in existing:
                    conn.execute(text(
                        "ALTER TABLE question_assets ADD COLUMN check_state VARCHAR(20) NULL"
                    ))
                if 'check_result' not in existing:
                    conn.execute(text(
                        "ALTER TABLE question_assets ADD COLUMN check_result TEXT NULL"
                    ))
                if 'checked_at' not in existing:
                    conn.execute(text(
                        "ALTER TABLE question_assets ADD COLUMN checked_at DATETIME NULL"
                    ))
        except Exception:
            pass  # pre-init DB / non-MySQL backend; init_db.py will handle creation

        # Auto-tagging / verification: add the whole-question verified columns
        # (verified / verified_at / verified_by) if absent. Same idempotent
        # INFORMATION_SCHEMA pattern so existing deployments upgrade
        # automatically without a manual migration.
        try:
            from sqlalchemy import text
            with db.engine.begin() as conn:
                existing = {row[0] for row in conn.execute(text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'questions'"
                ))}
                if 'verified' not in existing:
                    conn.execute(text(
                        "ALTER TABLE questions ADD COLUMN verified TINYINT(1) NOT NULL DEFAULT 0"
                    ))
                if 'verified_at' not in existing:
                    conn.execute(text(
                        "ALTER TABLE questions ADD COLUMN verified_at DATETIME NULL"
                    ))
                if 'verified_by' not in existing:
                    conn.execute(text(
                        "ALTER TABLE questions ADD COLUMN verified_by INT NULL"
                    ))
        except Exception:
            pass  # pre-init DB / non-MySQL backend; init_db.py will handle creation

    # Load DB-backed system settings into app.config, overriding the
    # .env / Config bootstrap. Safe to call before init_db.py — missing
    # tables are swallowed and the bootstrap defaults remain authoritative.
    from app import settings as _system_settings
    _system_settings.load_all(app)

    return app

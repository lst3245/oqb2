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
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(generator_bp)
    app.register_blueprint(user_bp)
    
    # Create output directory if it doesn't exist
    os.makedirs(app.config['OUTPUT_PATH'], exist_ok=True)
    
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
    
    return app

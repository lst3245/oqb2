"""
Flask application factory
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    """Create and configure the Flask application"""
    app = Flask(__name__)
    
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
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(generator_bp)
    
    # Create output directory if it doesn't exist
    os.makedirs(app.config['OUTPUT_PATH'], exist_ok=True)
    
    return app

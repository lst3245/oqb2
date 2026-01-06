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

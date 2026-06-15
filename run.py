"""
Application entry point for the Online Question Bank system
"""
from pathlib import Path
from dotenv import load_dotenv

# Load .env before any app imports so DB credentials are always available.
load_dotenv(Path(__file__).resolve().parent / '.env', override=True)

from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

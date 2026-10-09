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
    # Reloader stays on so code edits apply without a manual restart.
    # The interactive debugger (/console, the PIN shell) stays off: this
    # process is reachable from the internet through the reverse proxy.
    app.run(host='0.0.0.0', port=5000, debug=True, use_debugger=False)

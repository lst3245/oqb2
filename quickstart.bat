@echo off
echo ========================================
echo Online Question Bank - Quick Start
echo ========================================
echo.

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    echo.
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Check if .env exists
if not exist ".env" (
    echo.
    echo ERROR: .env file not found!
    echo Please copy env_template.txt to .env and configure your database settings.
    echo.
    pause
    exit /b 1
)

REM Install dependencies
echo.
echo Installing dependencies...
pip install -r requirements.txt

REM Check if database is initialized
echo.
echo Initializing database...
python init_db.py

echo.
echo ========================================
echo Setup Complete!
echo ========================================
echo.
echo To start the application:
echo   python run.py
echo.
echo To run the ingestor:
echo   python cli.py ingest
echo.
echo Login credentials:
echo   Username: admin
echo   Password: admin123
echo.
pause

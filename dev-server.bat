@echo off
setlocal
cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo ========================================
echo OQB2 Dev Server (auto-restart on exit)
echo ========================================
echo Run this outside Cursor so the server survives IDE crashes.
echo Press Ctrl+C twice quickly to stop the restart loop.
echo.

:loop
python run.py
echo.
echo Server stopped (exit code %ERRORLEVEL%). Restarting in 3 seconds...
timeout /t 3 /nobreak >nul
goto loop

@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 launch.py
) else (
    python launch.py
)
if errorlevel 1 (
    echo.
    echo Launch failed. Install Python 3.11 or newer, then try again.
    echo Keep this message and share the error text for help.
    pause
)

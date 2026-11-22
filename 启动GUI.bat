@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.9+ and add it to PATH.
    pause
    exit /b 1
)
start "" pythonw.exe "%~dp0rating_gui.py"

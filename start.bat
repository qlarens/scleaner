@echo off
cd /d "%~dp0"
if exist "build\SCleaner.exe" (
    start "" "build\SCleaner.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "run.py"
    exit /b
)
echo Install Python 3.11+ and follow README.md, or download SCleaner.exe from Releases.
pause

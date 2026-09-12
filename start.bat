@echo off
cd /d "%~dp0"
if exist "dist\SCleaner\SCleaner.exe" (
    start "" "dist\SCleaner\SCleaner.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "run.py"
    exit /b
)
echo Install Python 3.11+ and follow README.md, or use the portable release.
pause

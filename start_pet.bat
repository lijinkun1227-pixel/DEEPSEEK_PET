@echo off
cd /d "%~dp0"
python -c "import PySide6, markdown_it" >nul 2>&1
if errorlevel 1 python -m pip install -r requirements.txt
if errorlevel 1 goto failed
start "" pythonw.exe "%~dp0main.py"
exit /b
:failed
echo Installation failed. Check Python and your network.
pause

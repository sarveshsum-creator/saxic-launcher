@echo off
rem XI Launcher - start the desktop app (no console window when possible)
setlocal
cd /d "%~dp0"
set "PY=pythonw"
where pythonw >nul 2>nul || set "PY=python"
start "" %PY% "%~dp0saxic_launcher.py"

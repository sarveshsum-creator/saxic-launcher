@echo off
rem SAXIC Launcher - start the desktop app (no console window when possible)
cd /d "%~dp0"
set "PY=pythonw.exe"
where pythonw.exe >nul 2>nul || set "PY=python.exe"
start "" %PY% "%~dp0saxic_launcher.py"

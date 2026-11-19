@echo off
rem SAXIC Launcher - publish local changes.
rem Commits everything, pushes to GitHub, and the Action rebuilds the download.
cd /d "%~dp0"

set "GIT=git"
where git >nul 2>nul || set "GIT=C:\Program Files\Git\cmd\git.exe"

"%GIT%" rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo This folder is not a git repository yet.
  pause
  exit /b 1
)

"%GIT%" add -A
"%GIT%" diff --cached --quiet
if errorlevel 1 (
  "%GIT%" commit -m "Update %DATE% %TIME%"
) else (
  echo Nothing new to commit.
)

"%GIT%" push
if errorlevel 1 (
  echo.
  echo Push failed. Check your GitHub login / remote and try again.
  pause
  exit /b 1
)

echo.
echo Pushed. GitHub is rebuilding SAXIC-Launcher.exe now (about 1-2 minutes).
echo Download: https://github.com/srxvesh11/saxic-launcher/releases/latest/download/SAXIC-Launcher.exe
pause

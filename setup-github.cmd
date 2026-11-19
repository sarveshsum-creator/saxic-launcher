@echo off
setlocal
title SAXIC Launcher - GitHub setup
cd /d "%~dp0"

set "GH=C:\Program Files\GitHub CLI\gh.exe"
set "GIT=C:\Program Files\Git\cmd\git.exe"
if not exist "%GH%" set "GH=gh"
if not exist "%GIT%" set "GIT=git"

echo ============================================================
echo  SAXIC Launcher - publish to GitHub
echo ============================================================
echo.
echo A browser window will open. Sign in to GitHub (create a free
echo account if you do not have one), then click Authorize.
echo.

"%GH%" auth status >nul 2>nul
if errorlevel 1 (
  "%GH%" auth login --hostname github.com --git-protocol https --web
  if errorlevel 1 (
    echo.
    echo Login did not finish. Run this file again when ready.
    pause
    exit /b 1
  )
)

"%GH%" auth setup-git >nul 2>nul

for /f "delims=" %%u in ('"%GH%" api user --jq .login') do set "USER=%%u"
echo.
echo Signed in as: %USER%
echo.

"%GH%" repo view "%USER%/saxic-launcher" >nul 2>nul
if errorlevel 1 (
  echo Creating public repository %USER%/saxic-launcher ...
  "%GH%" repo create saxic-launcher --public --description "SAXIC Launcher - an original Minecraft launcher for Windows" --source=. --remote=origin --push
) else (
  echo Repository already exists, pushing to it ...
  "%GIT%" remote remove origin >nul 2>nul
  "%GIT%" remote add origin https://github.com/%USER%/saxic-launcher.git
  "%GIT%" push -u origin main
)

if errorlevel 1 (
  echo.
  echo Something went wrong. Copy the messages above and ask for help.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  DONE. Download link:
echo  https://github.com/%USER%/saxic-launcher/releases/latest
echo.
echo  The first build takes 1-3 minutes. Refresh that page.
echo ============================================================
echo.
pause

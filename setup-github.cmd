@echo off
setlocal
title XI Launcher - GitHub setup
cd /d "%~dp0"

set "GH=C:\Program Files\GitHub CLI\gh.exe"
set "GIT=C:\Program Files\Git\cmd\git.exe"
if not exist "%GH%" set "GH=gh"
if not exist "%GIT%" set "GIT=git"

echo ============================================================
echo  XI Launcher - publish to GitHub
echo ============================================================
echo.
echo A browser window will open. Sign in to GitHub (create a free
echo account if you do not have one).
echo.
echo IMPORTANT - just signing in is NOT enough:
echo  1. This black window will show a ONE-TIME CODE, like:  ABCD-1234
echo  2. In the browser, go to:  https://github.com/login/device
echo  3. Type that code, press Continue, then click "Authorize GitHub CLI".
echo  4. Come back here - it will only print DONE after you authorize.
echo.

"%GH%" auth status >nul 2>nul
if errorlevel 1 (
  "%GH%" auth login --hostname github.com --git-protocol https --web
  "%GH%" auth status >nul 2>nul
  if errorlevel 1 (
    echo.
    echo Login did not finish. You probably did not enter the one-time
    echo code at https://github.com/login/device , or you did not click
    echo "Authorize GitHub CLI". Run this file again and follow steps 1-4.
    pause
    exit /b 1
  )
)

"%GH%" auth setup-git >nul 2>nul

for /f "delims=" %%u in ('"%GH%" api user --jq .login') do set "USER=%%u"
echo.
echo Signed in as: %USER%
echo.

"%GH%" repo view "%USER%/xi-launcher" >nul 2>nul
if errorlevel 1 (
  echo Creating public repository %USER%/xi-launcher ...
  "%GH%" repo create xi-launcher --public --description "XI Launcher - an original Minecraft launcher and server hoster for Windows" --source=. --remote=origin --push
) else (
  echo Repository already exists, pushing to it ...
  "%GIT%" remote remove origin >nul 2>nul
  "%GIT%" remote add origin https://github.com/%USER%/xi-launcher.git
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
echo  https://github.com/%USER%/xi-launcher/releases/latest
echo.
echo  The first build takes 1-3 minutes. Refresh that page.
echo ============================================================
echo.
pause

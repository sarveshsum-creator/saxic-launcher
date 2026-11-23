@echo off
setlocal
title XI Launcher - enable pagefile

rem Re-launch this file elevated (administrator) if needed.
net session >nul 2>&1
if errorlevel 1 (
  echo Asking for administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo ============================================================
echo  XI LAUNCHER - ENABLE WINDOWS PAGEFILE
echo  fixes large modpack crashes
echo ============================================================
echo.
echo Without a pagefile Windows can only use physical RAM. A big
echo modpack then runs out of commit memory after a few minutes
echo and the game closes itself.
echo.
echo This adds a fixed pagefile on C: (8 GB, or 4 GB if disk is
echo tight). A restart is required.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable-pagefile.ps1"
if errorlevel 1 (
  echo.
  echo Something went wrong. Copy the messages above and ask for help.
  pause
  exit /b 1
)

echo.
echo Restart the PC now for the new pagefile to take effect.
echo After restarting, PLAY again - the game should stay open.
echo.
pause

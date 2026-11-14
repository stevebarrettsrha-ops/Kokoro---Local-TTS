@echo off
rem Kokoro Studio for Windows: double-click this file (or run it in a terminal).
rem Settings can be given first, e.g.:  set "KOKORO_PORT=8899" && start.bat
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
  echo.
  echo Kokoro Studio stopped with an error ^(see above^).
  pause
)

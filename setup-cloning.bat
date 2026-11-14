@echo off
rem Sets up cloned voices (Chatterbox). start.bat does this by itself in the background;
rem run this to do it in the foreground or to retry. Safe to rerun.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo .venv is missing. Run start.bat first.
  pause
  exit /b 1
)
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
".venv\Scripts\python.exe" -m bootstrap.cloning_setup
if errorlevel 1 pause

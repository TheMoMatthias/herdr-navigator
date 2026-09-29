@echo off
rem herdr-navigator launcher: run.cmd <module> [args]   e.g. run.cmd app resume / run.cmd status
set "PYTHONPATH=%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo herdr-navigator is not set up yet: run  py "%~dp0bootstrap.py"
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m navigator.%*

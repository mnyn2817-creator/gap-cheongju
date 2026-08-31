@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE="
for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE for /f "delims=" %%P in ('dir /b /s "%USERPROFILE%\.cache\codex-runtimes\*\dependencies\python\python.exe" 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
  echo Python runtime not found.
  pause
  exit /b 1
)
"%PYTHON_EXE%" server.py
if errorlevel 1 pause

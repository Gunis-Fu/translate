@echo off
cd /d "%~dp0"

:: Use portable Python if available
if exist "portable_python\python.exe" (
    set "PYTHON_DIR=%CD%\portable_python"
    set "PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%"
)

:: Add local ffmpeg to PATH
if exist "ffmpeg\ffprobe.exe" (
    set "PATH=%CD%\ffmpeg;%PATH%"
)

echo ============================================
echo  Translation Assistant - Debug Console
echo  Hotkey: Ctrl+0    Quit: Ctrl+Shift+Q
echo ============================================

python.exe hotkey_monitor.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Exited with code %errorlevel%
    echo Make sure you ran setup.bat first.
    pause
)

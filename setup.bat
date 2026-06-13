@echo off
cd /d "%~dp0"

set PYTHON_VER=3.12.4
set PYTHON_DIR=%CD%\portable_python
set PYTHON_EXE=%PYTHON_DIR%\python.exe

echo ============================================
echo  Translation Assistant - Portable Setup
echo ============================================
echo.

:: ============================================================
:: Step 1: Download / Install Python
:: ============================================================
echo [1/4] Python %PYTHON_VER% ...

if exist "%PYTHON_EXE%" (
    echo   OK - portable Python already exists
    goto :PYTHON_DONE
)

echo   Downloading Python %PYTHON_VER% (~25 MB) ...
set INSTALLER=%TEMP%\python-%PYTHON_VER%-amd64.exe
set PS_SCRIPT=%TEMP%\dl_python.ps1

echo $ProgressPreference = 'SilentlyContinue' > "%PS_SCRIPT%"
echo $url = 'https://www.python.org/ftp/python/%PYTHON_VER%/python-%PYTHON_VER%-amd64.exe' >> "%PS_SCRIPT%"
echo $out = '%INSTALLER%' >> "%PS_SCRIPT%"
echo try { >> "%PS_SCRIPT%"
echo     Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing >> "%PS_SCRIPT%"
echo     Write-Host '    download complete' >> "%PS_SCRIPT%"
echo } catch { >> "%PS_SCRIPT%"
echo     Write-Host ('download failed: ' + $_.Exception.Message) >> "%PS_SCRIPT%"
echo     exit 1 >> "%PS_SCRIPT%"
echo } >> "%PS_SCRIPT%"

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set PS_EXIT=%errorlevel%
del "%PS_SCRIPT%" 2>nul
if %PS_EXIT% neq 0 (
    echo   [ERROR] Failed to download Python. Please download manually from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo   Installing to %PYTHON_DIR% ...
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_launcher=0 InstallLauncherAllUsers=0 TargetDir="%PYTHON_DIR%" 2>nul
if not exist "%PYTHON_EXE%" (
    echo   [ERROR] Installation failed or was cancelled.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m ensurepip --upgrade >nul 2>nul
del "%INSTALLER%" 2>nul
echo   OK - Python %PYTHON_VER% installed to portable_python/
:PYTHON_DONE
echo.

:: ============================================================
:: Step 2: Install Python packages
:: ============================================================
echo [2/4] Installing Python packages ...
"%PYTHON_EXE%" -m pip install -r requirements.txt --quiet --no-warn-script-location
if %errorlevel% neq 0 (
    echo   [WARN] Some packages may have failed.
) else (
    echo   OK
)
echo.

:: ============================================================
:: Step 3: Download FFmpeg
:: ============================================================
echo [3/4] FFmpeg ...

if exist "ffmpeg\ffprobe.exe" (
    echo   OK - already present
    goto :FFMPEG_DONE
)

if not exist "ffmpeg" mkdir ffmpeg
echo   Downloading FFmpeg (~35 MB) ...
set PS_SCRIPT=%TEMP%\dl_ffmpeg.ps1

echo $ProgressPreference = 'SilentlyContinue' > "%PS_SCRIPT%"
echo $zip = $env:TEMP + '\ffmpeg.zip' >> "%PS_SCRIPT%"
echo $extract = $env:TEMP + '\ffmpeg_extracted' >> "%PS_SCRIPT%"
echo try { >> "%PS_SCRIPT%"
echo     Write-Host '    downloading...' >> "%PS_SCRIPT%"
echo     Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $zip -UseBasicParsing >> "%PS_SCRIPT%"
echo     Write-Host '    extracting...' >> "%PS_SCRIPT%"
echo     Expand-Archive -Path $zip -DestinationPath $extract -Force >> "%PS_SCRIPT%"
echo     $bin = (Get-ChildItem $extract -Directory ^| Select-Object -First 1).FullName >> "%PS_SCRIPT%"
echo     Copy-Item (Join-Path $bin 'bin\ffmpeg.exe') '.\ffmpeg\' -Force >> "%PS_SCRIPT%"
echo     Copy-Item (Join-Path $bin 'bin\ffprobe.exe') '.\ffmpeg\' -Force >> "%PS_SCRIPT%"
echo     Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue >> "%PS_SCRIPT%"
echo     Remove-Item $zip -Force -ErrorAction SilentlyContinue >> "%PS_SCRIPT%"
echo     Write-Host '    done' >> "%PS_SCRIPT%"
echo } catch { >> "%PS_SCRIPT%"
echo     Write-Host ('download failed: ' + $_.Exception.Message) >> "%PS_SCRIPT%"
echo     exit 1 >> "%PS_SCRIPT%"
echo } >> "%PS_SCRIPT%"

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
del "%PS_SCRIPT%" 2>nul

if exist "ffmpeg\ffprobe.exe" (
    echo   OK
) else (
    echo   [WARN] FFmpeg download failed
)
:FFMPEG_DONE
echo.

:: ============================================================
:: Step 4: Cache directory
:: ============================================================
echo [4/4] Preparing cache ...
if not exist "tts_cache" mkdir tts_cache
echo   OK
echo.

:: ============================================================
:: Done
:: ============================================================
echo ============================================
echo  Setup complete!
echo.
echo  Run:  run.vbs  (silent, no window)
echo  Run:  run.bat  (with debug console)
echo ============================================
echo.
pause

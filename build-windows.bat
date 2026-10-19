@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ============================================================
echo   MouseShare - Windows build script
echo ============================================================
echo.

set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"
if /i "%1"=="--pip-official" set "MIRROR="

rem ---------- 1. find a usable Python (3.12 - 3.14) ----------
set "PY="
for %%C in (py python python3) do (
    if not defined PY (
        %%C -c "import sys; sys.exit(0 if (3,12)<=sys.version_info[:2]<(3,15) else 1)" >nul 2>&1
        if !errorlevel! EQU 0 set "PY=%%C"
    )
)

if not defined PY (
    echo [!] No usable Python found - need 3.12, 3.13 or 3.14.
    echo.
    echo     Install one first:
    echo         winget install Python.Python.3.13
    echo     or download from https://www.python.org/downloads/
    echo.
    echo     IMPORTANT: tick "Add python.exe to PATH" during install,
    echo     then run this script again.
    echo.
    pause
    exit /b 1
)

set "PYVER=unknown"
for /f "tokens=2" %%V in ('%PY% --version 2^>^&1') do set "PYVER=%%V"
echo [OK] Python %PYVER%

rem ---------- 2. create an isolated virtualenv ----------
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtualenv .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [!] Failed to create virtualenv.
        pause
        exit /b 1
    )
)
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

echo [*] Installing dependencies - this takes a few minutes ...
"%VENV_PY%" -m pip install --upgrade pip %MIRROR%
"%VENV_PY%" -m pip install "PySide6-Essentials==6.10.3" "cryptography==46.0.7" "pyinstaller==6.22.3" %MIRROR%
if errorlevel 1 (
    echo.
    echo [!] pip install failed. If the network is the problem, retry with
    echo     the official index:
    echo         build-windows.bat --pip-official
    echo.
    pause
    exit /b 1
)

rem ---------- 3. optional installer builder ----------
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [*] Inno Setup not installed - portable ZIP only.
    echo     For a Setup.exe as well, run:
    echo         winget install JRSoftware.InnoSetup.6
    echo     then run this script again.
)

rem ---------- 4. build ----------
echo.
echo [*] Building ... do not close this window.
"%VENV_PY%" scripts\build.py
if errorlevel 1 (
    echo.
    echo [!] Build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done. Output in the dist folder:
echo ============================================================
dir /b dist\*.exe dist\*.zip 2>nul
echo.
echo   Portable: unzip MouseShare-0.7.1-Windows-x64-portable.zip
echo             then run MouseShare.exe
echo ============================================================
echo.
pause

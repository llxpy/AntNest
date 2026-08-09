@echo off
REM ============================================================
REM  AntNest launcher (distribution option B)
REM  Requires: nothing pre-installed. uv is fetched on first run.
REM  - Sets ANT_INSTALLED=1 so runtime state goes to a writable
REM    location (%LOCALAPPDATA%\AntNest) instead of the script dir.
REM  - uv auto-installs Python + pywebview into an isolated venv,
REM    then launches the desktop UI.
REM ============================================================
setlocal

set "ANT_INSTALLED=1"
set "SCRIPT_DIR=%~dp0"

REM 0) If a compiled AntNest.exe sits next to this bat, just run it (no console)
if exist "%~dp0AntNest.exe" (
    start "" "%~dp0AntNest.exe"
    goto :eof
)

REM 1) Make sure uv exists
where uv >nul 2>nul
if errorlevel 1 (
    echo [AntNest] uv not found - installing uv (one time)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    REM put the freshly installed uv on PATH for this session
    set "PATH=%LOCALAPPDATA%\uv;%PATH%"
)

REM 2) Run AntNest from the script directory
cd /d "%SCRIPT_DIR%"
echo [AntNest] starting...
uv run --project "%SCRIPT_DIR%" python prototype_antnest.py

endlocal

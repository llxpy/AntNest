@echo off
REM AntNest launcher (distribution / portable folder)
REM Desktop app: must NOT show a console window.
REM Launch through `uv run` so python inherits uv's windowless state.
REM (All pre-1.2.2 releases used this and produced zero console windows.)
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Prefer the compiled GUI launcher (no console at all)
if exist "%~dp0AntNest.exe" (
  start "" "%~dp0AntNest.exe"
  goto :eof
)

set "ANT_INSTALLED=1"

REM Ensure uv is available (install once if missing); keep its window hidden.
where uv >nul 2>nul
if errorlevel 1 (
  echo [AntNest] uv not found - installing uv (one time)...
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%LOCALAPPDATA%\uv;%USERPROFILE%\.local\bin;%PATH%"
)

REM Launch via uv run; cmd is started minimized so no visible console.
REM uv run auto-syncs deps and spawns python inheriting uv's no-console state.
echo [AntNest] starting via uv run...
start "" /min cmd /c "uv run --project \"%SCRIPT_DIR%\" python \"%SCRIPT_DIR%prototype_antnest.py\""
goto :eof

:done
endlocal
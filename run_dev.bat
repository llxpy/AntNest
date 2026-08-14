@echo off
chcp 65001 >nul
title AntNest
cd /d "%~dp0"

echo.
echo  ========================================
echo   AntNest 正在启动...
echo  ========================================
echo.

if exist ".venv\Scripts\pythonw.exe" (
  echo  使用本地 .venv（pythonw，无控制台窗口）...
  echo.
  start "" ".venv\Scripts\pythonw.exe" prototype_antnest.py
  goto :done
)

if exist ".venv\Scripts\python.exe" (
  echo  使用本地 .venv（python，保留窗口看输出）...
  echo.
  ".venv\Scripts\python.exe" prototype_antnest.py
  goto :done
)

echo  首次启动需通过 uv 安装依赖，约 1~3 分钟，请稍候...
echo.
start "" /min cmd /c "uv sync --project "%~dp0" && "%~dp0.venv\Scripts\pythonw.exe" "%~dp0prototype_antnest.py""

:done
if errorlevel 1 (
  echo.
  echo  启动失败。请查看 .antnest\startup_error.log
  pause
)
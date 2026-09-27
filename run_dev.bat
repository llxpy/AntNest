@echo off
chcp 65001 >nul
title AntNest
cd /d "%~dp0"

echo.
echo  ========================================
echo   AntNest ��������...
echo  ========================================
echo.

if exist ".venv\Scripts\pythonw.exe" (
  echo  ʹ�ñ��� .venv��pythonw���޿���̨���ڣ�...
  echo.
  start "" ".venv\Scripts\pythonw.exe" prototype_antnest.py
  goto :done
)

if exist ".venv\Scripts\python.exe" (
  echo  ʹ�ñ��� .venv��python���������ڿ������...
  echo.
  ".venv\Scripts\python.exe" prototype_antnest.py
  goto :done
)

echo  �״�������ͨ�� uv ��װ������Լ 1~3 ���ӣ����Ժ�...
echo.
start "" /min cmd /c "uv sync --project "%~dp0" && "%~dp0.venv\Scripts\pythonw.exe" "%~dp0prototype_antnest.py""

:done
if errorlevel 1 (
  echo.
  echo  ����ʧ�ܡ���鿴 .antnest\startup_error.log
  pause
)
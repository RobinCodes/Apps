@echo off
rem The Order on Windows: MSYS2 UCRT64s Python, which has PyYAML.
setlocal
set "PY=C:\msys64rt64in\python.exe"
if not exist "%PY%" set "PY=C:\msys64\mingw64in\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0order.py" %*

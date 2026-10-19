@echo off
rem Launcher for The Eye on Windows, through MSYS2's Python — the only one
rem with the GTK4 bindings and the GStreamer plugins this app needs. Untested:
rem see the README. pythonw rather than python so no console window appears.
setlocal
set PY=C:\msys64\mingw64\bin\pythonw.exe
if not exist "%PY%" set PY=C:\msys64\mingw64\bin\python.exe
if not exist "%PY%" (
  echo The Eye needs MSYS2's Python, with mingw-w64-x86_64-python-gobject,
  echo mingw-w64-x86_64-gtk4, mingw-w64-x86_64-libadwaita and the
  echo mingw-w64-x86_64-gst-plugins-* packages installed.
  exit /b 1
)
start "" "%PY%" "%~dp0the-eye.py" %*

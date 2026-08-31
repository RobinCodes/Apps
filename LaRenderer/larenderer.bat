@echo off
rem  LaRenderer - Windows launcher
rem
rem  The app is GTK4, and the GTK4 that exists for Windows is the one MSYS2
rem  ships, so it runs on MSYS2's Python rather than a python.org one.
rem
rem  It is started directly rather than through "bash -lc". A login shell
rem  replaces PATH with a minimal one, and git, gh, pdflatex and synctex all
rem  become invisible to the app; started this way the real Windows PATH is
rem  inherited and only the GTK runtime has to be added to it.
rem
rem  pythonw.exe rather than python.exe: no console window behind the app.

setlocal EnableExtensions

set "APPENTRY=%~dp0larenderer.py"

if not defined MSYS2_ROOT call :find_msys2
if not defined MSYS2_ROOT goto :no_msys2

set "PATH=%MSYS2_ROOT%\mingw64\bin;%PATH%"
start "" "%MSYS2_ROOT%\mingw64\bin\pythonw.exe" "%APPENTRY%" %*
exit /b 0

:find_msys2
for %%R in ("C:\msys64" "%LOCALAPPDATA%\msys64" "%USERPROFILE%\msys64" "C:\tools\msys64") do (
    if exist "%%~R\mingw64\bin\pythonw.exe" (
        set "MSYS2_ROOT=%%~R"
        exit /b 0
    )
)
exit /b 1

:no_msys2
echo.
echo   LaRenderer needs the GTK4 runtime, which on Windows comes from MSYS2.
echo.
echo   Install MSYS2 from https://www.msys2.org/ then run this in a
echo   "MSYS2 MINGW64" shell:
echo.
echo       pacman -S mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita mingw-w64-x86_64-python-gobject
echo.
echo   Already installed somewhere else? Set MSYS2_ROOT to that folder.
echo.
pause
exit /b 1

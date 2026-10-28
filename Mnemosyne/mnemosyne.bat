@echo off
rem  Mnemosyne - Windows launcher
rem
rem  The app is GTK4, and the GTK4 that exists for Windows is the one MSYS2
rem  ships, so it runs on MSYS2's Python rather than a python.org one.
rem
rem  It is started directly rather than through "bash -lc". A login shell
rem  replaces PATH with a minimal one; started this way the real Windows PATH
rem  is inherited and only the GTK runtime has to be added to it.
rem
rem  pythonw.exe rather than python.exe: no console window behind the app.
rem  The command-line answers (--due, --print, --help) are the exception: they
rem  print to the console they were asked from, so they go through python.exe
rem  and are waited for.

setlocal EnableExtensions

set "APPENTRY=%~dp0mnemosyne.py"

if not defined MSYS2_ROOT call :find_msys2
if not defined MSYS2_ROOT goto :no_msys2

if not defined MSYS2_ENV call :find_env
set "PATH=%MSYS2_ROOT%\%MSYS2_ENV%\bin;%PATH%"
for %%A in (%*) do (
    if /I "%%~A"=="--due" goto :console
    if /I "%%~A"=="--print" goto :console
    if /I "%%~A"=="--help" goto :console
    if /I "%%~A"=="-h" goto :console
    if /I "%%~A"=="--version" goto :console
)
start "" "%MSYS2_ROOT%\%MSYS2_ENV%\bin\pythonw.exe" "%APPENTRY%" %*
exit /b 0

:console
"%MSYS2_ROOT%\%MSYS2_ENV%\bin\python.exe" "%APPENTRY%" %*
exit /b %ERRORLEVEL%

:find_msys2
for %%R in ("C:\msys64" "%LOCALAPPDATA%\msys64" "%USERPROFILE%\msys64" "C:\tools\msys64") do (
    if exist "%%~R\ucrt64\bin\pythonw.exe" (
        set "MSYS2_ROOT=%%~R"
        exit /b 0
    )
    if exist "%%~R\mingw64\bin\pythonw.exe" (
        set "MSYS2_ROOT=%%~R"
        exit /b 0
    )
)
exit /b 1

:find_env
rem UCRT64 is where MSYS2 keeps GTK4 now; MINGW64 is what older installs have.
set "MSYS2_ENV=mingw64"
if exist "%MSYS2_ROOT%\ucrt64\bin\pythonw.exe" set "MSYS2_ENV=ucrt64"
exit /b 0

:no_msys2
echo.
echo   Mnemosyne needs the GTK4 runtime, which on Windows comes from MSYS2.
echo.
echo   Install MSYS2 from https://www.msys2.org/ then run this in a
echo   "MSYS2 UCRT64" shell:
echo.
echo       pacman -S mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita mingw-w64-ucrt-x86_64-python-gobject
echo.
echo   Already installed somewhere else? Set MSYS2_ROOT to that folder.
echo.
pause
exit /b 1

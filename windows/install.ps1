<#
.SYNOPSIS
    Install Start Menu (and optionally Desktop) shortcuts for the GTK apps.

.DESCRIPTION
    The Linux side of this repository installs itself with a symlink in
    ~/.local/bin and a .desktop file per app. Windows has neither, so the
    equivalent here is a .lnk per app in the Start Menu.

    The shortcuts point straight at MSYS2's pythonw.exe rather than at the
    .bat launchers. Windows looks for a program's DLLs next to the .exe
    first, so pythonw.exe in mingw64\bin finds GTK without anything being
    added to PATH — and going directly means no console window blinks up
    before the app appears. The .bat launchers stay for command-line use,
    where being handed a file to open is the point.

.PARAMETER Desktop
    Also put the shortcuts on the Desktop.

.PARAMETER Uninstall
    Remove every shortcut this script creates.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Desktop
#>

[CmdletBinding()]
param(
    [switch] $Desktop,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$IconDir = Join-Path $Root 'windows\icons'

# Kind 'python' runs on MSYS2's pythonw; kind 'exe' is a native binary and
# needs no interpreter — Lyndon carries its own DLLs in dist/.
$Apps = @(
    @{ Name = 'Claude Desk'; Kind = 'python'; Folder = 'Claude Program'; Entry = 'claude-desk'
       Icon = 'claude-desk.ico'; Svg = 'Claude Program\claude-desk.svg'
       Comment = 'Run several Claude Code sessions in one native window' }
    @{ Name = 'Git Manager'; Kind = 'python'; Folder = 'Git Manager'; Entry = 'git-manager'
       Icon = 'git-manager.ico'; Svg = 'Git Manager\git-manager.svg'
       Comment = 'Every git repository on this machine, and your GitHub account, in one window' }
    @{ Name = 'LaRenderer'; Kind = 'python'; Folder = 'LaRenderer'; Entry = 'larenderer.py'
       Icon = 'larenderer.ico'; Svg = 'LaRenderer\larenderer.svg'
       Comment = 'Write LaTeX on the left, watch the pages appear on the right' }
    @{ Name = 'Lyndon'; Kind = 'exe'; Folder = 'Lyndon Browser'; Entry = 'dist\lyndon.exe'
       Icon = 'lyndon.ico'; Svg = 'Lyndon Browser\data\icons\org.lyndon.Browser.svg'
       Comment = 'A small, fast, private native browser' }
)

$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$DesktopDir = [Environment]::GetFolderPath('Desktop')

# ---------------------------------------------------------------- uninstall --
if ($Uninstall) {
    foreach ($app in $Apps) {
        foreach ($dir in @($StartMenu, $DesktopDir)) {
            $lnk = Join-Path $dir ($app.Name + '.lnk')
            if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "  removed $lnk" }
        }
    }
    Write-Host "`nShortcuts removed. MSYS2 itself is untouched." -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------------- MSYS2 ---
$roots = @($env:MSYS2_ROOT, 'C:\msys64', "$env:LOCALAPPDATA\msys64",
           "$env:USERPROFILE\msys64", 'C:\tools\msys64') | Where-Object { $_ }
$Msys = $roots | Where-Object { Test-Path (Join-Path $_ 'mingw64\bin\pythonw.exe') } |
        Select-Object -First 1

if (-not $Msys) {
    Write-Host @"

  MSYS2 was not found, and it is what provides GTK4 on Windows.

  1. Install it from https://www.msys2.org/
  2. Open "MSYS2 MINGW64" and run:

       pacman -S mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita ``
                 mingw-w64-x86_64-python-gobject mingw-w64-x86_64-poppler

  3. Run this script again.

  If MSYS2 lives somewhere unusual, set MSYS2_ROOT first.

"@ -ForegroundColor Yellow
    exit 1
}

$PythonW = Join-Path $Msys 'mingw64\bin\pythonw.exe'
$Python = Join-Path $Msys 'mingw64\bin\python.exe'
Write-Host "MSYS2:  $Msys" -ForegroundColor Cyan

# Check the stack before making shortcuts that would only fail when clicked.
$probe = & $Python -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw; print(f'{Gtk.get_major_version()}.{Gtk.get_minor_version()}')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n  MSYS2 is installed but the GTK4 stack is not. In a MINGW64 shell:`n" -ForegroundColor Yellow
    Write-Host "      pacman -S mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita mingw-w64-x86_64-python-gobject`n"
    Write-Host "  ($probe)"
    exit 1
}
Write-Host "GTK:    $probe" -ForegroundColor Cyan

# ------------------------------------------------------------------- icons ---
New-Item -ItemType Directory -Force -Path $IconDir | Out-Null
foreach ($app in $Apps) {
    $ico = Join-Path $IconDir $app.Icon
    $svg = Join-Path $Root $app.Svg
    if ((-not (Test-Path $ico)) -and (Test-Path $svg)) {
        $env:PATH = (Join-Path $Msys 'mingw64\bin') + ';' + $env:PATH
        & $Python (Join-Path $Root 'windows\make-ico.py') $svg $ico 2>&1 | Out-Null
    }
}

# --------------------------------------------------------------- shortcuts ---
$shell = New-Object -ComObject WScript.Shell
$targets = @($StartMenu)
if ($Desktop) { $targets += $DesktopDir }

foreach ($app in $Apps) {
    $entry = Join-Path (Join-Path $Root $app.Folder) $app.Entry
    if (-not (Test-Path $entry)) {
        if ($app.Kind -eq 'exe') {
            Write-Host "  SKIP $($app.Name): not built yet — in an MSYS2 MINGW64 shell, run" -ForegroundColor Yellow
            Write-Host "       cd '$(Join-Path $Root $app.Folder)' && make -f win32/Makefile dist" -ForegroundColor Yellow
        } else {
            Write-Host "  SKIP $($app.Name): $entry missing" -ForegroundColor Yellow
        }
        continue
    }
    $ico = Join-Path $IconDir $app.Icon

    foreach ($dir in $targets) {
        $lnk = Join-Path $dir ($app.Name + '.lnk')
        $s = $shell.CreateShortcut($lnk)
        if ($app.Kind -eq 'exe') {
            $s.TargetPath = $entry
            $s.WorkingDirectory = Split-Path -Parent $entry
        } else {
            $s.TargetPath = $PythonW
            $s.Arguments = '"' + $entry + '"'
            $s.WorkingDirectory = Join-Path $Root $app.Folder
        }
        $s.Description = $app.Comment
        if (Test-Path $ico) { $s.IconLocation = $ico }
        $s.Save()
        Write-Host "  $($app.Name)  ->  $lnk" -ForegroundColor Green
    }
}

Write-Host "`nDone. The apps are in the Start Menu$(if ($Desktop) { ' and on the Desktop' })." -ForegroundColor Green
Write-Host "Remove them again with:  windows\install.ps1 -Uninstall`n"

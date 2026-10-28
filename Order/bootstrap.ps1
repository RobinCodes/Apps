<#
.SYNOPSIS
    A new Windows machine, brought into the Order in one command.

.DESCRIPTION
        irm https://raw.githubusercontent.com/RobinCodes/Apps/main/Order/bootstrap.ps1 | iex

    In order, and each step skipped when it is already done, so running it
    again is how a half-finished machine is finished:

      1. Git for Windows (winget), if there is no git.
      2. MSYS2 in C:\msys64, and the UCRT64 packages the apps and the brain
         need: GTK4, libadwaita, PyGObject, GStreamer, poppler, tesseract
         (Hungarian, English, Italian), PyYAML, numpy, Pillow, PyMuPDF, …
      3. A name for this machine (ORDER_MACHINE, a user variable): what its
         review logs, nodes and sync commits will say.
      4. The apps (public) and the brain (private: Git Credential Manager asks
         to sign in to GitHub once, and remembers).
      5. `order sync`: every other repository in the manifest, in its place.
      6. Shortcuts and file types (windows\install.ps1 -Desktop).
      7. The sync timer: `order sync` every ten minutes.
      8. `anam` and `order` on PATH, through %USERPROFILE%\.local\bin.
      9. Claude Code, for "Ask Claude…" in every app.
     10. `order doctor`, to say what, if anything, is still out of order.

    Nothing here needs an administrator. Nothing outside %USERPROFILE%,
    C:\msys64 and the user's environment variables is changed.
#>
param([string]$Machine)

$ErrorActionPreference = 'Stop'
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

# ------------------------------------------------------------------- git ---
Step 'git'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'No git and no winget: install Git for Windows from https://git-scm.com, then run this again.'
    }
    winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
    $env:PATH = "$env:ProgramFiles\Git\cmd;$env:PATH"
}
git --version

# ----------------------------------------------------------------- MSYS2 ---
Step 'MSYS2 UCRT64: GTK4 and the rest of what the apps run on'
$Msys = if ($env:MSYS2_ROOT) { $env:MSYS2_ROOT } else { 'C:\msys64' }
$Bash = Join-Path $Msys 'usr\bin\bash.exe'
if (-not (Test-Path $Bash)) {
    $sfx = Join-Path $env:TEMP 'msys2-base.sfx.exe'
    Invoke-WebRequest 'https://repo.msys2.org/distrib/msys2-x86_64-latest.sfx.exe' -OutFile $sfx
    & $sfx -y "-o$(Split-Path -Parent $Msys)" | Out-Null
    Remove-Item $sfx
    & $Bash -lc 'true'
    # Twice: the first run updates pacman itself and asks to be run again.
    & $Bash -lc 'pacman -Syuu --noconfirm'
    & $Bash -lc 'pacman -Syuu --noconfirm'
}
$packages = @(
    'gtk4', 'libadwaita', 'python', 'python-gobject', 'python-cairo', 'python-yaml',
    'python-numpy', 'python-pillow', 'python-charset-normalizer', 'python-beautifulsoup4',
    'python-lxml', 'python-pymupdf', 'python-pip', 'poppler', 'librsvg',
    'adwaita-icon-theme', 'webp-pixbuf-loader', 'libheif', 'libjxl', 'libavif',
    'gstreamer', 'gst-plugins-base', 'gst-plugins-good', 'gst-plugins-bad',
    'gst-plugins-ugly', 'gst-libav', 'gst-python', 'ffmpeg',
    'tesseract-ocr', 'tesseract-data-eng', 'tesseract-data-hun', 'tesseract-data-ita'
) | ForEach-Object { "mingw-w64-ucrt-x86_64-$_" }
$env:MSYSTEM = 'UCRT64'
for ($attempt = 1; $attempt -le 4; $attempt++) {
    # Mirrors rate-limit; pacman keeps what it already downloaded.
    & $Bash -lc ("pacman -S --needed --noconfirm --disable-download-timeout " + ($packages -join ' '))
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds (15 * $attempt)
}
$Py = Join-Path $Msys 'ucrt64\bin\python.exe'
& $Py -c "import docx" 2>$null
if ($LASTEXITCODE -ne 0) { & $Py -m pip install --break-system-packages --no-warn-script-location -q python-docx }
& $Py -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw; print('GTK', Gtk.get_major_version(), Gtk.get_minor_version())"

# --------------------------------------------------------------- the name ---
Step "this machine's name"
if (-not $Machine) { $Machine = [Environment]::GetEnvironmentVariable('ORDER_MACHINE', 'User') }
if (-not $Machine) {
    $default = $env:COMPUTERNAME.ToLower()
    $Machine = Read-Host "Name this machine (main-pc, old-laptop, ...) [Enter = $default]"
    if (-not $Machine) { $Machine = $default }
}
$Machine = ($Machine.ToLower() -replace '[^a-z0-9-]+', '-').Trim('-')
[Environment]::SetEnvironmentVariable('ORDER_MACHINE', $Machine, 'User')
$env:ORDER_MACHINE = $Machine
Write-Host "  $Machine"

# --------------------------------------------------- the apps and the brain ---
Step 'the apps and the brain'
$Apps = Join-Path $env:USERPROFILE 'Projects\Programming\Apps'
$Brain = Join-Path $env:USERPROFILE 'SecondBrain'
if (-not (Test-Path (Join-Path $Apps '.git'))) {
    git clone https://github.com/RobinCodes/Apps.git $Apps
}
if (-not (Test-Path (Join-Path $Brain '.git'))) {
    # Private: Git Credential Manager opens a browser to sign in to GitHub, once.
    git clone https://github.com/RobinCodes/SecondBrain.git $Brain
}
git -C $Brain config core.autocrlf false

# -------------------------------------------------------- everything else ---
Step 'every repository in the manifest'
$env:PATH = (Join-Path $Msys 'ucrt64\bin') + ";$env:PATH"
& $Py (Join-Path $Apps 'Order\order.py') sync

Step 'shortcuts and file types'
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Apps 'windows\install.ps1') -Desktop

Step 'the sync timer'
& $Py (Join-Path $Apps 'Order\order.py') timer install

Step 'anam and order on PATH'
$Bin = Join-Path $env:USERPROFILE '.local\bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Content -Encoding ascii -Path (Join-Path $Bin 'anam.cmd') -Value "@`"$Brain\anam.bat`" %*"
Set-Content -Encoding ascii -Path (Join-Path $Bin 'order.cmd') -Value "@`"$Apps\Order\order.bat`" %*"
$userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if (-not (($userPath -split ';') -contains $Bin)) {
    [Environment]::SetEnvironmentVariable('PATH', (@($Bin, $userPath) | Where-Object { $_ }) -join ';', 'User')
}

Step 'Claude Code, for "Ask Claude…" in every app'
if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and
    -not (Test-Path (Join-Path $env:USERPROFILE '.local\bin\claude.exe'))) {
    Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    Write-Host '  Run `claude` once in a terminal to sign in.' -ForegroundColor Yellow
}

Step 'where things stand'
& $Py (Join-Path $Apps 'Order\order.py') doctor
Write-Host "`nThis machine is in the Order. Open a new terminal for anam and order." -ForegroundColor Green

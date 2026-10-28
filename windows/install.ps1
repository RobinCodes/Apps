<#
.SYNOPSIS
    Install the apps on Windows: their shortcuts, and the file types they open.

.DESCRIPTION
    The Linux side of this repository installs itself with a symlink in
    ~/.local/bin and a .desktop file per app. Windows has neither, so the
    equivalent here is a .lnk per app in the Start Menu, and — for the apps
    that open documents — a registration of those types under the current
    user, so that each is offered by name, with its icon, under "Open with"
    and in Settings.

    The shortcuts point straight at MSYS2's pythonw.exe rather than at the
    .bat launchers. Windows looks for a program's DLLs next to the .exe
    first, so pythonw.exe in ucrt64\bin (mingw64\bin on an older install) finds GTK without anything being
    added to PATH — and going directly means no console window blinks up
    before the app appears. The .bat launchers stay for command-line use,
    where being handed a file to open is the point.

    Everything written holds absolute paths into this checkout, so after
    moving it, run this again: it rewrites every shortcut it made — the ones
    pinned to the taskbar included — and every registration. A default app
    chosen in the meantime survives that, because Windows records the choice
    by the registration's name, not by the path in it.

.PARAMETER Desktop
    Also put the shortcuts on the Desktop. Ones already there are refreshed
    either way.

.PARAMETER Default
    Afterwards, open Settings at this app's Default apps page, where "Set
    default" makes it what opens everything it registered. Windows reserves
    that choice for the user, so this is as far as a script can take it.

.PARAMETER Uninstall
    Remove every shortcut and registration this script creates, and the
    other programs' entries beside them not at all. A shortcut pinned to the
    taskbar is left for Windows to unpin.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Desktop

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Default Notedown
#>

[CmdletBinding()]
param(
    [switch] $Desktop,
    [ValidateSet('Notedown', 'LaRenderer', 'Lyndon', 'The Eye')]
    [string] $Default,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$IconDir = Join-Path $Root 'windows\icons'

# Every shortcut runs Entry on MSYS2's pythonw — Lyndon's is its launcher,
# which keeps dist\ built and current (README, "Updates").
#
# AppId is the application ID each app gives itself at startup (winenv.py's
# set_app_id). Stamped on the shortcut as well, it is how the taskbar knows a
# window belongs to that shortcut — without it every one of these is just
# pythonw.exe, and they get grouped under, and drawn with, one another's icon.
#
# Launcher is the .bat beside the app. It is named here only so that a choice
# of it under "Open with" can be undone: see Remove-DeadChoice.
#
# Types are what an app opens. Each is a ProgId: offered under "Open with" for
# its Extensions (or, for a browser, its Schemes), and what gives a file its
# Icon once the app is the one that opens it. Opener, when there is one, is
# the program those open with instead of Entry.
$Apps = @(
    @{ Name = 'Claude Desk'; Folder = 'Claude Program'; Entry = 'claude-desk'
       Launcher = 'claude-desk.bat'
       Icon = 'claude-desk.ico'; Svg = 'Claude Program\claude-desk.svg'
       AppId = 'com.robin.ClaudeDesk'
       Comment = 'Run several Claude Code sessions in one native window' }
    @{ Name = 'Git Manager'; Folder = 'Git Manager'; Entry = 'git-manager'
       Launcher = 'git-manager.bat'
       Icon = 'git-manager.ico'; Svg = 'Git Manager\git-manager.svg'
       AppId = 'com.robin.GitManager'
       Comment = 'Every git repository on this machine, and your GitHub account, in one window' }
    @{ Name = 'LaRenderer'; Folder = 'LaRenderer'; Entry = 'larenderer.py'
       Launcher = 'larenderer.bat'
       Icon = 'larenderer.ico'; Svg = 'LaRenderer\larenderer.svg'
       AppId = 'com.robin.LaRenderer'
       Comment = 'Write LaTeX on the left, watch the pages appear on the right'
       # text/x-tex on Linux, less .ins: Windows already has that for itself.
       Types = @(
           @{ ProgId = 'LaRenderer.LaTeX'; Name = 'LaTeX document'
              Icon = 'larenderer-document.ico'; Svg = 'LaRenderer\larenderer-document.svg'
              Extensions = @('.tex', '.ltx', '.latex', '.sty', '.cls', '.dtx') }) }
    @{ Name = 'Fathom'; Folder = 'Fathom'; Entry = 'fathom.py'
       Launcher = 'fathom.bat'
       Icon = 'fathom.ico'; Svg = 'Fathom\fathom.svg'
       AppId = 'com.robin.Fathom'
       Comment = 'See what is taking up the disk, as a tree and as a map' }
    # No file types: a file manager that registers itself as the handler for
    # anything would be a file manager that opens files with itself.
    @{ Name = 'Hermione'; Folder = 'Hermione'; Entry = 'hermione.py'
       Launcher = 'hermione.bat'
       Icon = 'hermione.ico'; Svg = 'Hermione\hermione.svg'
       AppId = 'com.robin.Hermione'
       Comment = 'Browse, search and tidy up the files on this machine' }
    @{ Name = 'Mnemosyne'; Folder = 'Mnemosyne'; Entry = 'mnemosyne.py'
       Launcher = 'mnemosyne.bat'
       Icon = 'mnemosyne.ico'; Svg = 'Mnemosyne\mnemosyne.svg'
       AppId = 'com.robin.Mnemosyne'
       Comment = 'Review the cards in your notes, on screen or on paper' }
    @{ Name = 'Notedown'; Folder = 'Notedown'; Entry = 'notedown.py'
       Launcher = 'notedown.bat'
       Icon = 'notedown.ico'; Svg = 'Notedown\notedown.svg'
       AppId = 'com.robin.Notedown'
       Comment = 'Write Markdown on the left, watch the page appear on the right'
       Types = @(
           @{ ProgId = 'Notedown.Markdown'; Name = 'Markdown document'
              Icon = 'notedown-document.ico'; Svg = 'Notedown\notedown-document.svg'
              Extensions = @('.md', '.markdown', '.mdown', '.mkd', '.mkdn',
                             '.mdwn', '.mdtxt', '.mdtext') }) }
    # What The Eye opens is asked rather than written down here: it says for
    # itself which formats this machine has loaders for (Read-Types), so it is
    # offered for OpenEXR or QOI only where one of those will actually open.
    # Each type names the Kind of line it takes from that answer.
    @{ Name = 'The Eye'; Folder = 'The Eye'; Entry = 'the-eye.py'
       Launcher = 'the-eye.bat'
       Icon = 'the-eye.ico'; Svg = 'The Eye\the-eye.svg'
       AppId = 'com.robin.TheEye'
       Comment = 'Look at photographs and video, and grade them while you do'
       AskTypes = '--list-types'
       Types = @(
           @{ ProgId = 'TheEye.Picture'; Name = 'Picture'; Kind = 'photo'
              Icon = 'the-eye.ico'; Svg = 'The Eye\the-eye.svg' }
           @{ ProgId = 'TheEye.Raw'; Name = 'Camera raw picture'; Kind = 'raw'
              Icon = 'the-eye.ico'; Svg = 'The Eye\the-eye.svg' }
           @{ ProgId = 'TheEye.Video'; Name = 'Video'; Kind = 'video'
              Icon = 'the-eye.ico'; Svg = 'The Eye\the-eye.svg' }
           @{ ProgId = 'TheEye.Sound'; Name = 'Sound'; Kind = 'audio'
              Icon = 'the-eye.ico'; Svg = 'The Eye\the-eye.svg' }) }
    # A page or a link goes straight to lyndon.exe, which hands it to the
    # window already open: a link clicked in another program should not wait
    # on Python, git and a fetch first. Starting the app is still the
    # launcher's job, so the build an update left ready gets swapped in.
    @{ Name = 'Lyndon'; Folder = 'Lyndon Browser'; Entry = 'lyndon.py'
       Opener = 'dist\lyndon.exe'; Browser = $true
       Icon = 'lyndon.ico'; Svg = 'Lyndon Browser\data\icons\org.lyndon.Browser.svg'
       AppId = 'org.lyndon.Browser'
       Comment = 'A small, fast, private native browser'
       Types = @(
           @{ ProgId = 'Lyndon.HTML'; Name = 'HTML document'
              Icon = 'lyndon-document.ico'; Svg = 'Lyndon Browser\data\icons\lyndon-document.svg'
              Extensions = @('.htm', '.html', '.shtml', '.xht', '.xhtml') }
           @{ ProgId = 'Lyndon.URL'; Name = 'Lyndon URL'
              Icon = 'lyndon-document.ico'; Svg = 'Lyndon Browser\data\icons\lyndon-document.svg'
              Schemes = @('http', 'https') }) }
)

$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$DesktopDir = [Environment]::GetFolderPath('Desktop')
# What "Pin to taskbar" leaves behind: a copy of the shortcut, which goes on
# pointing wherever the checkout was when it was pinned.
$Pinned = Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'
$Classes = 'HKCU:\Software\Classes'
$Registered = 'HKCU:\Software\RegisteredApplications'

# WScript.Shell can make a shortcut but not give it an application ID; that
# is a property on the link's property store, which only COM reaches.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace AppsInstall {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class CShellLink {}

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid fmtid; public uint pid; }

    [StructLayout(LayoutKind.Explicit)]
    struct PropVariant {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr p;
        [FieldOffset(16)] public IntPtr pad;
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    interface IPropertyStore {
        [PreserveSig] int GetCount(out uint count);
        [PreserveSig] int GetAt(uint index, out PropertyKey key);
        [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
        [PreserveSig] int SetValue(ref PropertyKey key, ref PropVariant value);
        [PreserveSig] int Commit();
    }

    public static class Shell {
        // PKEY_AppUserModel_ID
        static PropertyKey AppIdKey = new PropertyKey {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };

        public static void SetAppId(string lnk, string appId) {
            var link = new CShellLink();
            try {
                var file = (IPersistFile)link;
                file.Load(lnk, 2);                          // STGM_READWRITE
                var store = (IPropertyStore)link;
                var value = new PropVariant { vt = 31,      // VT_LPWSTR
                                              p = Marshal.StringToCoTaskMemUni(appId) };
                try {
                    Marshal.ThrowExceptionForHR(store.SetValue(ref AppIdKey, ref value));
                    Marshal.ThrowExceptionForHR(store.Commit());
                } finally {
                    Marshal.FreeCoTaskMem(value.p);
                }
                file.Save(lnk, true);
            } finally {
                Marshal.ReleaseComObject(link);
            }
        }

        [DllImport("shell32.dll")]
        static extern void SHChangeNotify(int eventId, uint flags, IntPtr a, IntPtr b);

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, EntryPoint = "SHChangeNotify")]
        static extern void SHChangeNotifyPath(int eventId, uint flags, string a, IntPtr b);

        // Explorer caches file-type icons; this tells it they changed.
        public static void AssociationsChanged() {
            SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);   // SHCNE_ASSOCCHANGED
        }

        // And the taskbar caches a pinned shortcut's; this tells it that one did.
        public static void ItemChanged(string path) {
            SHChangeNotifyPath(0x00002000, 0x0005, path, IntPtr.Zero);  // SHCNE_UPDATEITEM, SHCNF_PATHW
        }

        [DllImport("shlwapi.dll", CharSet = CharSet.Unicode)]
        static extern int AssocQueryString(int flags, int what, string assoc, string extra,
                                           StringBuilder result, ref uint length);

        // What Explorer resolves `assoc` (".md", "http") to: 20 is the ProgId
        // that opens it, 4 the name of the program. Null when nothing does.
        public static string Query(string assoc, int what) {
            uint length = 1024;
            var result = new StringBuilder((int)length);
            return AssocQueryString(0, what, assoc, null, result, ref length) == 0
                ? result.ToString() : null;
        }
    }
}
'@

# --------------------------------------------------------------- registry ---

# The key at `path`, made if it is missing — and only then. New-Item -Force on
# a key that exists deletes everything in it, and several keys written here
# are shared: .md\OpenWithProgids holds every editor's claim on Markdown, and
# RegisteredApplications every app's entry in Settings.
function Open-Key([string] $path) {
    if (-not (Test-Path -LiteralPath $path)) { New-Item -Path $path -Force | Out-Null }
    $path
}

function Set-Values([string] $path, [hashtable] $values) {
    $key = Open-Key $path
    foreach ($name in $values.Keys) {
        $type = if ($values[$name] -is [int]) { 'DWord' } else { 'String' }
        Set-ItemProperty -LiteralPath $key -Name $name -Value $values[$name] -Type $type
    }
}

function Remove-Value([string] $path, [string] $name) {
    if ((Test-Path -LiteralPath $path) -and
        ((Get-Item -LiteralPath $path).GetValueNames() -contains $name)) {
        Remove-ItemProperty -LiteralPath $path -Name $name
    }
}

function Remove-Key([string] $path) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
        Write-Host "  removed $path"
    }
}

# Where an app lists what it opens. A browser's goes under StartMenuInternet,
# which is where Windows looks for the list it offers as web browsers.
function Get-AppKey($app) {
    if ($app.Browser) { "Software\Clients\StartMenuInternet\$($app.Name)" }
    else { "Software\RobinCodes\$($app.Name)" }
}

# Choosing a .bat under "Open with > Choose another app > Browse" makes
# Windows invent Applications\<name>.bat for it: a handler with no icon, so
# every file of the type turns blank, and a path that dies with the checkout.
# The registration is what that was reaching for.
function Remove-DeadChoice($app) {
    if ($app.Launcher) { Remove-Key "$Classes\Applications\$($app.Launcher)" }
}

function Register-Types($app, [string] $command, [string] $appIcon, [string] $start) {
    $key = "HKCU:\$(Get-AppKey $app)"
    $caps = "$key\Capabilities"

    # Whatever an earlier run registered and this one no longer does.
    foreach ($list in 'FileAssociations', 'URLAssociations') {
        if (-not (Test-Path -LiteralPath "$caps\$list")) { continue }
        $current = @($app.Types | ForEach-Object { $_.Extensions + $_.Schemes } | Where-Object { $_ })
        $item = Get-Item -LiteralPath "$caps\$list"
        foreach ($name in $item.GetValueNames()) {
            if ($current -contains $name) { continue }
            Remove-Value "$Classes\$name\OpenWithProgids" $item.GetValue($name)
            Remove-Value "$caps\$list" $name
        }
    }

    foreach ($type in $app.Types) {
        $prog = "$Classes\$($type.ProgId)"
        $docIcon = Join-Path $IconDir $type.Icon
        $docIcon = if (Test-Path $docIcon) { "$docIcon,0" } else { $appIcon }
        Set-Values $prog @{ '(default)' = $type.Name; FriendlyTypeName = $type.Name
                            AppUserModelID = $app.AppId }
        if ($type.Schemes) { Set-Values $prog @{ 'URL Protocol' = ''; EditFlags = 2 } }
        Set-Values "$prog\DefaultIcon" @{ '(default)' = $docIcon }
        Set-Values "$prog\shell" @{ '(default)' = 'open' }
        Set-Values "$prog\shell\open\command" @{ '(default)' = $command }
        # The app's name wherever Windows names the program that opens this:
        # "Open with", "Opens with" in Properties, Settings. Without these it
        # reads pythonw.exe's version stamp, and every one of the apps is
        # "Python". FriendlyAppName is the older of the two places to say so.
        Set-Values "$prog\shell\open" @{ FriendlyAppName = $app.Name }
        Set-Values "$prog\Application" @{ ApplicationName = $app.Name
                                          ApplicationDescription = $app.Comment
                                          ApplicationIcon = $appIcon
                                          AppUserModelID = $app.AppId }

        # Offered for each, never imposed: which program opens a type is the
        # user's choice, made once under "Open with" or in Settings.
        foreach ($ext in $type.Extensions) {
            Set-Values "$Classes\$ext\OpenWithProgids" @{ $type.ProgId = '' }
            Set-Values "$caps\FileAssociations" @{ $ext = $type.ProgId }
        }
        foreach ($scheme in $type.Schemes) {
            Set-Values "$caps\URLAssociations" @{ $scheme = $type.ProgId }
        }
    }

    Set-Values $caps @{ ApplicationName = $app.Name; ApplicationDescription = $app.Comment
                        ApplicationIcon = $appIcon }
    if ($app.Browser) {
        Set-Values $key @{ '(default)' = $app.Name }
        Set-Values "$key\DefaultIcon" @{ '(default)' = $appIcon }
        Set-Values "$key\shell\open\command" @{ '(default)' = $start }
        Set-Values "$caps\StartMenu" @{ StartMenuInternet = $app.Name }
    }
    Set-Values $Registered @{ $app.Name = "$(Get-AppKey $app)\Capabilities" }
}

# An app that says for itself what it opens prints one `kind .ext` a line;
# each of its types takes the extensions of its own Kind. Anything else in
# the answer — a warning a library printed on its way up — is not a line of
# that shape and is passed over.
function Set-AskedTypes($app, [string[]] $lines) {
    foreach ($type in $app.Types) {
        $type.Extensions = @($lines | ForEach-Object {
            $kind, $ext, $rest = -split $_
            if ($kind -eq $type.Kind -and -not $rest -and $ext -match '^\.[a-z0-9]+$') { $ext }
        })
    }
}

function Read-Types($app, [string] $entry) {
    # Continue, not Stop, for the length of the question: Windows PowerShell
    # turns a native program's stderr into errors, and GStreamer starting up
    # may well say something there.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $said = @(& $Python $entry $app.AskTypes 2>$null) } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0 -or -not $said) { return $false }
    Set-AskedTypes $app $said
    $true
}

function Unregister-Types($app) {
    $key = "HKCU:\$(Get-AppKey $app)"
    foreach ($type in $app.Types) {
        Remove-Key "$Classes\$($type.ProgId)"
        foreach ($ext in $type.Extensions) { Remove-Value "$Classes\$ext\OpenWithProgids" $type.ProgId }
    }
    # Including anything an older version registered that this one does not.
    $files = "$key\Capabilities\FileAssociations"
    if (Test-Path -LiteralPath $files) {
        $item = Get-Item -LiteralPath $files
        foreach ($ext in $item.GetValueNames()) {
            Remove-Value "$Classes\$ext\OpenWithProgids" $item.GetValue($ext)
        }
    }
    Remove-Key $key
    Remove-Value $Registered $app.Name
}

# -------------------------------------------------------------- shortcuts ---

$shell = New-Object -ComObject WScript.Shell

# Whether `lnk` is one this script made, from here or from wherever the
# checkout was before. Known by what it runs, since the path to that may no
# longer exist.
function Test-Ours([string] $lnk, $app) {
    if (-not (Test-Path -LiteralPath $lnk)) { return $false }
    $s = $shell.CreateShortcut($lnk)
    $runs = ($s.TargetPath + ' ' + $s.Arguments).Replace('"', '')
    foreach ($tail in @($app.Entry, $app.Opener) | Where-Object { $_ }) {
        $pattern = '*\' + [WildcardPattern]::Escape("$($app.Folder)\$tail") + '*'
        if ($runs -like $pattern) { return $true }
    }
    $false
}

# ---------------------------------------------------------------- uninstall --
if ($Uninstall) {
    foreach ($app in $Apps) {
        foreach ($dir in @($StartMenu, $DesktopDir)) {
            $lnk = Join-Path $dir ($app.Name + '.lnk')
            if (Test-Ours $lnk $app) { Remove-Item -LiteralPath $lnk -Force; Write-Host "  removed $lnk" }
        }
        if ($app.Types) { Unregister-Types $app }
        Remove-DeadChoice $app
    }
    $mine = 'HKCU:\Software\RobinCodes'
    if ((Test-Path $mine) -and -not (Get-ChildItem $mine)) { Remove-Item $mine }
    [AppsInstall.Shell]::AssociationsChanged()
    Write-Host "`nShortcuts and file types removed. MSYS2 itself is untouched." -ForegroundColor Green
    Write-Host "A shortcut pinned to the taskbar is Windows' to remove: right-click it, Unpin."
    exit 0
}

# ------------------------------------------------------------------- MSYS2 ---
$roots = @($env:MSYS2_ROOT, 'C:\msys64', "$env:LOCALAPPDATA\msys64",
           "$env:USERPROFILE\msys64", 'C:\tools\msys64') | Where-Object { $_ }
$Msys = $roots | Where-Object { (Test-Path (Join-Path $_ 'ucrt64\bin\pythonw.exe')) -or
                                (Test-Path (Join-Path $_ 'mingw64\bin\pythonw.exe')) } |
        Select-Object -First 1

if (-not $Msys) {
    Write-Host @"

  MSYS2 was not found, and it is what provides GTK4 on Windows.

  1. Install it from https://www.msys2.org/
  2. Open "MSYS2 UCRT64" and run:

       pacman -S mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita ``
                 mingw-w64-ucrt-x86_64-python-gobject mingw-w64-ucrt-x86_64-poppler

  3. Run this script again.

  If MSYS2 lives somewhere unusual, set MSYS2_ROOT first.

"@ -ForegroundColor Yellow
    exit 1
}

# UCRT64 is where MSYS2 keeps GTK4 now (MINGW64 no longer has gtk4 or
# libadwaita), but an older install with its GTK in MINGW64 keeps working.
$MsysEnv = if (Test-Path (Join-Path $Msys 'ucrt64\bin\pythonw.exe')) { 'ucrt64' } else { 'mingw64' }
$MsysBin = Join-Path $Msys "$MsysEnv\bin"
$PythonW = Join-Path $MsysBin 'pythonw.exe'
$Python = Join-Path $MsysBin 'python.exe'
Write-Host "MSYS2:  $Msys ($MsysEnv)" -ForegroundColor Cyan

# Check the stack before making shortcuts that would only fail when clicked.
$probe = & $Python -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw; print(f'{Gtk.get_major_version()}.{Gtk.get_minor_version()}')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n  MSYS2 is installed but the GTK4 stack is not. In a UCRT64 shell:`n" -ForegroundColor Yellow
    Write-Host "      pacman -S mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita mingw-w64-ucrt-x86_64-python-gobject`n"
    Write-Host "  ($probe)"
    exit 1
}
Write-Host "GTK:    $probe" -ForegroundColor Cyan
Write-Host "Here:   $Root" -ForegroundColor Cyan

# ------------------------------------------------------------------- icons ---
New-Item -ItemType Directory -Force -Path $IconDir | Out-Null
$env:PATH = $MsysBin + ';' + $env:PATH
$wanted = @()
foreach ($app in $Apps) {
    $wanted += ,@($app.Icon, $app.Svg)
    foreach ($type in $app.Types) { $wanted += ,@($type.Icon, $type.Svg) }
}
foreach ($pair in $wanted) {
    $ico = Join-Path $IconDir $pair[0]
    $svg = Join-Path $Root $pair[1]
    if ((-not (Test-Path $ico)) -and (Test-Path $svg)) {
        & $Python (Join-Path $Root 'windows\make-ico.py') $svg $ico 2>&1 | Out-Null
    }
}

# ------------------------------------------------ shortcuts and file types ---
$installed = @()
$onDesktop = $false
foreach ($app in $Apps) {
    $folder = Join-Path $Root $app.Folder
    $entry = Join-Path $folder $app.Entry
    if (-not (Test-Path $entry)) {
        Write-Host "  SKIP $($app.Name): $entry missing" -ForegroundColor Yellow
        continue
    }
    $ico = Join-Path $IconDir $app.Icon

    $links = @(Join-Path $StartMenu ($app.Name + '.lnk'))
    $desk = Join-Path $DesktopDir ($app.Name + '.lnk')
    if ($Desktop -or (Test-Ours $desk $app)) { $links += $desk; $onDesktop = $true }
    $pin = Join-Path $Pinned ($app.Name + '.lnk')
    if (Test-Ours $pin $app) { $links += $pin }

    foreach ($lnk in $links) {
        $s = $shell.CreateShortcut($lnk)
        $s.TargetPath = $PythonW
        $s.Arguments = '"' + $entry + '"'
        $s.WorkingDirectory = $folder
        $s.Description = $app.Comment
        if (Test-Path $ico) { $s.IconLocation = "$ico,0" }
        $s.Save()
        [AppsInstall.Shell]::SetAppId($lnk, $app.AppId)
        [AppsInstall.Shell]::ItemChanged($lnk)
        Write-Host "  $($app.Name)  ->  $lnk" -ForegroundColor Green
    }

    Remove-DeadChoice $app
    if ($app.AskTypes -and -not (Read-Types $app $entry)) {
        # Registering an empty list would let go of every type it opened
        # before; saying so and leaving them is the kinder failure.
        Write-Host "  $($app.Name) could not say what it opens here; its file types are left as they were" -ForegroundColor Yellow
        continue
    }
    if ($app.Types) {
        $start = '"' + $PythonW + '" "' + $entry + '"'
        if ($app.Opener) {
            $opener = Join-Path $folder $app.Opener
            $command = '"' + $opener + '" "%1"'
            if (-not (Test-Path $opener)) {
                Write-Host "  $($app.Name) is not built yet; start it once from the Start Menu and" -ForegroundColor Yellow
                Write-Host "  it builds itself, after which what it opens works too." -ForegroundColor Yellow
            }
        } else {
            $command = $start + ' "%1"'
        }
        Register-Types $app $command "$ico,0" $start
        $what = @($app.Types | ForEach-Object { $_.Extensions + $_.Schemes } | Where-Object { $_ })
        Write-Host "  $($app.Name)  ->  $($what -join ' ')" -ForegroundColor Green
        $installed += $app
    }
}
[AppsInstall.Shell]::AssociationsChanged()

Write-Host "`nDone. The apps are in the Start Menu$(if ($onDesktop) { ' and on the Desktop' })." -ForegroundColor Green

# ------------------------------------------------------------ what opens ---
# Registering makes an app a choice; which one is used stays the user's to
# say. So say what the choice is now, type by type, and where to change it.
if ($installed) { Write-Host "`nWhat each opens now:" }
$pending = @()
foreach ($app in $installed) {
    $opens = @(); $not = @()
    foreach ($type in $app.Types) {
        foreach ($name in @($type.Extensions + $type.Schemes | Where-Object { $_ })) {
            if ([AppsInstall.Shell]::Query($name, 20) -eq $type.ProgId) { $opens += $name } else { $not += $name }
        }
    }
    $label = '  {0,-12}' -f $app.Name
    if ($opens) {
        Write-Host ($label + ($opens -join ' ')) -ForegroundColor Green
        $label = ' ' * $label.Length
    }
    if ($not) {
        Write-Host ($label + 'not yet: ' + ($not -join ' '))
        $pending += $app.Name
    }
}
if ($pending) {
    Write-Host "`nWindows lets only you choose what opens a type. To make an app the default for"
    Write-Host "everything it lists, run this again with -Default and its name, which opens its"
    Write-Host "page in Settings - or go to Settings > Apps > Default apps > the app - and press"
    Write-Host "Set default."
}
if ($Default) {
    if ($installed.Name -notcontains $Default) {
        Write-Host "`n$Default was not installed, so there is no page for it." -ForegroundColor Yellow
    } else {
        Start-Process ('ms-settings:defaultapps?registeredAppUser=' + [uri]::EscapeDataString($Default))
        Write-Host "`nSettings is open at $Default. Press Set default at the top." -ForegroundColor Cyan
    }
}
Write-Host "Moved this folder? Run this again. Remove it all with:  windows\install.ps1 -Uninstall`n"

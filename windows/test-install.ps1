<#
    What windows\install.ps1 does to the registry, done to a sandbox of it.

        powershell -ExecutionPolicy Bypass -File windows\test-install.ps1

    The functions are read out of install.ps1 itself, so this tests the file
    that runs, and they are pointed at HKCU\Software\ZzAppsInstallTest rather
    than at the keys every other program shares. Those shared keys are the
    point: an extension's OpenWithProgids holds every editor's claim on it,
    and RegisteredApplications every app's entry in Settings. An installer
    that recreates either instead of adding to it wipes the lot, and nothing
    says so until some other program is missing from "Open with".
#>

$ErrorActionPreference = 'Stop'
$installer = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'install.ps1'

# The installer's functions, without the script around them.
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$null, [ref]$null)
$defined = $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($function in $defined) { . ([scriptblock]::Create($function.Extent.Text)) }

# What they read from the script around them, sandboxed.
$Sandbox = 'HKCU:\Software\ZzAppsInstallTest'
$Classes = "$Sandbox\Classes"
$Registered = "$Sandbox\RegisteredApplications"
$IconDir = Join-Path $Sandbox 'no icons here'
$shell = New-Object -ComObject WScript.Shell
function Get-AppKey($app) { "Software\ZzAppsInstallTest\Apps\$($app.Name)" }

$failures = 0
$checks = 0
function Check([string] $what, [scriptblock] $test) {
    $script:checks++
    $ok = $false
    try { $ok = [bool](& $test) } catch { $what += " ($_)" }
    if ($ok) { Write-Host "  ok    $what" } else { Write-Host "  FAIL  $what"; $script:failures++ }
}

function Names([string] $path) {
    if (Test-Path -LiteralPath $path) { @((Get-Item -LiteralPath $path).GetValueNames() | Sort-Object) } else { @() }
}

function Value([string] $path, [string] $name = '') {
    (Get-Item -LiteralPath $path).GetValue($name)
}

# Every key and value under the sandbox, as one string to compare.
function Snapshot {
    (Get-ChildItem -LiteralPath $Sandbox -Recurse | ForEach-Object {
        $key = $_
        $key.Name
        foreach ($name in ($key.GetValueNames() | Sort-Object)) { "  $name = $($key.GetValue($name))" }
    }) -join "`n"
}

$app = @{ Name = 'Zz App'; Launcher = 'zz-app.bat'; AppId = 'com.example.ZzApp'
          Comment = 'An app that exists for this test'; Browser = $true
          Types = @(
              @{ ProgId = 'ZzApp.Doc'; Name = 'Zz document'; Icon = 'zz-document.ico'
                 Extensions = @('.zzmd', '.zzmkd') }
              @{ ProgId = 'ZzApp.URL'; Name = 'Zz URL'; Icon = 'zz-document.ico'
                 Schemes = @('zzhttp') }) }
$caps = "HKCU:\$(Get-AppKey $app)\Capabilities"
$command = '"C:\zz\zz.exe" "%1"'

try {
    if (Test-Path -LiteralPath $Sandbox) { Remove-Item -LiteralPath $Sandbox -Recurse -Force }
    # What another program left in the shared places, and a choice of the
    # app's .bat made by hand under "Open with".
    New-Item -Path "$Classes\.zzmd\OpenWithProgids" -Force | Out-Null
    Set-ItemProperty -LiteralPath "$Classes\.zzmd\OpenWithProgids" -Name 'Other.Editor' -Value ''
    New-Item -Path $Registered -Force | Out-Null
    Set-ItemProperty -LiteralPath $Registered -Name 'Other Editor' -Value 'Software\Other\Capabilities'
    New-Item -Path "$Classes\Applications\zz-app.bat\shell\open\command" -Force | Out-Null

    Write-Host "`nregistering"
    Register-Types $app $command '"C:\zz\zz.ico",0' '"C:\zz\zz.exe"'
    Remove-DeadChoice $app | Out-Null
    Check 'another program keeps its claim on the type' {
        (Names "$Classes\.zzmd\OpenWithProgids") -contains 'Other.Editor' }
    Check 'and the app makes its own beside it' {
        (Names "$Classes\.zzmd\OpenWithProgids") -contains 'ZzApp.Doc' }
    Check 'another app keeps its entry in Settings' {
        (Names $Registered) -contains 'Other Editor' }
    Check 'and the app has one of its own' {
        (Value $Registered 'Zz App') -eq 'Software\ZzAppsInstallTest\Apps\Zz App\Capabilities' }
    Check 'a file opens with the command given' {
        (Value "$Classes\ZzApp.Doc\shell\open\command") -eq $command }
    Check 'and is named for the app, not the program that runs it' {
        (Value "$Classes\ZzApp.Doc\Application" 'ApplicationName') -eq 'Zz App' -and
        (Value "$Classes\ZzApp.Doc\shell\open" 'FriendlyAppName') -eq 'Zz App' }
    Check 'a document icon that does not exist falls back to the app''s' {
        (Value "$Classes\ZzApp.Doc\DefaultIcon") -eq '"C:\zz\zz.ico",0' }
    Check 'a link type is marked as one, and a file type is not' {
        (Names "$Classes\ZzApp.URL") -contains 'URL Protocol' -and
        (Names "$Classes\ZzApp.Doc") -notcontains 'URL Protocol' }
    Check 'the capabilities list the files and the links' {
        ((Names "$caps\FileAssociations") -join ' ') -eq '.zzmd .zzmkd' -and
        ((Names "$caps\URLAssociations") -join ' ') -eq 'zzhttp' }
    Check 'a browser is listed as one' {
        (Value "$caps\StartMenu" 'StartMenuInternet') -eq 'Zz App' }
    Check 'the .bat chosen by hand is gone' {
        -not (Test-Path -LiteralPath "$Classes\Applications\zz-app.bat") }

    Write-Host "`nagain"
    $before = Snapshot
    Register-Types $app $command '"C:\zz\zz.ico",0' '"C:\zz\zz.exe"'
    Check 'a second run changes nothing' { (Snapshot) -eq $before }
    $app.Types[0].Extensions = @('.zzmd')
    Register-Types $app $command '"C:\zz\zz.ico",0' '"C:\zz\zz.exe"'
    Check 'an extension no longer listed is let go of' {
        (Names "$Classes\.zzmkd\OpenWithProgids") -notcontains 'ZzApp.Doc' -and
        (Names "$caps\FileAssociations") -notcontains '.zzmkd' }
    Check 'and one still listed is kept' {
        (Names "$Classes\.zzmd\OpenWithProgids") -contains 'ZzApp.Doc' }

    Write-Host "`nuninstalling"
    Unregister-Types $app | Out-Null
    Check 'the app''s types are gone' {
        -not (Test-Path -LiteralPath "$Classes\ZzApp.Doc") -and
        -not (Test-Path -LiteralPath "$Classes\ZzApp.URL") }
    Check 'its claim on the type is gone, and only its' {
        ((Names "$Classes\.zzmd\OpenWithProgids") -join ' ') -eq 'Other.Editor' }
    Check 'its entry in Settings is gone, and only its' {
        ((Names $Registered) -join ' ') -eq 'Other Editor' }
    Check 'and so is its own key' {
        -not (Test-Path -LiteralPath "HKCU:\$(Get-AppKey $app)") }

    Write-Host "`nshortcuts"
    $dir = Join-Path ([IO.Path]::GetTempPath()) "apps-install-test-$PID"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    function New-Link([string] $name, [string] $target, [string] $arguments = '') {
        $lnk = Join-Path $dir $name
        $s = $shell.CreateShortcut($lnk)
        $s.TargetPath = $target
        $s.Arguments = $arguments
        $s.Save()
        $lnk
    }
    $notedown = @{ Folder = 'Notedown'; Entry = 'notedown.py' }
    $moved = New-Link 'Notedown.lnk' 'C:\msys64\mingw64\bin\pythonw.exe' '"D:\moved away\Apps\Notedown\notedown.py"'
    $other = New-Link 'Other.lnk' 'C:\Windows\notepad.exe'
    $direct = New-Link 'Lyndon.lnk' 'D:\moved away\Apps\Lyndon Browser\dist\lyndon.exe'
    Check 'one of ours is known by what it runs, wherever that went' { Test-Ours $moved $notedown }
    Check 'someone else''s is left alone' { -not (Test-Ours $other $notedown) }
    Check 'and one that is not there is nobody''s' { -not (Test-Ours (Join-Path $dir 'none.lnk') $notedown) }
    Check 'as is an older one that ran the browser directly' {
        Test-Ours $direct @{ Folder = 'Lyndon Browser'; Entry = 'win32\launch.py'; Opener = 'dist\lyndon.exe' } }
    Remove-Item -LiteralPath $dir -Recurse -Force
} finally {
    if (Test-Path -LiteralPath $Sandbox) { Remove-Item -LiteralPath $Sandbox -Recurse -Force }
}

Write-Host "`n$checks checks, $failures failed"
if ($failures) { exit 1 }
Write-Host 'all good'

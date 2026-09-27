$ErrorActionPreference = 'SilentlyContinue'

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $MyInvocation.MyCommand.Path)
    )
    exit
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
New-Item -Path (Join-Path $repoRoot '.turzx-stop') -ItemType File -Force | Out-Null
$watchdogPidFile = Join-Path $repoRoot '.turzx-watchdog.pid'

$pythonParents = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$'
} | Select-Object -ExpandProperty ParentProcessId -Unique

$names = @(
    'python.exe',
    'pythonw.exe',
    'USBMonitor.exe',
    'ExtendScreen.exe',
    'SmartDisplay.exe',
    'SmartMonitor.exe',
    'UsbPCMonitor.exe'
)

foreach ($name in $names) {
    taskkill /F /IM $name | Out-Null
}

$procs = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^pythonw?\\.exe$') -and ($_.CommandLine -match 'turing-smart-screen-python|main.py|start-turzx.ps1')
}

foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force
}

if (Test-Path $watchdogPidFile) {
    $watchdogPid = Get-Content $watchdogPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($watchdogPid) {
        Stop-Process -Id ([int]$watchdogPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $watchdogPidFile -Force -ErrorAction SilentlyContinue
}

foreach ($parentId in $pythonParents) {
    $parent = Get-Process -Id $parentId -ErrorAction SilentlyContinue
    if ($parent -and $parent.ProcessName -eq 'powershell') {
        Stop-Process -Id $parentId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host 'TURZX monitor stop command completed.'

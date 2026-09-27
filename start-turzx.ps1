$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = 'F:\Scratch\tester\.venv\Scripts\python.exe'
$libusbDir = Join-Path $repoRoot 'external\libusb-1.0'

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

if (-not (Test-Path $pythonExe)) {
    throw "Python environment not found at $pythonExe"
}

$env:PATH = "$libusbDir;$env:PATH"
Set-Location $repoRoot

$stopMarker = Join-Path $repoRoot '.turzx-stop'
$watchdogPidFile = Join-Path $repoRoot '.turzx-watchdog.pid'

if (Test-Path $watchdogPidFile) {
    $existingWatchdogPid = Get-Content $watchdogPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existingWatchdogPid -and ([int]$existingWatchdogPid) -ne $PID) {
        Stop-Process -Id ([int]$existingWatchdogPid) -Force -ErrorAction SilentlyContinue
    }
}

$stalePython = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^pythonw?\.exe$') -and ($_.CommandLine -match 'turing-smart-screen-python|main.py')
}

foreach ($process in $stalePython) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Remove-Item $stopMarker -Force -ErrorAction SilentlyContinue
Set-Content -Path $watchdogPidFile -Value $PID -Encoding ASCII

try {
    while (-not (Test-Path $stopMarker)) {
        $monitor = Start-Process -FilePath $pythonExe -ArgumentList 'main.py' -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden
        Wait-Process -Id $monitor.Id

        if (-not (Test-Path $stopMarker)) {
            Start-Sleep -Seconds 3
        }
    }
}
finally {
    Remove-Item $watchdogPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item $stopMarker -Force -ErrorAction SilentlyContinue
}


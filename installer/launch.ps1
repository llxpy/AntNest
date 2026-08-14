# AntNest launcher (installed build entry point).
# Called by the Start Menu / Desktop shortcut with a hidden window:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<install dir>\launch.ps1"
# Uses the shared uv_helper for all uv/WebView2 logic and ALWAYS launches the
# UI through `uv run` (no direct python.exe console path -> no black boxes).
$ErrorActionPreference = "SilentlyContinue"

# Load shared helpers (this script is deployed next to uv_helper.ps1).
. "$PSScriptRoot\uv_helper.ps1"

$app = "$env:LOCALAPPDATA\AntNest"
$env:ANT_INSTALLED = "1"
Set-Location $app

# 1. Prereqs: uv + WebView2 runtime (idempotent; skip via marker on later runs)
$prereqMarker = Join-Path $env:LOCALAPPDATA "AntNest\.prereq_ok"
$runPrereq = $true
if (Test-Path $prereqMarker) {
    if ((Find-Uv) -and (Test-WebView2Installed)) { $runPrereq = $false }
}
if ($runPrereq) {
    if (-not (Find-Uv)) { $null = Install-Uv }
    if (-not (Test-WebView2Installed)) { $null = Install-WebView2 }
    if ((Find-Uv) -and (Test-WebView2Installed)) {
        $markerDir = Join-Path $env:LOCALAPPDATA "AntNest"
        if (-not (Test-Path $markerDir)) { New-Item -ItemType Directory -Path $markerDir -Force | Out-Null }
        Set-Content -Path $prereqMarker -Value "" -Force
    }
}

# 2. Ensure uv is available
$uv = Find-Uv
if (-not $uv) {
    Write-Warning "AntNest: uv missing and install failed; cannot start."
    exit 1
}

# 3. Launch the UI through `uv run` (auto-creates venv + installs pywebview,
#    then runs the app - all with no console window).
$logDir = Join-Path $env:LOCALAPPDATA "AntNest"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir "launch.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value "`n=== AntNest launch $stamp ==="

# uv run prints to its own (hidden) console; tee to the log for diagnostics.
& $uv run --project $app python "$app\prototype_antnest.py" 2>&1 | Tee-Object -FilePath $logFile -Append

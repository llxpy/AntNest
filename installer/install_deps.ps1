# AntNest install-time dependency setup.
# Installs uv, ensures the WebView2 runtime, then uses uv to pre-install the
# Python deps (pywebview) into the app's .venv so first launch is instant.
# Called by Inno Setup [Run]. Runs in a VISIBLE window so progress is shown.
# Uses the shared uv_helper for all uv/WebView2 logic.
$ErrorActionPreference = "SilentlyContinue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Load shared helpers (this script is deployed next to uv_helper.ps1).
. "$PSScriptRoot\uv_helper.ps1"

# 1. ensure uv (install if missing)
$uv = Find-Uv
if (-not $uv) {
    Write-Host "[AntNest] Installing uv ..."
    if (-not (Install-Uv)) {
        Write-Warning "[AntNest] uv install failed. Install manually: https://docs.astral.sh/uv/"
        exit 1
    }
    $uv = Find-Uv
}

# 2. ensure WebView2 runtime (best-effort, time-boxed to avoid hanging the installer)
if (-not (Test-WebView2Installed)) {
    Write-Host "[AntNest] WebView2 runtime not found. Downloading (this can take a while) ..."
    if (-not (Install-WebView2)) {
        Write-Host "[AntNest] WebView2 install failed/timed out; will retry on first launch."
    }
}

# 3. pre-install Python deps via uv (creates .venv + installs pywebview)
Write-Host "[AntNest] Installing Python dependencies via uv (pywebview) ..."
& $uv run --project $scriptDir python -c "import pywebview; import api_compat; import antnest_bridge; print('AntNest deps ready')"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[AntNest] Dependency install reported an issue; the app will retry on first launch."
}

Write-Host "[AntNest] Setup complete."

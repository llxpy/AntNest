# AntNest launcher (installed build entry point).
# Called by the Start Menu / Desktop shortcut with a hidden window:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<install dir>\launch.ps1"
$ErrorActionPreference = "SilentlyContinue"

$app = "$env:LOCALAPPDATA\AntNest"
$env:ANT_INSTALLED = "1"
Set-Location $app

# 1. Prereqs: uv + WebView2 runtime (idempotent)
& "$app\ensure_prereqs.ps1"

# 2. Ensure uv is available
$uv = "$env:LOCALAPPDATA\uv\uv.exe"
if (-not (Test-Path $uv)) {
    try { irm https://astral.sh/uv/install.ps1 | iex } catch { Write-Warning "uv missing, cannot start"; exit 1 }
}

# 3. uv run: auto-create venv + install pywebview, then launch the UI
& $uv run --quiet --project $app python "$app\prototype_antnest.py"

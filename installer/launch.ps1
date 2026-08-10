# AntNest launcher (installed build entry point).
# Called by the Start Menu / Desktop shortcut with a hidden window:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<install dir>\launch.ps1"
$ErrorActionPreference = "SilentlyContinue"

$app = "$env:LOCALAPPDATA\AntNest"
$env:ANT_INSTALLED = "1"
Set-Location $app

# 1. Prereqs: uv + WebView2 runtime (idempotent, skipped via marker on later runs)
$prereqMarker = Join-Path $env:LOCALAPPDATA "AntNest\.prereq_ok"
$runPrereq = $true
if (Test-Path $prereqMarker) {
    $uvReady = (Get-Command uv -ErrorAction SilentlyContinue) -or
               (Test-Path (Join-Path $env:LOCALAPPDATA "uv\uv.exe")) -or
               (Test-Path (Join-Path $env:USERPROFILE ".local\bin\uv.exe"))
    $wv2Ready = $false
    $wv2Paths = @()
    if ($env:ProgramFiles)        { $wv2Paths += (Join-Path $env:ProgramFiles "Microsoft\EdgeWebView") }
    if (${env:ProgramFiles(x86)}) { $wv2Paths += (Join-Path ${env:ProgramFiles(x86)} "Microsoft\EdgeWebView") }
    foreach ($p in $wv2Paths) { if (Test-Path $p) { $wv2Ready = $true; break } }
    if ($uvReady -and $wv2Ready) { $runPrereq = $false }
}
if ($runPrereq) { & "$app\ensure_prereqs.ps1" }

# 2. Ensure uv is available
$uv = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uv = (Get-Command uv).Source
} else {
    $localUv = Join-Path $env:LOCALAPPDATA "uv\uv.exe"
    $userUv  = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $localUv) {
        $uv = $localUv
    } elseif (Test-Path $userUv) {
        $uv = $userUv
    } else {
        try { irm https://astral.sh/uv/install.ps1 | iex } catch { Write-Warning "uv missing, cannot start"; exit 1 }
        if (Test-Path $localUv) { $uv = $localUv }
        elseif (Test-Path $userUv) { $uv = $userUv }
    }
}
if (-not $uv) { Write-Warning "uv missing, cannot start"; exit 1 }

# 3. uv run: auto-create venv + install pywebview, then launch the UI
& $uv run --quiet --project $app python "$app\prototype_antnest.py"

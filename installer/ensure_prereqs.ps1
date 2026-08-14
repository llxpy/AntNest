# AntNest prereq ensure (idempotent, offline-friendly).
# Detects already-installed uv / WebView2 from the filesystem/PATH without
# touching the network, and only downloads when a prereq is genuinely missing.
# On success it writes a marker so the launcher can skip this step later.
# Uses the shared uv_helper for all uv/WebView2 logic.
$ErrorActionPreference = "SilentlyContinue"

# Load shared helpers (this script is deployed next to uv_helper.ps1).
. "$PSScriptRoot\uv_helper.ps1"

$markerDir = Join-Path $env:LOCALAPPDATA "AntNest"
$marker    = Join-Path $markerDir ".prereq_ok"

$uvOk  = $null -ne (Find-Uv)
$wv2Ok = Test-WebView2Installed

if ($uvOk -and $wv2Ok) {
    if (-not (Test-Path $markerDir)) { New-Item -ItemType Directory -Path $markerDir -Force | Out-Null }
    Set-Content -Path $marker -Value "" -Force
    Write-Host "[AntNest] prereq check done (already satisfied)."
    exit 0
}

# At least one prereq is missing -> install only what's missing (network needed).
if (-not $uvOk) {
    Write-Host "[AntNest] installing uv ..."
    $null = Install-Uv
}
if (-not $wv2Ok) {
    Write-Host "[AntNest] installing WebView2 runtime ..."
    $null = Install-WebView2
}

# Re-check; write marker only if both are now present.
if (($null -ne (Find-Uv)) -and (Test-WebView2Installed)) {
    if (-not (Test-Path $markerDir)) { New-Item -ItemType Directory -Path $markerDir -Force | Out-Null }
    Set-Content -Path $marker -Value "" -Force
}

Write-Host "[AntNest] prereq check done."

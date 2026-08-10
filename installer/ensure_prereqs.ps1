# AntNest prereq check: ensure uv and the WebView2 runtime exist.
# Called by launcher.ps1 / launch.ps1. Idempotent and OFFLINE-FRIENDLY:
# detects already-installed states from the filesystem/PATH without touching
# the network, and only downloads when a prereq is genuinely missing.
# On success it writes a marker so the launcher can skip this script later.
$ErrorActionPreference = "SilentlyContinue"

$markerDir = Join-Path $env:LOCALAPPDATA "AntNest"
$marker    = Join-Path $markerDir ".prereq_ok"

# 1. uv (Astral) -- check PATH first (covers modern installs in ~/.local/bin),
#    then the legacy %LOCALAPPDATA%\uv location.
function Test-UvInstalled {
    if (Get-Command uv -ErrorAction SilentlyContinue) { return $true }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uvx\uv.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $true }
    }
    return $false
}

# 2. WebView2 runtime -- filesystem detection first (Win11 China builds may
#    have it installed without the EdgeUpdate registry key), registry as backup.
function Test-WebView2Installed {
    $roots = @()
    if ($env:ProgramFiles)        { $roots += (Join-Path $env:ProgramFiles "Microsoft\EdgeWebView\Application") }
    if (${env:ProgramFiles(x86)}) { $roots += (Join-Path ${env:ProgramFiles(x86)} "Microsoft\EdgeWebView\Application") }
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $bins = Get-ChildItem -Path $root -Recurse -Filter "msedgewebview2.exe" -ErrorAction SilentlyContinue
            if ($bins.Count -gt 0) { return $true }
        }
    }
    $regs = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}",
        "HKCU:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}"
    )
    foreach ($r in $regs) {
        if (Test-Path $r) { return $true }
    }
    return $false
}

function Write-Marker {
    if (-not (Test-Path $markerDir)) { New-Item -ItemType Directory -Path $markerDir -Force | Out-Null }
    Set-Content -Path $marker -Value "" -Force
}

$uvOk  = Test-UvInstalled
$wv2Ok = Test-WebView2Installed

if ($uvOk -and $wv2Ok) {
    Write-Marker
    Write-Host "[AntNest] prereq check done (already satisfied)."
    exit 0
}

# At least one prereq is missing -> install only what's missing (network needed).
if (-not $uvOk) {
    try {
        Write-Host "[AntNest] installing uv ..."
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Write-Warning "uv install failed, install manually: https://docs.astral.sh/uv/"
    }
}

if (-not $wv2Ok) {
    try {
        Write-Host "[AntNest] installing WebView2 runtime ..."
        $tmp = "$env:TEMP\wv2_setup.exe"
        Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=218470" -OutFile $tmp
        Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -Wait
    } catch {
        Write-Warning "WebView2 install failed, install manually: https://developer.microsoft.com/microsoft-edge/webview2/"
    }
}

# Re-check; write marker only if both are now present.
if ((Test-UvInstalled) -and (Test-WebView2Installed)) {
    Write-Marker
}

Write-Host "[AntNest] prereq check done."

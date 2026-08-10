# AntNest install-time dependency setup.
# Installs uv, ensures the WebView2 runtime, then uses uv to pre-install the
# Python deps (pywebview) into the app's .venv so first launch is instant.
# Called by Inno Setup [Run]. Runs in a VISIBLE window so progress is shown.
$ErrorActionPreference = "SilentlyContinue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# 1. install uv (Astral) if missing
function Find-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) { return (Get-Command uv).Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}
$uv = Find-Uv
if (-not $uv) {
    Write-Host "[AntNest] Installing uv ..."
    irm https://astral.sh/uv/install.ps1 | iex
    $uv = Find-Uv
}
if (-not $uv) {
    Write-Warning "[AntNest] uv install failed. Install manually: https://docs.astral.sh/uv/"
    exit 1
}

# 2. ensure WebView2 runtime (best-effort, time-boxed to avoid hanging the installer)
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
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}"
    )
    foreach ($r in $regs) { if (Test-Path $r) { return $true } }
    return $false
}
if (-not (Test-WebView2Installed)) {
    Write-Host "[AntNest] WebView2 runtime not found. Downloading (this can take a while) ..."
    $tmp = "$env:TEMP\wv2_setup.exe"
    try {
        Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=218470" -OutFile $tmp
        Write-Host "[AntNest] Installing WebView2 runtime (silent) ..."
        $proc = Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -PassThru
        $finished = $proc.Wait(180000)
        if (-not $finished) {
            Write-Host "[AntNest] WebView2 install timed out; will retry on first launch."
            try { $proc.Kill() } catch {}
        }
    } catch {
        Write-Host "[AntNest] WebView2 install failed; will retry on first launch."
    }
}

# 3. pre-install Python deps via uv (creates .venv + installs pywebview)
Write-Host "[AntNest] Installing Python dependencies via uv (pywebview) ..."
& $uv run --project $scriptDir python -c "import pywebview; print('AntNest deps ready')"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[AntNest] Dependency install reported an issue; the app will retry on first launch."
}

Write-Host "[AntNest] Setup complete."

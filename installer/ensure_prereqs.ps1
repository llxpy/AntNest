# AntNest prereq check: ensure uv and the WebView2 runtime exist.
# Called by the Inno Setup installer and by launch.ps1. Idempotent.
$ErrorActionPreference = "SilentlyContinue"

# 1. uv (Astral)
$uv = "$env:LOCALAPPDATA\uv\uv.exe"
if (-not (Test-Path $uv)) {
    try {
        Write-Host "[AntNest] installing uv ..."
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Write-Warning "uv install failed, install manually: https://docs.astral.sh/uv/"
    }
}

# 2. WebView2 runtime (pywebview default backend on Windows)
$reg = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}"
if (-not (Test-Path $reg)) {
    try {
        Write-Host "[AntNest] installing WebView2 runtime ..."
        $tmp = "$env:TEMP\wv2_setup.exe"
        Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=218470" -OutFile $tmp
        Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -Wait
    } catch {
        Write-Warning "WebView2 install failed, install manually: https://developer.microsoft.com/microsoft-edge/webview2/"
    }
}

Write-Host "[AntNest] prereq check done."

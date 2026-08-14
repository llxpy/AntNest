# AntNest launcher - compiled to AntNest.exe via ps2exe.
# Double-click AntNest.exe -> launch desktop UI, no console window.
# Self-contained (no external dot-sources) so the compiled exe works standalone.
# IMPORTANT: the UI is always launched through `uv run`, never a direct
# python/pythonw Start-Process. uv runs hidden (no console); the python it
# spawns inherits that windowless state, and every subprocess python later
# forks (workers, MCP, session checks) inherits it too -> zero black boxes.
# NOTE: keep this file ASCII-only (no non-English chars) - ps2exe / PowerShell
# parse it under the system ANSI codepage on some machines and CJK breaks it.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

function Show-Error([string]$msg) {
    try {
        [System.Windows.Forms.MessageBox]::Show($msg, "AntNest") | Out-Null
    } catch {}
    exit 1
}

# Where this (compiled) exe lives = install dir
$exePath = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
$app = Split-Path -Parent $exePath

$entry   = Join-Path $app "prototype_antnest.py"
$project = Join-Path $app "pyproject.toml"

# Dev tree (git + pyproject present) -> keep using project config;
# otherwise mark as installed so config resolves to %LOCALAPPDATA%\AntNest
$isDev = (Test-Path (Join-Path $app ".git")) -and (Test-Path $project)
if (-not $isDev) { $env:ANT_INSTALLED = "1" }

if (-not (Test-Path $entry)) {
    Show-Error "AntNest: prototype_antnest.py not found next to the exe. The install is broken; reinstall AntNest."
}

# --- Locate uv (PATH first, then known install locations) -----------------
function Find-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) { return (Get-Command uv).Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uvx\uv.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    # No uv on the machine: install it once, then re-locate.
    try {
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Show-Error "AntNest: uv is required but could not be installed. Install it manually from https://docs.astral.sh/uv/ and retry."
    }
    $uv = Find-Uv
    if (-not $uv) {
        Show-Error "AntNest: uv is required and still missing after install attempt. Install uv manually and retry."
    }
}

# --- Best-effort WebView2 runtime check (only installing if missing) ------
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
    try {
        $tmp = "$env:TEMP\wv2_setup.exe"
        Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=218470" -OutFile $tmp
        $p = Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -PassThru -WindowStyle Hidden
        $null = $p.WaitForExit(180000)
    } catch {
        # Non-fatal: the UI shows a clear error if WebView2 is truly absent.
    }
}

# --- Launch the UI through `uv run` (no console window) --------------------
# uv is started hidden; python + all its subprocesses inherit that windowless
# state, so there are no black boxes regardless of how the exe was started.
$p = Start-Process -FilePath $uv -ArgumentList "run", "--project", $app, "python", $entry `
    -WorkingDirectory $app -PassThru -WindowStyle Hidden

if ($p) { $p.WaitForExit() }

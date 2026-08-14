# AntNest shared uv / WebView2 helpers.
# Dot-source from launch.ps1 / install_deps.ps1 / ensure_prereqs.ps1:
#   . (Join-Path $PSScriptRoot "uv_helper.ps1")   # when deployed next to this file
#   . "$PSScriptRoot\uv_helper.ps1"
#
# Single source of truth for "where is uv", "is WebView2 present",
# "install the missing prereqs", and "launch the UI the no-console way".
# NOTE: keep this file ASCII-only (ps2exe / PowerShell parse it under the
# system ANSI codepage on some machines and CJK breaks it).

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

# --- WebView2 runtime detection (filesystem first, registry as backup) -----
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

# --- Installers (network needed) ------------------------------------------
function Install-Uv {
    try {
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Write-Warning "[AntNest] uv install failed: $_"
        return $false
    }
    return ($null -ne (Find-Uv))
}

function Install-WebView2 {
    $tmp = "$env:TEMP\wv2_setup.exe"
    try {
        Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=218470" -OutFile $tmp
        $proc = Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -PassThru
        $finished = $proc.Wait(180000)
        if (-not $finished) { try { $proc.Kill() } catch {} ; return $false }
        return $true
    } catch {
        Write-Warning "[AntNest] WebView2 install failed: $_"
        return $false
    }
}

# --- Launch the AntNest UI through `uv run` (no console window) ------------
# $ProjectRoot = dir containing pyproject.toml / prototype_antnest.py.
# uv is started hidden; the python it spawns inherits uv's windowless state,
# so python AND every subprocess it forks (workers, MCP, session checks)
# inherit "no console" -> zero black boxes. Returns the child exit code.
function Start-AntNestUi([string]$ProjectRoot, [string]$Uv) {
    $entry = Join-Path $ProjectRoot "prototype_antnest.py"
    if (-not (Test-Path $entry)) {
        Write-Warning "[AntNest] entry not found: $entry"
        return 1
    }
    $p = Start-Process -FilePath $Uv -ArgumentList "run", "--project", $ProjectRoot, "python", $entry `
        -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden
    if ($p) { $p.WaitForExit() }
    return $p.ExitCode
}

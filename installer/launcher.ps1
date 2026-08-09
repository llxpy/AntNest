# AntNest launcher - compiled to AntNest.exe via ps2exe (see build_launcher.ps1).
# Double-click AntNest.exe to launch the desktop UI. No console window.
# This file is the SOURCE for the exe; it is also harmless to run directly.
# NOTE: keep this file ASCII-only (no non-English chars) - ps2exe / PowerShell
# parse it under the system ANSI codepage on some machines and CJK breaks it.

Add-Type -AssemblyName System.Windows.Forms

try {
    # ps2exe extracts to a temp dir, so $PSScriptRoot is NOT the install dir.
    # The real install dir is where this (compiled) exe lives:
    $exePath = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    $app = Split-Path -Parent $exePath

    # Route runtime state to a writable location (%LOCALAPPDATA%\AntNest)
    $env:ANT_INSTALLED = "1"

    # Ensure uv + WebView2 runtime (idempotent; ensure_prereqs.ps1 ships next to this exe)
    $prereq = Join-Path $app "ensure_prereqs.ps1"
    if (Test-Path $prereq) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $prereq | Out-Null
    }

    # Locate uv (prefer on PATH, else the uv installer's location)
    $uv = $null
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $uv = (Get-Command uv).Source
    } else {
        $localUv = Join-Path $env:LOCALAPPDATA "uv\uv.exe"
        if (Test-Path $localUv) {
            $uv = $localUv
            $env:PATH = "$env:LOCALAPPDATA\uv;" + $env:PATH
        } else {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
            if (Test-Path $localUv) {
                $uv = $localUv
                $env:PATH = "$env:LOCALAPPDATA\uv;" + $env:PATH
            }
        }
    }

    if (-not $uv) {
        [System.Windows.Forms.MessageBox]::Show("Could not find or install uv; AntNest cannot start. Check your network and retry.", "AntNest") | Out-Null
        exit 1
    }

    # Launch detached so this launcher can exit; the app (uv -> python -> WebView2) keeps running.
    Start-Process -FilePath $uv -ArgumentList "run", "--project", $app, "python", "prototype_antnest.py" -WorkingDirectory $app -WindowStyle Hidden
} catch {
    [System.Windows.Forms.MessageBox]::Show("AntNest failed to start: $_", "AntNest") | Out-Null
    exit 1
}

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

    # Ensure uv + WebView2 runtime.
    # On later launches skip the full prereq script via a marker written once both
    # deps are confirmed present; a fast local re-check (no network) keeps it honest.
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
    if ($runPrereq) {
        $prereq = Join-Path $app "ensure_prereqs.ps1"
        if (Test-Path $prereq) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $prereq | Out-Null
        }
    }

    # Locate uv (prefer on PATH, else the uv installer's location)
    $uv = $null
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $uv = (Get-Command uv).Source
    } else {
        $localUv = Join-Path $env:LOCALAPPDATA "uv\uv.exe"
        $userUv  = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
        if (Test-Path $localUv) {
            $uv = $localUv
            $env:PATH = "$env:LOCALAPPDATA\uv;" + $env:PATH
        } elseif (Test-Path $userUv) {
            $uv = $userUv
            $env:PATH = "$env:USERPROFILE\.local\bin;" + $env:PATH
        } else {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
            if (Test-Path $localUv) {
                $uv = $localUv
                $env:PATH = "$env:LOCALAPPDATA\uv;" + $env:PATH
            } elseif (Test-Path $userUv) {
                $uv = $userUv
                $env:PATH = "$env:USERPROFILE\.local\bin;" + $env:PATH
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

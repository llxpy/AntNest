# AntNest boot - compiled to AntNest.exe via ps2exe (see build_launcher.ps1).
# Start Menu / Desktop shortcuts point at the exe (AntNest.iss [Icons]).
#
# ASCII ONLY in this file. ps2exe / Windows PowerShell 5.1 may parse the
# compiled form under the system ANSI code page, where CJK breaks it. The file
# is therefore saved with a UTF-8 BOM (which forces UTF-8 decoding) and
# contains no non-ASCII characters at all. All user-facing text goes through
# Resolve-Msg, which keeps the English source and CJK strings in one place.
#
# SELF-CONTAINED BY DESIGN. ps2exe compiles a single file; dot-sourcing
# uv_helper.ps1 is impossible here. That means the two uv/WebView2 probes
# below exist in two places:
#     installer/antnest_boot.ps1   (this file - exe path)
#     installer/uv_helper.ps1      (install_deps / ensure_prereqs)
# If you change either, change the other. Test T1 in
# tests/test_installer_entry.py fails when a function is renamed or removed.
#
# WHAT THIS FILE DOES NOT DO, on purpose:
#   * It does NOT check for an API key. A missing key already produces a
#     specific, actionable Chinese message inside the chat pane
#     (antnest_bridge.py:1192 renders the SystemExit text captured at :772).
#     Blocking here would stop the app, and with it the settings page that is
#     the only self-service way to set the key. See docs/v1.4.1-BOOT-DESIGN.md
#     section 0.3.
#   * It does NOT use Start-Process -RedirectStandard*. That forces
#     UseShellExecute=false, which makes ProcessStartInfo.WindowStyle inert;
#     the parent has no console (ps2exe -noConsole) so a console child would
#     allocate a fresh VISIBLE console. That would break the zero-black-box
#     invariant this launcher exists to protect (test T9).
#   * It does NOT time out the child. A healthy app runs until the user
#     closes the window, so any timeout fires on every successful start
#     (test T10).
#
# On failure we read the app's OWN log files instead of capturing child
# stdout: antnest.log already contains the full original error text.

$ErrorActionPreference = "Stop"
$BOOT_SCHEMA = "v1.4.1-b2"

# ---- user-facing strings -------------------------------------------------
# Kept here (not inline) so this file stays ASCII. Keys are intentionally
# boring so a missing key is obvious in the source.
$Msg = @{
    MsgNotInstalled = "AntNest files are missing. The install looks damaged; please reinstall AntNest."
    UvMissing       = "AntNest needs 'uv' to run, and it could not be installed automatically."
    UvMissingUrl    = "Install it manually from https://docs.astral.sh/uv/ then start AntNest again."
    BootFailed      = "AntNest failed to start."
    ChildFailed     = "AntNest did not start (exit code {0})."
    LogAt           = "Log location:"
    AppLogAt        = "App log:"
    NoAppLog        = "(no app log was written)"
}

function Show-Error([string]$text) {
    # Param is named $text, not $msg, on purpose: PowerShell variable names
    # are case-insensitive and this file already has a script-level hashtable
    # named $Msg. With a same-named variable, any script-scope code writing
    # $msg silently overwrites $Msg with a string and every message in the
    # launcher renders empty. (Hit this for real while testing.)
    # WinForms may be unavailable. Degrade to log-only rather than dying
    # inside the error path and losing the message entirely.
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        [System.Windows.Forms.MessageBox]::Show($text, "AntNest") | Out-Null
    } catch {
        # nothing better to do than write it down
    }
}

# ---- log ----------------------------------------------------------------
$script:LogFile = ""
function Write-BootLog([string]$line) {
    if (-not $script:LogFile) { return }
    try {
        $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        Add-Content -Path $script:LogFile -Value "$stamp  $line" -Encoding UTF8
    } catch {
        # logging must never throw
    }
}

function Open-BootLog([string]$appDir) {
    $dir = Join-Path $env:LOCALAPPDATA "AntNest\logs"
    try {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $script:LogFile = Join-Path $dir "startup.log"
    } catch {
        $script:LogFile = ""   # P2: degrade, do not block startup
    }
}

# ---- app log tails (the real diagnostics) --------------------------------
# antnest.log and ui_trace.log are written by the app itself. Reading them
# is strictly better than capturing the child's stdout: no extra failure
# surface, and they already contain full error text with paths.
function Get-AppLogTail([string]$appDir, [string]$name, [int]$lines) {
    $p = Join-Path $env:LOCALAPPDATA "AntNest\.antnest\$name"
    try {
        if (-not (Test-Path $p)) { return $null }
        $all = @(Get-Content -Path $p -Tail $lines -ErrorAction Stop)
        if ($all.Count -eq 0) { return $null }
        return ($all -join "`n")
    } catch {
        return $null
    }
}

function Report-ChildFailure([int]$code, [string]$appDir) {
    Write-BootLog "child exited with code $code"
    $tail = Get-AppLogTail $appDir "antnest.log" 12
    $uiTail = Get-AppLogTail $appDir "ui_trace.log" 8

    $text = [string]::Format($Msg.ChildFailed, $code)
    if ($tail) {
        $text += "`n`n" + $Msg.AppLogAt + "`n" + $tail
    } else {
        $text += "`n`n" + $Msg.NoAppLog
    }
    if ($uiTail) { $text += "`n`n" + $Msg.LogAt + " ui_trace.log`n" + $uiTail }
    if ($script:LogFile) { $text += "`n`n" + $Msg.LogAt + "`n" + $script:LogFile }
    Show-Error $text
}

# ---- uv -----------------------------------------------------------------
# Fingerprint pair: keep in sync with installer\uv_helper.ps1 :: Find-Uv
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

function Install-Uv {
    try {
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Write-BootLog ("uv install failed: " + $_.Exception.Message)
        return $false
    }
    return ($null -ne (Find-Uv))
}

# ---- WebView2 -----------------------------------------------------------
# Fingerprint pair: keep in sync with installer\uv_helper.ps1 :: Test-WebView2Installed
# Registry keys are what Edge Update maintains, and they cost ~1ms. The
# recursive scan below costs hundreds of ms and used to run on EVERY launch.
# So: registry first, recursive scan as fallback.
function Test-WebView2Installed {
    $regs = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}"
    )
    foreach ($r in $regs) {
        if (Test-Path $r) { return $true }
    }
    $roots = @()
    if ($env:ProgramFiles)        { $roots += (Join-Path $env:ProgramFiles "Microsoft\EdgeWebView\Application") }
    if (${env:ProgramFiles(x86)}) { $roots += (Join-Path ${env:ProgramFiles(x86)} "Microsoft\EdgeWebView\Application") }
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $bins = Get-ChildItem -Path $root -Recurse -Filter "msedgewebview2.exe" -ErrorAction SilentlyContinue
            if ($bins.Count -gt 0) { return $true }
        }
    }
    return $false
}

function Install-WebView2 {
    # Non-fatal by design: if this fails the app still starts and reports the
    # missing runtime from inside Python, which is more accurate than we can
    # be from here. (design section 3.2, P4)
    try {
        $tmp = Join-Path $env:TEMP "wv2_setup.exe"
        Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=218470" -OutFile $tmp
        $p = Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -PassThru -WindowStyle Hidden
        $null = $p.WaitForExit(180000)
    } catch {
        Write-BootLog ("WebView2 install failed: " + $_.Exception.Message)
        return $false
    }
    return $true
}

# ---- preflight ----------------------------------------------------------
# Only P1 and P3 may stop startup, and only because the app is guaranteed to
# fail in those cases. Everything else degrades.
function Assert-InstallComplete([string]$appDir) {
    $required = @("prototype_antnest.py", "pyproject.toml", "antnest_bridge.py")
    $missing = @()
    foreach ($f in $required) {
        if (-not (Test-Path (Join-Path $appDir $f))) { $missing += $f }
    }
    if ($missing.Count -gt 0) {
        throw ("missing: " + ($missing -join ", "))
    }
}

function Get-FreeSpaceMB([string]$path) {
    try {
        $root = (Get-Item $path).PSDrive
        return [int]($root.Free / 1MB)
    } catch {
        return -1
    }
}

# ---- main ---------------------------------------------------------------
function Start-AntNest {
    # Resolve the app dir. A compiled ps2exe image reports itself via
    # MainModule, which is correct. But running this file UNCOMPILED makes
    # MainModule = powershell.exe, so $app would be System32 and the
    # preflight would blame C:\Windows\System32 for a missing file. Prefer
    # $PSScriptRoot when it actually looks like the app dir.
    $app = $null
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
        $app = $PSScriptRoot
    } else {
        $exePath = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
        $app = Split-Path -Parent $exePath
    }

    Open-BootLog $app
    Write-BootLog ("schema=" + $BOOT_SCHEMA + " app=" + $app)

    # Dev tree (git + pyproject present) -> keep project config;
    # otherwise mark as installed so config resolves to %LOCALAPPDATA%\AntNest
    $isDev = (Test-Path (Join-Path $app ".git"))
    if (-not $isDev) { $env:ANT_INSTALLED = "1" }

    # --- P1: install completeness (may stop: app cannot start otherwise) ---
    try {
        Assert-InstallComplete $app
    } catch {
        Write-BootLog ("P1 FAILED: " + $_.Exception.Message)
        Show-Error ($Msg.MsgNotInstalled + "`n`n" + $_.Exception.Message)
        return
    }

    # --- P5: disk space (warn only) ---
    $free = Get-FreeSpaceMB $app
    if ($free -ge 0 -and $free -lt 500) {
        Write-BootLog "P5 WARN: only ${free}MB free"
    }

    # --- P3: uv (may stop: nothing can run without it) ---
    $uv = Find-Uv
    if (-not $uv) {
        Write-BootLog "P3: uv not found, installing"
        $null = Install-Uv
        $uv = Find-Uv
    }
    if (-not $uv) {
        Write-BootLog "P3 FAILED: uv still missing"
        Show-Error ($Msg.UvMissing + "`n`n" + $Msg.UvMissingUrl)
        return
    }
    Write-BootLog ("P3 ok: uv=" + $uv)

    # --- P4: WebView2 (registry-first; best effort, never blocks) ---
    if (-not (Test-WebView2Installed)) {
        Write-BootLog "P4: WebView2 missing, installing"
        $null = Install-WebView2
    } else {
        Write-BootLog "P4 ok: WebView2 present"
    }

    # --- launch ---
    # UseShellExecute stays TRUE and the window is Hidden: the child (and
    # every python it forks later - workers, MCP, session checks) inherits
    # the windowless state. Do not add -RedirectStandard*; see header.
    $entry = Join-Path $app "prototype_antnest.py"
    Write-BootLog "launching uv run"
    $p = Start-Process -FilePath $uv `
        -ArgumentList "run", "--project", $app, "python", $entry `
        -WorkingDirectory $app -PassThru -WindowStyle Hidden

    # Unbounded wait on purpose: the exe stays resident as the parent so that
    # closing the window also ends it. A timeout here would fire on every
    # healthy start.
    $p.WaitForExit()
    $code = $p.ExitCode
    Write-BootLog ("child exited: " + $code)
    if ($code -ne 0) {
        Report-ChildFailure $code $app
    }
}

# ---- entry point --------------------------------------------------------
# Without this, $ErrorActionPreference="Stop" makes every unhandled error
# vanish: under -noConsole there is no console to print it. Guarded throw
# sites include Add-Type (WinForms missing), Start-Process (AV quarantined
# uv.exe) and the WebView2 download.
try {
    Start-AntNest
} catch {
    $detail = $_.Exception.Message
    Write-BootLog ("FATAL: " + $detail)
    Show-Error ($Msg.BootFailed + "`n" + $detail +
                $(if ($script:LogFile) { "`n`n" + $Msg.LogAt + "`n" + $script:LogFile } else { "" }))
}

# AntNest release preflight check.
# Run from any directory: powershell -ExecutionPolicy Bypass -File tools\release_check.ps1
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$failures = New-Object System.Collections.Generic.List[string]

function Pass([string]$message) { Write-Host "[PASS] $message" -ForegroundColor Green }
function Fail([string]$message) { Write-Host "[FAIL] $message" -ForegroundColor Red; [void]$failures.Add($message) }
function Check([bool]$condition, [string]$ok, [string]$bad) {
    if ($condition) { Pass $ok } else { Fail $bad }
}

Write-Host "AntNest release preflight"
Write-Host "Repository: $repo"
Write-Host ""

# Version: pyproject.toml is the single source of truth.
$pyproject = Join-Path $repo "pyproject.toml"
$version = $null
if (Test-Path $pyproject) {
    $match = Select-String -Path $pyproject -Pattern '^\s*version\s*=\s*["'']([^"'']+)["'']' | Select-Object -First 1
    if ($match) { $version = $match.Matches[0].Groups[1].Value }
}
Check ($null -ne $version) "Version source found: $version" "Version not found in pyproject.toml"

if ($version) {
    $versionIss = Join-Path $repo "installer\version.iss"
    $versionIssText = if (Test-Path $versionIss) { Get-Content $versionIss -Raw } else { "" }
    Check ($versionIssText -match [regex]::Escape($version)) "installer/version.iss matches $version" "installer/version.iss does not match $version"

    $exe = Join-Path $repo "AntNest.exe"
    Check (Test-Path $exe) "AntNest.exe exists" "AntNest.exe is missing"
    if (Test-Path $exe) {
        $bytes = [IO.File]::ReadAllBytes($exe)
        $text = [Text.Encoding]::ASCII.GetString($bytes)
        Check ($text.Contains($version)) "AntNest.exe contains version resource $version" "AntNest.exe does not contain version resource $version"
    }
}

# Required release files.
$required = @(
    "AntNest.py", "prototype_antnest.py", "antnest_bridge.py", "phtmlwin.py",
    "api_compat.py", "mcp_client.py", "pyproject.toml", "uv.lock",
    "installer\AntNest.iss", "installer\launcher.ps1", "installer\launch.ps1",
    "installer\install_deps.ps1", "installer\ensure_prereqs.ps1",
    "installer\uv_helper.ps1", "installer\version.iss"
)
foreach ($file in $required) {
    Check (Test-Path (Join-Path $repo $file)) "required file exists: $file" "required file missing: $file"
}

# Git rules: launcher is tracked; installer output and runtime config are not.
$tracked = git ls-files
Check ($tracked -contains "AntNest.exe") "AntNest.exe is tracked" "AntNest.exe is not tracked"
Check (-not ($tracked -match '^installer/out/')) "installer/out is not tracked" "installer/out contains tracked files"
foreach ($private in @("config.json", "ui_config.json", ".env")) {
    Check (-not ($tracked -contains $private)) "$private is not tracked" "$private is tracked and may contain local secrets"
}
$ignoreResult = git check-ignore -q "installer/out/AntNest-Setup.exe"
Check ($LASTEXITCODE -eq 0) "installer/out is ignored" "installer/out is not ignored"

# No obvious key formats in tracked text files. This is a heuristic, not a secret scanner.
$secretPattern = 'sk-[A-Za-z0-9]{40,}|ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{36,}|AIza[A-Za-z0-9_-]{30,}'
$secretHits = @()
foreach ($file in $tracked) {
    if ($file -match '\.(py|json|md|toml|yml|yaml|ps1|bat|iss|txt)$' -and (Test-Path $file)) {
        $hits = Select-String -Path $file -Pattern $secretPattern -AllMatches -ErrorAction SilentlyContinue
        if ($hits) { $secretHits += $hits }
    }
}
Check ($secretHits.Count -eq 0) "no obvious API key patterns in tracked text" "possible API key pattern found in tracked text"

# Python syntax check.
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $pyFiles = @("AntNest.py", "prototype_antnest.py", "antnest_bridge.py", "api_compat.py", "mcp_client.py", "phtmlwin.py")
    & $python.Source -m py_compile $pyFiles
    Check ($LASTEXITCODE -eq 0) "core Python syntax check passed" "core Python syntax check failed"
} else {
    Fail "python executable not found"
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "Release preflight FAILED: $($failures.Count) issue(s)" -ForegroundColor Red
    exit 1
}
Write-Host "Release preflight PASSED" -ForegroundColor Green
exit 0
# Build AntNest.exe from launcher.ps1 using the ps2exe module.
# One-time: needs the ps2exe module from PSGallery (auto-installed here).
# Output: <repo root>\AntNest.exe  (picked up by AntNest.iss and the Release zip)
$ErrorActionPreference = "Stop"

if (-not (Get-Module -ListAvailable ps2exe)) {
    Write-Host "[build_launcher] Installing ps2exe module (one time)..."
    Install-Module ps2exe -Scope CurrentUser -Force
}

$root   = Split-Path -Parent $MyInvocation.MyCommand.Path   # installer/
$repo   = Split-Path -Parent $root                          # repo root
$input  = Join-Path $root "launcher.ps1"
$output = Join-Path $repo  "AntNest.exe"
$icon   = Join-Path $repo  "antnest.ico"

Write-Host "[build_launcher] Compiling $input -> $output"
Invoke-ps2exe -inputFile $input -outputFile $output -noConsole -windowStyle Hidden -noOutput -iconFile $icon -title "AntNest" -version "0.1.0"
Write-Host "[build_launcher] Done: $output"

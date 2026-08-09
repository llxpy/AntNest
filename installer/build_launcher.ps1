# Build AntNest.exe from launcher.ps1 using the ps2exe module.
# One-time: needs the ps2exe module from PSGallery (auto-installed here).
# Output: <repo root>\AntNest.exe  (picked up by AntNest.iss and the Release zip)
$ErrorActionPreference = "Stop"

# Bootstrap NuGet provider + trust PSGallery so Install-Module never prompts (non-interactive)
if (-not (Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue)) {
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser | Out-Null
}
if ((Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue).InstallationPolicy -ne 'Trusted') {
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
}

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
# NOTE: Invoke-ps2exe has NO -windowStyle param. -noConsole makes it a Windows
# app (no console window); that already covers the "no flash" goal.
Invoke-ps2exe -inputFile $input -outputFile $output -noConsole -iconFile $icon -title "AntNest" -version "0.1.0"
Write-Host "[build_launcher] Done: $output"

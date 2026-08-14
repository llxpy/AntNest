# Build AntNest.exe from launcher.ps1 using the ps2exe module.
# One-time: needs the ps2exe module from PSGallery (auto-installed here).
# Output: <repo root>\AntNest.exe  (picked up by AntNest.iss and the Release zip)
#
# The app version is read from pyproject.toml (single source of truth) and
# injected both into the exe metadata (-version) and into installer/version.iss
# (which AntNest.iss #includes) so the installer shows the same version.
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

# --- Read version from pyproject.toml (single source of truth) ------------
$version = "0.0.0"
try {
    $pp = Join-Path $repo "pyproject.toml"
    foreach ($line in (Get-Content $pp)) {
        if ($line -match '^\s*version\s*=\s*["'']([^"'']+)["'']') {
            $version = $Matches[1]
            break
        }
    }
} catch {
    Write-Warning "[build_launcher] Could not read version from pyproject.toml; defaulting to $version"
}

# --- Write installer/version.iss so AntNest.iss shows the same version ----
$versionIss = Join-Path $root "version.iss"
Set-Content -Path $versionIss -Value "#define MyAppVersion `"$version`"" -Encoding utf8
Write-Host "[build_launcher] Wrote $versionIss -> MyAppVersion = $version"

Write-Host "[build_launcher] Compiling $input -> $output (v$version)"
# NOTE: Invoke-ps2exe has NO -windowStyle param. -noConsole makes it a Windows
# app (no console window); that already covers the "no flash" goal.
Invoke-ps2exe -inputFile $input -outputFile $output -noConsole -iconFile $icon -title "AntNest" -version $version
Write-Host "[build_launcher] Done: $output"

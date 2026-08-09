; AntNest installer script (Inno Setup 6+)
; Build: install Inno Setup, right-click this file -> Compile, or from ISCC:
;   "D:\Software\Inno Setup 6\ISCC.exe" AntNest.iss
; Output: installer\out\AntNest-Setup.exe (unsigned; users see a SmartScreen warning on first run)
;
; Design: single-user install to %LOCALAPPDATA%\AntNest, no admin rights.
; Depends on uv (auto-installed) + WebView2 runtime (auto-installed). Python is NOT bundled;
; uv builds a venv and installs pywebview on first run (requires internet).

#define MyAppName "AntNest"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "AntNest"
#define MyAppURL "https://github.com/llxpy/AntNest"
; repo root (relative to this .iss, which lives in installer/)
#define SourceDir ".."

[Setup]
AppId={{A9F3B7C2-1E4D-4F2A-9B6E-7C5D8E0F1A2B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=out
OutputBaseFilename={#MyAppName}-Setup
Compression=lzma2
SolidCompression=yes
; single-user, no admin. Installs to LOCALAPPDATA, does not touch Program Files.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64os
WizardStyle=modern
SetupIconFile={#SourceDir}\antnest.ico
UninstallDisplayIcon={#SourceDir}\antnest.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:ChineseSimplified.isl"

[Files]
; ---- app source ----
Source: "{#SourceDir}\AntNest.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\phtmlwin.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest_bridge.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\prototype_antnest.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest_launcher.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\pyproject.toml";       DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\uv.lock";              DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest.ico";          DestDir: "{app}"; Flags: ignoreversion
; ---- installer helper scripts ----
Source: ".\launch.ps1";          DestDir: "{app}"; Flags: ignoreversion
Source: ".\ensure_prereqs.ps1";  DestDir: "{app}"; Flags: ignoreversion
Source: ".\launcher.ps1";        DestDir: "{app}"; Flags: ignoreversion
; ---- compiled launcher (built by installer\build_launcher.ps1 before ISCC) ----
Source: "{#SourceDir}\AntNest.exe"; DestDir: "{app}"; Flags: ignoreversion
; ---- user data (clean TEMPLATES, no secrets; write only on first install; upgrades keep user's edited config) ----
; NOTE: ship templates, NOT the dev config.json (which holds the real API key). Templates have empty api_key.
Source: ".\config.template.json";   DestDir: "{app}"; DestName: "config.json";   Flags: ignoreversion onlyifdoesntexist
Source: ".\ui_config.template.json"; DestDir: "{app}"; DestName: "ui_config.json"; Flags: ignoreversion onlyifdoesntexist

[Icons]
; Start Menu + Desktop shortcuts: double-click AntNest.exe (native launcher, no console)
Name: "{group}\{#MyAppName}"; Filename: "{app}\AntNest.exe"; \
  WorkingDir: "{app}"; IconFilename: "{app}\antnest.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\AntNest.exe"; \
  WorkingDir: "{app}"; IconFilename: "{app}\antnest.ico"

; Note: do NOT run ensure_prereqs.ps1 during setup. AntNest.exe calls it on first
; launch, so running it here would block the installer while downloading uv/WebView2.

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.antnest"
Type: filesandordirs; Name: "{app}\trace"

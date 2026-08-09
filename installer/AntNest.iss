; AntNest installer script (Inno Setup 6+)
; Build: install Inno Setup, right-click this file -> Compile, or from ISCC:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" AntNest.iss
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
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
SetupIconFile={#SourceDir}\antnest.ico
UninstallDisplayIcon={#SourceDir}\antnest.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

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
; ---- user data (clean TEMPLATES, no secrets; write only on first install; upgrades keep user's edited config) ----
; NOTE: ship templates, NOT the dev config.json (which holds the real API key). Templates have empty api_key.
Source: ".\config.template.json";   DestDir: "{app}"; DestName: "config.json";   Flags: ignoreversion onlyifdoesntexist
Source: ".\ui_config.template.json"; DestDir: "{app}"; DestName: "ui_config.json"; Flags: ignoreversion onlyifdoesntexist

[Icons]
; Start Menu + Desktop shortcuts: launch.ps1 hidden
Name: "{group}\{#MyAppName}"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\launch.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\antnest.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\launch.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\antnest.ico"

[Run]
; run prereq check (uv + WebView2) at install time; failure does not block install, launch.ps1 retries
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\ensure_prereqs.ps1"""; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.antnest"
Type: filesandordirs; Name: "{app}\trace"

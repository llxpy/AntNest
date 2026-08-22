; AntNest installer script (Inno Setup 6+)
; Build: install Inno Setup, right-click this file -> Compile, or from ISCC:
;   "D:\Software\Inno Setup 6\ISCC.exe" AntNest.iss
; Output: installer\out\AntNest-Setup.exe (unsigned; users see a SmartScreen warning on first run)
;
; Design: single-user install to %LOCALAPPDATA%\AntNest, no admin rights.
; Depends on uv (auto-installed) + WebView2 runtime (auto-installed). Python is NOT bundled;
; uv builds a venv and installs pywebview on first run (requires internet).
;
; Version is injected by installer\build_launcher.ps1 into version.iss
; (sourced from pyproject.toml) - keep that single source of truth.

#include "version.iss"
#define MyAppName "AntNest"
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
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
; ---- app source ----
Source: "{#SourceDir}\AntNest.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\phtmlwin.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest_bridge.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\prototype_antnest.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest_launcher.py"; DestDir: "{app}"; Flags: ignoreversion
; ---- v1.2.0+ modules (worker file ops / Skills / MCP / clone / session / UI render) ----
Source: "{#SourceDir}\code_tools.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\skills_loader.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\mcp_client.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest_clone_worker.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest_session.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\ui_render.py";            DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\ui_assets_loader.py";     DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\api_compat.py";            DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\memory_retrieval.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\task_manager.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\admin_utils.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\pyproject.toml";       DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\uv.lock";              DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest.ico";          DestDir: "{app}"; Flags: ignoreversion
; ---- UI assets (read at runtime by ui_render / ui_assets_loader) ----
Source: "{#SourceDir}\ui_assets\*"; DestDir: "{app}\ui_assets"; Flags: ignoreversion recursesubdirs
; ---- installer helper scripts (shared uv_helper is required by the others) ----
Source: ".\uv_helper.ps1";       DestDir: "{app}"; Flags: ignoreversion
Source: ".\launch.ps1";           DestDir: "{app}"; Flags: ignoreversion
Source: ".\ensure_prereqs.ps1";   DestDir: "{app}"; Flags: ignoreversion
Source: ".\launcher.ps1";         DestDir: "{app}"; Flags: ignoreversion
Source: ".\install_deps.ps1";     DestDir: "{app}"; Flags: ignoreversion
; ---- compiled launcher (built by installer\build_launcher.ps1 before ISCC) ----
Source: "{#SourceDir}\AntNest.exe"; DestDir: "{app}"; Flags: ignoreversion
; ---- user data (clean TEMPLATES, no secrets; write only on first install; upgrades keep user's edited config) ----
; NOTE: ship templates, NOT the dev config.json (which holds the real API key). Templates have empty api_key.
Source: ".\config.template.json";   DestDir: "{app}"; DestName: "config.json";   Flags: ignoreversion onlyifdoesntexist
Source: ".\ui_config.template.json"; DestDir: "{app}"; DestName: "ui_config.json"; Flags: ignoreversion onlyifdoesntexist
; ---- donation QR codes (sponsor popup; in project Page/, shipped with installer) ----
Source: "{#SourceDir}\Page\*"; DestDir: "{app}\Page"; Flags: ignoreversion recursesubdirs

[Dirs]
; Create an empty Skills folder on install; force-remove on uninstall
; (avoids "incomplete uninstall" complaints).
Name: "{app}\Skills"; Flags: uninsalwaysuninstall

[Icons]
; Start Menu + Desktop shortcuts: double-click AntNest.exe (native launcher, no console)
Name: "{group}\{#MyAppName}"; Filename: "{app}\AntNest.exe"; \
  WorkingDir: "{app}"; IconFilename: "{app}\antnest.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\AntNest.exe"; \
  WorkingDir: "{app}"; IconFilename: "{app}\antnest.ico"

; Install-time dependency setup: install uv, ensure WebView2, pre-install pywebview
; via uv. Runs VISIBLE (no runhidden) so the user sees progress instead of a
; "frozen" Finishing screen. Keep synchronous (no nowait) so deps are ready when done.
[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_deps.ps1"""; \
  Description: "Installing AntNest dependencies (uv + pywebview)"; Flags:

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.antnest"
Type: filesandordirs; Name: "{app}\trace"
Type: filesandordirs; Name: "{app}\Skills"
; Config files are also explicitly cleaned so uninstall leaves no residue
; (single-user install; config lives in the install dir, which is expected).
Type: files; Name: "{app}\config.json"
Type: files; Name: "{app}\ui_config.json"

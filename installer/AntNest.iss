; AntNest installer script (Inno Setup 6+)
; Build: install Inno Setup, right-click this file -> Compile, or from ISCC:
;   "D:\Software\Inno Setup 6\ISCC.exe" AntNest.iss
; Output: installer\out\AntNest-Setup.exe (unsigned; users see a SmartScreen warning on first run)
;
; Design: single-user install to %LOCALAPPDATA%\AntNest, no admin rights.
; Depends on uv (auto-installed) + WebView2 runtime (auto-installed). Python is NOT bundled;
; uv builds a venv and installs pywebview on first run (requires internet).

#define MyAppName "AntNest"
#define MyAppVersion "1.2.0"
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
Source: "{#SourceDir}\pyproject.toml";       DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\uv.lock";              DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\antnest.ico";          DestDir: "{app}"; Flags: ignoreversion
; ---- installer helper scripts ----
Source: ".\launch.ps1";          DestDir: "{app}"; Flags: ignoreversion
Source: ".\ensure_prereqs.ps1";  DestDir: "{app}"; Flags: ignoreversion
Source: ".\launcher.ps1";        DestDir: "{app}"; Flags: ignoreversion
Source: ".\install_deps.ps1";    DestDir: "{app}"; Flags: ignoreversion
; ---- compiled launcher (built by installer\build_launcher.ps1 before ISCC) ----
Source: "{#SourceDir}\AntNest.exe"; DestDir: "{app}"; Flags: ignoreversion
; ---- user data (clean TEMPLATES, no secrets; write only on first install; upgrades keep user's edited config) ----
; NOTE: ship templates, NOT the dev config.json (which holds the real API key). Templates have empty api_key.
Source: ".\config.template.json";   DestDir: "{app}"; DestName: "config.json";   Flags: ignoreversion onlyifdoesntexist
Source: ".\ui_config.template.json"; DestDir: "{app}"; DestName: "ui_config.json"; Flags: ignoreversion onlyifdoesntexist
; ---- 收款二维码（赞助作者弹窗用，项目内 Page/，随安装包分发）----
Source: "{#SourceDir}\Page\*"; DestDir: "{app}\Page"; Flags: ignoreversion recursesubdirs

[Dirs]
; 安装时创建空的 Skills 文件夹，uninstall 时强制一起删掉（避免用户说「卸载不完全」）。
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
  Description: "正在安装 AntNest 依赖（uv + pywebview）"; Flags:

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.antnest"
Type: filesandordirs; Name: "{app}\trace"
Type: filesandordirs; Name: "{app}\Skills"
; 配置文件也显式清理，确保卸载后没有残留（单用户安装，配置存在安装目录里，这是预期行为）。
Type: files; Name: "{app}\config.json"
Type: files; Name: "{app}\ui_config.json"

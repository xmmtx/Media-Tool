; MediaTool 安装程序脚本（Inno Setup 6）
; 由 .github/workflows/build-installer.yml 用 iscc 编译：
;   iscc /DMyAppVersion=<版本> /O"dist" packaging\installer.iss
; 打包源：dist\MediaTool\（PyInstaller --onedir 输出，见 workflow）。

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"      ; 本地手动编译时的默认值，CI 用 /D 覆盖
#endif

#define MyAppName "MediaTool"
#define MyAppPublisher "Media-Tool"
#define MyAppExeName "MediaTool.exe"
#define MyAppId "{{A3F4E2C1-6B7D-4E9A-9C8B-1D2E3F4A5B6C}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=MediaTool-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes

; 界面语言：默认英文 + 简体中文。
; 如需繁体，追加：Name: "chinesetraditional"; MessagesFile: "compiler:Languages\ChineseTradtional.isl"
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MediaTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

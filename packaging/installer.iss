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
SetupIconFile=..\src\assets\icon.ico
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

; 界面语言：默认英文 + 简体中文（语言文件随仓库分发 packaging/Languages/，
; 官方安装包不带中文翻译）。如需繁体，把 ChineseTradtional.isl 放入
; packaging/Languages/ 后在此追加一行即可。
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MediaTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 覆盖安装前先清掉上一版 PyInstaller 产物。
; [Setup] 的 MyAppId 是固定 GUID，Inno 因此把每次构建认定为“同一个应用”，
; 新版会直接覆盖旧安装（UsePreviousAppDir/CloseApplications 均默认 yes）。
; 但 Inno 只覆盖它要装的文件，**不会删除**旧版本独有、新版本里已不存在的
; 文件（跨 PyInstaller/Python 版本会残留 python3xx.dll、旧 Qt 插件等）。
; 本段在文件复制开始前执行，删掉整个 _internal 后由 [Files] 重新生成。
; 安全前提：_internal 是纯构建产物，用户数据都在 %APPDATA%\MediaTool
; （见 src/db/paths.py 的 frozen 分支），安装目录下没有用户资产。
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

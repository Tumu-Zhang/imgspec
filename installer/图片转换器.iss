; 图片转换器 —— Inno Setup 安装脚本
;
; 由 tools/make_installer.py 驱动（推荐）：
;     python tools/make_installer.py                  ; 两种范围都产出
;     python tools/make_installer.py --scope user     ; 只出当前用户版
;     python tools/make_installer.py --scope machine  ; 只出所有用户版
;
; 也可以直接用 Inno Setup 打开本文件编译，此时使用下面的默认值：
;     版本 1.0.0，当前用户范围（免管理员）
;
; 命令行覆盖：
;     ISCC.exe installer\图片转换器.iss /DMyAppVersion=1.0.0
;     ISCC.exe installer\图片转换器.iss /DMyAppVersion=1.0.0 /DMyAppScopeMachine
;
; 打包源是 PyInstaller 的产物（图片转换器.exe + _internal + README.md），
; 源码、测试与 tools/ 脚本都不进安装包 —— 见 installer/README.md 的清单说明。

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

; 定义 MyAppScopeMachine 即为「所有用户」安装；不定义则为「当前用户」安装。
; 用 #ifdef 而不是 #if 字符串比较，是因为 ISPP 各版本对后者的支持不一致。

#define MyAppName      "图片转换器"
#define MyAppExeName   "图片转换器.exe"
#define MyAppPublisher "imgspec"
#define MyAppComments  "科研投稿图片规格化工具"
#define MySourcePath   ".."

#ifdef MyAppScopeMachine
  ; —— 所有用户：装到 Program Files，需管理员权限 ——
  ; 卸载入口：控制面板 → 程序和功能
  #define MyAppDirName    "{pf}\" + MyAppName
  #define MyPrivileges    "admin"
  #define MyAppId         MyAppPublisher + "." + MyAppName
  #define MyScopeLabel    "所有用户"
#else
  ; —— 当前用户：装到 %LOCALAPPDATA%\Programs，无 UAC 弹窗 ——
  ; 卸载入口：Windows 设置 → 应用 → 已安装的应用
  ; AppId 加 .user 后缀，避免与机器级安装互相覆盖
  #define MyAppDirName    "{localappdata}\Programs\" + MyAppName
  #define MyPrivileges    "lowest"
  #define MyAppId         MyAppPublisher + "." + MyAppName + ".user"
  #define MyScopeLabel    "当前用户"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}（{#MyScopeLabel}）
AppPublisher={#MyAppPublisher}
AppComments={#MyAppComments}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}

; 安装目录：默认可改，但不允许装到根目录
DefaultDirName={#MyAppDirName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
DisableDirPage=auto

; lowest = 普通权限（当前用户）；admin = 需提权（所有用户）
PrivilegesRequired={#MyPrivileges}
PrivilegesRequiredOverridesAllowed=commandline

; 项目在 win_amd64 下打包（python310.dll / *.cp310-win_amd64.pyd），仅 64 位
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 覆盖安装时若程序正在运行，先关掉它再替换文件
CloseApplications=yes
RestartApplications=yes

WizardStyle=modern
WizardResizable=yes
SetupIconFile={#MySourcePath}\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}（{#MyScopeLabel}）

Compression=lzma2/max
SolidCompression=yes

OutputDir=..\dist\installer
OutputBaseFilename=图片转换器-{#MyAppVersion}-{#MyScopeLabel}-setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "{#MySourcePath}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MySourcePath}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MySourcePath}\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "安装完成后启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

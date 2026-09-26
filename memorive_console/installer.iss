#define AppName "Memorive 测试控制台"
#define AppVersion "1.01"
#define AppExeName "Memorive-Test-Console.exe"

[Setup]
AppId={{8E7A4DB0-0DA1-48B1-93EC-465A9E4898D7}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Memorive
DefaultDirName={localappdata}\Programs\Memorive Test Console
DefaultGroupName=Memorive
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=Memorive-Test-Console-Setup-{#AppVersion}-x64
SetupIconFile=assets\memorive_test_console.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile=NOTICE.txt
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
ChangesEnvironment=no
VersionInfoVersion=1.1.0.0
VersionInfoCompany=Memorive
VersionInfoDescription=Memorive Test Console Installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Files]
Source: "output\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Memorive 测试控制台"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Memorive 测试控制台"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 Memorive 测试控制台"; Flags: nowait postinstall skipifsilent

; Session cleanup is handled by the application. Uninstall retains diagnostics.

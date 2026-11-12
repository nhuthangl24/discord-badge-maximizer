; Inno Setup Script for Discord Badge Maximizer
; Generates DiscordBadgeMaximizer-Setup.exe (Standard Windows Installer)

#define MyAppName "Discord Badge Maximizer"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Discord Badge Maximizer"
#define MyAppURL "https://github.com/nhuthangl24/discord-badge-maximizer"
#define MyAppExeName "DiscordBadgeMaximizer-App.exe"

[Setup]
AppId={{D37E5C18-912A-4F8A-B2CE-572A1B70FE89}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\DiscordBadgeMaximizer
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=DiscordBadgeMaximizer-Setup
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\assets\icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "dist\DiscordBadgeMaximizer-App\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Khởi động {#MyAppName}"; Flags: nowait postinstall skipifsilent

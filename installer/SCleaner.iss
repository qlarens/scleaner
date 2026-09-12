; Compile through build-installer.ps1; the version comes from scleaner/__init__.py.
#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif
#ifndef AppPublisher
  #error AppPublisher must be supplied by the build script
#endif
#define ProjectRoot AddBackslash(SourcePath) + ".."

[Setup]
; Keep this ID stable across versions so Setup updates the existing installation.
AppId={{05E50260-E263-49E8-AFE0-775A32C25C55}
AppName=SCleaner
AppVersion={#AppVersion}
AppVerName=SCleaner {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/gutamurr/scleaner
AppSupportURL=https://github.com/gutamurr/scleaner/issues
AppUpdatesURL=https://github.com/gutamurr/scleaner/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription=SCleaner Setup
VersionInfoCopyright=Copyright (c) 2026 {#AppPublisher}
DefaultDirName={localappdata}\Programs\SCleaner
DefaultGroupName=SCleaner
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UsePreviousAppDir=yes
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
UninstallDisplayIcon={app}\SCleaner.exe
WizardStyle=modern
SetupIconFile={#ProjectRoot}\artifacts\SCleaner.ico
LicenseFile={#ProjectRoot}\LICENSE
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=SCleaner-{#AppVersion}-win64-Setup
Compression=lzma2
SolidCompression=yes
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Keep Python/Qt shared libraries separate, including licenses and application sources.
Source: "{#ProjectRoot}\dist\SCleaner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SCleaner"; Filename: "{app}\SCleaner.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\SCleaner"; Filename: "{app}\SCleaner.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\SCleaner.exe"; Description: "{cm:LaunchProgram,SCleaner}"; Flags: nowait postinstall skipifsilent

; Deliberately no wildcard [UninstallDelete]: remove only installed files.
; Settings, history and registry backups in LOCALAPPDATA\SCleaner are user data.

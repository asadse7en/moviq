#ifndef AppVersion
  #error AppVersion must be supplied by packaging/build.ps1
#endif

#ifndef SourceDir
  #error SourceDir must be supplied by packaging/build.ps1
#endif

#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\release\dist\v{#AppVersion}"
#endif

[Setup]
AppId={{B0CF6F2E-AB41-48E3-A56C-4366A17C4B4F}
AppName=Moviq
AppVerName=Moviq {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=asadse7en
UninstallDisplayName=Moviq {#AppVersion}
DefaultDirName={localappdata}\Programs\Moviq
DefaultGroupName=Moviq
UninstallDisplayIcon={app}\Moviq-Portable-{#AppVersion}.exe
OutputDir={#InstallerOutputDir}
OutputBaseFilename=Moviq-Setup-{#AppVersion}
SetupIconFile=..\assets\moviq-icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Messages]
SetupWindowTitle=Moviq {#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Moviq"; Filename: "{app}\Moviq-Portable-{#AppVersion}.exe"
Name: "{autodesktop}\Moviq"; \
    Filename: "{app}\Moviq-Portable-{#AppVersion}.exe"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\Moviq-Portable-{#AppVersion}.exe"; \
    Description: "Launch Moviq"; Flags: postinstall nowait skipifsilent

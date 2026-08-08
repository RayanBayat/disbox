; Inno Setup script for the Disbox desktop client.
;
; Build the application first, then compile this:
;     uv run pyinstaller packaging/disbox.spec --noconfirm --distpath build/dist
;     ISCC packaging/disbox.iss
;
; NOT YET COMPILED OR TESTED. Inno Setup was not available on the machine this
; was written on, so treat it as a starting point rather than a verified build
; step until someone has run ISCC against it.

#define AppName "Disbox"
#define AppVersion "0.1.0"
#define AppPublisher "Rayan Bayat"
#define AppURL "https://github.com/rayanbayat/disbox"
#define AppExeName "Disbox.exe"

[Setup]
AppId={{8F3D6A21-4C7E-4B19-9E2A-5D1C7B0E4A63}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\build\installer
OutputBaseFilename=disbox-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user by default so the installer does not demand administrator rights
; for something that writes only to the user's own directories.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\build\dist\Disbox\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\dist\Disbox\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Associate .dbx so double-clicking a vault opens it. Written under HKCU
; because the install is per-user.
Root: HKCU; Subkey: "Software\Classes\.dbx"; ValueType: string; \
    ValueName: ""; ValueData: "Disbox.Vault"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Disbox.Vault"; ValueType: string; \
    ValueName: ""; ValueData: "Disbox vault"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Disbox.Vault\DefaultIcon"; ValueType: string; \
    ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKCU; Subkey: "Software\Classes\Disbox.Vault\shell\open\command"; ValueType: string; \
    ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The bundle directory only. A vault is the user's data and lives wherever they
; put it; an uninstaller that deleted vaults would destroy everything they have
; stored, and the blobs on Discord would become unreachable.
Type: filesandordirs; Name: "{app}"

#define AppName "MouseShare"
#define AppVersion "0.8.24"
#define SourceDir "..\dist\MouseShare"

[Setup]
AppId={{57978AB7-DA5B-46D8-BB3F-69D818F7EC10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=makeSail
AppPublisherURL=https://github.com/413180794/MouseShare
DefaultDirName={autopf}\MouseShare
DefaultGroupName=MouseShare
UninstallDisplayIcon={app}\MouseShare.exe
OutputDir=..\dist
OutputBaseFilename=MouseShare-{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\build\icons\MouseShare.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE
InfoAfterFile=..\docs\INSTALL-Windows.txt
Uninstallable=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "firewall"; Description: "Allow MouseShare on private local networks (recommended)"; Flags: checkedonce
Name: "lockservice"; Description: "Control this computer while locked or before sign-in (installs a Windows service)"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\INSTALL-Windows.txt"; DestDir: "{app}"; DestName: "INSTALL.txt"

[Icons]
Name: "{group}\MouseShare"; Filename: "{app}\MouseShare.exe"
Name: "{commondesktop}\MouseShare"; Filename: "{app}\MouseShare.exe"; Tasks: desktopicon
Name: "{group}\Uninstall MouseShare"; Filename: "{uninstallexe}"

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MouseShare LAN TCP"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MouseShare LAN UDP"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""MouseShare LAN TCP"" dir=in action=allow program=""{app}\MouseShare.exe"" protocol=TCP localport=24872 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""MouseShare LAN UDP"" dir=in action=allow program=""{app}\MouseShare.exe"" protocol=UDP localport=24871 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "{app}\MouseShareService.exe"; Parameters: "--install"; Flags: runhidden; Tasks: lockservice
Filename: "{app}\MouseShare.exe"; Description: "Launch MouseShare"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM MouseShare.exe"; Flags: runhidden
Filename: "{app}\MouseShareService.exe"; Parameters: "--uninstall"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MouseShare LAN TCP"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MouseShare LAN UDP"""; Flags: runhidden

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "MouseShare"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\MouseSharePaste"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\MouseSharePaste"; Flags: dontcreatekey uninsdeletekey

[Code]
// Stop running instances before replacing files, so upgrading never hits
// "DeleteFile failed; code 5" while the old build is still running.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MouseShare.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MouseShareService.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop MouseShareInputService', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;


' Starts the background tracker with no window. Safe to run twice: a second copy exits.
' setup.ps1 saves the pythonw.exe to use in .python-path.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
py = ""
If fso.FileExists(here & "\.python-path") Then py = Trim(fso.OpenTextFile(here & "\.python-path").ReadLine())
If py = "" Or Not fso.FileExists(py) Then py = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")
If Not fso.FileExists(py) Then py = "pythonw.exe"
sh.CurrentDirectory = here
sh.Run """" & py & """ """ & here & "\tracker.pyw""", 0, False

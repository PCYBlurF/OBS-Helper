' OBS Helper silent launcher.
' Runs run.bat with its console hidden (no flash), then returns immediately.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c """ & dir & "\run.bat""", 0, False

' Silent launcher - no console window, uses portable Python
Dim shell, fso, scriptDir, pythonw, ffPath, env
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Use portable Python if available, otherwise system pythonw
pythonw = scriptDir & "\portable_python\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

' Add local ffmpeg to PATH
ffPath = scriptDir & "\ffmpeg"
If fso.FolderExists(ffPath) Then
    Set env = shell.Environment("PROCESS")
    env("PATH") = ffPath & ";" & env("PATH")
End If

' Launch (0 = hidden window)
shell.CurrentDirectory = scriptDir
shell.Run """" & pythonw & """ hotkey_monitor.py", 0, False

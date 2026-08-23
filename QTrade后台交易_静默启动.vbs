' QTrade Auto Paper Trading - silent background service launcher
' Starts the trading backend with no window; auto-trading continues
' after the QTrade window is closed. Safe to run twice (single-instance).

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

' Must run in this script's folder so it finds the account state file
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

' Prefer pythonw (no console); fall back to python (window hidden by flag 0)
If sh.Run("cmd /c where pythonw.exe >nul 2>nul", 0, True) = 0 Then
    sh.Run "pythonw.exe server.py --no-browser --port 8765 --single-instance", 0, False
Else
    sh.Run "python.exe server.py --no-browser --port 8765 --single-instance", 0, False
End If

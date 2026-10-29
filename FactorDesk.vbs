' Starts FactorDesk with no console window (double-click this file for daily use).
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c where pyw >nul 2>nul && (start """" pyw main.py) || (start """" pythonw main.py)", 0, False

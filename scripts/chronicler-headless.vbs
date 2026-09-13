' ck3_chronicler-yrv3: invisible wrapper around chronicler-headless.bat.
'
' Windows shortcuts that target a .bat unavoidably open a cmd.exe window
' for the duration of the .bat — even with WindowStyle=Hidden on the
' shortcut, it flashes. wscript.exe (the default handler for .vbs) runs
' without any console, and WshShell.Run with intWindowStyle=0 hides the
' .bat's cmd window completely.
'
' This .vbs is the recommended shortcut target instead of the .bat.

Option Explicit

Dim oShell, sScriptDir, sBat
Set oShell = CreateObject("WScript.Shell")
' WScript.ScriptFullName resolves to this .vbs path; trim to its directory.
sScriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sBat = sScriptDir & "chronicler-headless.bat"

' Run(command, intWindowStyle, bWaitOnReturn)
'   intWindowStyle=0 → hidden window
'   bWaitOnReturn=False → fire-and-forget so the .vbs exits immediately
oShell.Run """" & sBat & """", 0, False

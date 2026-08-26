' run_hidden.vbs — zamanlanmis gorevleri PENCERESIZ calistirir (8 Tem 2026)
' Sorun: Task Scheduler python.exe/.bat/powershell acinca kisa sureli konsol
' penceresi parliyor. Cozum: wscript bu sarmalayiciyi calistirir (penceresiz),
' o da asil komutu gizli (0) modda kosar. Davranis degismez, pencere yok.
' Kullanim: wscript.exe run_hidden.vbs "program" "arg1" "arg2" ...
Set sh = CreateObject("WScript.Shell")
cmd = ""
For i = 0 To WScript.Arguments.Count - 1
    cmd = cmd & """" & WScript.Arguments(i) & """ "
Next
sh.Run Trim(cmd), 0, True

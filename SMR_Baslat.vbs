Set WShell = CreateObject("WScript.Shell")
Dim BaseDir
BaseDir = "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal"

' 1. Streamlit'i arka planda başlat
WShell.Run "cmd /c """ & BaseDir & "\start_streamlit.bat""", 0, False

' 2. 35 saniye bekle (Streamlit ayağa kalksın)
WScript.Sleep 35000

' 3. Botu arka planda başlat
WShell.Run "cmd /c """ & BaseDir & "\start_bot.bat""", 0, False

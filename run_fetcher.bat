@echo off
REM ===========================================================================
REM run_fetcher.bat — Windows Task Scheduler tarafından 10dk'da bir tetiklenir.
REM Çalışma dizini sabit tutulur, pythonw.exe ile gizli çalışır (konsol açılmaz).
REM ===========================================================================
cd /d "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal"
"C:\Users\LENOVO\AppData\Local\Python\bin\pythonw.exe" fetcher.py
exit /b 0

@echo off
chcp 65001 >nul
cd /d "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal"
if not exist "logs" mkdir "logs"
"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\.venv\Scripts\python.exe" "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\v2_early_radar_telegram.py" --mode candidates --project-root "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal" >> "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\logs\v2_early_radar_candidates.log" 2>&1


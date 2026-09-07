@echo off
REM Cift tikla -> 8502 (S&P 200) terminalini temiz baslatir.
REM SADECE eski 8502 kopyasini kapatir; 8501 (BIST) calisiyorsa dokunmaz. Tek temiz acar + Brave.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0SMR_US200_Temiz_Baslat.ps1"

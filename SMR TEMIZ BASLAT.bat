@echo off
REM Cift tikla -> siteyi temiz baslatir (eski kopyalari kapatir, tek temiz acar)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0SMR_Temiz_Baslat.ps1"

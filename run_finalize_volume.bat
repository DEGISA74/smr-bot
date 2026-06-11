@echo off
REM SMR — Finalize Volume Runner
REM Windows Task Scheduler her iş günü 18:35'te çağırır.
cd /d "%~dp0"
python finalize_volume.py >> logs\finalize_volume_bat.log 2>&1

@echo off
REM Lokal yalnız tetikler; final hacmin tek yazıcısı VPS'tir.
ssh -o ConnectTimeout=20 -o BatchMode=yes wm11tr@34.153.19.220 "cd ~/smr && flock -n /tmp/smr_finalize_volume.lock venv/bin/python finalize_volume.py" >> logs\finalize_volume_bat.log 2>&1

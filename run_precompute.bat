@echo off
cd /d "%~dp0"
REM İş #8 — kurumsal takvim (temettü/bölünme) gece precompute'u.
REM 18:00 (19:30 backtest_runner'dan ÖNCE) → cache taze, backtest temettü düzeltmesi çalışır.
REM Pace'li + skip-fresh (5 gün) → ilk tam koşu uzun, sonrakiler kısa.
python kurumsal_takvim.py --precompute >> "logs\kurumsal_takvim_precompute.log" 2>&1

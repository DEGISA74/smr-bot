@echo off
cd /d "%~dp0"
echo.
echo ================================================
echo  SMR Backtest Runner
echo ================================================
echo.
python backtest_runner.py
exit /b %ERRORLEVEL%

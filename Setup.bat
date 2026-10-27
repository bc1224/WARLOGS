@echo off
rem Double-click to set up the WARDOGS Match Tracker.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause

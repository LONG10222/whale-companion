@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" -StartOnly
if errorlevel 1 pause

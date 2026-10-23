@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Your existing settings have been kept.
  echo Please check the message above or read docs/ONE_CLICK_SETUP.md.
  pause
  exit /b 1
)

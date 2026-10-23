@echo off
setlocal
cd /d "%~dp0"
py -3.12 -I "%~dp0scripts\deploy_windows.py" %*
set "WHALE_RESULT=%ERRORLEVEL%"
if errorlevel 1 echo Deployment did not complete. Requires 64-bit Python 3.12 from python.org. No system settings were changed.
pause
exit /b %WHALE_RESULT%

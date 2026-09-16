@echo off
setlocal
cd /d "%~dp0"
"%~dp0dual_holo.exe" %*
set "dualholo_exit=%errorlevel%"
if not "%dualholo_exit%"=="0" pause
exit /b %dualholo_exit%

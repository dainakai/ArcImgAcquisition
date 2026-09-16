@echo off
cd /d "%~dp0"
dual_holo.exe --config config.yml --simulate %*
if errorlevel 1 pause

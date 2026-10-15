@echo off
chcp 65001 >nul
cd /d "%~dp0"
"C:\Users\acer\.platformio\penv\Scripts\python.exe" "tools\read_serial_once.py"
echo.
pause

@echo off
REM start_jarvis.bat — launches Jarvis's GUI with the right working
REM directory, regardless of where this file gets run from (double-click,
REM Startup folder, Task Scheduler, etc).

cd /d "%~dp0"
python gui.py

REM If Jarvis crashes or exits, keep the window open so you can actually
REM read the error instead of it flashing shut instantly.
echo.
echo Jarvis has stopped. Press any key to close this window.
pause >nul

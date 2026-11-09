@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 gui.py & goto :end)
where python >nul 2>nul
if %errorlevel%==0 (python gui.py & goto :end)
echo Python 3 not found. Please install Python 3 from https://www.python.org/downloads/windows/
pause
:end

@echo off
"%LOCALAPPDATA%\xslope-venv\Scripts\python.exe" "%~dp0budget_studio.py" %*
if errorlevel 1 pause

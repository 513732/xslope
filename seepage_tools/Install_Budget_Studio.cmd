@echo off
setlocal
set "VENV=%LOCALAPPDATA%\xslope-venv"
set "VENV_PYTHON=%VENV%\Scripts\python.exe"

if exist "%VENV_PYTHON%" goto check_venv

set "BASE_PYTHON=%~1"
if not defined BASE_PYTHON for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "BASE_PYTHON=%%P"
if not defined BASE_PYTHON for /f "delims=" %%P in ('uv python find 3.12 --managed-python 2^>nul') do set "BASE_PYTHON=%%P"
if not defined BASE_PYTHON set "BASE_PYTHON=python"

"%BASE_PYTHON%" -c "import struct, sys; sys.exit(not (sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8))"
if errorlevel 1 (
    echo Install 64-bit Python 3.12 first, then run this installer again.
    echo You can also pass the full path to its python.exe as the first argument.
    goto failed
)
"%BASE_PYTHON%" -m venv "%VENV%"
if errorlevel 1 goto failed

:check_venv
"%VENV_PYTHON%" -c "import struct, sys; sys.exit(not (sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8))"
if errorlevel 1 (
    echo The existing environment at "%VENV%" cannot run 64-bit Python 3.12.
    echo Move it aside, then run this installer again.
    goto failed
)
"%VENV_PYTHON%" -m pip install "xslope[gui,fem]==1.0.9" netCDF4 imageio-ffmpeg
if errorlevel 1 goto failed
"%VENV_PYTHON%" -m pip check
if errorlevel 1 goto failed
"%VENV_PYTHON%" -c "from PySide6.QtCore import QEvent, qVersion; from PySide6.QtWebEngineWidgets import QWebEngineView; import netCDF4; from imageio_ffmpeg import get_ffmpeg_exe; print('Qt', qVersion(), '| FFmpeg:', get_ffmpeg_exe())"
if errorlevel 1 goto failed
echo Installation complete. Run Start_Budget_Studio.cmd to open the enhanced Studio.
exit /b 0

:failed
echo Installation failed.
exit /b 1

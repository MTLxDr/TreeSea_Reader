@echo off
setlocal
set "TREESEA_PY=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%TREESEA_PY%" (
    echo TreeSea Reader: project .venv is missing. See README.md for setup.
    pause
    exit /b 1
)
start "" /d "%~dp0." "%TREESEA_PY%" "%~dp0launch_reader.py" %*
exit /b %errorlevel%

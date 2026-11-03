@echo off
setlocal
if exist "%~dp0TreeSeaReaderCLI.exe" goto native
set "TREESEA_PY=%~dp0runtime\python.exe"
if not exist "%TREESEA_PY%" set "TREESEA_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%TREESEA_PY%" goto missing
"%TREESEA_PY%" "%~dp0launch_reader.py" %*
exit /b %errorlevel%
:native
"%~dp0TreeSeaReaderCLI.exe" %*
exit /b %errorlevel%
:missing
echo TreeSea Reader: Python runtime is missing. Extract the complete portable folder.
pause
exit /b 1

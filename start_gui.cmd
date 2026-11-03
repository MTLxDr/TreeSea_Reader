@echo off
setlocal
if exist "%~dp0TreeSeaReader.exe" goto native
set "TREESEA_PY=%~dp0runtime\pythonw.exe"
if not exist "%TREESEA_PY%" set "TREESEA_PY=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%TREESEA_PY%" goto missing
start "" /d "%~dp0" "%TREESEA_PY%" "%~dp0launch_reader.py" %*
exit /b %errorlevel%
:native
start "" /d "%~dp0" "%~dp0TreeSeaReader.exe" %*
exit /b %errorlevel%
:missing
echo TreeSea Reader: Python runtime is missing. Extract the complete portable folder.
pause
exit /b 1

"""Emit CRLF/ASCII batch shims and native Windows GUI/CLI entry points."""
import argparse
from pathlib import Path
import subprocess
import tempfile
import runpy

ROOT=Path(__file__).resolve().parents[1]


def batch_files(root):
    gui=r'''@echo off
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
'''
    cli=r'''@echo off
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
'''
    files={'start_gui.cmd':gui,'start_console.cmd':cli}
    for name,tool in [('启动树海Reader.cmd','reader'),('启动数值计算器.cmd','calculator'),('启动效果库.cmd','effects')]:
        files[name]='@echo off\ncall "%~dp0start_gui.cmd" --tool '+tool+' %*\n'
    files['启动数值接口.cmd']='@echo off\ncall "%~dp0start_console.cmd" --tool api %*\n'
    for name,text in files.items():(root/name).write_bytes(text.replace('\r\n','\n').replace('\n','\r\n').encode('ascii'))


def release_version():
    return runpy.run_path(str(ROOT/'reader/version.py'))


def compile_launchers(root):
    compiler=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    if not compiler.is_file():raise FileNotFoundError('Windows .NET Framework C# compiler not found')
    version=release_version()
    with tempfile.TemporaryDirectory(prefix='TreeSeaReader-assembly-') as temporary:
        assembly=Path(temporary)/'AssemblyInfo.cs'
        text='[assembly:System.Reflection.AssemblyTitle("TreeSea Reader")]\n[assembly:System.Reflection.AssemblyProduct("TreeSea Reader")]\n'
        for attribute,key in [('AssemblyVersion','FILE_VERSION'),('AssemblyFileVersion','FILE_VERSION'),('AssemblyInformationalVersion','DISPLAY_VERSION')]:
            text+='[assembly:System.Reflection.'+attribute+'("'+version[key]+'")]\n'
        assembly.write_text(text,encoding='utf-8')
        for name,kind,defines in [('TreeSeaReader.exe','winexe',[]),('TreeSeaReaderCLI.exe','exe',['/define:CONSOLE'])]:
            subprocess.run([str(compiler),'/nologo','/optimize+','/platform:x64','/target:'+kind,
                '/reference:System.Windows.Forms.dll','/out:'+str(root/name),*defines,str(ROOT/'packaging/Launcher.cs'),str(assembly)],check=True,cwd=ROOT)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT);parser.add_argument('--batches-only',action='store_true');args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True);batch_files(root)
    if not args.batches_only:compile_launchers(root)
    print('Launchers built: '+str(root))


if __name__=='__main__':main()

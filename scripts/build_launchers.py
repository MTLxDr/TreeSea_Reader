"""Build the native Windows GUI and CLI entry points."""
import argparse
from pathlib import Path
import subprocess
import tempfile
import runpy

ROOT=Path(__file__).resolve().parents[1]


LEGACY_BATCH_FILES = (
    'start_gui.cmd', 'start_console.cmd', '启动树海Reader.cmd',
    '启动数值计算器.cmd', '启动效果库.cmd', '启动数值接口.cmd',
)


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    root = parser.parse_args().output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    compile_launchers(root)
    print('Launchers built: '+str(root))


if __name__=='__main__':main()

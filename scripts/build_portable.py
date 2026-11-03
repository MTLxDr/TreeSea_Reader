"""Build a relocatable Windows folder with an application-local Python runtime.

Uses the official matching embeddable CPython distribution and the project's
installed dependencies. Game sessions, captures, logs and user settings are not
copied into the distribution. Existing output folders are never overwritten.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from urllib.request import Request,urlopen
from zipfile import ZipFile,ZIP_DEFLATED

from build_launchers import compile_launchers,release_version

ROOT=Path(__file__).resolve().parents[1]


def download_runtime(cache):
    version='.'.join(map(str,sys.version_info[:3]))
    filename=f'python-{version}-embed-amd64.zip';target=cache/filename
    url=f'https://www.python.org/ftp/python/{version}/{filename}'
    cache.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        temporary=target.with_suffix('.download')
        for attempt in range(3):
            try:
                with urlopen(Request(url,headers={'User-Agent':'TreeSeaReader portable builder'}),timeout=30) as response,temporary.open('wb') as output:
                    size=0
                    while chunk:=response.read(1024*1024):
                        size+=len(chunk)
                        if size>40_000_000:raise ValueError('Unexpected runtime archive size')
                        output.write(chunk)
                with ZipFile(temporary) as archive:
                    if archive.testzip() is not None:raise ValueError('Corrupt runtime archive')
                    if 'python.exe' not in archive.namelist():raise ValueError('Runtime archive has no python.exe')
                temporary.replace(target);break
            except Exception:
                if attempt==2:raise
                time.sleep(1)
    return target,url


def copy_tree(source,target):
    shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo'),dirs_exist_ok=False)


def copy_dependencies(target):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    required=[line.strip() for line in (ROOT/'requirements.txt').read_text(encoding='utf-8').splitlines() if line.strip() and not line.lstrip().startswith('#')]
    pending=[Requirement(line) for line in required];selected={}
    while pending:
        req=pending.pop()
        if req.marker and not req.marker.evaluate({'extra':''}):continue
        name=canonicalize_name(req.name)
        # windows-capture imports cv2 without its GUI features. Use the project's
        # headless OpenCV distribution for both requirements to avoid copying
        # two different native cv2 builds onto the same package path.
        if name=='opencv-python':name='opencv-python-headless'
        # Current UI uses Core/Gui/Widgets/Network, all in Essentials. Addons
        # (WebEngine, multimedia, etc.) is unused and intentionally excluded.
        if name=='pyside6-addons':continue
        if name in selected:continue
        dist=importlib.metadata.distribution(name)
        if req.specifier and dist.version not in req.specifier:raise ValueError('Installed dependency does not satisfy '+str(req))
        selected[name]=dist
        pending.extend(Requirement(value) for value in dist.requires or [])
    target.mkdir(parents=True)
    for name,dist in selected.items():
        if dist.files is None:raise ValueError('Missing package file manifest: '+name)
        for relative in dist.files:
            if relative.is_absolute() or '..' in relative.parts or relative.suffix in {'.pyc','.pyo'}:continue
            source=dist.locate_file(relative)
            if not source.is_file():raise FileNotFoundError(source)
            dest=target/relative;dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                if source.read_bytes()!=dest.read_bytes():raise ValueError('Dependency file collision: '+str(relative))
            else:shutil.copy2(source,dest)
    import rapidocr
    models=Path(rapidocr.__file__).parent/'models'
    shutil.copytree(models,target/'rapidocr/models',dirs_exist_ok=True)
    # Native OCR should not depend on a system-wide MSVC runtime or on first
    # importing Qt. Ship the runtime DLLs already redistributed by PySide6.
    import PySide6
    for pattern in ['msvcp140*.dll','vcruntime140*.dll']:
        for dll in Path(PySide6.__file__).parent.glob(pattern):shutil.copy2(dll,target.parents[1]/dll.name)
    return {dist.metadata['Name']:dist.version for dist in selected.values()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist/TreeSeaReader')
    parser.add_argument('--runtime-zip',type=Path)
    parser.add_argument('--skip-archive',action='store_true')
    args=parser.parse_args();output=args.output.resolve()
    output.relative_to(ROOT)
    if output.exists():raise FileExistsError('Use a new build folder; refusing to overwrite '+str(output))
    if sys.platform!='win32' or sys.maxsize<=2**32:raise RuntimeError('Build with the Windows x64 project environment')
    runtime_zip,url=(args.runtime_zip.resolve(),'user_supplied') if args.runtime_zip else download_runtime(ROOT/'build/cache')
    output.mkdir(parents=True)
    runtime=output/'runtime';runtime.mkdir()
    with ZipFile(runtime_zip) as archive:
        for member in archive.infolist():
            (runtime/member.filename).resolve().relative_to(runtime.resolve())
        archive.extractall(runtime)
    pth=next(runtime.glob('python*._pth'))
    library_zip=next(runtime.glob('python*.zip')).name
    pth.write_bytes((library_zip+'\r\n.\r\nLib/site-packages\r\n..\r\n../scripts\r\nimport site\r\n').encode('ascii'))
    print('Copying installed runtime dependencies...',flush=True)
    dependencies=copy_dependencies(runtime/'Lib/site-packages')
    for name in ['reader','scripts','assets','database','docs','packaging']:
        copy_tree(ROOT/name,output/name)
    for name in ['launch_reader.py','requirements.txt','README.md','basic_info.md','FACTS.md']:
        shutil.copy2(ROOT/name,output/name)
    data=output/'data';data.mkdir()
    print('Snapshotting public source database...',flush=True)
    with sqlite3.connect((ROOT/'data/reader.sqlite3').as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(data/'reader.sqlite3') as dest:
        source.backup(dest)
        if dest.execute('pragma integrity_check').fetchone()[0]!='ok':raise ValueError('Packaged database failed integrity check')
    for name in ['sources','effects','exploration']:copy_tree(ROOT/'data'/name,data/name)
    compile_launchers(output)
    usage=(ROOT/'packaging/portable_usage.txt').read_text(encoding='utf-8')
    (output/'使用说明.txt').write_text(usage,encoding='utf-8-sig')
    manifest={'version':release_version()['VERSION'],'display_version':release_version()['DISPLAY_VERSION'],'built_at':datetime.now(timezone.utc).isoformat(),'platform':'Windows x64','entry':'TreeSeaReader.exe','cli':'TreeSeaReaderCLI.exe',
        'runtime':{'python':sys.version.split()[0],'url':url,'archive_sha256':hashlib.sha256(runtime_zip.read_bytes()).hexdigest()},
        'dependencies':dependencies,'excluded_optional_packages':['PySide6_Addons: unused by the current Core/Gui/Widgets/Network UI'],
        'excluded_user_data':['data/sessions','data/captures','data/logs','data/settings.json'],'files':{}}
    for path in sorted(output.rglob('*')):
        if path.is_file():manifest['files'][path.relative_to(output).as_posix()]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (output/'portable_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not args.skip_archive:
        archive_path=output.with_suffix('.zip')
        if archive_path.exists():raise FileExistsError(archive_path)
        print('Compressing portable folder...',flush=True)
        with ZipFile(archive_path,'x',ZIP_DEFLATED,compresslevel=6) as archive:
            for path in output.rglob('*'):
                if path.is_file():archive.write(path,path.relative_to(output.parent))
        print('Archive: '+str(archive_path),flush=True)
    print(json.dumps({'output':str(output),'files':len(manifest['files']),'size_mib':round(sum(v['bytes'] for v in manifest['files'].values())/1048576,1)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

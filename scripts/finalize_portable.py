"""Refresh application sources and create a clean, hashed portable ZIP."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shutil
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]


def publishable(relative):
    parts=relative.parts
    if '__pycache__' in parts or relative.suffix in {'.pyc','.pyo'}:return False
    if parts[0] in {'reports','build','dist','.test-tmp'}:return False
    if relative.as_posix()=='data/settings.json':return False
    if len(parts)>1 and parts[0]=='data' and parts[1] in {'sessions','captures','logs'}:return False
    if relative.name in {'last_attempt.json'} or relative.name.endswith(('.new.sqlite3','.sqlite3-wal','.sqlite3-shm')):return False
    return relative.name!='portable_manifest.json'


def sync_exploration(folder,manifest):
    shutil.copytree(ROOT/'data/exploration',folder/'data/exploration',dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('last_attempt.json','*.new.sqlite3','*.sqlite3-wal','*.sqlite3-shm'))
    # This dependency was introduced after the base portable runtime was built.
    # OpenCV and NumPy already reside in that runtime; copy the native capture
    # distribution, including its metadata and licence, from the build environment.
    dist=importlib.metadata.distribution('windows-capture')
    for relative in dist.files or []:
        if relative.is_absolute() or '..' in relative.parts or relative.suffix in {'.pyc','.pyo'}:continue
        source=dist.locate_file(relative)
        if not source.is_file():raise FileNotFoundError(source)
        target=folder/'runtime/Lib/site-packages'/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    manifest.setdefault('dependencies',{})['windows-capture']=dist.version


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--validation',type=Path,required=True)
    parser.add_argument('--sync-only',action='store_true',help='Prepare the portable folder before running its acceptance checks')
    args=parser.parse_args()
    folder=args.root.resolve();archive_path=args.archive.resolve();folder.relative_to(ROOT);archive_path.relative_to(ROOT)
    if archive_path.exists():raise FileExistsError(archive_path)
    manifest_path=folder/'portable_manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('entry')!='TreeSeaReader.exe':raise ValueError('Not a TreeSea portable folder')
    for name in ['reader','scripts','assets','database','docs','packaging']:
        shutil.copytree(ROOT/name,folder/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo'))
    for name in ['README.md','requirements.txt','launch_reader.py','FACTS.md']:
        shutil.copy2(ROOT/name,folder/name)
    shutil.copytree(ROOT/'data/sources',folder/'data/sources',dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo'))
    shutil.copy2(ROOT/'data/reader.sqlite3',folder/'data/reader.sqlite3')
    shutil.copy2(ROOT/'data/effects/catalog.json',folder/'data/effects/catalog.json')
    spec=importlib.util.spec_from_file_location('release_launchers',ROOT/'scripts/build_launchers.py')
    launchers=importlib.util.module_from_spec(spec);spec.loader.exec_module(launchers)
    launchers.compile_launchers(folder)
    version=launchers.release_version()
    manifest.update(version=version['VERSION'],display_version=version['DISPLAY_VERSION'])
    sync_exploration(folder,manifest)
    (folder/'使用说明.txt').write_text((ROOT/'packaging/portable_usage.txt').read_text(encoding='utf-8'),encoding='utf-8-sig')
    shutil.copy2(args.validation,folder/'packaging_validation.json')
    if args.sync_only:
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('Portable sources, exploration data and Windows capture dependency synchronized.',flush=True)
        return
    manifest['finalized_at']=datetime.now(timezone.utc).isoformat();manifest['validation']='packaging_validation.json';manifest['files']={}
    manifest['native_runtime_dlls']='Matching MSVC DLLs redistributed with PySide6 are also present next to python.exe.'
    files=[]
    for path in sorted(folder.rglob('*')):
        if path.is_file() and publishable(path.relative_to(folder)):
            relative=path.relative_to(folder);files.append(path)
            manifest['files'][relative.as_posix()]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');files.append(manifest_path)
    print('Compressing verified application folder...',flush=True)
    with ZipFile(archive_path,'x',ZIP_DEFLATED,compresslevel=6) as archive:
        for path in files:archive.write(path,Path('TreeSeaReader')/path.relative_to(folder))
    with ZipFile(archive_path) as archive:
        if archive.testzip() is not None:raise ValueError('Archive verification failed')
        if any('/data/sessions/' in name or '/data/captures/' in name or '/data/logs/' in name for name in archive.namelist()):raise ValueError('User state leaked into archive')
    digest=hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(archive_path.suffix+'.sha256').write_text(digest+'  '+archive_path.name+'\n',encoding='ascii')
    print(json.dumps({'archive':str(archive_path),'size_mib':round(archive_path.stat().st_size/1048576,1),'files':len(files),'verified':True},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

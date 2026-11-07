"""Collect default and outfit avatars from the versioned public asset index."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1,sha256
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote
from urllib.request import Request,urlopen
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reader.catalog import Catalog


def main():
    api='https://api.github.com/repos/yuanyan3060/ArknightsGameResource/git/trees/main?recursive=1'
    def fetch(url):
        with urlopen(Request(url,headers={'User-Agent':'TreeSeaReader/1.0 avatar index'}),timeout=35) as response:
            return response.read()
    tree=json.loads(fetch(api))
    if tree.get('truncated'):
        raise ValueError('Source asset tree is truncated')
    revision=tree['sha']
    known={r['id'] for r in Catalog().inventory['operators']}
    paths=[r for r in tree['tree'] if r['path'].startswith('avatar/char_') and r['path'].endswith('.png')]
    folder=ROOT/'assets/operators'
    folder.mkdir(parents=True,exist_ok=True)
    manifest=folder/'manifest.json'
    previous=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    indexed={a['asset_key']:a for a in previous.get('assets',[])}
    def collect(record):
        path=record['path']
        identity=re.search(r'char_\d+_[a-zA-Z0-9]+',path).group()
        if identity not in known:
            return None
        url='https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/'+revision+'/'+quote(path,safe='/')
        old=indexed.get(path)
        cached=old and old.get('source_blob')==record['sha'] and (folder/old['file']).is_file()
        data=(folder/old['file']).read_bytes() if cached else None
        if cached and sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()!=record['sha']:
            cached=False
        filename=old['file'] if cached else sha256((path+record['sha']).encode()).hexdigest()[:24]+'.png'
        output=folder/filename
        data=data if cached else fetch(url)
        if sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()!=record['sha']:
            raise ValueError('Source blob mismatch: '+path)
        if cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_UNCHANGED) is None:
            raise ValueError(url)
        if not cached:
            temporary=output.with_suffix('.png.tmp')
            temporary.write_bytes(data)
            temporary.replace(output)
        return dict(operator_id=identity,asset_key=path,file=filename,source_url=url,source_blob=record['sha'],
                    source_sha256=sha256(data).hexdigest())
    with ThreadPoolExecutor(max_workers=4) as pool:
        assets=[r for r in pool.map(collect,paths) if r]
    if not assets:
        raise ValueError('No avatars found in source tree')
    temporary=folder/'manifest.json.tmp'
    temporary.write_text(json.dumps(dict(index_url=api,revision=revision,assets=assets),ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(manifest)
    print(f'{len(assets)} avatars / {len({a["operator_id"] for a in assets})} operators')


if __name__=='__main__':
    main()

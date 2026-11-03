"""Download source PNG icons for the active Blackstream relic catalogue."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import sys
import cv2
import numpy as np
import time
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.collect_lubiao import LiteralParser


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'assets/relics')
    parser.add_argument('--retry-missing',action='store_true');args=parser.parse_args()
    args.output=args.output.resolve();args.output.relative_to(ROOT)
    args.output.mkdir(parents=True,exist_ok=True)
    index_url='https://www.lubiao.wiki/_nuxt/Dgqgu8wI.js'
    def fetch(url):
        request=urllib.request.Request(url,headers={'User-Agent':'TreeSeaReaderDataCollector/0.1 (public relic icons)'})
        with urllib.request.urlopen(request,timeout=20) as response:return response.read(4*1024*1024)
    index=fetch(index_url).decode('utf-8')
    objects=dict(re.findall(r'"([^"\\]+)":`([^`]+)`',index))
    sprites=LiteralParser(index[index.index('{',index.index('sprites:')):]).value()
    atlas_cache={};atlas_lock=threading.Lock()
    config=json.loads((ROOT/'assets/nodes/manifest.json').read_text(encoding='utf-8'))
    db=sqlite3.connect((ROOT/'data/exploration/knowledge.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    archive=json.loads(db.execute("SELECT data FROM payloads WHERE kind='source' AND id='archive'").fetchone()[0]);db.close()
    items={r['itemId']:r for r in archive['classifier']['items'] if r.get('topicId')=='rogue_6' and r.get('dataType')=='藏品'}
    from reader.catalog import Catalog
    for item in Catalog().inventory['relics']:
        items.setdefault(item['id'],dict(itemId=item['id'],name=item['name'],topicItem={'iconId':item['id']}))
    previous=json.loads((args.output/'manifest.json').read_text(encoding='utf-8')) if (args.output/'manifest.json').exists() else {}
    known={r['id']:r for r in previous.get('assets',[]) if (ROOT/r['path']).exists()}
    def download(item):
        identity=item['itemId'];icon=item.get('topicItem',{}).get('iconId') or identity
        key='items/'+icon+'.png';asset=objects.get(key)
        visited=set()
        while asset in objects and asset not in visited:
            visited.add(asset);asset=objects[asset]
        sprite=sprites.get(key)
        if not asset and sprite:asset=objects.get(sprite['atlasKey'],sprite['atlasKey'])
        if not asset:return None,{'id':identity,'name':item['name'],'error':'No source object or sprite in asset index'}
        url=config['runtime_asset_base'].rstrip('/')+'/'+config['runtime_asset_prefix'].strip('/')+'/'+urllib.parse.quote(asset,safe='/')+'?response-policy=corp-v1'
        path=args.output/(identity+'.png')
        try:
            atlas_hash=None
            time.sleep(.15)
            if sprite and key not in objects:
                with atlas_lock:
                    if url not in atlas_cache:
                        atlas_data=fetch(url);atlas_path=args.output/'atlases'/Path(asset).name
                        atlas_path.parent.mkdir(parents=True,exist_ok=True);atlas_path.write_bytes(atlas_data)
                        atlas_cache[url]=(cv2.imdecode(np.frombuffer(atlas_data,np.uint8),cv2.IMREAD_UNCHANGED),hashlib.sha256(atlas_data).hexdigest())
                    atlas,atlas_hash=atlas_cache[url]
                x,y,w,h=[sprite[k] for k in ('x','y','width','height')]
                canvas=np.zeros((sprite['sourceHeight'],sprite['sourceWidth'],4),np.uint8)
                ox,oy=sprite['offsetX'],sprite['offsetY'];canvas[oy:oy+h,ox:ox+w]=atlas[y:y+h,x:x+w]
                ok,encoded=cv2.imencode('.png',canvas)
                if not ok:raise ValueError('Sprite encoding failed')
                data=encoded.tobytes()
            else:data=fetch(url)
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Not PNG')
            path.write_bytes(data)
            return dict(id=identity,name=item['name'],path=path.relative_to(ROOT).as_posix(),source_url=url,
                        sha256=hashlib.sha256(data).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat(),sprite=sprite if key not in objects else None,source_atlas_sha256=atlas_hash),None
        except Exception as exc:return None,dict(id=identity,name=item['name'],source_url=url,error=str(exc))
    missing=[];pending=[r for i,r in items.items() if i not in known]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for n,future in enumerate(as_completed([pool.submit(download,item) for item in pending]),1):
            asset,error=future.result()
            if asset:known[asset['id']]=asset
            else:missing.append(error)
            if n%25==0 or n==len(pending):print(f'Icons {n}/{len(pending)}; available {len(known)}; missing {len(missing)}',flush=True)
    unresolved=[r for r in missing if re.search(r'_[abc]$',r['id'])]
    missing=[r for r in missing if r not in unresolved]
    manifest=dict(schema_version=1,topic='rogue_6',asset_index=dict(url=index_url,sha256=hashlib.sha256(index.encode()).hexdigest()),
                  assets=sorted(known.values(),key=lambda r:r['id']),missing=missing,variants_without_distinct_asset=unresolved,pixel_policy='Original PNG bytes or exact RGBA sprite rectangle copied into original source dimensions; no resampling')
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return 1 if any('source_url' in row for row in missing) else 0


if __name__=='__main__':raise SystemExit(main())

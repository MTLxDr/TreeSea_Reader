"""Refresh Blackstream effect references and all stage enemy reference pages."""
from collections import Counter
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

from collect_prts import Collector, CORE_TITLES, utcnow

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-core-only',action='store_true')
    parser.add_argument('--minimum-age-hours',type=float,default=168)
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args()
    folder=ROOT/'data/sources/combat_prts'
    if args.refresh_core_only:
        manifest=json.loads((folder/'latest_manifest.json').read_text(encoding='utf-8'))
        primary=Collector(ROOT/'data/sources/prts')
        for page in manifest['pages']:
            if page['title'] not in CORE_TITLES:continue
            target=primary.output/page['path'];target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():target.write_bytes((folder/page['path']).read_bytes())
            primary.pages[page['title']]=page
        primary.save_manifest()
        print('Primary PRTS core catalog refreshed from captured HTTP responses')
        return 0
    existing=folder/'latest_manifest.json'
    if existing.exists() and not args.force:
        data=json.loads(existing.read_text(encoding='utf-8'))
        timestamp=data.get('generated_at')
        if timestamp and (datetime.now(timezone.utc)-datetime.fromisoformat(timestamp.replace('Z','+00:00'))).total_seconds()<args.minimum_age_hours*3600:
            print('Combat source cache is within its refresh interval')
            return 0
    db=sqlite3.connect(ROOT/'data/reader.sqlite3')
    rows=db.execute("SELECT DISTINCT json_extract(e.payload_json,'$.name') FROM entities e JOIN active_documents a ON a.document_id=e.document_id WHERE e.kind='stage_enemy'").fetchall()
    names=sorted(r[0] for r in rows if r[0])
    db.close()
    titles=list(dict.fromkeys(CORE_TITLES+['游戏数据基础','作战机制']+names))
    collector=Collector(folder)
    for index in range(0,len(titles),10):
        batch=titles[index:index+10]
        if not collector.fetch(batch,delay=.7,timeout=25,retries=1,max_bytes=8_000_000):break
        print(f'Combat sources fetched {min(index+10,len(titles))}/{len(titles)}',flush=True)
    manifest=collector.save_manifest()
    manifest['requested_titles']=titles
    manifest['enemy_titles']=names
    manifest['collected_scope']='Blackstream plus DLC, stage enemy references, formula foundations'
    path=folder/'latest_manifest.json'
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'requested':len(titles),'collected':len(manifest['pages']),'failures':len(manifest['failures'])},ensure_ascii=False))
    return int(any(f.get('http_status') for f in manifest['failures']))


if __name__=='__main__':raise SystemExit(main())

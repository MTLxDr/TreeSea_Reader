"""Refresh source snapshots and build the separate exploration database."""
import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import sys
import time
from urllib.error import URLError,HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
SOURCES={
    'floors':'https://www.lubiao.wiki/data/foundation-floors-rogue_6.json',
    'archive':'https://www.lubiao.wiki/data/archives/blackstream.json',
    'events':'https://www.lubiao.wiki/data/encounters/rogue_6.json',
    'battles':'https://www.lubiao.wiki/data/battles/rogue_6.json',
    'details':'https://www.lubiao.wiki/data/battles/rogue_6-details.json'}
SOURCES['enemy_names']='https://map.ark-nights.com/data/levels/enemydata/enemy_database.json'


def collect_map_index(output,resume=False,offline=False):
    target=output/'map_index.json'
    if (resume or offline) and target.exists():return json.loads(target.read_text(encoding='utf-8'))
    if offline:raise ValueError('Map index not collected')
    with urlopen('https://map.ark-nights.com/',timeout=25) as r:page=r.read().decode()
    module=re.search(r'src="(/bundle\.[^"/]+\.esm\.js)"',page)
    if not module:raise ValueError('PRTS.Map bundle not found')
    url='https://map.ark-nights.com'+module[1]
    with urlopen(url,timeout=30) as r:raw=r.read(30_000_001)
    if len(raw)>30_000_000:raise ValueError('Map bundle exceeds 30MB')
    return parse_map_index(raw,url,output,target)


def parse_map_index(raw,url,output,target,retrieved_at=None):
    digest=hashlib.sha256(raw).hexdigest();blob=output/'blobs'/(digest+'.js')
    if not blob.exists():blob.write_bytes(raw)
    text=raw.decode();stages={};metadata={};names={};decoder=json.JSONDecoder()
    for match in re.finditer(r'"(ro6_[^" ]+)":(\{[^{}]{1,600}\})',text):
        try:
            row=json.loads(match[2])
            if row.get('data_path'):stages[match[1]]=row
        except ValueError:pass
    for match in re.finditer(r'"(ro6_[^" ]+)":(?=\{"zh_CN")',text):
        try:
            row,_=decoder.raw_decode(text,match.end());metadata[match[1]]=row['zh_CN']
        except (ValueError,KeyError):pass
    for match in re.finditer(r'"((?:trap|enemy)_[^" ]+)":(?=\{"zh_CN")',text):
        try:
            row,_=decoder.raw_decode(text,match.end())
            if row['zh_CN'].get('name'):names[match[1]]=row['zh_CN']['name']
        except (ValueError,KeyError):pass
    result={'source':{'url':url,'sha256':digest,'path':blob.relative_to(output).as_posix(),
        'retrieved_at':retrieved_at or datetime.now(timezone.utc).isoformat()},'stages':stages,'metadata':metadata,'names':names}
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return result


def collect(output=ROOT/'data/exploration', offline=False, resume=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);blobs=output/'blobs';blobs.mkdir(exist_ok=True)
    previous=json.loads((output/'manifest.json').read_text(encoding='utf-8')) if (output/'manifest.json').exists() else {}
    attempt_path=output/'last_attempt.json'
    if resume and attempt_path.exists():previous=json.loads(attempt_path.read_text(encoding='utf-8'))
    manifest={'schema_version':1,'retrieved_at':datetime.now(timezone.utc).isoformat(),'sources':{},'errors':[]}
    def fetch(url):
        if offline or (resume and url in previous.get('sources',{})):
            old=previous.get('sources',{}).get(url)
            if not old:raise ValueError('No saved source: '+url)
            path=output/old['path'];raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=old['sha256']:raise ValueError('Source hash mismatch')
        else:
            for attempt in range(2):
                try:
                    with urlopen(Request(url,headers={'User-Agent':'TreeSeaReader/0.2 public data refresh'}),timeout=25) as response:raw=response.read(30_000_001)
                    break
                except (URLError,TimeoutError) as e:
                    if attempt or isinstance(e,HTTPError) and e.code in {403,404}:raise
                    time.sleep(.5)
            if len(raw)>30_000_000:raise ValueError('Source exceeds 30MB')
        data=json.loads(raw);digest=hashlib.sha256(raw).hexdigest();path=blobs/(digest+'.json')
        if not path.exists():path.write_bytes(raw)
        return data,{'url':url,'path':path.relative_to(output).as_posix(),'sha256':digest,
                     'retrieved_at':previous.get('sources',{}).get(url,{}).get('retrieved_at') if offline or (resume and url in previous.get('sources',{})) else manifest['retrieved_at']}
    data={}
    for key,url in SOURCES.items():
        payload,source=fetch(url);data[key]=payload;manifest['sources'][url]=source
    cached_bundle=next((s for url,s in previous.get('sources',{}).items() if url.endswith('.esm.js')),None)
    if resume and cached_bundle:
        raw=(output/cached_bundle['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=cached_bundle['sha256']:raise ValueError('Map index hash mismatch')
        map_index=parse_map_index(raw,cached_bundle['url'],output,output/'map_index.json',cached_bundle.get('retrieved_at'))
    else:map_index=collect_map_index(output,resume,offline)
    manifest['sources'][map_index['source']['url']]=map_index['source']
    details=dict(data['details']['stages']);stages={};names={}
    battle_index={s['id']:s for s in data['battles']['stages']}
    for sid,index in map_index['stages'].items():
        meta=map_index.get('metadata',{}).get(sid,{})
        if meta.get('enabled') is False:continue
        if sid not in details:
            details[sid]={'id':sid,'levelId':index['data_path'].removesuffix('.json'),'difficulty':index['difficulty']}
            floor_match=re.match(r'ro6_[ne]_(\d)_',sid)
            battle_index[sid]={'id':sid,'name':meta.get('name',sid),'description':meta.get('description',''),
                'floor':int(floor_match[1]) if floor_match else None,'type':sid.split('_')[1]}
    print(f'Collecting {len(details)} stage variants from Lubiao + PRTS.Map',flush=True)
    level_ids={}
    for sid,detail in details.items():level_ids.setdefault(detail['levelId'].lower(),[]).append(sid)
    def stage_job(level):
        url='https://map.ark-nights.com/data/levels/'+level+'.json'
        payload,source=fetch(url)
        return level,payload,source
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending={executor.submit(stage_job,level):level for level in level_ids}
        for completed,future in enumerate(as_completed(pending),1):
            try:
                level,payload,source=future.result();manifest['sources'][source['url']]=source
                for sid in level_ids[level]:stages[sid]=dict(battle_index.get(sid,{}),id=sid,detail=details[sid],level=payload,source=source)
            except Exception as e:manifest['errors'].append({'ids':level_ids[pending[future]],'error':str(e)})
            if completed%10==0 or completed==len(pending):print(f'关卡文件 {completed}/{len(pending)}，失败 {len(manifest["errors"])}',flush=True)
    if manifest['errors']:
        (output/'last_attempt.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raise ValueError('Some source levels could not be collected; existing knowledge database retained. See last_attempt.json')
    for s in data['details']['stages'].values():
        for e in s.get('enemies',[])+s.get('devices',[]):names[e['id']]=e['name']
    for row in data['enemy_names'].get('enemies',[]):
        key=row.get('Key');variants=row.get('Value',[])
        for variant in variants:
            name=variant.get('enemyData',{}).get('name',{})
            if name.get('m_defined') and name.get('m_value'):names[key]=name['m_value'];break
    names.update(map_index.get('names',{}))
    # Elite variants reuse normal metadata; the difficulty remains explicit.
    for sid,s in stages.items():
        if not s.get('name'):
            linked=battle_index.get(s['detail'].get('linkedStageId'),{})
            s.update(name=linked.get('name',sid),floor=linked.get('floor'),type='e' if sid.startswith('ro6_e_') else linked.get('type'))
    target=output/'knowledge.sqlite3';temporary=output/'knowledge.new.sqlite3'
    if temporary.exists():temporary.unlink()
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript('CREATE TABLE payloads(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));CREATE TABLE sources(url TEXT PRIMARY KEY,sha256 TEXT,data TEXT);')
        def put(kind,identity,value):db.execute('INSERT INTO payloads VALUES(?,?,?)',(kind,str(identity),json.dumps(value,ensure_ascii=False)))
        for key in ('floors','archive','events'):put('source',key,data[key])
        put('index','names',names);put('index','versions',list(data['archive']['pools'].get('dataVersions',{})))
        for sid,s in stages.items():put('stage',sid,s)
        for url,s in manifest['sources'].items():db.execute('INSERT INTO sources VALUES(?,?,?)',(url,s['sha256'],json.dumps(s,ensure_ascii=False)))
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity failure')
        db.commit()
    temporary.replace(target)
    manifest['stage_count']=len(stages);manifest['expected_stages']=len(details)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if attempt_path.exists():attempt_path.unlink()
    print(json.dumps({'stage_count':len(stages),'expected':len(details),'errors':manifest['errors'],'database':str(target)},ensure_ascii=False),flush=True)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--offline',action='store_true');p.add_argument('--output',type=Path,default=ROOT/'data/exploration')
    p.add_argument('--resume',action='store_true',help='Reuse already collected immutable sources and fetch missing levels')
    a=p.parse_args();collect(a.output,a.offline,a.resume)

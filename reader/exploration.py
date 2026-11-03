"""Read-only source/version-aware exploration queries."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .catalog import ROOT


class ExplorationCatalog:
    _shared=None
    @classmethod
    def shared(cls):
        if cls._shared is None:cls._shared=cls()
        return cls._shared
    def __init__(self,path=ROOT/'data/exploration/knowledge.sqlite3'):
        self.path=Path(path)
        self.reload()
    def reload(self):
        """Drop cached payloads and re-read the source snapshots (after an update)."""
        self.cache={}
        self.floors=self.read('source','floors') or {};self.archive=self.read('source','archive') or {}
        self.events=(self.read('source','events') or {}).get('events',{})
        self.names=self.read('index','names') or {}
        pools=self.archive.get('pools',{});self.default_version=pools.get('defaultVersionId','dlc1')
        self.versions=pools.get('dataVersions',{}) or {self.default_version:pools.get('pools',[])}
        self.event_names={e['name']:e['id'] for e in self.events.values()}
        for f in self.floors.get('floors',[]):
            for e in f.get('eventsByNodeType',{}).get('INCIDENT',[]):self.event_names.setdefault(e['name'],'rogue_6:event:'+e['name'])
    def read(self,kind,identity):
        key=kind,str(identity)
        if key in self.cache:return self.cache[key]
        if not self.path.exists():return None
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT data FROM payloads WHERE kind=? AND id=?',key).fetchone()
        value=json.loads(row[0]) if row else None
        # Full map levels are loaded only by the independent query UI.
        if kind!='stage':self.cache[key]=value
        return value
    def stage_index(self):
        if not self.path.exists():return []
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            return [dict(id=row[0],name=row[1],floor=row[2],type=row[3]) for row in db.execute(
                "SELECT id,json_extract(data,'$.name'),json_extract(data,'$.floor'),json_extract(data,'$.type') FROM payloads WHERE kind='stage' ORDER BY id")]
    def match_event(self,tokens):
        for token in tokens:
            text=token['text'].strip()
            if token.get('score',0)>=.92 and text in self.event_names:
                return {'id':self.event_names[text],'name':text}
        return None
    def eligible_events(self,floor,history=(),entrance_floor=None):
        reference_only=floor=='treehole'
        if reference_only:floor=entrance_floor
        row=next((f for f in self.floors.get('floors',[]) if f.get('floor')==floor),None)
        if row is None:return {'events':[],'conditional':[],'method':'选择楼层','excluded':[]}
        seen={e.get('name') for e in history};eligible=[];excluded=[]
        for e in row.get('eventsByNodeType',{}).get('INCIDENT',[]):
            item=dict(e,id=self.event_names.get(e['name'],'rogue_6:event:'+e['name']))
            if e['name'] in seen and not e.get('repeatable',False):excluded.append(item);continue
            eligible.append(item)
        observed=all(isinstance(e.get('count'),(int,float)) and e['count']>=0 for e in eligible) and bool(eligible)
        total=sum(e.get('count',1) if observed else 1 for e in eligible)
        for e in eligible:e['probability']=(e.get('count',1) if observed else 1)/total if total else None
        method='路标频数归一化' if observed else '按路标池成员等权估算'
        return {'events':eligible,'excluded':excluded,'conditional':[e for e in row.get('conditionalEvents',[]) if e.get('nodeType')=='INCIDENT'],
            'method':('入口楼层参考池 · ' if reference_only else '')+method,'reference_only':reference_only,
            'floor':floor,'source':'https://www.lubiao.wiki/data/foundation-floors-rogue_6.json'}
    def event_index(self):
        items={}
        for floor in self.floors.get('floors',[]):
            pools=[(floor.get('eventsByNodeType',{}).get('INCIDENT',[]),False),
                   ([e for e in floor.get('conditionalEvents',[]) if e.get('nodeType')=='INCIDENT'],True)]
            for rows,conditional in pools:
                for event in rows:
                    identity=self.event_names.get(event['name'],'rogue_6:event:'+event['name'])
                    row=items.setdefault(identity,dict(event,id=identity,floors=[],conditional_floors=[]))
                    key='conditional_floors' if conditional else 'floors'
                    if floor['floor'] not in row[key]:row[key].append(floor['floor'])
                    row['repeatable']=bool(row.get('repeatable') or event.get('repeatable'))
        return items

    def event_info(self,identity):
        return self.event_index().get(identity,{})

    def event_options(self,floor,history=(),entrance_floor=None):
        current=entrance_floor if floor=='treehole' else floor
        seen={e.get('id') for e in history}
        rows=[dict(e,current=current in e['floors'],conditional=current in e['conditional_floors'])
              for e in self.event_index().values() if e['id'] not in seen or e.get('repeatable')]
        return sorted(rows,key=lambda e:(not e['current'],not e['conditional'],e['name']))

    def pools(self,version=None):return self.versions.get(version or self.default_version,[])
    def remaining_items(self,pool_id,inventories,version=None):
        pool=next((p for p in self.pools(version) if p['id']==pool_id),None)
        if pool is None:return None
        owned={i['id'] for kind in ('relics','parts') for i in inventories.get(kind,{}).get('items',[])
               if i.get('confirmed') and not i.get('excluded')}
        members=pool.get('collectibles',[])
        return {'pool':pool,'remaining':[i for i in members if i not in owned],
                'owned':[i for i in members if i in owned],'version':version or self.default_version}

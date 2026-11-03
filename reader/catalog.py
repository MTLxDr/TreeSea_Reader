from __future__ import annotations

from collections import deque
from contextlib import closing
import json
import re
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]

NODE_NAMES = {
    "unknown-mystery": "未知的诡秘", "unknown-ferocity": "未知的凶戾",
    "combat": "作战", "emergency-combat": "紧急作战", "rogue-trader": "诡意行商",
    "secret-trader": "秘境行商", "danger-end": "险路尽头", "danger-path": "险路小径",
    "danger-enemy": "险路恶敌", "overlook": "羽瞰点", "empty": "林间空地",
    "encounter": "不期而遇", "wish": "得偿所愿", "rest": "安全的角落",
    "settlement": "居民据点", "duel": "狭路相逢", "exchange": "失与得",
    "expedition": "先行一步", "aid": "应急助力", "wonderland": "误入奇境",
    "tunnel": "曲折密道", "fate-choice": "命运所指", "unobserved": "未识别",
}


class Catalog:
    def __init__(self, database=ROOT / "data/reader.sqlite3"):
        self.database = Path(database)
        if not self.database.exists():
            raise FileNotFoundError("缺少数据库，请先运行 python scripts/database.py build")
        self.reload()

    def reload(self):
        """Re-read every cached table; used after the database has been rebuilt."""
        self.templates = []
        self.side_templates = []
        self.floor_names = {}
        self.distance_counts = {}
        self.count_limits = {}
        self.count_limit_gaps = {}
        self.detail_index = {}
        self.inventory = {"relics": [], "parts": [], "operators": []}
        with closing(self.connect()) as db:
            gap_pairs=set()
            for document,payload in db.execute('SELECT DISTINCT d.id,d.payload FROM documents d JOIN active_node_count_limits c ON c.document_id=d.id'):
                raw=json.loads(payload)
                for item in raw.get('rows',[]):
                    if item['label']=='地图节点总数':continue
                    for floor,bounds in zip(raw.get('floors',[]),item['values']):
                        if bounds is None:
                            gap_pairs.add((document,str(floor),item['label']))
                            self.count_limit_gaps.setdefault(str(floor),[]).append(item['label'])
            for row in db.execute("SELECT * FROM active_node_count_limits"):
                if (row["document_id"],row["floor_id"],row["node_name"]) in gap_pairs:continue
                self.count_limits.setdefault(row["floor_id"],{})[row["node_name"]] = {
                    "minimum":row["minimum"],"maximum":row["maximum"],"document_id":row["document_id"]}
            for row in db.execute("SELECT operator_id,name,document_id FROM active_operator_details"):
                self.detail_index[row["operator_id"]] = dict(row)
            for row in db.execute("SELECT c.*,d.source_url FROM current_prts_catalog c JOIN documents d ON d.id=c.document_id"):
                kind = "relics" if row["kind"] == "prts_relic" else "parts"
                self.inventory[kind].append({"id": row["entity_id"], "name": row["name"],
                    "document_id": row["document_id"], "source_url": row["source_url"],
                    "effect": row["effect_wikitext"]})
            for row in db.execute("SELECT e.*,d.source_url FROM entities e JOIN active_documents a ON a.document_id=e.document_id JOIN documents d ON d.id=e.document_id WHERE e.kind='prts_operator'"):
                data = json.loads(row["payload_json"])
                self.inventory["operators"].append({"id": row["entity_id"], "name": row["name"],
                    "aliases": [re.sub(r"\([^)]*\)$", "", row["name"])],
                    "document_id": row["document_id"], "source_url": data.get("source_url", row["source_url"]), "attributes": data["attributes"]})
            for row in db.execute("SELECT template_id,payload_json FROM active_map_templates"):
                item = json.loads(row["payload_json"])
                item["template_id"] = row["template_id"]
                self.templates.append(item)
            for row in db.execute("SELECT template_id,payload_json,document_id FROM active_side_map_templates"):
                item = json.loads(row["payload_json"])
                item["template_id"] = row["template_id"]
                item["document_id"] = row["document_id"]
                self.side_templates.append(item)
            for row in db.execute("SELECT f.payload_json FROM map_floors f JOIN active_documents a ON a.document_id=f.document_id"):
                item = json.loads(row[0])
                if item.get("zoneName") and isinstance(item.get("floor"), int):
                    self.floor_names[item["zoneName"]] = item["floor"]
            for row in db.execute("SELECT floor_id,distance,node_type,observation_count,document_id FROM active_node_distance_counts"):
                key = (row["floor_id"], row["distance"])
                self.distance_counts.setdefault(key, {}).setdefault(row["document_id"], {})[row["node_type"]] = row["observation_count"]

    def connect(self):
        conn = sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def include_exploration_items(self):
        from .exploration import ExplorationCatalog
        knowledge=ExplorationCatalog.shared()
        if not knowledge.archive:return
        source='https://www.lubiao.wiki/data/archives/blackstream.json'
        known={kind:{r['id'] for r in rows} for kind,rows in self.inventory.items()}
        for row in knowledge.archive.get('classifier',{}).get('items',[]):
            kind={'藏品':'relics','零件':'parts'}.get(row.get('dataType'));identity=row.get('itemId')
            if kind is None or not identity or identity in known[kind] or row.get('topicId')!='rogue_6':continue
            self.inventory[kind].append({'id':identity,'name':row['name'],'effect':row.get('rawEffect',''),
                'document_id':'exploration:archive','source_url':source})
            known[kind].add(identity)

    def statistics_floor(self,floor):
        return '追忆' if str(floor)=='4' and getattr(self,'fourth_floor_memory',False) else str(floor)

    def probabilities(self, floor, distance, node_type):
        floor=Catalog.statistics_floor(self,floor)
        if node_type not in {"unknown-mystery", "unknown-ferocity"} or distance is None:
            return None
        data = self.distance_counts.get((str(floor), distance), {})
        if len(data) != 1:
            return None
        doc, counts = next(iter(data.items()))
        names = (["作战", "紧急作战", "居民据点", "“居民”据点"] if node_type == "unknown-ferocity" else
                 ["诡意行商", "秘境行商", "不期而遇", "得偿所愿", "安全的角落", "狭路相逢",
                  "失与得", "先行一步", "应急助力", "误入奇境"])
        if floor=='1' and node_type=='unknown-mystery':names.remove('诡意行商')
        if floor not in {'2','4','追忆','5'}:
            names=[n for n in names if n not in {'居民据点','“居民”据点'}]
        counts = {name: counts[name] for name in names if name in counts}
        total = sum(counts.values())
        if not total:
            return None
        return {"document_id": doc, "sample_count": total, "conditions": "楼层、源站距起点距离、未知类别",
                "estimates": [{"name": name, "count": count, "probability": count / total}
                              for name, count in sorted(counts.items(), key=lambda pair: -pair[1])],
                "limitation": "经验条件频率；未使用模板/标记的联合统计，零观测不证明不可能。"}

    def distance_distribution(self,floor,name):
        floor=Catalog.statistics_floor(self,floor)
        rows=[]
        documents=set()
        for (fid,distance),sources in self.distance_counts.items():
            if fid!=str(floor): continue
            for doc,counts in sources.items():
                documents.add(doc)
                if name in counts: rows.append({"distance":distance,"count":counts[name]})
        total=sum(r["count"] for r in rows)
        if len(documents)!=1 or not total:return None
        return {"sample_count":total,"document_id":next(iter(documents)),"estimates":[dict(r,probability=r["count"]/total) for r in sorted(rows,key=lambda r:r["distance"])]}

    def operator_detail(self,operator_id):
        with closing(self.connect()) as db:
            row=db.execute("SELECT t.payload_json,d.acquisition,d.revision_id,d.fetched_at FROM active_operator_details t JOIN documents d ON d.id=t.document_id WHERE operator_id=?",(operator_id,)).fetchone()
            if row is None:return None
            return dict(json.loads(row["payload_json"]),acquisition=row["acquisition"],revision_id=row["revision_id"],fetched_at=row["fetched_at"])

    def effect_summary(self):
        with closing(self.connect()) as db:
            row=db.execute("SELECT value FROM database_meta WHERE key='effect_catalog_summary'").fetchone()
            data=json.loads(row[0]) if row else {'entry_count':0,'actual_by_category':{},'review_status':{},'execution_status':{}}
            data['catalog_hash']=(db.execute("SELECT value FROM database_meta WHERE key='effect_catalog_hash'").fetchone() or [None])[0]
            return data

    def search_effects(self,text='',category=None,side=None,status=None,limit=200,offset=0):
        limit=max(1,min(500,int(limit)));offset=max(0,int(offset))
        clauses=['a.document_id=e.document_id'];params=[]
        for column,value in [('category',category),('side',side)]:
            if value:clauses.append('e.'+column+'=?');params.append(value)
        if status:
            column='review_status' if status in {'catalogued','source_annotated','source_parameters'} else 'execution_status'
            clauses.append('e.'+column+'=?');params.append(status)
        if text:
            pattern='%'+text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            clauses.append("(e.name LIKE ? ESCAPE '\\' OR e.variant LIKE ? ESCAPE '\\' OR json_extract(e.payload_json,'$.raw_effect') LIKE ? ESCAPE '\\')")
            params.extend([pattern]*3)
        where=' AND '.join(clauses)
        with closing(self.connect()) as db:
            total=db.execute('SELECT COUNT(*) FROM effect_entries e JOIN active_documents a ON '+where,params).fetchone()[0]
            rows=[dict(r) for r in db.execute('SELECT e.id,e.category,e.name,e.variant,e.side,e.has_numeric,e.review_status,e.execution_status FROM effect_entries e JOIN active_documents a ON '+where+' ORDER BY e.category,e.name,e.variant LIMIT ? OFFSET ?',params+[limit,offset])]
            return {'total':total,'limit':limit,'offset':offset,'entries':rows}

    def effect_detail(self,identity):
        with closing(self.connect()) as db:
            row=db.execute('SELECT e.payload_json,EXISTS(SELECT 1 FROM active_documents a WHERE a.document_id=e.document_id) '
                           'FROM effect_entries e WHERE e.id=?',(identity,)).fetchone()
            if row is None:return None
            data=json.loads(row[0]);data['active_revision']=bool(row[1])
            if row[1]:
                from .effect_rules import compile_entry
                data['compilation']=compile_entry(data)
            return data


def distances(template):
    """Source distance semantics: neighbors sharing an exit count as adjacent.

    This graph is used only for source statistics, not action point planning.
    """
    graph = {tuple(p): set() for p in template["occupiedSlots"]}
    for a, b in template["edges"]:
        graph[tuple(a)].add(tuple(b))
        graph[tuple(b)].add(tuple(a))
    for terminal in template.get("terminalSlots", []):
        neighbors = graph[tuple(terminal)].copy()
        for a in neighbors:
            graph[a].update(neighbors - {a})
    start = tuple(template["startSlot"])
    result, queue = {start: 0}, deque([start])
    while queue:
        a = queue.popleft()
        for b in graph[a]:
            if b not in result:
                result[b] = result[a] + 1
                queue.append(b)
    return result

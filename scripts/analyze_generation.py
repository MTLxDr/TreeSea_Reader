"""Export the available generation statistics without inventing joint observations."""
from datetime import datetime,timezone,timedelta
from contextlib import closing
import argparse
import json
from pathlib import Path
import sqlite3
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reader.catalog import Catalog
from reader.exploration import ExplorationCatalog


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'data/exploration/generation_model.json')
    parser.add_argument('--markdown',type=Path,default=ROOT/'docs/generation_statistics.md')
    parser.add_argument('--checked-source',type=Path,default=ROOT/'data/sources/lubiao/latest_manifest.json');args=parser.parse_args()
    args.output=args.output.resolve();args.markdown=args.markdown.resolve();args.checked_source=args.checked_source.resolve()
    catalogue=Catalog();events=ExplorationCatalog()
    with closing(catalogue.connect()) as db:
        templates=[dict(r) for r in db.execute('SELECT t.* FROM template_statistics t JOIN active_documents a ON a.document_id=t.document_id ORDER BY t.floor_id,t.template_id')]
    distributions=[]
    for (floor,distance),sources in sorted(catalogue.distance_counts.items(),key=lambda p:(p[0][0],p[0][1])):
        for document,counts in sources.items():
            distributions.append(dict(floor=floor,distance=distance,document_id=document,counts=counts))
    source=json.loads(args.checked_source.read_text(encoding='utf-8'))
    source_records={r['url']:r for r in source.get('resources',[])}
    checked=[]
    for url,row in source_records.items():
        if not any(name in url for name in ('node-distance-frequencies-rogue_6','foundation-map-constructions-rogue_6','foundation-floors-rogue_6','encounters/rogue_6')):continue
        original=args.checked_source.parent/row['relative_path']
        target=ROOT/'data/exploration/blobs'/original.name
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():shutil.copy2(original,target)
        checked.append(dict(url=url,sha256=row['sha256'],fetched_at=row.get('fetched_at'),origin=row.get('origin'),path=target.relative_to(ROOT).as_posix()))
    source_check=ROOT/'data/exploration/generation_source_check.json'
    source_check.write_text(json.dumps(dict(checked_at=source.get('run_at'),sources=checked),ensure_ascii=False,indent=2),encoding='utf-8')
    event_index=events.event_index()
    model=dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),
        source_check=str(source_check.relative_to(ROOT)),source_run_at=source.get('run_at'),
        templates=templates,fixed_nodes=[dict(template=t['template_id'],floor=t['floor'],nodes=t['fixedNodes']) for t in catalogue.templates if t.get('fixedNodes')],
        node_distance_counts=distributions,observed_count_ranges=catalogue.count_limits,missing_count_ranges=catalogue.count_limit_gaps,
        count_range_kind='observed extrema, not proven generator limits',
        events=list(event_index.values()),event_weights_available=False,
        event_probability='equal membership conditional on floor, eligibility and repeat history; not measured encounter frequency',
        joint_observations_available=False,
        scope='Main map. Right-hand ending map is separate. Distance uses source adjacency including shared-exit adjacency.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding='utf-8')
    text=['# 地图生成与事件分布统计','',f"整理日期：{datetime.now(timezone(timedelta(hours=8))).date()}。原始统计与导出：`{args.output.relative_to(ROOT).as_posix()}`。",'',
        '## 1. 当前可用证据','',
        '- 构型频数、节点距起点频数、数量范围来自不同来源快照；不能拼成不存在的“构型×位置×类型”逐图联合样本。',
        '- 数量表空白表示暂无具名观测，不能导入为0–0；一层未知诡秘排除诡意行商，固定商店单独标注。',
        '- 第4层普通规则和追忆统计分开选择；居民据点的楼层、5步范围及有正初始证据时的空地数量共同约束。',
        '- 数量范围原文为观测极值。默认范围估算保留为可关闭模型；冲突时回到距离频率。范围模型中的零不表示游戏绝对禁止。',
        '- 事件资料提供楼层池、重复性与条件项，没有遭遇次数。已遇非重复事件退出池，可重复事件继续保留。',
        '- 来源获取时间见导出记录；没有遭遇频数的事件不填造权重。','',
        '## 2. 构型分布','', '| 楼层 | 构型 | 计数 | 本层样本 | 频率 |','|---|---|---:|---:|---:|']
    for row in templates:
        n=row['floor_sample_count'];text.append(f"| {row['floor_id']} | {row['template_id']} | {row['observation_count']} | {n} | {row['observation_count']/n:.2%} |" if n else f"| {row['floor_id']} | {row['template_id']} | {row['observation_count']} | 0 | — |")
    text+=['','## 3. 一层固定商店','', '| 构型 | 诡意行商格点（x向右、y向下） |','|---|---|']
    for row in model['fixed_nodes']:
        if row['floor']==1:text.append(f"| {row['template']} | {', '.join(str(n['slot']) for n in row['nodes'] if n['type']=='rogue-trader')} |")
    text+=['','## 4. 不期而遇池','', '| 事件 | 普通可遇楼层 | 条件楼层 | 可重复 |','|---|---|---|---|']
    for row in sorted(event_index.values(),key=lambda r:r['name']):
        text.append(f"| {row['name']} | {'、'.join(map(str,row['floors'])) or '—'} | {'、'.join(map(str,row['conditional_floors'])) or '—'} | {'是' if row.get('repeatable') else '否'} |")
    text+=['','## 5. 概率的输入与更新','',
        '节点输入为楼层、来源距离、未知类别、固定节点、可靠已揭示节点、居民记录和数量范围；事件输入为楼层、入口楼层、资格和本局事件记录。输入、人工修改或统计开关变化后重新计算，同图不重复累计。',
        '显示使用整数百分比，内部保留计数归一化结果。剩余藏品池随持有清单更新。没有来源的事件条件、版本关系与联合权重不填经验数值。','',
        '来源：路标 `foundation-map-constructions-rogue_6.json`、`node-distance-frequencies-rogue_6.json`、`foundation-floors-rogue_6.json`、`encounters/rogue_6.json`；具体文档编号、计数与快照记录见JSON。']
    args.markdown.parent.mkdir(parents=True,exist_ok=True);args.markdown.write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(json.dumps(dict(templates=len(templates),distance_rows=len(distributions),events=len(event_index),output=str(args.output)),ensure_ascii=False))


if __name__=='__main__':main()

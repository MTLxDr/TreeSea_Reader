"""Check source freshness and run bounded collectors when their interval expires.

Invoke this command from application startup or a user hotkey in the next UI
stage. It does not install a system scheduled task or keep a background process.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]


def freshness(provider, interval_hours, now):
    if provider=='exploration':
        path=ROOT/'data/exploration/manifest.json';data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        times=[datetime.fromisoformat(s['retrieved_at'].replace('Z','+00:00')) for s in data.get('sources',{}).values() if s.get('retrieved_at')]
        oldest=min(times) if times else None;age=(now-oldest).total_seconds()/3600 if oldest else None
        return {'provider':provider,'last_successful_fetch':max(times).isoformat() if times else None,
            'oldest_resource_fetch':oldest.isoformat() if oldest else None,'age_hours':age,'interval_hours':interval_hours,
            'due':age is None or age>=interval_hours or bool(data.get('errors')),
            'failure_count':len(data.get('errors',[])),'has_failed_requests':bool(data.get('errors'))}
    path = ROOT / "data" / "sources" / provider / "latest_manifest.json"
    latest = None
    oldest = None
    failures = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("pages", []) if provider == "prts" else data.get("resources", [])
        for record in records:
            if record.get("origin") in {"local_baseline", "local_snapshot"}:
                continue
            timestamp = record.get("fetched_at")
            if timestamp:
                try:
                    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if value.tzinfo is None:
                        continue
                    latest = max(latest, value) if latest else value
                    oldest = min(oldest, value) if oldest else value
                except ValueError:
                    continue
        failures = data.get("failures", [])
    # A rejected/failed source should be retried on an explicit later update.
    # Discovery budget exhaustion is a coverage note, not a failed HTTP fetch.
    failed_fetch = any(f.get("kind") != "asset_discovery_budget" for f in failures)
    age = (now - oldest).total_seconds() / 3600 if oldest else None
    return {"provider": provider, "last_successful_fetch": latest.isoformat() if latest else None,
            "oldest_resource_fetch": oldest.isoformat() if oldest else None,
            "age_hours": age, "interval_hours": interval_hours,
            "due": age is None or age >= interval_hours or failed_fetch,
            "failure_count": len(failures), "has_failed_requests": failed_fetch}


@contextmanager
def preserve_published(exploration_only=False):
    names=['data/exploration/knowledge.sqlite3','data/exploration/manifest.json','data/exploration/map_index.json',
           'assets/relics/rules.json','assets/operators/manifest.json']
    if not exploration_only:names+=['data/reader.sqlite3','data/effects/catalog.json','reports/effect_coverage.json']
    (ROOT/'data').mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.update-',dir=ROOT/'data') as temporary:
        folder=Path(temporary).resolve();assert folder.is_relative_to((ROOT/'data').resolve())
        existing=set()
        for name in names:
            path=ROOT/name
            if path.exists():
                dest=folder/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest);existing.add(name)
        try:yield
        except BaseException:
            import filecmp
            for name in names:
                path=ROOT/name
                if name in existing:
                    if not path.exists() or not filecmp.cmp(folder/name,path,shallow=False):shutil.copy2(folder/name,path)
                elif path.exists():path.unlink()
            raise


def execute(args,report,now):
    providers=['lubiao','prts','exploration'] if args.source=='all' else ['lubiao','prts'] if args.source=='both' else [args.source]
    base_attempted=False;failed=False;exploration_updated=False;operators_updated=False
    for provider in providers:
        result=freshness(provider,args.minimum_age_hours,now)
        if not args.check_only and (args.force or result['due']):
            base_attempted|=provider!='exploration'
            exploration_updated|=provider=='exploration'
            operators_updated|=provider=='prts'
            script='update_exploration.py' if provider=='exploration' else f'collect_{provider}.py'
            command=[sys.executable,str(ROOT/'scripts'/script)]
            if provider=='exploration' and (ROOT/'data/exploration/last_attempt.json').exists():command.append('--resume')
            if provider=='prts':command.append('--online')
            run=subprocess.run(command,cwd=ROOT,check=False)
            result['collector_exit_code']=run.returncode;failed|=run.returncode!=0
            followups=[]
            if provider=='prts':
                followups=[('operator_collector_exit_code',['collect_operators.py']),
                    ('operator_details_exit_code',['collect_operator_details.py','--online']+(['--minimum-age-hours','0'] if args.force else [])),
                    ('combat_sources_exit_code',['collect_combat_sources.py']+(['--force'] if args.force else []))]
            elif provider=='lubiao':followups=[('side_map_collector_exit_code',['collect_side_maps.py']),('node_limits_exit_code',['collect_node_limits.py'])]
            for key,parts in followups:
                if failed:break
                run=subprocess.run([sys.executable,str(ROOT/'scripts'/parts[0]),*parts[1:]],cwd=ROOT,check=False)
                result[key]=run.returncode;failed|=run.returncode!=0
            result['after_update']=freshness(provider,args.minimum_age_hours,datetime.now(timezone.utc))
            failed|=result['after_update']['has_failed_requests']
        report['sources'].append(result)
        if failed:break
    if base_attempted and not failed:
        run=subprocess.run([sys.executable,str(ROOT/'scripts/database.py'),'build'],cwd=ROOT,check=False)
        report['database_rebuilt']=run.returncode==0;failed|=run.returncode!=0
        if not failed and (ROOT/'data/sources/combat_prts/latest_manifest.json').exists():
            for key,script in [('effect_catalog_built','build_effect_catalog.py'),('effect_catalog_imported','import_effect_catalog.py')]:
                run=subprocess.run([sys.executable,str(ROOT/'scripts'/script)],cwd=ROOT,check=False)
                report[key]=run.returncode==0;failed|=run.returncode!=0
                if failed:break
    if not failed and exploration_updated:
        for name,key in [('collect_relic_assets.py','relic_assets_exit_code'),
                         ('collect_relic_rules.py','relic_rules_exit_code')]:
            run=subprocess.run([sys.executable,str(ROOT/'scripts'/name)],cwd=ROOT,check=False)
            report[key]=run.returncode
            failed|=run.returncode!=0
            if failed:break
    if not failed and operators_updated:
        run=subprocess.run([sys.executable,str(ROOT/'scripts/collect_operator_avatars.py')],cwd=ROOT,check=False)
        report['operator_assets_exit_code']=run.returncode
        failed|=run.returncode!=0
    if not failed and not args.check_only and any('collector_exit_code' in row for row in report['sources']):
        run=subprocess.run([sys.executable,str(ROOT/'scripts/analyze_generation.py')],cwd=ROOT,check=False)
        report['generation_report_exit_code']=run.returncode;failed|=run.returncode!=0
    if failed:raise RuntimeError('资料采集或建库失败，已恢复上一套数据库和效果目录')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minimum-age-hours',type=float,default=24)
    parser.add_argument('--source',choices=['all','both','prts','lubiao','exploration'],default='all')
    parser.add_argument('--check-only',action='store_true');parser.add_argument('--force',action='store_true')
    args=parser.parse_args()
    if args.minimum_age_hours<0:parser.error('--minimum-age-hours must be nonnegative')
    now=datetime.now(timezone.utc);report={'checked_at':now.isoformat(),'sources':[],'database_rebuilt':False}
    failed=False
    try:
        if args.check_only:execute(args,report,now)
        else:
            with preserve_published(args.source=='exploration'):execute(args,report,now)
        report['status']='ok'
    except Exception as error:
        failed=True;report.update(status='failed',error=str(error),published_data_restored=True)
        print(str(error),flush=True)
    output=ROOT/'reports/update_status.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return int(failed)


if __name__=='__main__':raise SystemExit(main())

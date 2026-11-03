"""Run every test module in its own process so Qt event loops cannot leak across modules."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--python',type=Path,default=Path(sys.executable))
    parser.add_argument('--tests',type=Path,default=ROOT/'tests');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=120)
    parser.add_argument('--modules',nargs='*',help='Rerun selected modules and merge their records into the existing summary')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    tests=args.tests.resolve();environment=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONIOENCODING='utf-8')
    selected={name if name.endswith('.py') else name+'.py' for name in args.modules or []}
    summary=output/'summary.json'
    rows=[r for r in json.loads(summary.read_text(encoding='utf-8'))['modules'] if r['module'] not in selected] if selected and summary.exists() else []
    for module in sorted(tests.glob('test_*.py')):
        if selected and module.name not in selected:continue
        log=output/(module.stem+'.log')
        command=[str(args.python.resolve()),'-B','-m','unittest','discover','-s',str(tests),'-p',module.name,'-v']
        try:
            with log.open('w',encoding='utf-8') as stream:
                result=subprocess.run(command,cwd=ROOT,env=environment,stdout=stream,stderr=subprocess.STDOUT,timeout=args.timeout)
            code=result.returncode
        except subprocess.TimeoutExpired:code='timeout'
        import re
        text=log.read_text(encoding='utf-8',errors='replace');count=re.findall(r'Ran (\d+) tests?',text)
        rows.append({'module':module.name,'exit_code':code,'tests':int(count[-1]) if count else 0,'log':str(log)})
        print(f'{module.name}: {code} ({rows[-1]["tests"]} tests)',flush=True)
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'python':str(args.python.resolve()),'modules':rows,
        'tests':sum(r['tests'] for r in rows),'passed':all(r['exit_code']==0 for r in rows)}
    (output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'modules':len(rows),'tests':report['tests']}),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())

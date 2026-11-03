"""Exercise native GUI launches, Unicode CLI and the loopback API."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime,timezone
import json
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
NO_WINDOW=getattr(subprocess,'CREATE_NO_WINDOW',0)


def wait_json(path,timeout=40):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.exists():
            try:return json.loads(path.read_text(encoding='utf-8'))
            except (ValueError,OSError):pass
        time.sleep(.1)
    raise TimeoutError('No startup report: '+str(path))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--label',default='source');parser.add_argument('--ocr-image',type=Path);args=parser.parse_args()
    app_root=args.root.resolve();stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    folder=ROOT/'reports/packaging'/('startup-'+args.label+'-'+stamp);folder.mkdir(parents=True)
    foreign=folder/'foreign cwd';foreign.mkdir()
    report={'root':str(app_root),'checked_at':datetime.now(timezone.utc).isoformat(),'cases':[]}
    visible=ctypes.windll.user32.IsWindowVisible;visible.argtypes=[wintypes.HWND];visible.restype=wintypes.BOOL
    gui=app_root/'TreeSeaReader.exe'
    for tool in ('reader','calculator','effects'):
        marker=folder/(tool+'.json')
        command=[str(gui),'--tool',tool,'--quit-after-ms','3000','--launch-report',str(marker)]
        if tool=='reader':command+=['--no-hotkeys','--session',str(folder/'isolated_session.json')]
        if tool=='calculator':command+=['--review-example']
        # Detached GUI children may inherit stdout pipe handles. Capturing the
        # pipe would wait until the GUI closes, too late to inspect visibility.
        process=subprocess.Popen(command,cwd=foreign,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=NO_WINDOW)
        try:
            state=wait_json(marker)
            native_visible=bool(visible(wintypes.HWND(state['window_id'])))
            if not state['visible'] or state['minimized'] or not native_visible:raise AssertionError((tool,state,native_visible))
        finally:
            if process.wait(timeout=15):raise AssertionError(('GUI launcher failed',tool,process.returncode))
        report['cases'].append({'name':tool,'window_created':True,'native_visible':native_visible,'title':state['title'],'pid':state['pid']})
    cli=app_root/'TreeSeaReaderCLI.exe'
    data={'attacker':{'atk':{'base':{'value':1000,'source':{'kind':'manual','name':'中文 启动测试'}}}},'defender':{'def':{'base':300}}}
    run=subprocess.run([str(cli),'--tool','calculate'],input=json.dumps(data,ensure_ascii=False).encode('utf-8'),capture_output=True,cwd=foreign,timeout=30,creationflags=NO_WINDOW)
    parsed=json.loads(run.stdout)
    if run.returncode or parsed['hp_damage']!=700 or parsed['attributes']['attacker']['atk']['trace'][0]['contributors'][0]['source']['name']!='中文 启动测试':raise AssertionError('Native CLI stdin/Unicode failure')
    report['cases'].append({'name':'native_cli_stdin_unicode','hp_damage':parsed['hp_damage'],'passed':True})
    request_file=folder/'中文 请求.json';request_file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    output_file=folder/'中文 结果.json'
    run=subprocess.run([str(cli),'--tool','calculate','--input',str(request_file),'--output',str(output_file)],capture_output=True,cwd=foreign,timeout=30,creationflags=NO_WINDOW)
    if run.returncode or json.loads(output_file.read_text(encoding='utf-8'))['hp_damage']!=700:raise AssertionError('Native CLI quoted path failure')
    report['cases'].append({'name':'native_cli_unicode_file_paths','passed':True})
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    api=subprocess.Popen([str(cli),'--tool','api','--port',str(port),'--quit-after-ms','5000'],cwd=foreign,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=NO_WINDOW)
    base=f'http://127.0.0.1:{port}';health=None
    end=time.monotonic()+12
    while time.monotonic()<end and api.poll() is None:
        try:
            with urlopen(base+'/health',timeout=.5) as response:health=json.load(response)
            break
        except OSError:time.sleep(.1)
    if health is None:
        stdout,stderr=api.communicate(timeout=10);raise AssertionError(('API failed',stdout,stderr))
    request=Request(base+'/v1/calculate',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=5) as response:result=json.load(response)
    if result['hp_damage']!=700:raise AssertionError('API calculation failure')
    stdout,stderr=api.communicate(timeout=10)
    if api.returncode:raise AssertionError(stderr.decode('utf-8','replace'))
    report['cases'].append({'name':'api_cli_health_calculation_and_clean_exit','health':health,'passed':True})
    if args.ocr_image:
        target=folder/'ocr_result.json'
        run=subprocess.run([str(cli),'--tool','recognize',str(args.ocr_image.resolve()),'--output',str(target)],capture_output=True,cwd=foreign,timeout=90,creationflags=NO_WINDOW)
        if run.returncode:raise AssertionError(run.stderr.decode('utf-8','replace'))
        result=json.loads(target.read_text(encoding='utf-8'))
        if result.get('template_id')!='F2-T05':raise AssertionError(('OCR template mismatch',result.get('template_id')))
        report['cases'].append({'name':'portable_real_map_ocr','template':'F2-T05','passed':True})
    report['passed']=True
    (folder/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(folder/'validation.json'),'cases':len(report['cases']),'passed':True},ensure_ascii=False))


if __name__=='__main__':main()

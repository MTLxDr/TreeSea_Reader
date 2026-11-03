"""One source and portable entry point for all TreeSea Reader tools."""
from __future__ import annotations
import argparse
from contextlib import closing
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import traceback
from datetime import datetime,timezone

from .version import VERSION,DISPLAY_VERSION

ROOT=Path(__file__).resolve().parents[1]
TOOLS={'reader':('reader.app','main'),'calculator':('reader.calculation_app','main'),
       'effects':('reader.effects_ui','main'),'api':('reader.calculation_api','main'),
       'calculate':('reader.calculation_api','main'),'update':('scripts.update_data','main'),
       'recognize':('scripts.recognize','main')}


def diagnostic_report(with_ocr=False):
    report={'app_version':VERSION,'display_version':DISPLAY_VERSION,'root':str(ROOT),'executable':sys.executable,'python':sys.version.split()[0],'isolated_runtime':bool(sys.flags.isolated),
            'status':'ok','checks':{},'module_paths':{}}
    for name in ('PySide6','numpy','cv2','PIL','mss','rapidocr','onnxruntime','windows_capture','reader.app','reader.calculation_app','reader.effects_ui','reader.calculation_api'):
        try:
            module=importlib.import_module(name);report['checks'][name]='ok';report['module_paths'][name]=str(getattr(module,'__file__',''))
        except Exception as error:report['checks'][name]=str(error);report['status']='error'
    try:
        with closing(sqlite3.connect((ROOT/'data/reader.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            report['database_integrity']=db.execute('pragma integrity_check').fetchone()[0]
            report['effect_entries']=db.execute('select count(*) from effect_entries').fetchone()[0]
        if report['database_integrity']!='ok':report['status']='error'
    except Exception as error:report['database_error']=str(error);report['status']='error'
    if with_ocr:
        try:
            from .ocr import TextReader
            engine=TextReader();engine._load();report['ocr_models']='loaded'
        except Exception as error:report['ocr_models']=str(error);report['status']='error'
    return report


def save_report(path,report):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def show_error(message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,message,'树海 Reader 启动失败',0x10)
    except Exception:pass


def main(argv=None):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--tool',choices=TOOLS,default='reader')
    parser.add_argument('--self-check',action='store_true');parser.add_argument('--with-ocr',action='store_true')
    parser.add_argument('--report',type=Path);parser.add_argument('--launch-report',type=Path)
    parser.add_argument('--quit-after-ms',type=int)
    args,remaining=parser.parse_known_args(argv)
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    log=ROOT/'data/logs/startup.log'
    try:
        log.parent.mkdir(parents=True,exist_ok=True)
        if sys.stderr is None:sys.stderr=log.open('a',encoding='utf-8',buffering=1)
        if sys.stdout is None:sys.stdout=sys.stderr
        if args.launch_report:os.environ['TREESEA_LAUNCH_REPORT']=str(args.launch_report.resolve())
        if args.quit_after_ms is not None:
            if not 300<=args.quit_after_ms<=60000:raise ValueError('quit-after-ms must be 300..60000')
            os.environ['TREESEA_QUIT_AFTER_MS']=str(args.quit_after_ms)
        if args.self_check:
            report=diagnostic_report(args.with_ocr)
            if args.report:save_report(args.report,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 0 if report['status']=='ok' else 1
        with log.open('a',encoding='utf-8') as stream:
            stream.write(f'{datetime.now(timezone.utc).isoformat()} tool={args.tool} pid={os.getpid()} executable={sys.executable}\n')
        module,function=TOOLS[args.tool]
        if args.tool=='api':remaining=['--serve']+remaining
        sys.argv=[str(ROOT/'launch_reader.py')]+remaining
        timer=None
        if args.tool=='api' and args.quit_after_ms is not None:
            import threading,_thread
            timer=threading.Timer(args.quit_after_ms/1000,_thread.interrupt_main);timer.daemon=True;timer.start()
        try:result=getattr(importlib.import_module(module),function)()
        finally:
            if timer:timer.cancel()
        return result or 0
    except Exception as error:
        text=traceback.format_exc()
        try:
            log.parent.mkdir(parents=True,exist_ok=True)
            with log.open('a',encoding='utf-8') as stream:stream.write(text+'\n')
        except OSError:pass
        if args.report:
            try:save_report(args.report,{'status':'error','tool':args.tool,'error':str(error),'traceback':text})
            except OSError:pass
        if args.tool in {'reader','calculator','effects'}:show_error(str(error)+'\n\n诊断日志：'+str(log))
        else:print(text,file=sys.stderr)
        return 1

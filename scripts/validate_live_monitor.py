"""Observe a real game or a covered animated test window without foreground requests."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
import tempfile
from time import monotonic
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor,QPainter,QImage
from PySide6.QtWidgets import QApplication,QWidget
from reader.app import ReaderWindow


class Pattern(QWidget):
    def __init__(self,image=None):
        super().__init__();self.step=0;self.image=QImage(str(image)) if image else None
        self.timer=QTimer(self);self.timer.timeout.connect(self.advance);self.timer.start(350)
    def advance(self):self.step+=1;self.update()
    def paintEvent(self,event):
        p=QPainter(self)
        if self.image is not None:
            p.drawImage(self.rect(),self.image)
            p.fillRect(0,0,12,12,QColor('#28aa83' if self.step%2 else '#29436b'))
            return
        for y in range(8):
            for x in range(12):p.fillRect(x*50,y*50,50,50,QColor('#28aa83' if (x+y+self.step)%2 else '#29436b'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--hwnd',type=int)
    parser.add_argument('--image',type=Path,help='Replay a captured game map in the covered test window')
    parser.add_argument('--seconds',type=float,default=16);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.seconds<6:parser.error('Use at least 6 seconds for continuous observation')
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);windows=[];report={};done=False
    foreground=ctypes.windll.user32.GetForegroundWindow;foreground.restype=ctypes.c_void_p
    before=foreground();start=monotonic()
    with tempfile.TemporaryDirectory(prefix='TreeSeaReader-live-monitor-') as t:
        folder=Path(t).resolve();assert folder.is_relative_to(Path(tempfile.gettempdir()).resolve())
        if args.hwnd:hwnd=args.hwnd
        else:
            target=Pattern(args.image);target.setWindowTitle('Reader continuous capture test')
            width,height=(1000,580) if args.image else (600,400)
            target.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);target.resize(width,height);target.move(70,70);target.show();windows.append(target)
            cover=QWidget();cover.setWindowTitle('Reader test cover');cover.setStyleSheet('background:#27222d')
            cover.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);cover.resize(width+40,height+50);cover.move(50,50);cover.show();cover.raise_();windows.append(cover)
            hwnd=int(target.winId())
        reader=ReaderWindow(folder/'session.json',no_hotkeys=True,settings_path=folder/'settings.json')
        reader.present_results=Mock();messages=[]
        reader.live_monitor.status.connect(lambda text:messages.append(text))
        def begin():reader.live_monitor.start(hwnd)
        def finish():
            nonlocal done
            if done:return
            done=True;monitor=reader.live_monitor
            image=None
            if monitor.source:
                try:image,_=monitor.source.latest()
                except Exception:pass
            frames=getattr(monitor.source,'frame_count',0);monitor.stop();reader.tasks.waitForDone(20000)
            app.processEvents()
            report.update(scenario='game_window' if args.hwnd else 'covered_animated_window',
                elapsed_seconds=round(monotonic()-start,2),captured_source_frames=frames,stats=dict(monitor.stats),
                foreground_before=before,foreground_after=foreground(),foreground_unchanged=before==foreground(),
                reader_shown=reader.isVisible(),foreground_requests=reader.present_results.call_count,
                last_messages=messages[-8:],saved_inventory_counts={k:len(v['items']) for k,v in reader.session.inventories.items()},
                saved_map=(reader.session.current or {}).get('template_id'))
            report['current_slot']=(reader.session.current or {}).get('current_slot')
            report['floor']=(reader.session.current or {}).get('floor')
            report['fixture_replay']=str(args.image) if args.image else None
            report['minimum_source_frames']=max(5,int(args.seconds))
            report['passed']=frames>=report['minimum_source_frames'] and monitor.stats['recognized']>=1 and not reader.isVisible() and not reader.present_results.called
            if args.image:report['passed']&=report['saved_map'] is not None
            if image is not None and args.hwnd:
                import cv2
                ok,encoded=cv2.imencode('.png',image)
                if ok:
                    args.output.parent.mkdir(parents=True,exist_ok=True);encoded.tofile(args.output.with_suffix('.png'))
            reader.close()
            for window in windows:window.close()
            app.quit()
        QTimer.singleShot(200,begin);QTimer.singleShot(int(args.seconds*1000),finish)
        app.exec()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report.get('passed') else 1


if __name__=='__main__':raise SystemExit(main())

"""Capture a temporary Qt window while a second window covers it."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor,QPainter
from PySide6.QtWidgets import QApplication,QWidget
from reader.live_monitor import WgcSource


class Pattern(QWidget):
    def paintEvent(self,event):
        p=QPainter(self)
        for y in range(8):
            for x in range(12):p.fillRect(x*50,y*50,50,50,QColor('#28aa83' if (x+y)%2 else '#29436b'))


def main(output):
    app=QApplication([]);window=Pattern();window.setWindowTitle('TreeSea capture validation')
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);window.resize(600,400);window.move(70,70);window.show()
    cover=QWidget();cover.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);cover.resize(640,450);cover.move(50,50)
    cover.setStyleSheet('background:#4d2323');cover.setWindowTitle('TreeSea capture validation cover')
    report={'scenario':'WGC bound Qt window covered by another window','passed':False};state={}
    def start():
        try:
            state['source']=WgcSource(int(window.winId()));state['source'].start();cover.show();cover.raise_();window.update()
        except Exception as e:report['error']=str(e);finish()
    def finish():
        try:
            if 'source' in state:
                image,meta=state['source'].latest()
                import numpy as np
                colors=np.array([[131,170,40],[107,67,41]],dtype=float)
                match=np.min(np.linalg.norm(image.astype(float)[:,:,None,:]-colors,axis=3),axis=2)<8
                ratio=float(match.mean())
                report.update(passed=image.shape[:2]==(meta['height'],meta['width']) and ratio>.95,
                              shape=list(image.shape),method=meta['method'],pattern_fraction=ratio)
        except Exception as e:report['error']=str(e)
        finally:
            if 'source' in state:state['source'].stop()
            cover.close();window.close();app.quit()
    QTimer.singleShot(300,start);QTimer.singleShot(2300,finish);QTimer.singleShot(7000,app.quit)
    app.exec();output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['passed'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();raise SystemExit(main(a.output))

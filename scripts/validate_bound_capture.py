"""Verify simultaneous one-shot and continuous capture of one covered HWND."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import cv2
import numpy as np
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication,QWidget
from reader.capture import capture_window
from reader.window_capture import WgcSource
from reader.recognition import load_image


class Target(QWidget):
    def __init__(self,image):super().__init__();self.image=QImage(str(image))
    def paintEvent(self,event):QPainter(self).drawImage(self.rect(),self.image)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--image',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    foreground=ctypes.windll.user32.GetForegroundWindow;foreground.restype=ctypes.c_void_p
    report=dict(foreground_before=foreground(),passed=False);state={}
    target=Target(args.image);target.setWindowTitle('Reader bound-window capture validation');target.resize(1000,580);target.move(70,70)
    target.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);target.show()
    cover=QWidget();cover.resize(1040,630);cover.move(50,50);cover.setStyleSheet('background:#27222d')
    cover.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);cover.show();cover.raise_()
    source=WgcSource(int(target.winId()))
    def begin():
        try:source.start();state['stream']=source.stream
        except Exception as exc:report['error']=str(exc)
    def snapshot():
        try:
            image,meta=capture_window(int(target.winId()),allow_own_process=True)
            expected=cv2.resize(load_image(args.image),(image.shape[1],image.shape[0]))
            difference=float(np.abs(image.astype(float)-expected.astype(float)).mean())
            report.update(snapshot=meta,mean_absolute_pixel_difference=round(difference,3),
                shared_stream=state.get('stream') is source.stream,continuous_still_active=source.latest()[0].shape==image.shape)
            report['passed']=difference<15 and report['shared_stream'] and report['continuous_still_active'] and meta['method']=='WindowsGraphicsCapture'
        except Exception as exc:report['error']=str(exc)
    def finish():
        report.update(captured_source_frames=source.frame_count,foreground_after=foreground())
        report['foreground_unchanged']=report['foreground_before']==report['foreground_after']
        source.stop();target.close();cover.close();app.quit()
    QTimer.singleShot(200,begin);QTimer.singleShot(6500,snapshot);QTimer.singleShot(7500,finish);app.exec()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())

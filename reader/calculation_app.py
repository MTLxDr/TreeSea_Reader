"""Independent calculator entry point; does not require a game session."""
import argparse
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer,Qt
from .calculation_ui import CalculationWindow


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',type=Path)
    parser.add_argument('--review-example',action='store_true')
    parser.add_argument('--on-top',action='store_true')
    args=parser.parse_args();app=QApplication(sys.argv[:1]);window=CalculationWindow()
    if args.on_top:window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,True)
    window.show()
    from .startup import gui_ready
    gui_ready(app,window,'calculator')
    if args.review_example:QTimer.singleShot(100,window.panel.run)
    if args.preview:
        def save():
            args.preview.parent.mkdir(parents=True,exist_ok=True);window.grab().save(str(args.preview))
        QTimer.singleShot(500,save)
    return app.exec()


if __name__=='__main__':raise SystemExit(main())

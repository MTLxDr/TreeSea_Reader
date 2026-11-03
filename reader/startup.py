"""Report actual GUI creation and support bounded startup smoke tests."""
import json
import os
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]


def gui_ready(app,window,tool):
    from PySide6.QtCore import QTimer
    def record():
        if app.platformName()!='offscreen':
            window.showNormal();window.raise_();window.activateWindow()
        result={'tool':tool,'status':'window_created','pid':os.getpid(),'title':window.windowTitle(),
                'visible':window.isVisible(),'minimized':window.isMinimized(),'window_id':int(window.winId()),
                'checked_at':datetime.now(timezone.utc).isoformat()}
        path=Path(os.environ.get('TREESEA_LAUNCH_REPORT') or ROOT/f'data/logs/launch_{tool}.json')
        try:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        except OSError:pass
    QTimer.singleShot(150,record)
    delay=os.environ.get('TREESEA_QUIT_AFTER_MS')
    if delay:QTimer.singleShot(max(300,int(delay)),app.quit)

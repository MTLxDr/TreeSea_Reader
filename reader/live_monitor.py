"""Bound-window capture and two-observation state updates; no focus changes."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QTimer, Signal

from .capture import CaptureError, capture_window
from .window_capture import WgcSource, capture_log
from .run_tracking import frame_observation


def observation_key(result):
    page = result.get('page_type')
    if page == 'map' and not result.get('needs_review') and result.get('template_id') and result.get('nodes'):
        values = [page, result.get('floor'), result.get('template_id'), result.get('current_slot'),
            sorted((n['slot'], n['type'], bool(n.get('resident'))) for n in result.get('nodes', [])),
            sorted((k, v.get('value')) for k, v in result.get('fields', {}).items()),
            sorted(r['id'] for r in result.get('recent_relics',[]) if not r.get('needs_review')),
            result.get('side_map', {}).get('template_id'),
            [(n['slot'],n['type']) for n in result.get('side_map',{}).get('nodes',[])]]
    elif page == 'inventory' and result.get('context_detected') == result.get('inventory_kind') and not result.get('ownership_risk'):
        values = [page, result['inventory_kind'], sorted((r['id'], sorted((k, v.get('value'))
            for k, v in r['fields'].items())) for r in result['entries'] if not r.get('needs_review'))]
        if not values[2]:return None
    elif page == 'event':
        values = [page, result.get('event_id')]
    else:return None
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ObservationGate:
    def __init__(self):self.pending=None;self.count=0;self.committed=None;self.last_frame=None
    def offer(self, result):
        key=observation_key(result)
        if key is None:self.pending=None;self.count=0;self.last_frame=None;return False
        stamp=frame_observation(result)
        if result.get('_new_run_candidate') and key==self.pending and stamp is not None and stamp==self.last_frame:return False
        self.last_frame=stamp
        self.count=self.count+1 if key==self.pending else 1;self.pending=key
        if self.count>=2 and key!=self.committed:
            self.committed=key;return True
        return False


class LiveSignals(QObject):
    ready=Signal(int,object,object)
    failed=Signal(int,str)


class LiveTask(QRunnable):
    def __init__(self, epoch, recognizer, source, hwnd, hints, skip_digest=None):
        super().__init__();self.epoch=epoch;self.recognizer=recognizer;self.source=source
        self.hwnd=hwnd;self.hints=hints;self.signals=LiveSignals()
        self.skip_digest=skip_digest
    def run(self):
        try:
            image,metadata=self.source.latest() if self.source else capture_window(self.hwnd)
            # Hash the original pixels: a changed quantity digit must not disappear
            # through thumbnail resampling or colour quantization.
            digest=hashlib.sha256(image.tobytes()+str(image.shape).encode()+json.dumps(self.hints,sort_keys=True).encode()).hexdigest()
            if digest==self.skip_digest:
                self.signals.ready.emit(self.epoch,{'page_type':'unchanged','_live_fingerprint':digest},None);return
            source={'method':'live','window':metadata}
            result=self.recognizer.analyze(image,source=source,page_hint='auto',**self.hints)
            result['_live_fingerprint']=digest
            self.signals.ready.emit(self.epoch,result,image)
        except Exception as e:self.signals.failed.emit(self.epoch,str(e))


class LiveMonitor(QObject):
    changed=Signal(object,object)
    preview=Signal(object,object)
    status=Signal(str)
    def __init__(self, recognizer, pool, guard=lambda:False, hints=lambda:{}, normalize=lambda r:r, parent=None):
        super().__init__(parent);self.recognizer=recognizer;self.pool=pool;self.guard=guard;self.hints=hints
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick)
        self.normalize=normalize;self.preview_key=None
        self.epoch=0;self.busy=False;self.running=False;self.source=None;self.hwnd=None;self.gate=ObservationGate()
        self.last_digest=None
        self.stats={'captures':0,'recognized':0,'unchanged':0,'committed':0,'failures':0}
    def start(self,hwnd):
        self.stop();self.hwnd=int(hwnd);self.gate=ObservationGate();self.running=True;self.last_digest=None;self.preview_key=None
        self.stats=dict.fromkeys(self.stats,0)
        try:
            self.source=WgcSource(hwnd);self.source.start();self.status.emit('实时跟踪中 · 地图、干员、藏品、零件')
        except Exception as e:
            if self.source:
                try:self.source.stop()
                except Exception:pass
            self.source=None;capture_log('live_start_error',hwnd=self.hwnd,error=str(e));self.status.emit('指定窗口采集等待重试：'+str(e))
        self.timer.start();self.tick()
    def stop(self):
        self.timer.stop();self.running=False;self.epoch+=1;self.busy=False
        if self.source:
            try:self.source.stop()
            except Exception:pass
        self.source=None
    def reset_observations(self,current_result=None):
        """Discard in-flight observations when the user starts another run."""
        self.epoch+=1;self.gate=ObservationGate();self.last_digest=None;self.preview_key=None;self.busy=False
        if current_result:
            key=observation_key(current_result)
            self.gate.pending=key;self.gate.committed=key;self.gate.count=2 if key else 0
            self.gate.last_frame=frame_observation(current_result)
    def tick(self):
        if not self.running or self.busy or self.guard():return
        self.busy=True;task=LiveTask(self.epoch,self.recognizer,self.source,self.hwnd,self.hints(),self.last_digest if self.gate.count>=2 else None)
        task.signals.ready.connect(self.ready);task.signals.failed.connect(self.failed);self.task=task;self.pool.start(task)
    def ready(self,epoch,result,image):
        if epoch!=self.epoch or not self.running:return
        self.busy=False
        self.stats['captures']+=1
        self.last_digest=result.pop('_live_fingerprint',None)
        if result.get('page_type')=='unchanged':self.stats['unchanged']+=1;return
        self.stats['recognized']+=1
        result=self.normalize(result)
        if result.get('page_type')=='map' and result.get('needs_review'):
            key=(result.get('floor'),result.get('template_id'))
            if key!=self.preview_key:self.preview_key=key;self.preview.emit(result,image)
            self.gate.offer(result);self.status.emit('选择地图构型后确认');return
        if self.gate.offer(result):
            result['_stable_observations']=self.gate.count
            self.stats['committed']+=1;self.changed.emit(result,image)
        self.status.emit({'map':'地图跟踪中','inventory':'清单同步中','event':'事件记录中'}.get(result.get('page_type'),'等待地图、清单或事件页面'))
    def failed(self,epoch,message):
        if epoch!=self.epoch:return
        self.busy=False;self.stats['failures']+=1
        if message!=getattr(self,'last_error_message',None):capture_log('live_error',hwnd=self.hwnd,error=message)
        self.last_error_message=message;self.status.emit(message)

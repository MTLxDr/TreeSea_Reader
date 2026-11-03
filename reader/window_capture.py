"""Shared Windows Graphics Capture sessions, bound to an exact HWND and PID."""
from datetime import datetime, timezone
import json
import threading
import time
import traceback

from .catalog import ROOT
from .capture import CaptureError, _api, window_info


def capture_log(event, **values):
    path = ROOT / 'data/logs/capture.jsonl'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 512_000:
            path.replace(path.with_suffix('.previous.jsonl'))
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), event=event, **values), ensure_ascii=False) + '\n')
    except OSError:
        pass


class _Stream:
    def __init__(self, hwnd):
        self.hwnd = int(hwnd)
        self.pid = window_info(self.hwnd)['pid']
        self.lock = threading.Condition()
        self.restart_lock = threading.Lock()
        self.control = None
        self.frame = None
        self.timestamp = 0.
        self.frame_time = None
        self.frame_count = 0
        self.closed = False
        self.error = None
        self.last_start = 0.
        self.refs = 0
        self.generation = 0

    def start(self):
        from windows_capture import WindowsCapture
        self.generation += 1
        generation = self.generation
        self.closed = False
        self.error = None
        self.frame = None
        self.timestamp = 0.
        self.last_start = time.monotonic()
        capture = WindowsCapture(window_hwnd=self.hwnd, cursor_capture=False,
                                 draw_border=False, minimum_update_interval=250)

        @capture.event
        def on_frame_arrived(frame, capture_control):
            try:
                now = time.monotonic()
                with self.lock:
                    if generation != self.generation or now - self.timestamp < .2:
                        return
                    self.frame = frame.frame_buffer[:, :, :3].copy()
                    self.timestamp = now
                    self.frame_time = datetime.now(timezone.utc).isoformat()
                    self.frame_count += 1
                    self.lock.notify_all()
            except Exception:
                self.error = traceback.format_exc()
                capture_log('frame_error', hwnd=self.hwnd, pid=self.pid, error=self.error)

        @capture.event
        def on_closed():
            with self.lock:
                if generation == self.generation:
                    self.closed = True
                    self.lock.notify_all()

        self.capture = capture
        self.control = capture.start_free_threaded()
        capture_log('start', hwnd=self.hwnd, pid=self.pid, method='WindowsGraphicsCapture')

    def stop(self):
        self.generation += 1
        control, self.control = self.control, None
        self.closed = True
        if control:
            try:
                control.stop()
            except Exception as exc:
                capture_log('stop_error', hwnd=self.hwnd, error=str(exc))

    def recover(self):
        with self.restart_lock:
            if time.monotonic() - self.last_start < 3:
                return
            capture_log('restart', hwnd=self.hwnd, pid=self.pid, error=self.error)
            self.stop()
            self.start()

    def latest(self, timeout=0.):
        user, _ = _api()
        if not user.IsWindow(self.hwnd):
            raise CaptureError('指定窗口已关闭，请重新选择窗口')
        info = window_info(self.hwnd, user)
        if info['pid'] != self.pid:
            raise CaptureError('指定窗口句柄已被其他进程使用，请重新选择窗口')
        if user.IsIconic(self.hwnd):
            raise CaptureError('指定窗口已最小化，恢复后继续采集')
        if not user.IsWindowVisible(self.hwnd):
            raise CaptureError('指定窗口已隐藏，显示后继续采集')
        if min(info['width'], info['height']) < 200 or info['width'] * info['height'] > 40_000_000:
            raise CaptureError('指定窗口尺寸不适合识别')
        finished = bool(self.control and self.control.is_finished())
        if self.closed or finished or self.error or (self.frame is None and time.monotonic() - self.last_start > 3):
            self.recover()
        deadline = time.monotonic() + timeout
        with self.lock:
            while self.frame is None and not self.closed and time.monotonic() < deadline:
                self.lock.wait(min(.2, max(0, deadline - time.monotonic())))
            if self.frame is None:
                raise CaptureError('等待指定窗口首帧；采集状态已记录到截图日志')
            image = self.frame.copy()
            age = time.monotonic() - self.timestamp
        # A static scene is valid while the bound capture session is alive. WGC's
        # minimum update interval is a rate limit, not a guaranteed frame rate.
        import ctypes
        from ctypes import wintypes
        box = wintypes.RECT()
        try:
            ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(self.hwnd), 9, ctypes.byref(box), ctypes.sizeof(box)) == 0
            if ok and abs(image.shape[1] - (box.right - box.left)) <= 2 and abs(image.shape[0] - (box.bottom - box.top)) <= 2:
                x, y = info['left'] - box.left, info['top'] - box.top
                if x >= 0 and y >= 0 and x + info['width'] <= image.shape[1] and y + info['height'] <= image.shape[0]:
                    image = image[y:y + info['height'], x:x + info['width']].copy()
        except (AttributeError, OSError):
            pass
        if min(image.shape[:2]) < 200 or image.std() < 3:
            raise CaptureError('指定窗口返回空白画面，等待有效画面')
        return image, dict(info, method='WindowsGraphicsCapture', frame_count=self.frame_count,
                           frame_age_seconds=round(age, 3), captured_at=self.frame_time, observed_at=datetime.now(timezone.utc).isoformat())


_streams = {}
_registry_lock = threading.RLock()


class WgcSource:
    """Lease used by both one-shot captures and the continuous monitor."""
    def __init__(self, hwnd):
        self.hwnd = int(hwnd)
        self.stream = None

    @property
    def frame_count(self):
        return self.stream.frame_count if self.stream else 0

    def start(self):
        with _registry_lock:
            if self.stream is not None:
                return
            stream = _streams.get(self.hwnd)
            if stream is None:
                stream = _Stream(self.hwnd)
                try:
                    stream.start()
                except Exception:
                    stream.stop()
                    capture_log('start_error', hwnd=self.hwnd, error=traceback.format_exc())
                    raise
                _streams[self.hwnd] = stream
            stream.refs += 1
            self.stream = stream

    def latest(self, timeout=0.):
        if self.stream is None:
            raise CaptureError('指定窗口采集尚未启动')
        return self.stream.latest(timeout)

    def stop(self):
        with _registry_lock:
            stream, self.stream = self.stream, None
            if stream is None:
                return
            stream.refs -= 1
            if stream.refs == 0:
                _streams.pop(self.hwnd, None)
                stream.stop()

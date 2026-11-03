"""Windows client-area capture. Never substitutes an unrelated desktop image."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys

import cv2
import numpy as np

from .catalog import ROOT


class CaptureError(RuntimeError):
    pass


def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass


def _api():
    if sys.platform != "win32":
        raise CaptureError("窗口截图与全局热键当前支持 Windows；其他系统可导入截图。")
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    return user, gdi


def window_info(hwnd, user=None):
    user = user or _api()[0]
    length = user.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user.GetWindowTextW(hwnd, title, length + 1)
    rect = wintypes.RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(rect)):
        raise CaptureError("目标窗口尺寸无法读取。")
    point = wintypes.POINT(0, 0)
    if not user.ClientToScreen(hwnd, ctypes.byref(point)):
        raise CaptureError("目标窗口位置无法读取。")
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {"hwnd": int(hwnd), "title": title.value, "pid": pid.value,
            "left": point.x, "top": point.y, "width": rect.right, "height": rect.bottom}


def list_windows():
    user, _ = _api()
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def callback(hwnd, _):
        if user.IsWindowVisible(hwnd) and user.GetWindowTextLengthW(hwnd):
            try:
                item = window_info(hwnd, user)
                if item["pid"] != os.getpid() and item["width"] >= 320 and item["height"] >= 200:
                    result.append(item)
            except CaptureError:
                pass
        return True
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(callback, 0)
    return result


def foreground_window():
    user, _ = _api()
    hwnd = user.GetForegroundWindow()
    if not hwnd:
        raise CaptureError("没有可截取的前台窗口。")
    item = window_info(hwnd, user)
    if item["pid"] == os.getpid():
        raise CaptureError("请切换到游戏窗口后再按截图热键。")
    return item


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _print_window(info, user, gdi):
    width, height, hwnd = info["width"], info["height"], info["hwnd"]
    source_dc = user.GetDC(hwnd)
    if not source_dc:
        return None
    dc, bitmap, old = None, None, None
    try:
        dc = gdi.CreateCompatibleDC(source_dc)
        bitmap = gdi.CreateCompatibleBitmap(source_dc, width, height)
        if not dc or not bitmap:
            return None
        old = gdi.SelectObject(dc, bitmap)
        ok = user.PrintWindow(hwnd, dc, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
        gdi.SelectObject(dc, old)
        old = None
        if not ok:
            return None
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight = width, -height
        header.biPlanes, header.biBitCount = 1, 32
        raw = ctypes.create_string_buffer(width * height * 4)
        gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        if gdi.GetDIBits(dc, bitmap, 0, height, raw, ctypes.byref(header), 0) != height:
            return None
        return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
    finally:
        if old and dc:
            gdi.SelectObject(dc, old)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)
        user.ReleaseDC(hwnd, source_dc)


def capture_window(hwnd=None, allow_own_process=False):
    user, gdi = _api()
    if hwnd is None:
        hwnd = foreground_window()["hwnd"]
    if not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd):
        raise CaptureError("目标窗口已关闭或不可见，请重新选择。")
    if user.IsIconic(hwnd):
        raise CaptureError("目标窗口已最小化，请先恢复游戏窗口。")
    info = window_info(hwnd, user)
    if info["pid"] == os.getpid() and not allow_own_process:
        raise CaptureError("目标是 Reader 自身，请选择游戏窗口。")
    if min(info["width"], info["height"]) < 200 or info["width"] * info["height"] > 40_000_000:
        raise CaptureError("目标窗口尺寸不适合识别。")
    from .window_capture import WgcSource, capture_log
    source = WgcSource(hwnd)
    try:
        source.start()
        return source.latest(timeout=3.)
    except Exception as exc:
        capture_log('snapshot_error', hwnd=int(hwnd), pid=info['pid'], error=str(exc))
        if isinstance(exc, CaptureError):
            raise
        raise CaptureError('指定窗口截图失败：' + str(exc)) from exc
    finally:
        source.stop()


def persist_capture(image, metadata=None, directory=ROOT / "data/captures"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    ok, raw = cv2.imencode(".png", image)
    if not ok:
        raise CaptureError("截图编码失败。")
    digest = hashlib.sha256(raw.tobytes()).hexdigest()
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + digest[:8]
    path = directory / (name + ".png")
    raw.tofile(path)
    return path

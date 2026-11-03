from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter

DEFAULT_HOTKEYS = {"capture": "Ctrl+Alt+S", "toggle": "Ctrl+Alt+V", "undo": "Ctrl+Alt+Z",
                   "relics": "Ctrl+Alt+1", "parts": "Ctrl+Alt+2", "operators": "Ctrl+Alt+3"}


def parse_hotkey(sequence):
    pieces = sequence.upper().replace("CONTROL", "CTRL").split("+")
    modifiers = 0x4000  # MOD_NOREPEAT
    values = {"ALT": 1, "CTRL": 2, "SHIFT": 4}
    for part in pieces[:-1]:
        if part not in values:
            raise ValueError("热键只支持 Ctrl、Alt、Shift 加字母、数字或 F1–F11。")
        modifiers |= values[part]
    key = pieces[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 11:
        code = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError("热键须为字母、数字或 F1–F11；F12为系统保留键。")
    if len(pieces) < 2:
        raise ValueError("请使用至少一个修饰键，避免占用游戏普通按键。")
    return modifiers, code


class GlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self, application, callbacks):
        super().__init__()
        self.application, self.callbacks, self.registered = application, callbacks, {}
        if sys.platform == "win32":
            self.user = ctypes.WinDLL("user32", use_last_error=True)
            self.user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            application.installNativeEventFilter(self)

    def register(self, config):
        parsed = {name: parse_hotkey(value) for name, value in config.items()}
        if len(set(parsed.values())) != len(parsed):
            raise ValueError("多个功能不能使用相同热键。")
        self.close()
        errors = []
        if sys.platform != "win32":
            return ["此系统不支持全局热键，可使用界面按钮。"]
        for number, (name, (modifiers, key)) in enumerate(parsed.items(), 0x51A0):
            if self.user.RegisterHotKey(None, number, modifiers, key):
                self.registered[number] = name
            else:
                errors.append(f"{config[name]} 注册失败，可能已被占用。")
        return errors

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self.registered:
                self.callbacks[self.registered[msg.wParam]]()
                return True, 0
        return False, 0

    def close(self):
        if sys.platform == "win32":
            for number in self.registered:
                self.user.UnregisterHotKey(None, number)
        self.registered.clear()

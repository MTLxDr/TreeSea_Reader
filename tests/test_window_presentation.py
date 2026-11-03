"""Five focused window checks. Set QT_QPA_PLATFORM=windows for native QA.

Only temporary test windows are activated; no desktop/game input is sent.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton, QWidget

from reader.app import ReaderWindow, STYLE


class NormalWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)
        cls.native = cls.app.platformName() == "windows"
        if os.environ.get("TREESEA_REQUIRE_NATIVE") == "1" and not cls.native:
            raise AssertionError("Native Windows checks must not silently run offscreen")
        if cls.native:
            cls.user = ctypes.WinDLL("user32", use_last_error=True)
            cls.user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            cls.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            cls.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            cls.user.SetWindowPos.restype = wintypes.BOOL
            cls.user.GetForegroundWindow.restype = wintypes.HWND
            cls.user.SetForegroundWindow.argtypes = [wintypes.HWND]
            cls.user.SetForegroundWindow.restype = wintypes.BOOL

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.windows = []
        self.window = self.make_window()
        self.result = {"page_type": "map", "needs_review": False, "floor": 1,
                       "template_id": "F1-T01", "fields": {}, "nodes": [], "edges": [],
                       "candidates": [], "notices": [], "elapsed_seconds": .1}
        self.image = np.zeros((400, 600, 3), np.uint8)

    def make_window(self, settings=None):
        index = len(self.windows)
        settings_path = Path(self.temp.name)/f"settings-{index}.json"
        if settings is not None:
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
        window = ReaderWindow(Path(self.temp.name)/f"session-{index}.json", no_hotkeys=True,
                              settings_path=settings_path)
        window.setWindowTitle("Reader 窗口检查（自动关闭）")
        window.setMinimumSize(700, 500)
        window.resize(1000, 720)
        self.windows.append(window)
        return window

    def tearDown(self):
        for window in reversed(self.windows):
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def assert_normal(self, window):
        self.assertFalse(bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        if self.native:
            self.assertFalse(bool(self.user.GetWindowLongPtrW(int(window.winId()), -20) & 0x8))

    def test_1_default_and_legacy_preferences_never_pin(self):
        self.window.show()
        self.app.processEvents()
        self.assert_normal(self.window)
        legacy = self.make_window({"hotkeys": {"capture": "Ctrl+Shift+S"},
            "ui": {"show_after_capture": True, "always_on_top": True, "unrelated_option": "keep"}})
        legacy.show()
        self.app.processEvents()
        self.assert_normal(legacy)
        self.assertFalse(any(c.text() == "结果窗口置顶" for c in legacy.findChildren(QCheckBox)))
        legacy.auto_show.setChecked(False)
        saved = json.loads(legacy.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("always_on_top", saved["ui"])
        self.assertEqual(saved["hotkeys"]["capture"], "Ctrl+Shift+S")
        self.assertEqual(saved["ui"]["unrelated_option"], "keep")

    def test_2_map_and_inventory_completion_present_once(self):
        self.window.hide()
        with patch.object(self.window, "present_results", wraps=self.window.present_results) as present:
            self.window.ready(self.result, self.image)
            self.app.processEvents()
            self.assertEqual(present.call_count, 1)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assert_normal(self.window)
        self.window.showMinimized()
        scan = {"page_type": "inventory", "inventory_kind": "parts", "entries": [],
                "captured_at": "window-qa", "page_fingerprint": "window-qa", "ownership_risk": False,
                "notices": [], "elapsed_seconds": .1}
        with patch.object(self.window, "present_results", wraps=self.window.present_results) as present:
            self.window.ready(scan, self.image)
            self.app.processEvents()
            self.assertEqual(present.call_count, 1)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assert_normal(self.window)
        preview = os.environ.get("TREESEA_WINDOW_PREVIEW")
        if preview:
            self.window.grab().save(preview)

    def test_3_explicitly_clear_native_topmost_then_allow_other_foreground(self):
        self.window.show()
        self.app.processEvents()
        interactive = self.native and os.environ.get("TREESEA_INTERACTIVE_WINDOW_TEST") == "1"
        if interactive:
            # Establish a normal user-input context instead of bypassing the
            # Windows restriction on background processes activating windows.
            ready = []
            start = QPushButton("开始窗口切换检查")
            start.clicked.connect(lambda: ready.append(True))
            self.window.centralWidget().layout().insertWidget(0, start)
            print("Waiting for a click on the temporary QA window.", flush=True)
            for _ in range(1200):
                if ready:
                    break
                QTest.qWait(100)
            self.assertTrue(ready, "Temporary QA window was not clicked within 120 seconds")
            start.hide()
        if self.native:
            # Simulate a legacy native flag not represented by the Qt setting.
            self.assertTrue(self.user.SetWindowPos(int(self.window.winId()), wintypes.HWND(-1),
                                                   0, 0, 0, 0, 0x1 | 0x2 | 0x10))
            self.assertTrue(self.user.GetWindowLongPtrW(int(self.window.winId()), -20) & 0x8)
        else:
            self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if self.native:
            calls = []
            def activate_once(hwnd):
                success = self.user.SetForegroundWindow(hwnd)
                calls.append({"success": bool(success), "requested": hwnd,
                              "foreground_after_call": self.user.GetForegroundWindow()})
                return success
            proxy = SimpleNamespace(ShowWindow=self.user.ShowWindow, SetWindowPos=self.user.SetWindowPos,
                                    SetForegroundWindow=activate_once)
            with patch("reader.app.ctypes.WinDLL", return_value=proxy):
                self.window.present_results()
            self.assertEqual(len(calls), 1)
            if not calls[0]["success"]:
                self.assert_normal(self.window)
                self.skipTest("Windows denied background activation; native topmost was cleared. "
                              "Use TREESEA_INTERACTIVE_WINDOW_TEST=1 from an interactive session.")
            self.assertEqual(calls[0]["foreground_after_call"], calls[0]["requested"], calls)
        else:
            self.window.present_results()
        QTest.qWait(150)
        self.assert_normal(self.window)
        if interactive:
            QTest.qWait(4000)
        other = QWidget()
        self.windows.append(other)
        other.setWindowTitle("普通窗口切换检查（自动关闭）")
        other.resize(450, 250)
        other.show()
        other.raise_()
        other.activateWindow()
        if self.native:
            self.assertTrue(self.user.SetForegroundWindow(int(other.winId())))
        QTest.qWait(700)
        # The user or another program may activate a third window during QA.
        # Reader must allow that too; it must not reclaim the foreground.
        self.assertIsNot(self.app.activeWindow(), self.window)
        if self.native:
            self.assertNotEqual(self.user.GetForegroundWindow(), int(self.window.winId()))
        self.assert_normal(self.window)

    def test_4_reader_auxiliary_windows_are_normal(self):
        self.window.open_effects()
        self.window.open_calculator()
        self.windows.extend([self.window.effects_window, self.window.calculation_window])
        self.app.processEvents()
        for window in (self.window.effects_window, self.window.calculation_window):
            self.assertTrue(window.isVisible())
            self.assert_normal(window)

    def test_5_disabled_auto_show_does_not_force_a_popup(self):
        self.window.auto_show.setChecked(False)
        self.window.hide()
        with patch.object(self.window, "present_results", wraps=self.window.present_results) as present:
            self.window.ready(self.result, self.image)
            self.app.processEvents()
            self.assertEqual(present.call_count, 0)
        self.assertFalse(self.window.isVisible())
        self.window.showMinimized()
        with patch.object(self.window, "present_results", wraps=self.window.present_results) as present:
            self.window.ready(self.result, self.image)
            self.app.processEvents()
            self.assertEqual(present.call_count, 0)
        self.assertTrue(self.window.isMinimized())


if __name__ == "__main__":
    unittest.main()

"""Five focused TAB-mode groups; Qt input is offscreen, never desktop input."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from reader.app import ReaderWindow, STYLE
from reader.probability import refresh_map_probabilities
from test_probability_overlay import sample_map, SourceCounts


class TabModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.windows = []
        self.window = self.new_window()
        self.window.result = sample_map()
        refresh_map_probabilities(self.window.result, SourceCounts())
        self.window.image = np.zeros((600, 900, 3), np.uint8)
        self.window.render()
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.target = self.window.map_view.viewport()
        self.control = self.window.probability_overlay
        self.assertTrue(self.control.eligible())

    def new_window(self, settings_path=None):
        index = len(self.windows)
        w = ReaderWindow(Path(self.temp.name)/f"session-{index}.json", no_hotkeys=True,
                         settings_path=settings_path or Path(self.temp.name)/f"settings-{index}.json")
        self.windows.append(w)
        return w

    def tearDown(self):
        for w in reversed(self.windows):
            w.close()
            w.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def toggle_mode(self):
        self.window.tab_mode.setCurrentIndex(self.window.tab_mode.findData("toggle"))
        self.assertEqual(self.control.mode(), "toggle")

    def tap(self):
        QTest.keyClick(self.target, Qt.Key.Key_Tab)

    def test_1_hold_short_long_and_three_second_retention(self):
        self.assertEqual(self.window.tab_mode.currentData(), "hold")
        QTest.keyPress(self.target, Qt.Key.Key_Tab)
        QTest.qWait(100)
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertFalse(self.control.active)
        QTest.keyPress(self.target, Qt.Key.Key_Tab)
        QTest.qWait(1060)
        self.assertTrue(self.control.running())
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertTrue(self.control.retaining)
        self.assertFalse(self.control.running())
        self.assertTrue(all(i.opacity() == 1. for _, i in self.control.items))
        QTest.qWait(3100)
        self.assertFalse(self.control.active)
        self.assertFalse(self.control.items)

    def test_2_toggle_survives_release_ignores_repeat_and_stops(self):
        self.toggle_mode()
        QTest.keyPress(self.target, Qt.Key.Key_Tab)
        self.assertTrue(self.control.running())
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            repeated = QKeyEvent(kind, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, "\t", True)
            self.app.sendEvent(self.target, repeated)
            self.assertTrue(self.control.running())
            self.assertFalse(self.control.retaining)
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertFalse(self.control.held)
        self.assertTrue(self.control.running())
        previous = self.control.index
        QTest.qWait(2100)
        self.assertNotEqual(self.control.index, previous)
        self.tap()
        frozen = self.control.index
        self.assertTrue(self.control.retaining)
        self.assertFalse(self.control.type_timer.isActive())
        self.assertFalse(self.control.flash_timer.isActive())
        self.assertTrue(all(i.opacity() == 1. for _, i in self.control.items))
        QTest.qWait(500)
        self.assertEqual(self.control.index, frozen)
        self.assertTrue(self.control.active)
        QTest.qWait(2600)
        self.assertFalse(self.control.active)

    def test_3_preference_persists_and_invalid_value_falls_back(self):
        self.toggle_mode()
        saved = json.loads(self.window.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["ui"]["tab_mode"], "toggle")
        reopened = self.new_window(self.window.config_path)
        self.assertEqual(reopened.tab_mode.currentData(), "toggle")
        self.assertIn("按一次开启", reopened.key_help())
        self.assertFalse(bool(reopened.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        invalid_path = Path(self.temp.name)/"invalid.json"
        invalid_path.write_text(json.dumps({"hotkeys": {"capture": "Ctrl+Shift+S"},
            "ui": {"tab_mode": "not-supported", "show_after_capture": False, "always_on_top": True}}),
            encoding="utf-8")
        invalid = self.new_window(invalid_path)
        self.assertEqual(invalid.tab_mode.currentData(), "hold")
        self.assertFalse(invalid.auto_show.isChecked())
        self.assertEqual(invalid.config["capture"], "Ctrl+Shift+S")
        self.assertNotIn("always_on_top", invalid.ui_options)

    def test_4_toggle_stops_on_page_focus_and_minimize(self):
        self.toggle_mode()
        self.tap()
        self.assertTrue(self.control.running())
        self.window.tabs.setCurrentWidget(self.window.probability_panel)
        self.assertFalse(self.control.active)
        self.window.tabs.setCurrentWidget(self.window.map_view)
        self.tap()
        other = QWidget()
        self.windows.append(other)
        other.show()
        other.activateWindow()
        self.app.processEvents()
        self.assertFalse(self.control.active)
        QTest.keyClick(other, Qt.Key.Key_Tab)
        self.assertFalse(self.control.active)
        other.close()
        self.window.activateWindow()
        self.app.processEvents()
        self.tap()
        self.assertTrue(self.control.running())
        self.window.showMinimized()
        self.app.processEvents()
        self.assertFalse(self.control.active)

    def test_5_restart_refresh_mode_switch_and_empty_input(self):
        self.toggle_mode()
        self.tap()
        self.tap()
        self.assertTrue(self.control.retaining)
        QTest.qWait(100)
        self.tap()
        self.assertTrue(self.control.running())
        self.assertFalse(self.control.release_timer.isActive())
        QTest.qWait(3100)
        self.assertTrue(self.control.running())
        node = self.window.result["nodes"][1]
        node["probabilities"]["estimates"] = [{"name": "作战", "probability": .6},
                                               {"name": "紧急作战", "probability": .4}]
        self.window.render()
        frame = next(f for f in self.control.frames if f["name"] == "作战")
        self.assertEqual(next(r["display"] for r in frame["locations"] if r["node"]["slot"] == [1, 0]), "60%")
        self.window.tab_mode.setCurrentIndex(self.window.tab_mode.findData("hold"))
        self.assertFalse(self.control.active)
        self.assertFalse(self.control.held)
        self.assertFalse(any(t.isActive() for t in (self.control.hold_timer, self.control.type_timer,
                                                   self.control.flash_timer, self.control.release_timer)))
        self.toggle_mode()
        self.window.result = None
        self.window.render()
        self.tap()
        self.assertFalse(self.control.active)
        self.assertFalse(self.control.items)
        self.assertIn("没有可展示", self.window.status.text())
        unconfirmed = deepcopy(sample_map())
        unconfirmed["needs_review"] = True
        self.window.result = unconfirmed
        self.window.render()
        self.tap()
        self.assertFalse(self.control.active)


if __name__ == "__main__":
    unittest.main()

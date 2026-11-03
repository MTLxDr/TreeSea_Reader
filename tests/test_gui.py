"""Run with QT_QPA_PLATFORM=offscreen; no desktop/game input is generated."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from reader.app import ReaderWindow


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = ReaderWindow(Path(self.temp.name) / "session.json", no_hotkeys=True,
                                   settings_path=Path(self.temp.name) / "settings.json")
        self.window.auto_show.setChecked(True)
        self.result = {"page_type": "map", "needs_review": False, "floor": 1, "template_id": "F1-T01",
                       "fields": {}, "nodes": [], "edges": [], "candidates": [], "notices": [], "elapsed_seconds": 1}

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temp.cleanup()

    def test_successful_read_restores_hidden_window_without_topmost(self):
        self.window.hide()
        self.window.ready(self.result, np.zeros((400, 600, 3), np.uint8))
        self.application.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(bool(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))

    def test_disabled_auto_show_keeps_hidden_window_hidden(self):
        self.window.auto_show.setChecked(False)
        self.window.hide()
        self.window.ready(self.result, np.zeros((400, 600, 3), np.uint8))
        self.application.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_present_clears_legacy_topmost_without_disappearing(self):
        self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.window.present_results()
        self.application.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(bool(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))

    def test_inventory_review_merge_and_undo_do_not_replace_map(self):
        self.window.auto_show.setChecked(False)
        image=np.zeros((400,600,3),np.uint8)
        self.window.ready(self.result,image)
        item=self.window.recognizer.catalog.inventory["parts"][0]
        scan={"page_type":"inventory","inventory_kind":"parts","captured_at":"test","page_fingerprint":"test",
            "entries":[dict(item,raw=item["name"],confidence=.99,needs_review=False,fields={},
                            candidates=[dict(item,score=1.)])],"notices":[],"ownership_risk":False,"elapsed_seconds":1.}
        self.window.ready(scan,image)
        self.assertEqual(self.window.result["template_id"],"F1-T01")
        self.assertFalse(self.window.session.inventories["parts"]["items"])
        self.assertEqual(self.window.tabs.currentWidget(),self.window.inventory_panel)
        self.window.inventory_panel.merge()
        self.assertEqual(len(self.window.session.inventories["parts"]["items"]),1)
        self.window.undo()
        self.assertFalse(self.window.session.inventories["parts"]["items"])


if __name__ == "__main__":
    unittest.main()

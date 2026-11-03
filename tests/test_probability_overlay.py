"""Acceptance groups; offscreen Qt events never send desktop/game input."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from reader.app import ReaderWindow, STYLE
from reader.catalog import Catalog
from reader.probability import refresh_map_probabilities
from reader.probability_overlay import prediction_frames
from reader.state import Session


def sample_map():
    return {"page_type": "map", "floor": 3, "template_id": "acceptance-map", "needs_review": False,
        "fields": {}, "start_slot": [0, 0], "terminal_slots": [],
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]], "grid": {"spacing": 130},
        "nodes": [{"slot": [i, 0], "pixel": [150+i*180, 220], "type": "unknown-ferocity",
            "label": "未知的凶戾", "confidence": .99, "evidence": "ocr_label", "manual": False,
            "distance": 1, "region_id": "main", "probabilities": None} for i in range(3)],
        "candidates": [], "notices": [], "elapsed_seconds": 0.}


class SourceCounts:
    probabilities = Catalog.probabilities

    def __init__(self):
        self.distance_counts = {("3", 1): {1: {"作战": 60, "紧急作战": 40}}}
        self.count_limits = {"3": {"作战": {"minimum": 0, "maximum": 1}}}


class OverlayAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = ReaderWindow(Path(self.temp.name)/"session.json", no_hotkeys=True,
                                   settings_path=Path(self.temp.name)/"settings.json")
        self.window.result = sample_map()
        self.catalog = SourceCounts()
        refresh_map_probabilities(self.window.result, self.catalog)
        self.window.image = np.zeros((600, 900, 3), np.uint8)
        self.window.render()
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.target = self.window.map_view.viewport()
        self.control = self.window.probability_overlay
        self.assertTrue(self.control.eligible())

    def tearDown(self):
        self.control.cancel()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def hold(self):
        QTest.keyPress(self.target, Qt.Key.Key_Tab)
        QTest.qWait(1060)
        self.assertTrue(self.control.active)

    def test_1_short_press_empty_and_unconfirmed_map(self):
        QTest.keyPress(self.target, Qt.Key.Key_Tab)
        QTest.qWait(100)
        self.assertFalse(self.control.active)
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertFalse(self.control.held)
        self.assertFalse(self.control.hold_timer.isActive())
        self.window.result = None
        self.window.render()
        QTest.keyPress(self.target, Qt.Key.Key_Tab)
        QTest.qWait(1060)
        self.assertFalse(self.control.active)
        self.assertIn("没有可展示", self.window.status.text())
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        unconfirmed = sample_map()
        unconfirmed["needs_review"] = True
        self.assertEqual(prediction_frames(unconfirmed), [])

    def test_2_long_press_single_type_flash_cycle_release_and_live_refresh(self):
        self.hold()
        self.assertTrue(self.control.items)
        self.assertEqual({i.data(1) for _, i in self.control.items}, {self.control.frames[0]["name"]})
        before = self.control.frames[self.control.index]["name"]
        QTest.qWait(340)
        self.assertTrue(any(i.opacity() == .5 for _, i in self.control.items))
        QTest.qWait(1350)
        self.assertEqual(self.control.frames[self.control.index]["name"], before)
        QTest.qWait(400)
        self.assertNotEqual(self.control.frames[self.control.index]["name"], before)
        # Updating a posterior while held changes the drawn percentage immediately.
        node = self.window.result["nodes"][1]
        node["probabilities"]["estimates"] = [{"name": "作战", "probability": .6},
                                               {"name": "紧急作战", "probability": .4}]
        self.window.render()
        frame = next(f for f in self.control.frames if f["name"] == "作战")
        self.assertEqual(next(r["display"] for r in frame["locations"] if r["node"]["slot"] == [1, 0]), "60%")
        self.assertEqual({i.data(1) for _, i in self.control.items}, {self.control.frames[self.control.index]["name"]})
        preview = os.environ.get("TREESEA_OVERLAY_PREVIEW")
        if preview:
            self.control.index = next(i for i, f in enumerate(self.control.frames) if f["name"] == "作战")
            self.control.show_frame()
            self.app.processEvents()
            self.window.grab().save(preview)
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertTrue(self.control.active)
        self.assertTrue(self.control.retaining)
        frozen = self.control.frames[self.control.index]["name"]
        self.assertFalse(self.control.type_timer.isActive())
        self.assertFalse(self.control.flash_timer.isActive())
        self.assertTrue(all(i.opacity() == 1. for _, i in self.control.items))
        QTest.qWait(2600)
        self.assertTrue(self.control.active)
        self.assertEqual(self.control.frames[self.control.index]["name"], frozen)
        QTest.qWait(500)
        self.assertFalse(self.control.active)
        self.assertFalse(self.control.items)
        self.assertFalse(self.window.prediction_label.isVisible())
        # Per-node percentages are not normalized across locations.
        values = [r["probability"] for r in frame["locations"]]
        self.assertNotAlmostEqual(sum(values), 1.)

    def test_3_focus_loss_page_switch_and_minimize_stop(self):
        self.hold()
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertTrue(self.control.retaining)
        self.window.tabs.setCurrentWidget(self.window.probability_panel)
        self.assertFalse(self.control.active)
        self.window.tabs.setCurrentWidget(self.window.map_view)
        self.hold()
        other = QWidget()
        other.show()
        other.activateWindow()
        self.app.processEvents()
        self.assertFalse(self.control.active)
        QTest.keyPress(other, Qt.Key.Key_Tab)
        self.assertFalse(self.control.held)
        other.close()
        self.window.activateWindow()
        self.app.processEvents()
        self.hold()
        self.window.showMinimized()
        self.app.processEvents()
        self.assertFalse(self.control.active)

    def test_4_same_map_merge_geometry_conflict_and_new_run(self):
        session = Session(Path(self.temp.name)/"merge.json")
        first = sample_map()
        first["nodes"][1].update(type="combat", label="作战")
        session.apply(first)
        refresh_map_probabilities(session.current, self.catalog)
        incoming = sample_map()
        incoming["nodes"][1].update(type="unobserved", label="未识别", confidence=0.,
                                    evidence="template_only", pixel=[777, 444])
        incoming["edges"] = [list(reversed(e)) for e in reversed(incoming["edges"])]
        session.apply(incoming)
        refresh_map_probabilities(session.current, self.catalog)
        self.assertTrue(session.current["map_continuity"]["same_map"])
        self.assertEqual(session.current["nodes"][1]["type"], "combat")
        self.assertEqual(session.current["nodes"][1]["pixel"], [777, 444])
        self.assertTrue(session.current["probability_cache"]["hit"])
        self.assertEqual(session.current["floor_node_counts"]["confirmed"], {"作战": 1})
        conflict = sample_map()
        conflict["nodes"][1].update(type="emergency-combat", label="紧急作战")
        session.apply(conflict)
        refresh_map_probabilities(session.current, self.catalog)
        self.assertTrue(session.current["needs_node_review"])
        self.assertIsNone(session.current["nodes"][2]["probabilities"])
        session.edit_node([1, 0], "combat", "作战")
        refresh_map_probabilities(session.current, self.catalog)
        self.assertFalse(session.current["needs_node_review"])
        changed = sample_map()
        changed["edges"] = [[[0, 0], [2, 0]]]
        session.apply(changed)
        self.assertFalse(session.current["map_continuity"]["same_map"])
        session.reset()
        session.apply(sample_map())
        self.assertFalse(session.current["map_continuity"]["same_map"])

    def test_5_cache_invalidation_reveal_manual_constraints_and_source(self):
        from reader import probability
        result = sample_map()
        refresh_map_probabilities(result, self.catalog)
        old_key = result["probability_cache"]["key"]
        with patch.object(probability, "conditional_marginals", wraps=probability.conditional_marginals) as compute:
            repeat = deepcopy(result)
            repeat["nodes"][1]["pixel"] = [999, 222]
            repeat["fields"] = {"life": {"value": 3}}
            refresh_map_probabilities(repeat, self.catalog)
            self.assertTrue(repeat["probability_cache"]["hit"])
            self.assertEqual(compute.call_count, 0)
            repeat["nodes"][1].update(type="combat", label="作战", manual=True, evidence="manual")
            refresh_map_probabilities(repeat, self.catalog)
            self.assertFalse(repeat["probability_cache"]["hit"])
            self.assertNotEqual(repeat["probability_cache"]["key"], old_key)
            p = repeat["nodes"][2]["probabilities"]["estimates"]
            self.assertEqual(next(r["probability"] for r in p if r["name"] == "作战"), 0.)
            self.catalog.count_limits["3"]["作战"]["maximum"] = 2
            refresh_map_probabilities(repeat, self.catalog)
            self.assertFalse(repeat["probability_cache"]["hit"])
            self.assertGreater(next(r["probability"] for r in repeat["nodes"][2]["probabilities"]["estimates"]
                                    if r["name"] == "作战"), 0.)
            self.catalog.distance_counts[("3", 1)][1]["作战"] = 30
            refresh_map_probabilities(repeat, self.catalog)
            self.assertFalse(repeat["probability_cache"]["hit"])
            self.assertEqual(compute.call_count, 3)

    def test_6_concrete_icons_labels_and_mystery_before_ferocity(self):
        expected = {"失与得": "gain-loss.png", "先行一步": "first-step.png",
                    "狭路相逢": "narrow-meet.png", "安全的角落": "safe-corner.png",
                    "应急助力": "emergency-aid.png", "误入奇境": "wrong-turn.png"}
        mystery = self.window.result["nodes"][1]
        mystery.update(type="unknown-mystery", label="未知的诡秘")
        mystery["probabilities"]["estimates"] = [{"name": name, "probability": .15} for name in expected]
        mystery["probabilities"]["estimates"].append({"name": "未知的诡秘", "probability": .1})
        self.window.render()
        self.control.frames = prediction_frames(self.window.result)
        families = [f["family"] for f in self.control.frames]
        self.assertEqual(families, sorted(families, key={"mystery": 0, "ferocity": 1}.get))
        self.assertFalse(any(f["name"].startswith("未知") for f in self.control.frames))
        for name, filename in expected.items():
            self.control.index = next(i for i, f in enumerate(self.control.frames) if f["name"] == name)
            self.control.show_frame()
            icons = [i for _, i in self.control.items if i.data(3) == "icon"]
            self.assertEqual(len(icons), 2)  # Main map and screenshot each have this one position.
            self.assertTrue(all(Path(i.data(4)).name == filename and not i.pixmap().isNull() for i in icons))
            labels = [i.toPlainText() for _, i in self.control.items if i.data(3) == "type_label"]
            self.assertEqual(labels, [name, name])
            visuals = self.window.map_view.node_visuals[("main", (1, 0))]
            self.assertTrue(visuals)
            self.assertTrue(all(not i.isVisible() for i in visuals))
        self.control.cancel()
        self.assertTrue(all(i.isVisible() for i in visuals))

    def test_7_repress_cancels_old_retention_timeout(self):
        self.hold()
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertTrue(self.control.release_timer.isActive())
        QTest.qWait(200)
        self.hold()
        self.assertFalse(self.control.retaining)
        self.assertFalse(self.control.release_timer.isActive())
        QTest.qWait(2200)  # Past the previous release deadline while still holding.
        self.assertTrue(self.control.active)
        self.assertTrue(self.control.held)
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.assertTrue(self.control.retaining)
        self.window.hide()
        self.assertFalse(self.control.active)

    def test_8_screenshot_mask_missing_icon_and_retained_refresh(self):
        self.window.tabs.setCurrentWidget(self.window.screenshot_view)
        node = self.window.result["nodes"][1]
        node.update(type="unknown-mystery", label="未知的诡秘")
        node["probabilities"]["estimates"] = [{"name": "安全的角落", "probability": .7},
                                               {"name": "应急助力", "probability": .3}]
        original_image = self.window.image.copy()
        self.window.screenshot_view.prediction_icon_paths.pop("安全的角落")
        self.window.render()
        self.hold()
        self.assertEqual(self.control.frames[self.control.index]["name"], "安全的角落")
        self.assertTrue(any(i.data(3) == "type_label" and i.toPlainText() == "安全的角落"
                            for _, i in self.control.items))
        masks = [i for _, i in self.control.items if i.data(3) == "background_mask"]
        self.assertTrue(masks)
        self.control.flash()
        self.assertTrue(all(i.opacity() == 1. for i in masks))
        QTest.keyRelease(self.target, Qt.Key.Key_Tab)
        self.window.render()
        self.assertTrue(self.control.retaining)
        self.assertEqual(self.control.frames[self.control.index]["name"], "安全的角落")
        node["probabilities"]["estimates"] = [{"name": "应急助力", "probability": 1.}]
        self.window.render()  # Frozen type ceased to be possible; do not switch to another type.
        self.assertFalse(self.control.active)
        self.assertFalse(self.control.items)
        self.assertFalse(self.control.hidden_items)
        self.assertTrue(np.array_equal(self.window.image, original_image))


if __name__ == "__main__":
    unittest.main()

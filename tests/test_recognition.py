from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reader.catalog import Catalog, distances
from reader.hotkeys import parse_hotkey
from reader.recognition import anchors_from_text, grid_hypotheses, label_type, Recognizer,load_image
from reader.state import Session


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = Session(Path(self.temp.name) / "state.json")
        self.result = {"page_type": "map", "needs_review": False, "floor": 2, "template_id": "T",
                       "fields": {"life": {"value": 4}}, "nodes": [{"slot": [0, 0], "pixel": [100, 100],
                       "type": "combat", "label": "作战", "confidence": .9, "manual": False,
                       "evidence": "ocr_label", "distance": 0, "probabilities": None}]}

    def tearDown(self):
        self.temp.cleanup()

    def test_unsupported_or_ambiguous_frames_do_not_overwrite_map(self):
        self.session.apply(self.result)
        previous = deepcopy(self.session.current)
        for patch in ({"page_type": "unsupported"}, {"needs_review": True}):
            candidate = dict(self.result, **patch)
            self.assertFalse(self.session.apply(candidate))
            self.assertEqual(self.session.current, previous)

    def test_manual_values_survive_same_map_but_not_next_floor(self):
        self.session.apply(self.result)
        self.session.edit_node([0, 0], "empty", "林间空地")
        self.session.edit_field("life", 8)
        incoming = deepcopy(self.result)
        incoming["nodes"][0]["pixel"] = [200, 200]
        self.session.apply(incoming)
        self.assertEqual(self.session.current["nodes"][0]["type"], "empty")
        self.assertEqual(self.session.current["nodes"][0]["pixel"], [200, 200])
        self.assertEqual(self.session.current["fields"]["life"]["value"], 8)
        incoming["floor"] = 3
        self.session.apply(incoming)
        self.assertEqual(self.session.current["fields"]["life"]["value"], 4)

    def test_undo_and_reload_preserve_corrections(self):
        self.session.apply(self.result)
        self.session.edit_field("life", 9)
        reopened = Session(self.session.path)
        self.assertEqual(reopened.current["fields"]["life"]["value"], 9)
        reopened.undo()
        self.assertEqual(reopened.current["fields"]["life"]["value"], 4)


class GeometryTests(unittest.TestCase):
    def test_source_distance_shortcut_is_not_action_point_distance(self):
        t = {"occupiedSlots": [[0, 0], [1, 0], [2, 0]], "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
             "startSlot": [0, 0], "terminalSlots": [[1, 0]]}
        self.assertEqual(distances(t)[(2, 0)], 1)

    def test_template_occupancy_alone_does_not_distinguish_shared_layout(self):
        templates = Catalog().templates
        a = next(t for t in templates if t["template_id"] == "F2-T04")
        b = next(t for t in templates if t["template_id"] == "F2-T05")
        self.assertEqual(set(map(tuple, a["occupiedSlots"])), set(map(tuple, b["occupiedSlots"])))
        self.assertNotEqual(a["terminalSlots"], b["terminalSlots"])

    def test_short_partial_ocr_word_is_not_combat(self):
        self.assertIsNone(label_type("作", {"combat": "作战"})[0])

    def test_hotkey_reserved_key_and_duplicates_are_rejected(self):
        self.assertEqual(parse_hotkey("Ctrl+Alt+S"), (0x4003, ord("S")))
        for bad in ("F12", "Ctrl+F12", "S", "Win+S"):
            with self.assertRaises(ValueError):
                parse_hotkey(bad)


class NoTextOCR:
    def read(self, image):
        return []


class RecognitionBoundaryTests(unittest.TestCase):
    def test_sparse_sixth_floor_and_current_position(self):
        result=Recognizer().analyze(load_image(Path(__file__).with_name('fixtures')/'map_floor6_sample.png'))
        self.assertEqual(result['page_type'],'map');self.assertFalse(result['needs_review'])
        self.assertEqual(result['template_id'],'maintainer-blindness-20261022')
        self.assertEqual(result['floor'],6);self.assertEqual(result['current_slot'],[2,2])
        self.assertGreaterEqual(result['marker_anchor_count'],8)
        self.assertTrue(any(n['evidence']=='small_marker_candidate' for n in result['nodes']))

    def test_blank_frame_is_not_a_map(self):
        result = Recognizer(ocr=NoTextOCR()).analyze(np.zeros((800, 1280, 3), np.uint8))
        self.assertEqual(result["page_type"], "unsupported")
        self.assertEqual(result["nodes"], [])


if __name__ == "__main__":
    unittest.main()

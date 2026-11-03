import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("side_maps", ROOT / "scripts/collect_side_maps.py")
SIDE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIDE)


def js_source(record):
    return "const renamedModuleVariable=" + json.dumps([record], ensure_ascii=False) + ";"


def example():
    return {"id": "sunset-3-1", "floor": 3, "label": "S3.1",
            "traversable": ["0:0", "0:1", "0:2"],
            "edges": [["0:0", "0:1"], ["0:1", "0:2"]], "fate": "0:2"}


class SideMapExtraction(unittest.TestCase):
    def test_row_column_transposition_does_not_apply_label_flip(self):
        self.assertEqual(SIDE.slot("0:2"), [2, 0])
        self.assertEqual(SIDE.slot("2:0"), [0, 2])
        source = js_source(example())
        result = SIDE.extract_templates(source)[0]
        self.assertEqual(result["fateSlot"], [2, 0])
        self.assertEqual(result["raw"]["fate"], "0:2")
        self.assertNotIn("startSlot", result)

    def test_out_of_grid_slot_is_rejected(self):
        for value in ("3:0", "0:3", "-1:0", "0,1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SIDE.slot(value)

    def test_published_twelve_template_geometry_and_floor_counts(self):
        geometry, controller = SIDE.cached_sources()
        templates = SIDE.extract_templates(geometry[1])
        self.assertEqual(len(templates), 12)
        self.assertEqual({f: sum(t["floor"] == f for t in templates) for f in (3, 4, 5)}, {3: 3, 4: 4, 5: 5})
        self.assertEqual(next(t for t in templates if t["id"] == "sunset-5-1")["fateSlot"], [2, 0])
        self.assertTrue(all(t["region_kind"] == "sunset" and "startSlot" not in t for t in templates))
        self.assertEqual(len(SIDE.coordinate_evidence(controller[1])["alignment_labels"]), 3)

    def test_ambiguous_multiple_arrays_are_rejected(self):
        literal = 'var t=[{id:`sunset-3-1`,floor:3,traversable:[`0:0`],edges:[],fate:`0:0`}];'
        with self.assertRaisesRegex(ValueError, "found 2"):
            SIDE.extract_templates(literal + literal.replace("var t", "var z"))

    def test_fate_and_edge_endpoints_must_exist(self):
        template = 'var t=[{id:`sunset-3-1`,floor:3,traversable:[`0:0`,`0:1`],edges:[[`0:0`,`2:2`]],fate:`0:1`}];'
        with self.assertRaisesRegex(ValueError, "Edge endpoint"):
            SIDE.extract_templates(template)
        template = 'var t=[{id:`sunset-3-1`,floor:3,traversable:[`0:0`,`0:1`],edges:[[`0:0`,`0:1`]],fate:`2:2`}];'
        with self.assertRaisesRegex(ValueError, "Fate slot"):
            SIDE.extract_templates(template)


if __name__ == "__main__":
    unittest.main()

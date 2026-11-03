import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("collect_lubiao", ROOT / "scripts/collect_lubiao.py")
COLLECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECT)

SOURCE = {"url": "https://www.lubiao.wiki/_nuxt/example.js", "sha256": "a" * 64,
          "relative_path": "blobs/example.js", "fetched_at": "2026-10-26T00:00:00+00:00"}
ARRAY = """var s=[{id:`floor-1-example`,sourceId:`F1-T01`,layerIndex:0,
gridShape:[2,1],occupiedSlots:[[0,0],[1,0]],startSlot:[0,0],terminalSlots:[[1,0]],
edges:[[[0,0],[1,0]]]},o({id:`treehole-example`,sourceId:`treehole-test`,layerIndex:6,
gridShape:[3,1],occupiedSlots:[[0,0],[1,0],[2,0]],startSlot:[0,0],terminalSlots:[],
excludedEdges:[[[1,0],[2,0]]]})];"""


class CollectorBoundaries(unittest.TestCase):
    def template_data(self):
        return COLLECT.extract_templates(COLLECT.UPSTREAM_ADJACENCY_HELPER + ARRAY, copy.deepcopy(SOURCE))

    def test_verified_helper_applies_excluded_edges(self):
        data = self.template_data()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["templates"][1]["edges"], [[[0, 0], [1, 0]]])
        self.assertEqual(data["templates"][1]["floor"], "treehole")

    def test_unknown_functions_are_not_accepted_as_data_helpers(self):
        with self.assertRaisesRegex(ValueError, "Unsupported data helper"):
            COLLECT.LiteralParser("arbitrary({occupiedSlots:[]})").value()

    def test_modified_upstream_helper_requires_review(self):
        changed = COLLECT.UPSTREAM_ADJACENCY_HELPER.replace("[t+1,n]", "[t+2,n]")
        with self.assertRaisesRegex(ValueError, "Unsupported data helper"):
            COLLECT.extract_templates(changed + ARRAY, SOURCE)

    def test_template_interpolation_is_not_data(self):
        with self.assertRaisesRegex(ValueError, "interpolation"):
            COLLECT.LiteralParser("`value-${process.exit()}`").value()

    def test_definition_hash_ignores_times_but_tracks_geometry_and_source(self):
        first = self.template_data()
        second = copy.deepcopy(first)
        second["extracted_at"] = "a different time"
        second["source"]["fetched_at"] = "another capture"
        self.assertEqual(COLLECT.template_definition_hash(first), COLLECT.template_definition_hash(second))
        second["source"]["sha256"] = "b" * 64
        self.assertNotEqual(COLLECT.template_definition_hash(first), COLLECT.template_definition_hash(second))
        second = copy.deepcopy(first)
        second["templates"][0]["terminalSlots"] = []
        self.assertNotEqual(COLLECT.template_definition_hash(first), COLLECT.template_definition_hash(second))

    def test_local_import_time_is_distinct_from_network_fetch_and_blobs_are_reused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data/sources/lubiao") as directory:
            collector = COLLECT.Collector(Path(directory), 0.5, 5, 1)
            record, _ = collector.store("https://example.test/data.json", b'{"updatedAt":"old"}', "local_baseline")
            self.assertIsNone(record["fetched_at"])
            self.assertTrue(record["imported_at"])
            live, _ = collector.store("https://example.test/data.json", b'{"updatedAt":"old"}', "live")
            self.assertTrue(live["fetched_at"])
            self.assertEqual(record["relative_path"], live["relative_path"])
            self.assertEqual(len(list((Path(directory) / "blobs").iterdir())), 1)
            data = self.template_data()
            collector.finish(data)
            derived = list((Path(directory) / "derived").glob("*.json"))
            self.assertEqual(len(derived), 1)
            original = derived[0].read_bytes()
            data["source"]["fetched_at"] = "later"
            data["extracted_at"] = "later"
            collector.finish(data)
            self.assertEqual(len(list((Path(directory) / "derived").glob("*.json"))), 1)
            self.assertEqual(derived[0].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

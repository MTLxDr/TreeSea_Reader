import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("reader_database", Path(__file__).resolve().parents[1] / "scripts/database.py")
database = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(database)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = database.connect(self.root / "reader.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def ingest(self, counts, acquisition="live", path="counts.json"):
        data = {"denominator": "node-type-within-floor", "floors": [{
            "id": "2", "mapSampleCount": 20, "rows": [
                {"name": kind, "sampleCount": sum(dist.values()), "counts": dist}
                for kind, dist in counts.items()]}]}
        file = self.root / path
        file.write_text(json.dumps(data), encoding="utf-8")
        return database.add_document(self.db, file, provider="lubiao", url="https://example.invalid/counts",
                                     acquisition=acquisition, filename="node-distance-frequencies-rogue_6.json")[0]

    def test_conditional_probability_uses_counts_instead_of_type_percentages(self):
        self.ingest({"combat": {"1": 99, "3": 1}, "elite": {"3": 1}})
        result = database.probability(self.db, "2", 3, ["combat", "elite"])
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["probabilities"]["combat"]["probability"], .5)

    def test_reimport_is_idempotent(self):
        first = self.ingest({"combat": {"3": 5}})
        second = self.ingest({"combat": {"3": 5}})
        self.assertEqual(first, second)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT SUM(observation_count) FROM node_distance_counts").fetchone()[0], 5)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0], 1)

    def test_cumulative_snapshots_are_replaced_and_can_be_pinned(self):
        first = self.ingest({"combat": {"3": 5}}, path="old.json")
        self.ingest({"combat": {"3": 8}}, path="new.json")
        self.assertEqual(database.probability(self.db, "2", 3, None)["sample_count"], 8)
        self.assertEqual(database.probability(self.db, "2", 3, None, first)["sample_count"], 5)

    def test_baseline_cannot_downgrade_live_data(self):
        self.ingest({"combat": {"3": 8}}, path="new.json")
        self.ingest({"combat": {"3": 5}}, acquisition="local_baseline", path="old.json")
        self.assertEqual(database.probability(self.db, "2", 3, None)["sample_count"], 8)

    def test_no_observations_is_unknown_not_uniform(self):
        self.ingest({"combat": {"3": 5}})
        result = database.probability(self.db, "2", 9, None)
        self.assertEqual(result["sample_count"], 0)
        self.assertIsNone(result["probabilities"]["combat"]["probability"])

    def test_item_effect_variants_are_preserved(self):
        file = self.root / "archive.json"
        file.write_text(json.dumps({"classifier": {"items": [
            {"id": "one", "itemId": "same", "dataType": "utopia", "variant": 1},
            {"id": "two", "itemId": "same", "dataType": "utopia", "variant": 2}]}}), encoding="utf-8")
        database.add_document(self.db, file, provider="lubiao", url="https://example.invalid/archive",
                              acquisition="live", filename="archives_blackstream.json")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 2)

    def test_base_and_dlc_pool_versions_are_kept_separately(self):
        file = self.root / "archive.json"
        file.write_text(json.dumps({"pools": {
            "defaultVersionId": "dlc1", "pools": [{"id": "shop", "name": "base"}],
            "dataVersions": {"dlc1": [{"id": "shop", "name": "expanded"}]}}}), encoding="utf-8")
        database.add_document(self.db, file, provider="lubiao", url="https://example.invalid/archive",
                              acquisition="live", filename="archives_blackstream.json")
        self.assertEqual(self.db.execute("SELECT entity_id FROM entities ORDER BY entity_id").fetchall()[0][0], "base:shop")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 2)

    def test_side_geometry_import_is_separate_and_idempotent(self):
        source=database.ROOT / "data/sources/lubiao/side_map_definitions.json"
        target=self.root/"side_map_definitions.json"
        target.write_bytes(source.read_bytes())
        database.import_side_templates(self.db,target)
        database.import_side_templates(self.db,target)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM active_side_map_templates").fetchone()[0],12)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM map_templates").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()

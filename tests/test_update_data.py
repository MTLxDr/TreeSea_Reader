import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("update_data", Path(__file__).resolve().parents[1] / "scripts/update_data.py")
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)


class UpdateFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = update.ROOT
        update.ROOT = Path(self.temp.name)
        self.folder = update.ROOT / "data/sources/lubiao"
        self.folder.mkdir(parents=True)
        self.now = datetime(2026, 10, 26, 14, tzinfo=timezone.utc)

    def tearDown(self):
        update.ROOT = self.previous_root
        self.temp.cleanup()

    def write(self, records, failures=None):
        (self.folder / "latest_manifest.json").write_text(json.dumps({"resources": records, "failures": failures or []}), encoding="utf-8")

    def test_local_copy_time_does_not_make_source_fresh(self):
        self.write([{"origin": "local_baseline", "fetched_at": self.now.isoformat()}])
        self.assertTrue(update.freshness("lubiao", 24, self.now)["due"])

    def test_stale_resource_is_not_hidden_by_newly_fetched_resource(self):
        self.write([{"origin": "live", "fetched_at": "2026-10-24T14:00:00Z"},
                    {"origin": "live", "fetched_at": self.now.isoformat()}])
        self.assertTrue(update.freshness("lubiao", 24, self.now)["due"])

    def test_discovery_budget_is_distinct_from_failed_http_request(self):
        self.write([{"origin": "live", "fetched_at": self.now.isoformat()}], [{"kind": "asset_discovery_budget"}])
        self.assertFalse(update.freshness("lubiao", 24, self.now)["due"])
        self.write([{"origin": "live", "fetched_at": self.now.isoformat()}], [{"url": "https://example.invalid", "error": "HTTP 503"}])
        self.assertTrue(update.freshness("lubiao", 24, self.now)["due"])


if __name__ == "__main__":
    unittest.main()

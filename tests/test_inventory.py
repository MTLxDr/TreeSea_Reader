from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reader.catalog import Catalog
from reader.inventory import InventoryRecognizer, name_candidates, page_kind
from reader.recognition import Recognizer
from reader.state import Session


def token(text, x, y, score=.99):
    return {"text": text, "score": score, "box": [[x,y],[x+100,y],[x+100,y+24],[x,y+24]]}


class OCR:
    def __init__(self, tokens): self.tokens = tokens
    def read(self, image): return deepcopy(self.tokens)


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def scan(self, tokens, kind):
        return InventoryRecognizer(self.catalog, OCR(tokens)).analyze(np.zeros((800,1200,3), np.uint8), kind)

    def test_all_three_catalogs_loaded_with_source_ids(self):
        self.assertEqual(len(self.catalog.inventory["relics"]), 251)
        self.assertEqual(len(self.catalog.inventory["parts"]), 44)
        self.assertGreaterEqual(len(self.catalog.inventory["operators"]), 400)
        self.assertTrue(all(i.get("document_id") for rows in self.catalog.inventory.values() for i in rows))

    def test_short_names_are_not_guessed_and_effect_mentions_are_ignored(self):
        records = [{"id":"x", "name":"山"}, {"id":"y", "name":"支援补给站"}]
        self.assertFalse(name_candidates("出", records))
        self.assertFalse(name_candidates("获得一个支援补给站", records))
        self.assertEqual(name_candidates("山", records)[0][1]["id"], "x")

    def test_amiya_forms_require_identity_review(self):
        result=self.scan([token("阿米娅",300,400)],"operators")
        self.assertTrue(result["entries"][0]["needs_review"])
        self.assertEqual(len(result["entries"][0]["candidates"]),3)

    def test_nearby_operator_levels_stay_on_their_cards(self):
        tokens = [token("银灰",100,400), token("LV90",100,350), token("E2",100,320),
                  token("能天使",700,400), token("LV60",700,350), token("E1",700,320)]
        result = self.scan(tokens, "operators")
        by_name = {r["name"]: r for r in result["entries"]}
        self.assertEqual(by_name["银灰"]["fields"]["level"]["value"], 90)
        self.assertEqual(by_name["能天使"]["fields"]["level"]["value"], 60)
        self.assertEqual(by_name["银灰"]["fields"]["elite"]["value"], 2)

    def test_conflicting_levels_and_icon_only_elite_remain_unknown(self):
        result = self.scan([token("银灰",100,400), token("LV90",100,350), token("LV80",100,300)], "operators")
        fields = result["entries"][0]["fields"]
        self.assertIsNone(fields["level"]["value"])
        self.assertNotIn("elite", fields)

    def test_skill_number_is_not_misread_as_skill_level(self):
        result=self.scan([token("银灰",100,400),token("技能3",100,350),token("专精3",100,300)],"operators")
        fields=result["entries"][0]["fields"]
        self.assertNotIn("skill_level",fields)
        self.assertEqual(fields["mastery"]["skill_index"],3)

    def test_parts_quantity_requires_explicit_multiplier(self):
        item = self.catalog.inventory["parts"][0]
        result = self.scan([token(item["name"],300,300), token("×3",300,350)], "parts")
        self.assertEqual(result["entries"][0]["fields"]["quantity"]["value"], 3)
        result = self.scan([token(item["name"],300,300), token("3",300,350)], "parts")
        self.assertNotIn("quantity", result["entries"][0]["fields"])

    def test_shop_text_keeps_ownership_risk(self):
        result = self.scan([token("银灰",100,400), token("招募费用",100,100)], "operators")
        self.assertTrue(result["ownership_risk"])
        self.assertTrue(result["needs_review"])

    def test_map_bottom_hud_is_not_an_inventory_title(self):
        self.assertIsNone(page_kind([token("收藏品",100,740)],800))

    def test_explicit_mode_dispatches_to_inventory_without_map_fields(self):
        result = Recognizer(self.catalog, OCR([token("银灰",100,400)])).analyze(
            np.zeros((800,1200,3), np.uint8), page_hint="operators")
        self.assertEqual(result["page_type"], "inventory")


class InventorySessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = Session(Path(self.temp.name) / "state.json")
        self.result = {"page_type":"inventory", "inventory_kind":"parts", "captured_at":"test",
                       "page_fingerprint":"page1", "entries":[], "source":{"path":"first.png"}}
        self.entry = {"id":"part1", "name":"测试件", "fields":{"quantity":{"value":3,"manual":False}}}

    def tearDown(self): self.temp.cleanup()

    def test_repeated_and_overlapping_pages_never_add_quantities(self):
        self.session.merge_inventory(self.result,[self.entry])
        self.session.merge_inventory(self.result,[self.entry])
        self.session.merge_inventory(dict(self.result,page_fingerprint="page2"),[self.entry])
        bucket=self.session.inventories["parts"]
        self.assertEqual(len(bucket["items"]),1)
        self.assertEqual(len(bucket["pages"]),2)
        self.assertEqual(bucket["items"][0]["fields"]["quantity"]["value"],3)
        self.assertIsNone(self.session.current)

    def test_manual_lock_removal_and_readd_survive_ocr(self):
        self.session.merge_inventory(self.result,[self.entry])
        self.session.manual_inventory("parts", self.entry, {"quantity":8})
        self.session.merge_inventory(self.result,[self.entry])
        self.assertEqual(self.session.inventories["parts"]["items"][0]["fields"]["quantity"]["value"],8)
        self.session.manual_inventory("parts", self.entry, excluded=True)
        self.session.merge_inventory(self.result,[self.entry])
        self.assertTrue(self.session.inventories["parts"]["items"][0]["excluded"])
        self.session.manual_inventory("parts", self.entry, excluded=False)
        self.assertFalse(self.session.inventories["parts"]["items"][0]["excluded"])

    def test_undo_reload_and_reset_cover_inventories(self):
        self.session.merge_inventory(self.result,[self.entry])
        restored=Session(self.session.path)
        self.assertEqual(len(restored.inventories["parts"]["items"]),1)
        restored.reset()
        self.assertFalse(restored.inventories["parts"]["items"])
        restored.undo()
        self.assertEqual(len(restored.inventories["parts"]["items"]),1)
        restored.undo()
        self.assertFalse(restored.inventories["parts"]["items"])


if __name__ == "__main__": unittest.main()

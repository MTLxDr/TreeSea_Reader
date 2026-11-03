from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reader.catalog import Catalog
from reader.recognition import grid_hypotheses, Recognizer, load_image
from reader.regions import partition_hypotheses, side_candidates, zoom_control
from reader.state import Session


class RegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.catalog=Catalog()

    def test_ten_column_main_maps_are_not_rejected_by_old_eight_limit(self):
        anchors=[{"x":100+x*60,"y":200+y*60} for x in range(10) for y in range(4)]
        grids=grid_hypotheses(anchors,1200,max_shape=(10,5))
        self.assertTrue(any(g["span"]==[10,4] for g in grids))

    def test_zoom_symbols_on_two_real_fixtures(self):
        self.assertEqual(zoom_control(load_image("tests/fixtures/map_sample.png"))["state"],"overview")
        self.assertEqual(zoom_control(load_image("tests/fixtures/map_live_f1.png"))["state"],"detail")

    def test_side_templates_are_separate_and_never_have_main_start(self):
        self.assertEqual(len(self.catalog.side_templates),12)
        self.assertTrue(all("startSlot" not in t for t in self.catalog.side_templates))

    def test_side_match_coordinates_do_not_enter_main_probability(self):
        template=self.catalog.side_templates[0]
        anchors=[{"x":800+x*60,"y":300+y*60,"type":"fate-choice" if [x,y]==template["fateSlot"] else "unknown-mystery",
                  "confidence":.99} for x,y in template["occupiedSlots"]]
        rows=side_candidates(np.zeros((800,1200,3),np.uint8),anchors,self.catalog.side_templates,3,60,lambda *args:0,hint=template["sourceId"])
        self.assertGreater(rows[0]["geometry_score"],.99)
        class OCR:
            def read(self, image): return []
        recognizer=Recognizer(self.catalog,OCR())
        side=recognizer._side_result(np.zeros((800,1200,3),np.uint8),anchors,rows,1.,template["sourceId"])
        self.assertFalse(side["needs_review"])
        self.assertTrue(all(n["distance"] is None and n["probabilities"] is None for n in side["nodes"]))

    def test_same_slot_correction_is_scoped_by_region(self):
        with tempfile.TemporaryDirectory() as temp:
            session=Session(Path(temp)/"state.json")
            node={"slot":[0,0],"type":"combat","label":"作战","manual":False}
            data={"page_type":"map","needs_review":False,"floor":3,"template_id":"T","fields":{},
                  "nodes":[deepcopy(node)],"side_map":{"template_id":"S","nodes":[deepcopy(node)]}}
            session.apply(data)
            session.edit_node([0,0],"empty","林间空地",region="sunset")
            self.assertEqual(session.current["nodes"][0]["type"],"combat")
            session.apply(data)
            self.assertEqual(session.current["side_map"]["nodes"][0]["type"],"empty")

    def test_full_recognizer_separates_synthetic_main_and_offset_side_lattices(self):
        from reader.catalog import NODE_NAMES
        main=next(t for t in self.catalog.templates if t["template_id"]=="F3-T01")
        side=self.catalog.side_templates[0]
        tokens=[]
        def add(slot, origin, kind):
            x,y=[origin[i]+slot[i]*60 for i in range(2)]
            top=y+(1.5 if kind=="danger-end" else .75)*14
            tokens.append({"text":NODE_NAMES[kind],"score":.99,"box":[[x-40,top],[x+40,top],[x+40,top+14],[x-40,top+14]]})
        for slot in main["occupiedSlots"]:
            add(slot,[100,240],"danger-end" if slot in main["terminalSlots"] else "unknown-mystery")
        for slot in side["occupiedSlots"]:
            add(slot,[100+(main["gridShape"][0]+1.2)*60,285],"fate-choice" if slot==side["fateSlot"] else "unknown-mystery")
        class OCR:
            def read(self,image): return deepcopy(tokens)
        result=Recognizer(self.catalog,OCR()).analyze(np.zeros((800,1200,3),np.uint8),floor_hint=3,
            template_hint=main["template_id"],side_template_hint=side["sourceId"])
        self.assertEqual(result["region_detection"]["partition"],"main+sunset")
        self.assertEqual(len(result["nodes"]),len(main["occupiedSlots"]))
        self.assertEqual(len(result["side_map"]["nodes"]),len(side["occupiedSlots"]))
        self.assertFalse(result["needs_review"])


if __name__ == "__main__": unittest.main()

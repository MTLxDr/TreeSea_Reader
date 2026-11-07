"""Five acceptance groups for inventory state, badges, outfits and map tools."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import cv2
import numpy as np
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from reader.app import MapView
from reader.catalog import ROOT as RUNTIME_ROOT
from reader.effect_rules import RuleCatalog,compile_counted_entry
from reader.operator_vision import OperatorVision
from reader.recognition import Recognizer,load_image
from reader.relic_state import rules
from reader.resident_vision import ResidentVision
from reader.state import Session
from test_live_exploration import line_map

PROJECT=Path(__file__).resolve().parents[1]


class RevisionR7(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_01_real_cards_counters_and_cached_speed(self):
        path=PROJECT/'data/captures/藏品1.jpg'
        if not path.exists():
            self.skipTest('Private real-game screenshot is unavailable')
        reader=Recognizer()
        image=load_image(path)
        first=reader.analyze(image)
        start=time.perf_counter()
        result=reader.analyze(image)
        elapsed=time.perf_counter()-start
        self.assertEqual((result['coverage']['visible'],result['coverage']['recognized']),(12,12))
        values={e['name']:e['fields'].get('stacks',{}).get('value') for e in result['entries']}
        self.assertEqual(values['可疑的集点卡'],6)
        self.assertEqual(values['悲伤的红'],10)
        self.assertLess(elapsed,2.22)  # Half the previous measured warm-page time, 4.444 s.
        operator=next(PROJECT.glob('dist/*/data/captures/20261104T082039797303Z_f154fe6a.png'),None)
        if operator:
            roster=reader.analyze(load_image(operator))
            self.assertEqual(roster['coverage']['recognized'],4)
            self.assertEqual({e['name'] for e in roster['entries']},{'机械师','桃金娘','梅','布丁'})

    def test_02_exchange_reacquire_counter_and_once_are_distinct(self):
        metadata=rules()
        ids={row['name']:identity for identity,row in metadata.items()}
        with TemporaryDirectory() as folder:
            session=Session(Path(folder)/'state.json')
            def observe(name,page,fields=None):
                entry=dict(id=ids[name],name=name,fields=fields or {})
                scan=dict(page_type='inventory',inventory_kind='relics',captured_at=page,page_fingerprint=page)
                session.merge_inventory(scan,[entry])
                return next(r for r in session.inventories['relics']['items'] if r['id']==ids[name])
            row=observe('特殊抑制器','a')
            self.assertEqual(row['fields']['stacks']['value'],1)
            observe('特殊抑制器','a')
            self.assertEqual(row['fields']['stacks']['value'],1)
            session.lose_relic(ids['特殊抑制器'])
            self.assertTrue(session.reacquire_relic(ids['特殊抑制器']))
            self.assertEqual(row['fields']['stacks']['value'],2)
            continuous=observe('银餐叉','b')
            session.lose_relic(ids['银餐叉'])
            session.reacquire_relic(ids['银餐叉'])
            self.assertEqual(continuous['fields']['stacks']['value'],1)
            counter=observe('悲伤的红','c',{'stacks':{'value':10,'confidence':1}})
            observe('悲伤的红','d',{'stacks':{'value':11,'confidence':1}})
            self.assertEqual(counter['fields']['stacks']['value'],11)
            once=observe('指中狼','e',{'used':{'value':True,'confidence':1}})
            observe('指中狼','e',{'used':{'value':True,'confidence':1}})
            self.assertEqual(once['fields']['stacks']['value'],1)
            session.lose_relic(ids['指中狼'])
            session.reacquire_relic(ids['指中狼'])
            session.record_relic_trigger(ids['指中狼'])
            self.assertEqual(once['fields']['stacks']['value'],2)
            loaded=Session(session.path)
            self.assertEqual(loaded.inventories['relics']['items'][0]['fields']['stacks']['value'],2)
            catalog=RuleCatalog()
            entry=next(e for e in catalog.entries.values() if e['name']=='“鸡尾酒针”' and e['category']=='路标藏品')
            compiled=compile_counted_entry(entry,3)
            self.assertIn(60,[r['value'] for r in compiled['rules'] if r['stat']=='attack_speed'])
            self.assertFalse(compiled['unsupported'])
            from test_rule_bridge import example_session_request
            from reader.session_calculation import compile_session
            envelope=example_session_request()
            envelope['session']['inventories']['relics']['items']=[
                dict(id=entry['subject_id'],confirmed=True,fields={'stacks':{'value':3}})]
            connected=compile_session(envelope,catalog)
            self.assertIn(60,[r['value'] for r in connected['audit']['rules']
                             if r['stat']=='attack_speed' and r['source']['kind']=='relic'])
            session.complete_inventory('relics',True)
            observe('银餐叉','next-complete-round')
            session.complete_inventory('relics',True)
            self.assertTrue(counter['excluded'])
            observe('悲伤的红','reacquired-counter',{'stacks':{'value':2,'confidence':1}})
            self.assertEqual(counter['fields']['stacks']['value'],2)

    def test_03_real_badges_are_found_before_map_recognition(self):
        path=next(PROJECT.glob('dist/*/data/captures/20261104T082941670472Z_2036a466.png'),None)
        if path is None:
            self.skipTest('Private resident screenshot is unavailable')
        image=load_image(path)
        working=cv2.resize(image,(1280,round(image.shape[0]*1280/image.shape[1])))
        self.assertEqual(len(ResidentVision().detect(working)),3)
        result=Recognizer().analyze(image)
        self.assertEqual(result['template_id'],'F2-T05')
        self.assertEqual(sum(bool(n.get('resident')) for n in result['nodes']),3)
        self.assertEqual(len(ResidentVision().detect(np.zeros_like(working))),0)
        empty_ocr=Mock(read=Mock(return_value=[]),line=Mock(return_value=('',0.)))
        unsupported=Recognizer(ocr=empty_ocr).analyze(image)
        self.assertEqual(len(unsupported['resident_markers']),3)
        effect=next(PROJECT.glob('dist/*/data/captures/20261104T090516980185Z_6bb99414.png'),None)
        if effect:
            mapped=Recognizer().analyze(load_image(effect))
            self.assertEqual(mapped['template_id'],'F3-T04')
            self.assertEqual(len(mapped['nodes']),29)
            self.assertEqual(mapped['resident_markers'],[])

    def test_04_pen_eraser_refresh_and_map_undo(self):
        with TemporaryDirectory() as folder:
            session=Session(Path(folder)/'state.json')
            region=line_map()
            session.apply(region)
            view=MapView(lambda node:None)
            view.annotations.bind(session)
            view.resize(900,600)
            view.show()
            view.show_map(session.current)
            self.app.processEvents()
            tools=view.annotations
            QTest.mousePress(view.viewport(),Qt.MouseButton.RightButton,pos=QPoint(280,300))
            QTest.qWait(340)
            QTest.mouseMove(view.viewport(),QPoint(390,330))
            QTest.mouseRelease(view.viewport(),Qt.MouseButton.RightButton,pos=QPoint(390,330))
            self.assertEqual(len(tools.strokes()),1)
            view.show_map(deepcopy(session.current))
            self.assertEqual(len(tools.strokes()),1)
            QTest.mousePress(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(340,316))
            QTest.qWait(340)
            QTest.mouseRelease(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(340,316))
            self.assertEqual(tools.strokes(),[])
            tools.undo()
            self.assertEqual(len(tools.strokes()),1)
            tools.select('pen')
            self.assertEqual(tools.mode,'pen')
            tools.select('pen')
            self.assertIsNone(tools.mode)
            session.reset()
            view.show_map(None)
            self.assertIsNone(tools.key)
            view.close()
            view.deleteLater()
            self.app.processEvents()

    def test_05_unique_resident_routes_and_outfit_index(self):
        with TemporaryDirectory() as folder:
            session=Session(Path(folder)/'state.json')
            region=line_map()
            region['nodes'][2]['resident']=True
            session.apply(region)
            following=deepcopy(region)
            following['nodes'][2]['resident']=False
            following['nodes'][3]['resident']=True
            session.apply(following)
            self.assertEqual(session.exploration['resident_routes'][0]['to'],[3,0])
            duplicate=deepcopy(following)
            session.apply(duplicate)
            self.assertEqual(len(session.exploration['resident_routes']),1)
        folder=RUNTIME_ROOT/'assets/operators'
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        outfits=[r for r in manifest['assets'] if '#' in r['asset_key']]
        self.assertTrue(outfits)
        asset=outfits[0]
        image=cv2.imdecode(np.fromfile(folder/asset['file'],np.uint8),cv2.IMREAD_COLOR)
        result=OperatorVision(folder).match(image,[asset['operator_id']])
        self.assertIsNotNone(result)
        self.assertEqual(result['operator_id'],asset['operator_id'])

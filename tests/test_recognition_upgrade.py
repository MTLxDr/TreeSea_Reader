"""Regression cases for state continuity, action evidence and per-card fields."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from reader.event_images import EventImages
from reader.exploration import ExplorationCatalog
from reader.recognition import Recognizer, load_image
from reader.state import Session
from test_live_exploration import line_map


class RecognitionUpgrade(unittest.TestCase):
    def test_resident_badge_above_right_of_unrevealed_node(self):
        region = line_map()
        node = region['nodes'][1]
        node.update(type='unobserved', evidence='template_only')
        canvas = np.zeros((600,1700,3), np.uint8)
        from reader.catalog import ROOT

        asset = cv2.imdecode(np.fromfile(ROOT/'assets/nodes/mark-1.png', np.uint8), cv2.IMREAD_COLOR)
        badge = cv2.resize(asset,(32,32))
        x, y = node['pixel']
        canvas[y-65:y-33,x+38:x+70] = badge
        Recognizer().locate_overlays(canvas,[],region)
        self.assertTrue(node['resident'])
        self.assertEqual(node['type'], 'unobserved')

    def test_confirmed_map_keeps_types_and_records_arrival_once(self):
        with TemporaryDirectory() as folder:
            session = Session(Path(folder)/'session.json')
            first = line_map()
            first.update(current_slot=[0,0], at_start=True, position_evidence='人物标记')
            first['nodes'][1].update(type='combat', label='作战', confidence=.81)
            session.apply(first)
            later = deepcopy(first)
            later.update(current_slot=[1,0], at_start=False)
            later['nodes'][1].update(type='unobserved', label='未识别', evidence='template_only',
                                     confidence=0, pixel=[245,315])
            later['nodes'][2].update(type='encounter', label='不期而遇', confidence=.98)
            session.apply(later)
            session.apply(later)
            self.assertEqual(session.current['nodes'][1]['type'], 'combat')
            self.assertEqual(session.current['nodes'][1]['pixel'], [245,315])
            self.assertEqual(session.current['nodes'][2]['type'], 'encounter')
            self.assertEqual([a['slot'] for a in session.exploration['actions']], [[1,0]])

    def test_event_illustration_records_once_and_black_frame_is_not_an_event(self):
        catalog = ExplorationCatalog.shared()
        images = EventImages()
        art = load_image(images.folder/'Avg_pic_rogue_6_1.jpg')
        art = cv2.resize(art, None, fx=.78, fy=.78)
        h, w = art.shape[:2]
        canvas = np.zeros((720,1280,3), np.uint8)
        self.assertIsNone(images.match(canvas, catalog.event_names))
        canvas[70:70+h,40:40+w] = art
        recognizer = Recognizer(ocr=Mock(read=Mock(return_value=[])))
        recognizer.event_images = images
        result = recognizer.analyze(canvas)
        self.assertEqual(result['page_type'], 'event')
        match = result['event_evidence']
        self.assertEqual(match['name'], '思乡心切')
        self.assertEqual(match['method'], 'image')
        with TemporaryDirectory() as folder:
            session = Session(Path(folder)/'session.json')
            self.assertTrue(session.record_event(result))
            self.assertFalse(session.record_event(result))
            self.assertEqual(session.exploration['actions'][0]['evidence']['method'], 'image')

    def test_same_name_parts_keep_separate_visible_values(self):
        result = dict(page_type='inventory', inventory_kind='parts', coverage={'visible':2},
                      captured_at='test', page_fingerprint='page')
        card = dict(id='part',name='零件',fields={'valuation':{'value':1,'confidence':1}})
        other = deepcopy(card)
        other['fields']['valuation']['value'] = 2
        with TemporaryDirectory() as folder:
            session = Session(Path(folder)/'session.json')
            session.merge_inventory(result,[card,other])
            bucket = session.inventories['parts']
            self.assertEqual([c['fields']['valuation']['value'] for c in bucket['visible_cards']], [1,2])
            self.assertIsNone(bucket['items'][0]['fields']['valuation']['value'])
            self.assertEqual(bucket['items'][0]['visible_instances'], 2)

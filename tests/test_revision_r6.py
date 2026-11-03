"""Five acceptance groups for capture, run ownership, events, probability and icons."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from reader.catalog import Catalog,ROOT,distances
from reader.exploration import ExplorationCatalog
from reader.state import Session
from reader.probability import refresh_map_probabilities
from reader.recognition import load_image
from test_live_exploration import line_map


class RevisionAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.catalog=Catalog();cls.catalog.include_exploration_items()

    def test_01_shared_bound_capture_static_and_invalid_window(self):
        from reader.window_capture import WgcSource,_Stream,_streams
        user=Mock();user.IsWindow.return_value=True;user.IsIconic.return_value=False;user.IsWindowVisible.return_value=True
        info=dict(pid=123,hwnd=42,width=320,height=240,left=0,top=0)
        with patch('reader.window_capture._api',return_value=(user,None)),patch('reader.window_capture.window_info',return_value=info):
            stream=_Stream(42);stream.control=Mock();stream.control.is_finished.return_value=False
            stream.frame=np.indices((240,320)).sum(axis=0).astype(np.uint8)[:,:,None].repeat(3,axis=2)
            stream.timestamp=time.monotonic()-60;stream.last_start=time.monotonic()-60;stream.frame_count=1
            image,metadata=stream.latest();self.assertGreater(metadata['frame_age_seconds'],59)
            self.assertEqual(image.shape,(240,320,3))
            user.IsIconic.return_value=True
            with self.assertRaisesRegex(RuntimeError,'最小化'):stream.latest()
            user.IsIconic.return_value=False;stream.pid=999
            with self.assertRaisesRegex(RuntimeError,'其他进程'):stream.latest()
        mock=Mock();mock.refs=0
        with patch('reader.window_capture._Stream',return_value=mock):
            first,second=WgcSource(99999),WgcSource(99999)
            try:
                first.start();second.start();mock.start.assert_called_once();self.assertIs(first.stream,second.stream)
                first.stop();mock.stop.assert_not_called();second.stop();mock.stop.assert_called_once()
            finally:first.stop();second.stop();_streams.pop(99999,None)

    def test_02_run_transition_manual_fields_inventory_and_undo(self):
        from reader.live_monitor import ObservationGate
        from reader.run_tracking import RunTracker
        first_frame=line_map();first_frame.update(floor=1,template_id='fresh-start',at_start=True,current_slot=[0,0],
            _new_run_candidate=True,source={'captured_at':'frame-1','hwnd':123,'pid':456},captured_at='ocr-1')
        gate=ObservationGate();self.assertFalse(gate.offer(first_frame))
        repeated=deepcopy(first_frame);repeated['captured_at']='ocr-2'
        self.assertFalse(gate.offer(repeated));self.assertEqual(gate.count,1)
        repeated['source']['captured_at']='frame-2';self.assertTrue(gate.offer(repeated))
        tracker=RunTracker();old={'floor':3}
        self.assertEqual(tracker.assess(old,first_frame)[0],'wait')
        duplicate=deepcopy(first_frame);duplicate['captured_at']='another-ocr'
        self.assertEqual(tracker.assess(old,duplicate)[0],'wait')
        self.assertEqual(tracker.assess(old,repeated)[0],'new')
        from reader.live_monitor import LiveMonitor
        from PySide6.QtCore import QThreadPool
        monitor=LiveMonitor(None,QThreadPool());monitor.reset_observations(repeated)
        self.assertFalse(monitor.gate.offer(deepcopy(repeated)))
        with tempfile.TemporaryDirectory() as t:
            session=Session(Path(t)/'session.json');old=line_map();old['floor']=3;session.apply(old);run=session.run['id']
            session.edit_field('floor',1);session.edit_field('life',99);session.edit_node([1,0],'combat','作战')
            for kind in session.inventories:session.manual_inventory(kind,self.catalog.inventory[kind][0],{'quantity':2})
            session.record_event({'event_id':'test-event','event_name':'测试事件','source':{'method':'manual'}})
            first=line_map();first.update(floor=1,template_id='first-floor-start',at_start=True,current_slot=[0,0],captured_at='first')
            hinted=dict(first,floor_hint_used=True);self.assertFalse(session.apply(hinted));self.assertEqual(session.run['id'],run)
            self.assertFalse(session.apply(first));self.assertTrue(session.exploration['events'])
            second=dict(first,captured_at='second');self.assertTrue(session.apply(second));self.assertNotEqual(session.run['id'],run)
            self.assertFalse(session.exploration['events']);self.assertTrue(all(not b['items'] for b in session.inventories.values()))
            self.assertFalse(any(n.get('manual') for n in session.current['nodes']));self.assertNotIn('life',session.current['fields'])
            self.assertTrue(session.undo());self.assertEqual(session.run['id'],run);self.assertTrue(session.exploration['events'])
            self.assertGreater(session.clear_manual(),5);self.assertFalse(session.exploration['events'])
            self.assertTrue(all(not b['items'] for b in session.inventories.values()))
            self.assertFalse(any(n.get('manual') for n in session.current['nodes']))
            session.set_entrance_floor(4);session.clear_manual();self.assertNotIn('entrance_floor',session.exploration)
            previous=deepcopy(session.current.get('current_slot'));session.current['nodes'][2]['resident']=True
            session.set_position([0,0]);self.assertTrue(session.exploration['initial_residents'])
            session.clear_manual();self.assertEqual(session.current.get('current_slot'),previous)
            self.assertFalse(session.exploration['initial_residents'])

    def test_03_event_candidates_delete_repeat_and_inventory_tab(self):
        from reader.exploration_ui import EventPoolPanel
        from reader.inventory_ui import InventoryPanel
        knowledge=ExplorationCatalog()
        with tempfile.TemporaryDirectory() as t:
            session=Session(Path(t)/'session.json');m=line_map();m['floor']=1;session.apply(m)
            once=next(e for e in knowledge.eligible_events(1)['events'] if not e.get('repeatable'))
            repeat=next(e for e in knowledge.eligible_events(1)['events'] if e.get('repeatable'))
            sidebar=EventPoolPanel(session,lambda:None,knowledge);panel=InventoryPanel(session,self.catalog)
            try:
                session.record_event(dict(event_id=once['id'],event_name=once['name'],source={'method':'manual'}));sidebar.refresh()
                self.assertEqual(sidebar.events.findData(once['id']),-1)
                self.assertIn(1,knowledge.event_info(sidebar.events.currentData())['floors'])
                panel.refresh();panel.show_kind('events');self.assertEqual(panel.event_panel.table.rowCount(),1)
                panel.event_panel.table.selectRow(0);panel.remove('events');sidebar.refresh()
                self.assertGreaterEqual(sidebar.events.findData(once['id']),0)
                self.assertFalse(session.record_event(dict(event_id=once['id'],event_name=once['name'])))
                for _ in range(2):self.assertTrue(session.record_event(dict(event_id=repeat['id'],event_name=repeat['name'],source={'method':'manual'})))
                self.assertEqual(len(session.exploration['events']),2)
                options=knowledge.event_options(1,session.exploration['events']);self.assertTrue(options[0]['current'])
                self.assertTrue(any(not e['current'] for e in options))
                self.assertTrue(all(e['floors'] or e['conditional_floors'] for e in options))
                panel.set_scan(dict(inventory_kind='relics',entries=[],notices=[]),follow=False);self.assertEqual(panel.kind,'events')
            finally:panel.close();sidebar.close()

    def test_04_fixed_shops_and_observed_ranges(self):
        self.assertNotIn('诡意行商',self.catalog.count_limits['1'])
        self.assertIn('诡意行商',self.catalog.count_limit_gaps['1'])
        prior=self.catalog.probabilities(1,3,'unknown-mystery')
        self.assertNotIn('诡意行商',[r['name'] for r in prior['estimates']])
        self.catalog.fourth_floor_memory=True
        self.assertEqual(self.catalog.probabilities(4,3,'unknown-mystery'),self.catalog.probabilities('追忆',3,'unknown-mystery'))
        self.catalog.fourth_floor_memory=False
        from reader.resident_tracking import update_resident_constraints
        resident=line_map();resident['floor']=4;resident['nodes'][1]['resident']=True
        resident['nodes'][2].update(type='empty',manual=True,confidence=1.)
        self.assertNotIn([6,0],update_resident_constraints(resident,[[1,0]])['settlement_candidates'])
        resident['fourth_floor_memory']=True
        self.assertIn([6,0],update_resident_constraints(resident,[[1,0]])['settlement_candidates'])
        for template in [t for t in self.catalog.templates if t['floor']==1]:
            source_distances=distances(template)
            result=dict(page_type='map',floor=1,template_id=template['template_id'],needs_review=False,
                start_slot=template['startSlot'],terminal_slots=template['terminalSlots'],edges=template['edges'],fields={},
                nodes=[dict(slot=s,type='unknown-mystery',label='未知的诡秘',distance=source_distances[tuple(s)],confidence=.99,evidence='ocr_label') for s in template['occupiedSlots']])
            refresh_map_probabilities(result,self.catalog)
            shops={tuple(n['slot']) for n in result['nodes'] if n.get('evidence')=='template_fixed_node' and n['type']=='rogue-trader'}
            self.assertEqual(shops,{tuple(n['slot']) for n in template['fixedNodes']})
            before=result['probability_cache']['key'];self.catalog.use_observed_ranges=False;refresh_map_probabilities(result,self.catalog)
            self.assertNotEqual(result['probability_cache']['key'],before)
            for node in result['nodes']:
                if node.get('probabilities'):
                    self.assertAlmostEqual(sum(r['probability'] for r in node['probabilities']['estimates']),1)
                    self.assertFalse(any(r.get('hard_zero') for r in node['probabilities']['estimates']))
            self.catalog.use_observed_ranges=True

    def test_05_real_recent_icons_idempotence_manual_removal_and_empty(self):
        from reader.recent_relics import RecentRelicRecognizer
        recognizer=RecentRelicRecognizer(self.catalog)
        self.assertEqual(recognizer.analyze(np.zeros((100,100,3),np.uint8)),[])
        image=load_image(Path(__file__).parent/'fixtures/map_floor6_sample.png');entries=recognizer.analyze(image)
        accepted={e['name'] for e in entries if not e['needs_review']}
        self.assertEqual(accepted,{'传芳雕版','皇帝的收藏','囊中骨','幸运饼干'})
        with tempfile.TemporaryDirectory() as t:
            session=Session(Path(t)/'session.json');m=line_map();m['recent_relics']=entries;session.apply(m)
            self.assertEqual(len(session.inventories['relics']['items']),4)
            session.apply(m);self.assertEqual(len(session.inventories['relics']['items']),4)
            item=session.inventories['relics']['items'][0];session.manual_inventory('relics',item,excluded=True)
            session.apply(m);self.assertTrue(next(r for r in session.inventories['relics']['items'] if r['id']==item['id'])['excluded'])
            self.assertEqual(recognizer.analyze(image),entries)


if __name__=='__main__':unittest.main()

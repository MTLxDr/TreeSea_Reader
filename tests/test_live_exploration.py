"""Five acceptance groups for live tracking and exploration queries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import cv2
import numpy as np
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication
from reader.catalog import Catalog,ROOT
from reader.exploration import ExplorationCatalog
from reader.live_monitor import ObservationGate,LiveMonitor,LiveTask
from reader.probability import refresh_map_probabilities,known_counts
from reader.recognition import Recognizer,load_image
from reader.resident_tracking import infer_position,update_resident_constraints
from reader.stage_routes import stage_events,placed_units
from reader.state import Session


def line_map():
    return {'page_type':'map','needs_review':False,'floor':2,'template_id':'test-line',
        'start_slot':[0,0],'terminal_slots':[],'current_slot':None,'fields':{},'candidates':[],
        'grid':{'origin':[80,300],'spacing':160},'notices':[],
        'nodes':[{'slot':[i,0],'pixel':[80+160*i,300],'type':'unobserved' if i==0 else 'unknown-ferocity',
            'label':'未识别' if i==0 else '未知的凶戾','evidence':'template_only' if i==0 else 'ocr_label',
            'confidence':0 if i==0 else .99,'manual':False,'distance':i} for i in range(10)],
        'edges':[[[i,0],[i+1,0]] for i in range(9)]}


class LiveExplorationAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.catalog=Catalog();cls.knowledge=ExplorationCatalog()

    def test_01_position_initial_residents_and_probability_constraints(self):
        result=line_map();result['nodes'][1]['resident']=True
        self.assertEqual(infer_position(result),[0,0]);self.assertTrue(result['at_start'])
        rules=update_resident_constraints(result)
        self.assertIn([6,0],rules['settlement_candidates']);self.assertNotIn([7,0],rules['settlement_candidates'])
        duplicate=deepcopy(result);duplicate['nodes'][2].update(type='unobserved',evidence='template_only')
        self.assertIsNone(infer_position(duplicate))
        class Priors:
            distance_counts={};count_limits={}
            def probabilities(self,floor,distance,kind):
                if kind!='unknown-ferocity':return None
                return {'estimates':[{'name':'作战','count':3,'probability':.75},{'name':'居民据点','count':1,'probability':.25}],
                    'document_id':1,'sample_count':4,'conditions':'source','limitation':'source'}
        priors=Priors();repeat=deepcopy(result)
        refresh_map_probabilities(result,priors)
        refresh_map_probabilities(repeat,priors)
        self.assertTrue(repeat['probability_cache']['hit']);self.assertEqual(repeat['nodes'][1]['type'],'empty')
        self.assertEqual(result['nodes'][1]['type'],'empty')
        self.assertEqual(known_counts(result)['林间空地'],1)
        excluded=next(r for r in result['nodes'][7]['probabilities']['estimates'] if r['name']=='居民据点')
        self.assertEqual(excluded['probability'],0)
        # Synthetic positive marker uses the real asset; it does not establish game accuracy.
        region=line_map();image=np.zeros((600,1700,3),np.uint8)
        icon=cv2.imdecode(np.fromfile(ROOT/'assets/nodes/mark-1.png',dtype=np.uint8),cv2.IMREAD_COLOR);icon=cv2.resize(icon,(32,32))
        x,y=map(int,region['nodes'][2]['pixel']);image[y-16:y+16,x-16:x+16]=icon
        Recognizer(self.catalog).locate_overlays(image,[],region)
        self.assertTrue(region['nodes'][2].get('resident'))
        # A resident moves away from an unlabelled node. Its exclusion evidence
        # must survive a save/reload and must invalidate the probability cache.
        with tempfile.TemporaryDirectory() as t:
            session=Session(Path(t)/'session.json');old=line_map()
            old.update(at_start=False,current_slot=[9,0]);old['nodes'][2].update(type='unobserved',evidence='template_only',resident=True)
            session.apply(old)
            moved=line_map();moved.update(at_start=False,current_slot=[8,0])
            moved['nodes'][2].update(type='unobserved',evidence='template_only');moved['nodes'][3]['resident']=True
            session.apply(moved);saved=Session(session.path).current
            self.assertTrue(saved['nodes'][2]['resident_visited']);self.assertFalse(saved['nodes'][2].get('resident'))
            self.assertIn('险路尽头',saved['nodes'][2]['excluded_types'])
            refresh_map_probabilities(saved,Priors())
            self.assertEqual(next(r['probability'] for r in saved['nodes'][2]['probabilities']['estimates'] if r['name']=='居民据点'),0)
            prior= Priors();prior.distance_counts={('2',2):{1:{'作战':3,'居民据点':1,'林间空地':4,'羽瞰点':2}}}
            refresh_map_probabilities(saved,prior)
            rows={r['name']:r['probability'] for r in saved['nodes'][2]['probabilities']['estimates']}
            self.assertAlmostEqual(rows['林间空地'],4/9);self.assertAlmostEqual(rows['羽瞰点'],2/9)
            before=saved['probability_cache']['key'];saved['nodes'][2]['resident_visited']=False
            refresh_map_probabilities(saved,Priors());self.assertNotEqual(before,saved['probability_cache']['key'])
            other=line_map();other['template_id']='different-map';session.apply(other)
            self.assertFalse(any(n.get('resident_visited') for n in session.current['nodes']))

    def test_02_stability_inventory_autosave_and_stop(self):
        result=line_map();gate=ObservationGate()
        self.assertFalse(gate.offer(result));self.assertTrue(gate.offer(result));self.assertFalse(gate.offer(result))
        self.assertFalse(gate.offer({'page_type':'map','needs_review':False,'nodes':[]}))
        changed=deepcopy(result);changed['side_map']={'template_id':'sunset-3-1','nodes':[{'slot':[0,0],'type':'combat'}]}
        self.assertFalse(gate.offer(changed));self.assertTrue(gate.offer(changed))
        with tempfile.TemporaryDirectory() as t:
            session=Session(Path(t)/'session.json')
            for kind in ('operators','relics','parts'):
                item=deepcopy(self.catalog.inventory[kind][0]);item.update(fields={},needs_review=False,confidence=.99)
                scan={'page_type':'inventory','inventory_kind':kind,'context_detected':kind,'ownership_risk':False,
                    'entries':[item],'captured_at':'test','page_fingerprint':kind}
                self.assertTrue(session.auto_inventory(scan));self.assertFalse(session.auto_inventory(scan))
                scan['ownership_risk']=True;self.assertFalse(session.auto_inventory(scan))
            reloaded=Session(session.path)
            self.assertTrue(all(len(b['items'])==1 for b in reloaded.inventories.values()))
            self.assertTrue(all(b['items'][0]['confirmed_by']=='live_owned_page' for b in reloaded.inventories.values()))
            scan['ownership_risk']=False;scan['page_fingerprint']='new-parts-page'
            scan['entries'][0]['fields']={'quantity':{'value':3,'confidence':.99}}
            self.assertTrue(session.auto_inventory(scan))
            scan['page_fingerprint']='weak-parts-page';scan['entries'][0]['fields']['quantity']={'value':8,'confidence':.5}
            self.assertTrue(session.auto_inventory(scan))
            self.assertEqual(session.inventories['parts']['items'][0]['fields']['quantity']['value'],3)
        monitor=LiveMonitor(None,QThreadPool());monitor.running=True;callback=Mock();monitor.changed.connect(callback)
        old=monitor.epoch;monitor.stop();monitor.ready(old,result,np.zeros((400,600,3),np.uint8));callback.assert_not_called()
        monitor.running=True;monitor.gate.offer(result);monitor.gate.offer(result);monitor.last_digest='old-frame'
        old=monitor.epoch;monitor.reset_observations()
        monitor.ready(old,result,None);self.assertIsNone(monitor.last_digest)
        monitor.ready(monitor.epoch,deepcopy(result),None);callback.assert_not_called()
        monitor.ready(monitor.epoch,deepcopy(result),None);callback.assert_called_once()
        # A one-pixel change in an inventory digit must trigger recognition.
        source=Mock();source.latest.return_value=(np.zeros((400,600,3),np.uint8),{})
        recognizer=Mock();recognizer.analyze.return_value={'page_type':'unsupported'}
        seen=[];task=LiveTask(0,recognizer,source,1,{})
        task.signals.ready.connect(lambda epoch,r,image:seen.append(r));task.run()
        source.latest.return_value[0][200,300]=1
        next_task=LiveTask(0,recognizer,source,1,{},seen[0]['_live_fingerprint']);next_task.run()
        self.assertEqual(recognizer.analyze.call_count,2)

    def test_03_event_repeats_remaining_pools_and_undo(self):
        pool=self.knowledge.eligible_events(1);self.assertTrue(pool['events'])
        repeat=next(e for e in pool['events'] if e.get('repeatable'));once=next(e for e in pool['events'] if not e.get('repeatable'))
        remaining=self.knowledge.eligible_events(1,[repeat,once])
        self.assertIn(repeat['name'],[e['name'] for e in remaining['events']]);self.assertNotIn(once['name'],[e['name'] for e in remaining['events']])
        self.assertAlmostEqual(sum(e['probability'] for e in remaining['events']),1)
        self.assertIn('等权',remaining['method'])
        treehole=self.knowledge.eligible_events('treehole',entrance_floor=3)
        self.assertTrue(treehole['reference_only']);self.assertIn('入口楼层参考池',treehole['method'])
        p=self.knowledge.pools()[0];owned=p['collectibles'][0]
        result=self.knowledge.remaining_items(p['id'],{'relics':{'items':[{'id':owned,'confirmed':True}]}})
        self.assertNotIn(owned,result['remaining']);self.assertEqual(len(result['remaining']),len(p['collectibles'])-1)
        self.assertFalse(self.knowledge.eligible_events(None)['events'])
        with tempfile.TemporaryDirectory() as t:
            session=Session(Path(t)/'state.json');event={'event_id':once['id'],'event_name':once['name']}
            self.assertTrue(session.record_event(event));self.assertFalse(session.record_event(event));session.undo()
            self.assertFalse(session.exploration['events'])

    def test_04_every_source_stage_proto_and_explorer_conditions(self):
        index=self.knowledge.stage_index()
        manifest=json.loads((self.knowledge.path.parent/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(index),manifest['expected_stages']);self.assertFalse(manifest['errors'])
        proto_total=npc_total=0
        for info in index:
            stage=self.knowledge.read('stage',info['id']);rows=stage_events(stage,self.knowledge.names)
            units=placed_units(stage,self.knowledge.names)
            self.assertTrue(all(r['name'] for r in rows))
            proto_total+=sum(r['proto'] for r in rows);npc_total+=sum(u['npc'] for u in units)
            self.assertTrue(stage['source']['sha256']);self.assertTrue(stage['level']['mapData']['map'])
        self.assertGreater(proto_total,0);self.assertGreater(npc_total,0)
        real=self.knowledge.read('stage','ro6_n_1_1');proto=[r for r in stage_events(real,self.knowledge.names) if r['proto']]
        self.assertTrue(any(r['delay']==80 and r['hidden_group']=='shsdgo' for r in proto))
        variant=self.knowledge.read('stage','ro6_n_3_1_dlc1');units=[u for u in placed_units(variant,self.knowledge.names) if u['npc']]
        self.assertEqual({u['id'] for u in units},{'trap_344_shadvr','trap_348_shadvr2'})
        self.assertTrue(all(u['position'] and u['conditions'] for u in units))

    def test_05_ui_silent_updates_queries_and_real_fixture(self):
        from reader.app import ReaderWindow,STYLE
        from reader.exploration_ui import RelicPoolsDialog
        self.app.setStyleSheet(STYLE)
        image=load_image(Path(__file__).resolve().parent/'fixtures/map_sample.png')
        recognized=Recognizer(self.catalog).analyze(image,floor_hint=2,template_hint='F2-T05')
        self.assertFalse(recognized['needs_review']);self.assertEqual(recognized['current_slot'],recognized['start_slot'])
        with tempfile.TemporaryDirectory() as t:
            window=ReaderWindow(Path(t)/'session.json',no_hotkeys=True,settings_path=Path(t)/'settings.json')
            try:
                window.present_results=Mock();window.live_ready(recognized,image)
                window.present_results.assert_not_called();self.assertFalse(window.isVisible())
                self.assertEqual(window.session.current['template_id'],'F2-T05')
                # The resource HUD and map summary use the same confirmed floor.
                window.result['fields']['floor']={'value':3};window.render()
                self.assertEqual(window.hud_chips['floor'].text(),'第 2 层')
                window.live_monitor.last_digest='unchanged';window.new_run()
                self.assertIsNone(window.live_monitor.last_digest);self.assertIsNone(window.session.current)
                window.live_ready(recognized,image)
                window.stage_panel.search.setText('预算否决');self.assertGreater(window.stage_panel.selector.count(),0)
                sid=window.stage_panel.selector.findData('ro6_n_3_1_dlc1');window.stage_panel.selector.setCurrentIndex(sid)
                self.assertIn('探险者',window.stage_panel.units.text())
                pools=RelicPoolsDialog(window.session,self.catalog,window);self.assertGreater(pools.table.rowCount(),0);pools.close()
                if os.environ.get('TREESEA_EXPLORATION_PREVIEW'):
                    folder=Path(os.environ['TREESEA_EXPLORATION_PREVIEW']);folder.mkdir(parents=True,exist_ok=True)
                    window.resize(1500,950);window.show();self.app.processEvents();window.grab().save(str(folder/'main.png'))
                    window.tabs.setCurrentWidget(window.stage_panel);self.app.processEvents();window.grab().save(str(folder/'stage.png'))
            finally:window.close();self.app.processEvents()


if __name__=='__main__':unittest.main()

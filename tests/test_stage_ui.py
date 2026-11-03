"""Five acceptance groups for terrain, teleportation and the completed UI paths."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from argparse import Namespace
from datetime import datetime,timezone

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from reader.catalog import ROOT
from reader.exploration import ExplorationCatalog
from reader.stage_tiles import describe_tile
from reader.stage_routes import route_segments,portal_links


def tile(key='tile_road',height='LOWLAND',build='MELEE',mask='ALL'):
    return dict(tileKey=key,heightType=height,buildableType=build,passableMask=mask)


class StageUiAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        from reader.theme import STYLE
        cls.app.setStyleSheet(STYLE);cls.catalog=ExplorationCatalog()

    def test_01_independent_walk_deploy_height_and_unknown(self):
        high=describe_tile(tile('tile_wall','HIGHLAND','RANGED','FLY_ONLY'))
        self.assertEqual(high['category'],'highland');self.assertFalse(high['ground_passable']);self.assertFalse(high['undeployable'])
        wall=describe_tile(tile('tile_forbidden','HIGHLAND','NONE','FLY_ONLY'))
        self.assertEqual(wall['category'],'wall');self.assertTrue(wall['undeployable'])
        fence=describe_tile(tile('tile_fence_bound',build='MELEE',mask='FLY_ONLY'))
        self.assertEqual(fence['category'],'obstacle');self.assertFalse(fence['ground_passable']);self.assertFalse(fence['undeployable'])
        floor=describe_tile(tile('tile_floor',build='NONE'))
        self.assertTrue(floor['ground_passable']);self.assertTrue(floor['undeployable']);self.assertEqual(floor['fill'],fence['fill'])
        self.assertEqual(describe_tile(tile('tile_hole',build='NONE'))['category'],'hole')
        self.assertEqual(describe_tile({})['category'],'unknown')
        for t in (high,wall,fence,floor):self.assertNotIn(t['fill'],{'#65b9ff','#ff6666'})

    def test_02_portal_direction_pairing_and_discontinuous_routes(self):
        from reader.stage_paths import estimate_path
        grid={'mapData':{'map':[[0,0,0],[0,1,0],[0,0,0]],'tiles':[tile(),tile('tile_forbidden','HIGHLAND','NONE','FLY_ONLY')]}}
        path=estimate_path(grid,{'motionMode':'WALK','allowDiagonalMove':True},{'row':1,'col':0},{'row':1,'col':2})
        self.assertTrue(path);self.assertNotIn({'row':1,'col':1},path);self.assertGreater(len(path),3)
        self.assertIsNone(estimate_path(grid,{'motionMode':'UNKNOWN'},{'row':0,'col':0},{'row':2,'col':2}))
        level=self.catalog.read('stage','ro6_b_1')['level'];links=portal_links(level)
        self.assertGreaterEqual(len(links),3)
        self.assertEqual(len({tuple(p['exit'].values()) for p in links}),1)
        route=next(r for r in level['routes'] if any(c['type']=='DISAPPEAR' for c in r.get('checkpoints',[])))
        jumps=[s for s in route_segments(route) if s['kind']=='teleport']
        self.assertEqual(jumps[0]['end'],{'row':3,'col':4})
        self.assertEqual(describe_tile(tile('tile_telin',build='NONE'))['portal_role'],'in')
        self.assertEqual(describe_tile(tile('tile_telout',build='NONE'))['portal_role'],'out')
        self.assertFalse(portal_links({'mapData':{'map':[],'tiles':[]},'routes':[]}))

    def test_03_scene_real_tiles_and_new_workbench(self):
        from reader.app import ReaderWindow
        with tempfile.TemporaryDirectory() as t:
            w=ReaderWindow(Path(t)/'session.json',no_hotkeys=True,settings_path=Path(t)/'settings.json')
            try:
                w.go_page('关卡');panel=w.stage_panel
                for sid in ('ro6_b_1','ro6_b_3','ro6_e_3_7'):
                    index=panel.selector.findData(sid);self.assertGreaterEqual(index,0);panel.selector.setCurrentIndex(index)
                    specs=[i.data(0) for i in panel.scene.items() if isinstance(i.data(0),dict)]
                    self.assertTrue(specs);self.assertTrue(any(s['undeployable'] for s in specs))
                    hatch=[i for i in panel.scene.items() if hasattr(i,'brush') and i.brush().style()==Qt.BrushStyle.BDiagPattern]
                    self.assertEqual(len(hatch),sum(s['undeployable'] for s in specs))
                panel.selector.setCurrentIndex(panel.selector.findData('ro6_b_1'))
                w.resize(1500,950);w.show();self.app.processEvents()
                if os.environ.get('TREESEA_UI_PREVIEW'):
                    folder=Path(os.environ['TREESEA_UI_PREVIEW']);folder.mkdir(parents=True,exist_ok=True);w.grab().save(str(folder/'stage.png'))
                panel.search.setText('no-such-stage');self.assertEqual(panel.table.rowCount(),0)
            finally:w.close();w.deleteLater();self.app.processEvents()

    def test_04_update_rollback_and_real_check_subprocess(self):
        spec=importlib.util.spec_from_file_location('stage_update',ROOT/'scripts/update_data.py');update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)
        with tempfile.TemporaryDirectory() as t:
            update.ROOT=Path(t);db=Path(t)/'data/reader.sqlite3';db.parent.mkdir();db.write_bytes(b'old-public-database')
            def fail(*args,**kwargs):db.write_bytes(b'partly-replaced');return Namespace(returncode=1)
            with patch.object(update.subprocess,'run',side_effect=fail) as runner:
                with self.assertRaises(RuntimeError):
                    with update.preserve_published():update.execute(Namespace(source='lubiao',minimum_age_hours=24,check_only=False,force=True),{'sources':[]},datetime.now(timezone.utc))
                self.assertEqual(db.read_bytes(),b'old-public-database');self.assertEqual(runner.call_count,1)
        from reader.app import ReaderWindow
        with tempfile.TemporaryDirectory() as t:
            w=ReaderWindow(Path(t)/'session.json',no_hotkeys=True,settings_path=Path(t)/'settings.json')
            try:
                settings=w.settings_panel;settings.update_source.setCurrentIndex(settings.update_source.findData('exploration'))
                settings._start_update(True);deadline=time.monotonic()+20
                while settings.process is not None and time.monotonic()<deadline:self.app.processEvents();time.sleep(.03)
                self.assertIsNone(settings.process);self.assertIn('检查完成',settings.update_status.text())
                self.assertTrue(w.reload_data());self.assertFalse(w.data_reload_error)
            finally:w.close();w.deleteLater();self.app.processEvents()

    def test_05_visible_live_feedback_and_calculator_results_first(self):
        from reader.app import ReaderWindow
        from reader.calculation_ui import CalculationWindow
        from test_session_dps import fixture
        with tempfile.TemporaryDirectory() as t:
            w=ReaderWindow(Path(t)/'session.json',no_hotkeys=True,settings_path=Path(t)/'settings.json')
            try:
                w.live_feedback('等待游戏窗口画面');self.assertIn('等待',w.status.text())
                calculator=CalculationWindow();panel=calculator.dps_panel;self.assertFalse(panel.details_toggle.isChecked())
                request=fixture()
                panel.load_document(request);self.assertTrue(panel.details_toggle.isChecked())
                self.assertTrue(panel.run());panel.details_toggle.setChecked(False)
                calculator.resize(1300,900);calculator.show();self.app.processEvents()
                if os.environ.get('TREESEA_UI_PREVIEW'):
                    calculator.grab().save(str(Path(os.environ['TREESEA_UI_PREVIEW'])/'calculator.png'))
                calculator.close();calculator.deleteLater()
            finally:w.close();w.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()

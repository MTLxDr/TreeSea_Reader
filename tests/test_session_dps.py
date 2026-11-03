"""Five focused acceptance groups for the guided finite-window calculation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from reader.calculation import CalculationError
from reader.calculation_api import contract, start_server
from reader.calculation_client import calculate_request
from reader.effect_rules import RuleCatalog
from reader.session_calculation import calculate_session
from reader.session_dps import calculate_session_dps
from reader.skill_timing import finite_window, skill_profile

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    request = json.loads((ROOT/'docs/examples/session_strong_strike.request.json').read_text(encoding='utf-8'))
    request['operation'] = 'session_dps'
    request['selection']['operator_id'] = 'char_208_melan'
    request['session']['inventories']['operators']['items'][0] = {
        'id': 'char_208_melan', 'name': '玫兰莎', 'confirmed': True,
        'fields': {k: {'value': v} for k, v in dict(elite=0, level=40, trust=0, potential=1,
            module_id='none', skill_index=1, skill_level=7, mastery=0).items()}}
    for key in ('enemy_id', 'enemy_level', 'stage_id'):
        request['selection'].pop(key, None)
    return request


class SessionDpsAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = RuleCatalog()

    def test_01_interval_endpoint_and_abnormal_input(self):
        request = {'attacker': {'atk': {'base': 100}, 'attack_speed': {'base': 200}},
                   'hit': {'type': 'physical'}}
        # (1.5 - .5) * 2 / (200/100) = 1s; hits at 0,1,2,3 only.
        result = finite_window(request, duration=4, base_interval=1.5, interval_add=-.5, interval_factor=2)
        self.assertEqual((result['actual_interval'], result['attack_count'], result['skill_average_dps']), (1, 4, 100))
        self.assertEqual(finite_window(request, duration=4, base_interval=1.5, first_hit=4)['attack_count'], 0)
        # 1.05 seconds is 31.5 frames, half-up to 32; [0,3.2) has three hits.
        request['attacker']['attack_speed']['base'] = 100
        framed = finite_window(request, duration=3.2, base_interval=1.05, frame_align=True)
        self.assertEqual(framed['actual_interval'], 32/30)
        self.assertEqual(framed['attack_count'], 3)
        for args in ({'duration': 0, 'base_interval': 1},
                     {'duration': 1, 'base_interval': 1e308, 'interval_factor': 1e308}):
            with self.assertRaises(CalculationError): finite_window(request, **args)
        malformed = fixture();malformed['session']['inventories']['operators']['items'] = [None]
        with self.assertRaises(CalculationError):calculate_session_dps(malformed, self.catalog)

    def test_02_sourced_zero_target_damage_and_immutable_session(self):
        request = fixture();before = deepcopy(request)
        result = calculate_session_dps(request, self.catalog)
        self.assertEqual(request, before)
        self.assertEqual(result['status'], 'complete_for_supported_scope')
        # Source E0L40 ATK583, Emperor relic +15%, skill rank7 +50%, 20s/1.5s.
        self.assertAlmostEqual(result['result']['dph'], 583*1.15*1.5)
        self.assertEqual(result['window_result']['attack_count'], 14)
        self.assertAlmostEqual(result['result']['skill_average_dps'], 583*1.15*1.5*14/20)
        self.assertTrue(result['audit']['sources'])
        self.assertTrue(result['window_result']['source'])
        self.assertTrue(all(s['base']['value'] == 0 for s in result['compiled_request']['defender'].values()))
        missing = deepcopy(request);missing['session']['inventories']['relics']['complete'] = False
        blocked = calculate_session_dps(missing, self.catalog)
        self.assertIsNone(blocked['result']['dph']);self.assertIsNone(blocked['result']['skill_average_dps'])
        missing['allow_partial'] = True
        self.assertIsNotNone(calculate_session_dps(missing, self.catalog)['result']['dph'])

    def test_03_multihit_speed_cap_and_unknown_skill_timing(self):
        core = {'attacker': {'atk': {'base': 100}, 'attack_speed': {'base': 700}}, 'hit': {'type': 'physical'}}
        result = finite_window(core, duration=1, base_interval=6, hits_per_attack=3)
        self.assertEqual((result['attack_speed'], result['effective_attack_speed']), (700, 600))
        self.assertEqual((result['hit_count'], result['skill_average_dps']), (3, 300))
        template = deepcopy(next(e for e in self.catalog.entries.values() if e['category']=='干员技能' and e['context'].get('operator_id')=='char_208_melan'))
        template['raw_effect'] = '攻击变为3连射，攻击间隔缩短(-0.5)，攻击力+50%'
        profile = skill_profile(template)
        self.assertEqual((profile['hits_per_attack'], profile['interval_add']), (3, -.5))
        template['raw_effect'] = '每秒造成100点法术伤害'
        self.assertTrue(skill_profile(template)['timing_gaps'])
        # SilverAsh Power Strike is instantaneous; manual duration cannot invent a sustained cycle.
        instant = json.loads((ROOT/'docs/examples/session_strong_strike.request.json').read_text(encoding='utf-8'))
        instant['operation']='session_dps';instant['allow_partial']=True;instant['selection']['model']={'duration':20}
        result=calculate_session_dps(instant,self.catalog)
        self.assertIsNone(result['window_result']);self.assertTrue(result['timing_missing'])

    def test_04_old_api_and_python_cli_http_schema(self):
        old=json.loads((ROOT/'docs/examples/session_strong_strike.request.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(calculate_session(old,self.catalog)['result']['basic_damage'],648.945)
        request=fixture();python=calculate_request(request);cli=calculate_request(request,'cli')
        self.assertAlmostEqual(python['result']['skill_average_dps'],703.9725)
        server,thread=start_server(0)
        try:http=calculate_request(request,'http',url=f'http://127.0.0.1:{server.server_port}')
        finally:server.shutdown();server.server_close();thread.join(5)
        self.assertEqual(python,cli);self.assertEqual(python,http)
        self.assertTrue(any(branch['properties'].get('operation',{}).get('const')=='session_dps' for branch in contract()['oneOf']))

    def test_05_guided_ui_import_edit_and_export(self):
        from PySide6.QtWidgets import QApplication
        from reader.calculation_ui import CalculationWindow
        app=QApplication.instance() or QApplication([])
        request=fixture();before=deepcopy(request['session'])
        window=CalculationWindow(snapshot_provider=lambda:request['session']);panel=window.dps_panel
        try:
            self.assertIs(window.pages.currentWidget(),panel)
            self.assertFalse(hasattr(panel,'enemy'))
            self.assertTrue(panel.run(),panel.status.text())
            self.assertIn('703.9725',panel.dps_card.text())
            self.assertTrue(panel.dps_card.toolTip());self.assertGreater(panel.trace.topLevelItemCount(),0)
            bundle=json.loads(json.dumps({'request':panel.last_request,'response':panel.last_response}))
            panel.first_hit.setValue(.1)
            self.assertIsNone(panel.last_response);self.assertNotIn('703.9725',panel.dps_card.text())
            panel.load_document(bundle);self.assertTrue(panel.json_mode.isChecked());self.assertTrue(panel.run())
            panel.refresh_snapshot();self.assertFalse(panel.json_mode.isChecked());self.assertIsNone(panel.last_response)
            panel.load_document(before);self.assertTrue(panel.run())
            self.assertEqual(request['session'],before)
            if os.environ.get('TREESEA_DPS_PREVIEW'):
                window.resize(1280,860);window.show();app.processEvents()
                panel.grab().save(os.environ['TREESEA_DPS_PREVIEW'])
        finally:window.close();app.processEvents()


if __name__=='__main__':unittest.main()

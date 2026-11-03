from copy import deepcopy
import json
import unittest

from PySide6.QtWidgets import QApplication
from reader.calculation_ui import CalculationWindow
from test_rule_bridge import example_session_request


class SessionCalculationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.envelope = example_session_request()
        self.snapshot = deepcopy(self.envelope['session'])
        self.window = CalculationWindow(snapshot_provider=lambda: self.snapshot)
        self.panel = self.window.session_panel
        self.panel.json_mode.setChecked(True)
        self.panel.selection_json.setPlainText(json.dumps(self.envelope['selection']))

    def tearDown(self):
        self.window.close(); self.window.deleteLater(); self.app.processEvents()

    def test_confirmed_snapshot_runs_and_trace_reuses_manual_core(self):
        self.assertTrue(self.panel.run())
        self.assertEqual(self.panel.last_response['status'], 'complete_for_supported_scope')
        self.panel.view_trace()
        self.assertAlmostEqual(self.window.panel.last_result['hp_damage'], 648.945)
        self.assertEqual(self.snapshot, self.envelope['session'])
        self.assertIn('本局来源演算', self.window.panel.status.text())

    def test_missing_inputs_block_and_partial_is_visible(self):
        self.snapshot['inventories']['parts']['complete'] = False
        self.panel.set_snapshot(self.snapshot)
        self.panel.selection_json.setPlainText(json.dumps(self.envelope['selection']))
        self.assertTrue(self.panel.run()); self.assertIsNone(self.panel.last_response['result'])
        self.panel.partial.setChecked(True); self.assertTrue(self.panel.run())
        self.assertIn('不完整', self.panel.status.text())
        self.assertTrue(self.panel.last_response['partial'])

    def test_invalid_request_clears_previous_result(self):
        self.assertTrue(self.panel.run())
        self.panel.selection_json.setPlainText('{')
        self.assertFalse(self.panel.run()); self.assertIsNone(self.panel.last_response)
        self.assertEqual(self.panel.output.toPlainText(), '')

    def test_exported_request_bundle_can_be_loaded_without_confirming_state(self):
        self.envelope['context']={'purpose':'round trip'}
        self.panel.load_document({'request':self.envelope,'response':{'ignored':True}})
        self.assertTrue(self.panel.run())
        self.assertEqual(self.panel.last_request['context'],{'purpose':'round trip'})
        self.assertEqual(self.panel.last_request['session'],self.snapshot)
        self.assertAlmostEqual(self.panel.last_response['result']['hp_damage'],648.945)

    def test_form_controls_do_not_require_manual_json_sync(self):
        self.panel.json_mode.setChecked(False)
        self.panel.operator.setCurrentIndex(self.panel.operator.findData('char_172_svrash'))
        self.panel.stage.setCurrentIndex(self.panel.stage.findData('ro6_b_1'))
        self.panel.enemy.setCurrentIndex(self.panel.enemy.findData('enemy_1140_merhd:0'))
        self.panel.difficulty.setValue(0); self.panel.squad.setCurrentIndex(self.panel.squad.findData(None))
        self.panel.skill.setCurrentIndex(self.panel.skill.findData(True)); self.panel.kind.setCurrentIndex(self.panel.kind.findData('physical'))
        self.panel.attack_mode.setCurrentIndex(self.panel.attack_mode.findData('melee'))
        self.assertTrue(self.panel.run())
        self.assertAlmostEqual(self.panel.last_response['result']['hp_damage'], 648.945)


if __name__ == '__main__': unittest.main()

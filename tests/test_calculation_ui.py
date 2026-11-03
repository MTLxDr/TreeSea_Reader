import unittest
from PySide6.QtWidgets import QApplication,QTableWidgetItem
from reader.calculation_ui import CalculationWindow


class CalculationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):self.window=CalculationWindow();self.panel=self.window.panel

    def tearDown(self):self.window.close();self.window.deleteLater();self.app.processEvents()

    def test_named_groups_survive_stat_switches_and_calculate_in_form(self):
        editor=self.panel.attack
        editor.groups.add_group({'group_id':'attack_final','deltas':[.2,.3],'stacking':'add'})
        editor.stat.setCurrentIndex(editor.stat.findData('attack_speed'))
        editor.stat.setCurrentIndex(editor.stat.findData('atk'))
        self.assertEqual(editor.groups.data()[0]['deltas'],[.2,.3])
        self.panel.post_factors.setText('')
        self.panel.post_groups.add_group({'group_id':'arts_bonus','deltas':[.2],'stacking':'add'})
        self.panel.post_groups.add_group({'group_id':'arts_bonus','deltas':[.3],'stacking':'add'})
        self.panel.damage_type.setCurrentIndex(self.panel.damage_type.findData('arts'))
        self.assertTrue(self.panel.run())
        self.assertAlmostEqual(self.panel.last_result['hp_damage'],833*3.25*1.5*1.5)

    def test_element_switch_omits_ignore_and_buildup_has_separate_output(self):
        self.panel.damage_type.setCurrentIndex(self.panel.damage_type.findData('arts'))
        self.panel.hit_fields['res_penetration_flat'].setValue(20)
        self.panel.damage_type.setCurrentIndex(self.panel.damage_type.findData('element'))
        self.assertNotIn('res_penetration_flat',self.panel.form_request()['hit'])
        self.assertFalse(self.panel.hit_fields['res_penetration_flat'].isEnabled())
        self.panel.operation.setCurrentIndex(self.panel.operation.findData('elemental_buildup'))
        self.panel.defense.enabled.setChecked(True)
        self.panel.defense.table.setItem(0,1,QTableWidgetItem('30'))
        self.panel.buildup_amount.setValue(1000);self.panel.buildup_hits.setValue(2)
        self.assertTrue(self.panel.run())
        self.assertEqual(self.panel.last_result['total_buildup'],1400)
        self.assertNotIn('hp_damage',self.panel.last_result)
        self.assertIn('损伤累积',self.panel.summary.text())

    def test_numeric_tooltips_show_formulas_and_source_names(self):
        self.assertTrue(self.panel.run())
        self.assertIn('833',self.panel.white_card.toolTip())
        self.assertIn('真银斩',self.panel.white_card.toolTip())
        self.assertIn('领袖',self.panel.white_card.toolTip())
        root=self.panel.trace.topLevelItem(0)
        self.assertIn('真银斩',root.toolTip(2))
        found=[]
        def visit(item):
            found.append(item.text(0))
            for index in range(item.childCount()):visit(item.child(index))
        visit(root)
        self.assertTrue(any('技能 · 真银斩' in text for text in found))

    def test_labelled_operands_round_trip_through_attribute_switch(self):
        editor=self.panel.attack
        before=editor.data()['atk']
        editor.stat.setCurrentIndex(editor.stat.findData('res'))
        editor.stat.setCurrentIndex(editor.stat.findData('atk'))
        self.assertEqual(editor.data()['atk'],before)


if __name__=='__main__':unittest.main()

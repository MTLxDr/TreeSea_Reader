import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from collect_operator_details import extract
from collect_node_limits import extract as extract_limits
from reader.catalog import Catalog


class DetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pages=json.loads((ROOT/'data/sources/prts/operator_details_probe.json').read_text(encoding='utf-8'))['query']['pages']
        cls.text=next(p['revisions'][0]['slots']['main']['content'] for p in pages if p['title']=='银灰')

    def test_all_skill_levels_and_nested_numbers_are_preserved(self):
        data=extract(self.text)
        self.assertEqual(len(data['components']['skills']),3)
        self.assertTrue(all(len(skill['levels'])==10 for skill in data['components']['skills']))
        self.assertIn('{{',data['components']['skills'][2]['levels'][-1]['description'])
        self.assertEqual(data['components']['skills'][2]['levels'][-1]['mastery'],3)

    def test_talent_potential_module_and_notes_are_retained(self):
        data=extract(self.text)
        self.assertGreaterEqual(len(data['components']['talents']),2)
        self.assertIn('潜能6',data['components']['potentials'][0]['fields'])
        self.assertTrue(any('天赋3' in row['fields'] for row in data['components']['modules']))
        self.assertIn('==',data['sections']['模组'])

    def test_complete_catalog_details_are_available(self):
        c=Catalog()
        self.assertEqual(len(c.detail_index),444)
        data=c.operator_detail('char_172_svrash')
        self.assertEqual(len(data['components']['skills']),3)

    def test_floor_limits_match_captured_source(self):
        data=extract_limits((ROOT/'data/sources/lubiao/inspection/qM9KoKUi.js').read_text(encoding='utf-8'))
        row=next(r for r in data['rows'] if r['label']=='紧急作战')
        self.assertEqual(row['values'][0],[0,1])
        self.assertIn('追忆',data['floors'])


if __name__=='__main__':unittest.main()

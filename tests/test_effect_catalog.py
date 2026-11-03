import importlib.util
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import database
from build_effect_catalog import prose_notes,annotations


class EffectCatalogTests(unittest.TestCase):
    def test_nested_skill_templates_are_removed_without_losing_notes(self):
        text='技能说明\n{{技能2|技能名=测试|技能7描述={{color|red|攻击+20%}}}}\n实际机制：仅对地面单位生效。'
        result=prose_notes(text)
        self.assertNotIn('技能7描述',result)
        self.assertIn('仅对地面单位生效',result)

    def test_percent_wording_alone_does_not_invent_a_bucket(self):
        self.assertEqual(annotations('攻击力+20%',{}),[])
        records=annotations('攻击力+20%',{'semanticEffect':{'lines':[{'label':'攻击力','value':'+20%','tooltip':'藏品符文：乘算'}]}})
        self.assertEqual(records[0]['bucket'],'relic_mul')
        self.assertEqual(records[0]['value'],.2)

    def test_second_source_copy_cannot_double_current_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            db=database.connect(Path(folder)/'test.sqlite3')
            p=Path(folder)/'page.json';p.write_text(json.dumps({'title':'沉沦者的黑流树海/拟造物质编目','wikitext':'x',
                'structured_data':{'templates':[{'template':'收藏品','fields':{'ID':'1','iconId':'same','名称':'测试'}}]}}),encoding='utf-8')
            for provider in ('prts','prts_combat'):
                database.add_document(db,p,provider=provider,url='https://example.invalid/page',acquisition='live')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM current_prts_catalog').fetchone()[0],1)
            db.close()

    def test_inventory_has_explicit_pending_rules_and_full_source_links(self):
        with closing(sqlite3.connect(database.DEFAULT_DB.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            count=db.execute('SELECT count(*) FROM effect_entries').fetchone()[0]
            self.assertGreater(count,20000)
            self.assertEqual(db.execute('SELECT count(*) FROM effect_entries WHERE execution_status="not_compiled"').fetchone()[0]>0,True)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            summary=json.loads(db.execute("SELECT value FROM database_meta WHERE key='effect_catalog_summary'").fetchone()[0])
            self.assertTrue(summary['source_coverage_complete'])
            self.assertEqual(summary['entry_count'],count)


if __name__=='__main__':unittest.main()

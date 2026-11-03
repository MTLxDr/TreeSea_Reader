from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reader.calculation import calculate,calculate_attribute,decode_game_final_scalar,CalculationError
from reader.calculation_api import parse_json,contract


def request(kind='physical',atk=1000,defense=300,res=30):
    return {'attacker':{'atk':{'base':atk}},'defender':{'def':{'base':defense},'res':{'base':res}},'hit':{'type':kind}}


class CalculationTests(unittest.TestCase):
    def test_four_buckets_and_relic_rune_example(self):
        data={'base':500,'relic_mul_delta':[.2],'direct_add':[50],'direct_mul_delta':[.5,.2],
              'final_add':[100],'final_factors':[.8,1.1]}
        result=calculate_attribute(data,'atk')
        self.assertAlmostEqual(result['value'],1060.4)
        by_step={r['step']:r['after'] for r in result['trace']}
        for step,expected in [('relic_runes',600),('direct_add',650),('direct_mul',1105),('final_add',1205)]:
            self.assertAlmostEqual(by_step[step],expected)

    def test_stage_factors_multiply_but_relic_rates_add(self):
        self.assertAlmostEqual(calculate_attribute({'base':1000,'stage_factors':[1.2,1.3]})['value'],1560)
        self.assertAlmostEqual(calculate_attribute({'base':1000,'relic_mul_delta':[.2,.3]})['value'],1500)

    def test_physical_and_arts_basics(self):
        self.assertEqual(calculate(request())['hp_damage'],700)
        self.assertEqual(calculate(request('arts'))['hp_damage'],700)

    def test_attribute_buffs_happen_before_defense(self):
        r=request(atk=100,defense=50);r['attacker']['atk']['direct_mul_delta']=[.5]
        self.assertEqual(calculate(r)['hp_damage'],100)
        r=request(atk=100,defense=50);r['hit']['post_scale_factors']=[1.5]
        self.assertEqual(calculate(r)['hp_damage'],75)

    def test_penetration_flat_then_percentage(self):
        r=request(defense=500);r['hit'].update(def_penetration_flat=100,def_penetration_ratio=.5)
        self.assertEqual(calculate(r)['hp_damage'],800)
        r=request('arts',res=60);r['hit'].update(res_penetration_flat=10,res_penetration_ratio=.5)
        self.assertEqual(calculate(r)['hp_damage'],750)

    def test_five_percent_floor_then_post_damage_reduction(self):
        r=request(atk=100,defense=10000);r['hit']['post_scale_factors']=[.5]
        self.assertEqual(calculate(r)['hp_damage'],2.5)
        self.assertEqual(calculate(request('arts',atk=100,res=100))['hp_damage'],5)

    def test_post_factors_in_complete_review_example(self):
        r=request();r['attacker']['atk']={'base':500,'relic_mul_delta':[.2],'direct_add':[50],
            'direct_mul_delta':[.5,.2],'final_add':[100],'final_factors':[.8,1.1]}
        r['hit']['post_scale_factors']=[1.2,.75]
        self.assertAlmostEqual(calculate(r)['hp_damage'],684.36)
        r['hit']['type']='arts'
        self.assertAlmostEqual(calculate(r)['hp_damage'],668.052)

    def test_shield_is_shared_across_hits_and_barriers_are_separate(self):
        r=request(atk=200,defense=0);r['hit'].update(hits=2,shield=250)
        self.assertEqual(calculate(r)['hp_damage'],150)
        r['hit'].update(barrier_charges=1,shield=50)
        result=calculate(r)
        self.assertEqual(result['barrier_blocked_damage'],200)
        self.assertEqual(result['hp_damage'],150)

    def test_true_and_element_resistance_are_distinct(self):
        self.assertEqual(calculate(request('true',defense=10000,res=100))['hp_damage'],1000)
        r=request('element',res=100);r['defender']['element_res']={'base':30}
        self.assertEqual(calculate(r)['hp_damage'],700)

    def test_immune_does_not_spend_barrier(self):
        r=request();r['hit'].update(immune=True,barrier_charges=2)
        result=calculate(r);self.assertEqual(result['hp_damage'],0);self.assertEqual(result['barrier_remaining'],2)

    def test_rate_clamp_and_encoded_factor_adapter(self):
        self.assertEqual(calculate_attribute({'base':100,'direct_mul_delta':[-2]})['value'],0)
        self.assertAlmostEqual(decode_game_final_scalar(-.7),.3)
        self.assertEqual(decode_game_final_scalar(1.2),1.2)
        with self.assertRaises(CalculationError):calculate_attribute({'base':100,'final_factors':[-.7]})

    def test_single_attribute_export_contract_and_speed(self):
        result=calculate({'operation':'attribute','stat':'attack_speed','attribute':{'base':100,'direct_add':[50]}})
        self.assertEqual(result['value'],150)
        self.assertEqual(result['approval_status'],'source_aligned')
        r=request();r['attacker']['attack_speed']={'base':200};r['timing']={'base_interval':2}
        self.assertEqual(calculate(r)['timing']['actual_interval'],1)

    def test_invalid_and_ambiguous_inputs_are_rejected(self):
        for spec in [{'base':True},{'base':float('nan')},{'base':5,'direct_mul':[.5]},
                     {'base':100,'maximum':10,'minimum':20},{'base':1,'stage_factors':['1.2']}]:
            with self.assertRaises(CalculationError):calculate_attribute(spec)
        with self.assertRaises(CalculationError):parse_json('{"a":1,"a":2}')
        with self.assertRaises(CalculationError):parse_json('{"a":NaN}')
        r=request();r['hit']['res_penetration_ratio']=2
        with self.assertRaises(CalculationError):calculate(r)

    def test_calculation_does_not_mutate_request(self):
        r=request();before=deepcopy(r);calculate(r);self.assertEqual(r,before)

    def test_same_named_groups_add_before_different_groups_multiply(self):
        r=request('arts',atk=1000,res=0)
        r['hit']['post_scale_groups']=[{'group_id':'arts_bonus','deltas':[.2],'stacking':'add'},
            {'group_id':'arts_bonus','deltas':[.3],'stacking':'add'},{'group_id':'vulnerability','deltas':[.1]}]
        result=calculate(r)
        self.assertAlmostEqual(result['hp_damage'],1650)
        rows=[v for v in result['trace'] if v['step'].startswith('post_group_')]
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['components']['deltas'],[.2,.3])
        self.assertEqual(rows[0]['components']['factor'],1.5)

    def test_named_final_attribute_groups_preserve_mechanic_identity(self):
        groups=[{'group_id':'same','deltas':[.2,.3],'stacking':'add'},{'group_id':'other','deltas':[.1]}]
        spec={'base':100,'final_add':[10],'final_groups':groups,'final_factors':[.5]}
        before=deepcopy(spec)
        self.assertAlmostEqual(calculate_attribute(spec)['value'],90.75)
        self.assertEqual(spec,before)

    def test_group_stacking_rules_and_conflicts(self):
        for rule,expected in [('add',150),('max',130),('min',120),('multiply',156)]:
            spec={'base':100,'final_groups':[{'group_id':'g','deltas':[.2,.3],'stacking':rule}]}
            self.assertAlmostEqual(calculate_attribute(spec)['value'],expected)
        for groups in [[{'group_id':'g','deltas':[.2]},{'group_id':'g','stacking':'max','deltas':[.3]}],
                       [{'group_id':'','deltas':[.2]}],[{'group_id':' g','deltas':[.2]}],
                       [{'group_id':'g','deltas':[]}],[{'group_id':'g','deltas':[True]}],
                       [{'group_id':'g','deltas':[.2],'stacking':[]}]]:
            with self.assertRaises(CalculationError):calculate_attribute({'base':100,'final_groups':groups})

    def test_res_overflow_is_capped_before_ignore_not_before_ordinary_reduction(self):
        r=request('arts',atk=1000)
        r['defender']['res']={'base':90,'direct_add':[50]}
        r['hit']['res_penetration_flat']=20
        result=calculate(r)
        self.assertEqual(result['attributes']['defender']['res']['value'],100)
        self.assertAlmostEqual(result['hp_damage'],200)
        r['hit'].pop('res_penetration_flat')
        r['defender']['res']['direct_add'].append(-20)
        self.assertEqual(calculate(r)['hp_damage'],50)
        r['hit']['res_penetration_ratio']=.5
        self.assertEqual(calculate(r)['hp_damage'],500)

    def test_element_damage_uses_element_res_not_buildup_res_and_has_no_ignore(self):
        r=request('element',atk=1000,res=99)
        r['defender'].update(element_res={'base':30},ep_resistance={'base':100})
        self.assertEqual(calculate(r)['hp_damage'],700)
        for key in ['res_penetration_flat','res_penetration_ratio','def_penetration_flat','def_penetration_ratio']:
            with self.subTest(key=key):
                invalid=deepcopy(r);invalid['hit'][key]=.2
                with self.assertRaises(CalculationError):calculate(invalid)

    def test_elemental_buildup_is_an_independent_resistance_channel(self):
        r={'operation':'elemental_buildup','amount':1000,'defender':{'ep_resistance':{'base':30}},
           'post_scale_groups':[{'group_id':'buildup','deltas':[.2,.3],'stacking':'add'}],'hits':2}
        result=calculate(r)
        self.assertAlmostEqual(result['buildup_per_hit'],1050)
        self.assertAlmostEqual(result['total_buildup'],2100)
        self.assertNotIn('hp_damage',result)
        r['defender']['ep_resistance']['base']=100
        r['post_scale_groups']=[]
        self.assertEqual(calculate(r)['buildup_per_hit'],50)
        r['res_penetration_flat']=10
        with self.assertRaises(CalculationError):calculate(r)

    def test_version_compatibility_and_invalid_version_types(self):
        for version in ['1.0','1.1','1.2']:
            r=request();r['schema_version']=version
            self.assertEqual(calculate(r)['schema_version'],'1.2')
        for version in ['2.0',None,[],True]:
            r=request();r['schema_version']=version
            with self.assertRaises(CalculationError):calculate(r)

    def test_cli_json_is_exportable_and_errors_are_structured(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'input.json';output=Path(folder)/'output.json';p.write_text(json.dumps(request()),encoding='utf-8')
            run=subprocess.run([sys.executable,str(root/'scripts/calculate.py'),'--input',str(p),'--output',str(output)],capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['hp_damage'],700)
            p.write_text('{"invalid":1}',encoding='utf-8')
            run=subprocess.run([sys.executable,str(root/'scripts/calculate.py'),'--input',str(p)],capture_output=True)
            self.assertEqual(run.returncode,2)
            self.assertIn('error',json.loads(run.stdout))


if __name__=='__main__':unittest.main()

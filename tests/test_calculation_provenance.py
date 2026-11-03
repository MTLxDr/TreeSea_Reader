from copy import deepcopy
import unittest
from reader.calculation import calculate,calculate_attribute,CalculationError


def term(value,kind,name):return {'value':value,'source':{'kind':kind,'name':name}}


class ProvenanceTests(unittest.TestCase):
    def test_prts_final_add_precedes_final_scaler(self):
        spec={'base':term(1000,'base','测试面板'),'direct_mul_delta':[term(2,'skill','真银斩'),term(-.4,'enemy','挠伤')],
              'final_add':[term(500,'skill','战斗之歌')],'final_factors':[term(.6,'enemy','寸劲')]}
        result=calculate_attribute(spec,'atk')
        self.assertEqual(result['value'],1860)
        self.assertNotEqual(result['value'],1000*(1+2-.4)*.6+500)
        rows=result['trace'];names=[row['step'] for row in rows]
        self.assertLess(names.index('final_add'),names.index('final_mul'))
        final_add=next(row for row in rows if row['step']=='final_add')
        self.assertIn('2600 + 500 = 3100',final_add['formula'])
        self.assertEqual(final_add['contributors'][0]['source']['name'],'战斗之歌')

    def test_attack_up_changes_white_value_but_attack_ratio_does_not(self):
        base={'attacker':{'atk':{'base':1000}},'defender':{'def':{'base':500}},'hit':{'type':'physical'}}
        skill=deepcopy(base);skill['attacker']['atk']['direct_mul_delta']=[term(1,'skill','攻击力+100%')]
        coefficient=deepcopy(base);coefficient['hit']['attack_scale']=term(2,'skill','造成攻击力200%的伤害')
        post=deepcopy(base);post['hit']['post_scale_factors']=[term(2,'relic','伤害提升100%')]
        self.assertEqual(calculate(skill)['white_values']['attacker']['atk'],2000)
        result=calculate(coefficient)
        self.assertEqual(result['white_values']['attacker']['atk'],1000)
        self.assertEqual(result['hp_damage'],1500)
        self.assertEqual(calculate(post)['hp_damage'],1000)
        self.assertEqual(result['trace'][0]['contributors'][0]['source']['name'],'造成攻击力200%的伤害')

    def test_group_selection_retains_suppressed_sources(self):
        r={'attacker':{'atk':{'base':100}},'hit':{'post_scale_groups':[{'group_id':'vulnerability','stacking':'max',
            'deltas':[term(.2,'talent','弱效果'),term(.3,'skill','强效果')]}]}}
        before=deepcopy(r);result=calculate(r)
        self.assertEqual(result['hp_damage'],130)
        row=next(row for row in result['trace'] if row['step']=='post_group_vulnerability')
        self.assertEqual([term['applied'] for term in row['contributors']],[False,True])
        self.assertIn('max(0, 1 + max(0.2, 0.3))',row['formula'])
        self.assertEqual(r,before)

    def test_source_aligned_default_and_explicit_legacy_compatibility(self):
        r={'attacker':{'atk':{'base':100}},'hit':{'post_scale_groups':[{'group_id':'g','deltas':[.2,.3]}]}}
        self.assertAlmostEqual(calculate(r)['hp_damage'],156)
        r['schema_version']='1.1';old=calculate(r)
        self.assertEqual(old['hp_damage'],150)
        self.assertIn('compatibility_notes',old)

    def test_every_numeric_step_has_formula_and_unlabelled_terms_stay_manual(self):
        result=calculate({'attacker':{'atk':{'base':100,'direct_add':[50]}},'hit':{'attack_scale_groups':[{
            'group_id':'skill_scale','stacking':'add','deltas':[term(.2,'skill','比例甲'),term(.3,'talent','比例乙')]}]}})
        for row in result['attributes']['attacker']['atk']['trace']+result['trace']:self.assertTrue(row['formula'])
        self.assertEqual(result['attributes']['attacker']['atk']['trace'][0]['contributors'][0]['source']['kind'],'manual')
        self.assertIn('0.2 + 0.3',result['trace'][0]['formula'])

    def test_invalid_contribution_is_rejected(self):
        for value in [{'value':True},{'value':1,'source':None},{'value':1,'source':{'kind':'skill','name':''}},
                      {'value':1,'source':{'kind':[],'name':'x'}},{'value':1,'source':{'kind':'skill','name':'x','activate':True}}]:
            with self.assertRaises(CalculationError):calculate_attribute({'base':value})

    def test_tagged_timing_and_floor_keep_their_contributors(self):
        r={'attacker':{'atk':{'base':1000},'attack_speed':{'base':200}},'defender':{'def':{'base':300}},
           'hit':{'min_damage_ratio':term(.05,'map','基础伤害下限')},'timing':{'base_interval':term(2,'base','测试攻击间隔')}}
        result=calculate(r)
        self.assertEqual(result['timing']['actual_interval'],1)
        self.assertEqual(result['timing']['theoretical_dps_before_absorption'],700)
        self.assertEqual(result['timing']['contributors'][0]['source']['name'],'测试攻击间隔')
        mitigation=next(row for row in result['trace'] if row['step']=='mitigation')
        self.assertIn('基础伤害下限',[v['source']['name'] for v in mitigation['contributors']])


if __name__=='__main__':unittest.main()

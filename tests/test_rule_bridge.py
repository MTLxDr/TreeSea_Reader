from copy import deepcopy
import json
import unittest
from urllib.request import urlopen

from reader.calculation import CalculationError, calculate
from reader.calculation_api import contract, start_server
from reader.calculation_client import calculate_request
from reader.effect_rules import RuleCatalog, compile_entry, apply_rules
from reader.session_calculation import calculate_session, compile_session


def example_session_request():
    fields = {k: {'value': v, 'manual': True} for k, v in {'elite': 0, 'level': 1,
        'trust': 0, 'potential': 1, 'module_id': 'none', 'skill_index': 1,
        'skill_level': 1, 'mastery': 0}.items()}
    session = {'schema_version': 2, 'inventories': {kind: {'items': [], 'complete': True}
        for kind in ('operators', 'relics', 'parts')}}
    session['inventories']['operators']['items'] = [{'id': 'char_172_svrash', 'name': '银灰',
        'confirmed': True, 'fields': fields}]
    session['inventories']['relics']['items'] = [{'id': 'rogue_6_relic_legacy_15', 'confirmed': True,
        'fields': {'quantity': {'value': 1}}}]
    return {'schema_version': '1.2', 'operation': 'session_damage', 'session': session,
        'selection': {'operator_id': 'char_172_svrash', 'stage_id': 'ro6_b_1',
            'enemy_id': 'enemy_1140_merhd', 'enemy_level': 0, 'difficulty': 0,
            'squad_id': None, 'damage_type': 'physical', 'skill_active': True, 'attack_mode':'melee'}}


class RuleCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.catalog = RuleCatalog()

    def test_rune_and_tooltip_are_one_contribution_with_both_evidence(self):
        entry = self.catalog.select('rogue_6_relic_legacy_12')
        compiled = compile_entry(entry)
        self.assertEqual(compiled['status'], 'compiled')
        self.assertEqual(len(compiled['rules']), 1)
        r = compiled['rules'][0]
        self.assertEqual((r['value'], r['stage'], r['unit']), (.15, 'relic_mul_delta', 'ratio_delta'))
        self.assertTrue(r['evidence']['corroborating_tooltips'])
        for k in ('source_sha256', 'applicable_version', 'group_id', 'stacking', 'target', 'conditions'):
            self.assertIn(k, r)

    def test_conflicting_tooltip_is_not_silently_overridden(self):
        entry = deepcopy(self.catalog.select('rogue_6_relic_legacy_12'))
        entry['modifiers'][0]['value'] = .99
        compiled = compile_entry(entry)
        self.assertEqual(compiled['rules'], [])
        self.assertEqual(compiled['unsupported'][0]['code'], 'conflicting_evidence')

    def test_unknown_selector_and_parameters_do_not_become_unconditional(self):
        entry = deepcopy(self.catalog.select('rogue_6_relic_legacy_12'))
        for key in ('selector.unknown', 'duration'):
            copy = deepcopy(entry)
            copy['source_parameters']['topicBuffs'][0]['blackboard'].append({'key': key, 'value': 2})
            self.assertFalse(compile_entry(copy)['rules'])
        entry['source_parameters']['topicBuffs'][0]['blackboard'].append({'key': 'selector.profession', 'valueStr': 'new_class'})
        self.assertFalse(compile_entry(entry)['rules'])

    def test_conditional_rune_remainder_remains_visible(self):
        compiled = compile_entry(self.catalog.select('rogue_6_relic_artifact_7'))
        self.assertEqual(compiled['status'], 'partial')
        self.assertEqual(len(compiled['rules']), 1)
        self.assertTrue(any('global_buff_layer' in r['detail'] for r in compiled['unsupported']))

    def test_no_natural_language_bucket_guess(self):
        entry = deepcopy(self.catalog.select('rogue_6_relic_legacy_12'))
        entry.update(raw_effect='攻击力+20%', modifiers=[], source_parameters={})
        self.assertEqual(compile_entry(entry)['status'], 'unsupported')

    def test_attack_scale_is_not_white_value_or_post_damage(self):
        request = example_session_request()
        result = calculate_session(request, self.catalog)
        self.assertAlmostEqual(result['result']['white_values']['attacker']['atk'], 341.55)
        self.assertAlmostEqual(result['result']['basic_damage'], 648.945)
        r = next(r for r in result['audit']['rules'] if r['stage'] == 'attack_scale_groups')
        self.assertEqual(r['stacking'], 'multiply')
        self.assertEqual(r['unit'], 'ratio_delta')

    def test_inactive_revision_and_ambiguous_variants_rejected(self):
        with self.assertRaises(CalculationError): self.catalog.compile('missing-old-revision')
        with self.assertRaises(CalculationError): self.catalog.select('char_172_svrash:skills:0')

    def test_coverage_partials_are_separate_from_compiled_entries(self):
        coverage = self.catalog.coverage()
        self.assertEqual(sum(coverage['entry_status'].values()), coverage['active_source_entries'])
        self.assertGreater(coverage['entry_status']['unsupported'], 10000)
        self.assertGreater(coverage['entry_status']['partial'], 0)


class SessionBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.catalog = RuleCatalog()

    def setUp(self): self.request = example_session_request()

    def calculate(self): return calculate_session(self.request, self.catalog)

    def operator_field(self, name, value):
        self.request['session']['inventories']['operators']['items'][0]['fields'][name] = {'value': value}

    def test_real_catalog_hand_calculation_and_inputs_untouched(self):
        before = deepcopy(self.request); result = self.calculate()
        self.assertEqual(result['status'], 'complete_for_supported_scope')
        self.assertFalse(result['partial'])
        self.assertAlmostEqual(result['result']['hp_damage'], 297 * 1.15 * 1.9)
        self.assertEqual(self.request, before)
        self.assertEqual(calculate(result['compiled_request'])['trace'], result['result']['trace'])

    def test_profession_mismatch_keeps_suppressed_rules_and_sources(self):
        self.request['session']['inventories']['relics']['items'][0]['id'] = 'rogue_6_relic_book_7'
        result = self.calculate()
        self.assertAlmostEqual(result['result']['hp_damage'], 297 * 1.9)
        suppressed = [r for r in result['audit']['rules'] if not r['applied']]
        self.assertEqual(len(suppressed), 2)
        self.assertTrue(all('profession' in r['reason'] for r in suppressed))

    def test_unknown_profession_is_missing_not_mismatch(self):
        catalog = deepcopy(self.catalog); catalog.profiles['char_172_svrash'].pop('data-profession')
        self.request['session']['inventories']['relics']['items'][0]['id'] = 'rogue_6_relic_book_7'
        result = calculate_session(self.request, catalog)
        self.assertIsNone(result['result'])
        self.assertTrue(any(r['code'] == 'missing_selector' for r in result['audit']['missing']))

    def test_incomplete_inventory_blocks_default_and_partial_is_explicit(self):
        self.request['session']['inventories']['relics']['complete'] = False
        result = self.calculate(); self.assertIsNone(result['result']); self.assertEqual(result['status'], 'incomplete')
        self.request['allow_partial'] = True
        result = self.calculate(); self.assertTrue(result['partial']); self.assertIsNotNone(result['result'])

    def test_unconfirmed_or_manually_removed_inventory_not_activated(self):
        row = self.request['session']['inventories']['relics']['items'][0]
        row['confirmed'] = False; result = self.calculate(); self.assertIsNone(result['result'])
        self.request['allow_partial'] = True
        self.assertAlmostEqual(self.calculate()['result']['hp_damage'], 297 * 1.9)
        row.update(confirmed=True, excluded=True)
        result = self.calculate(); self.assertTrue(any(r['reason'] == '人工移除' for r in result['audit']['excluded']))

    def test_dynamic_unknown_mechanic_is_not_zero(self):
        self.request['session']['inventories']['relics']['items'][0]['id'] = 'rogue_6_relic_artifact_7'
        result = self.calculate(); self.assertIsNone(result['result']); self.assertTrue(result['audit']['unsupported'])

    def test_source_duplicates_and_multiple_copies_are_not_stacked(self):
        row = self.request['session']['inventories']['relics']['items'][0]
        row['fields']['quantity']['value'] = 2
        self.assertIsNone(self.calculate()['result'])
        row['fields']['quantity']['value'] = 1
        self.request['session']['inventories']['relics']['items'].append(deepcopy(row))
        with self.assertRaises(CalculationError): self.calculate()
        self.request['session']['inventories']['relics']['items'][-1]['id'] = self.catalog.select(row['id'])['id']
        with self.assertRaises(CalculationError): self.calculate()

    def test_panel_included_potential_and_trust_are_not_added_twice(self):
        self.operator_field('potential', 4); self.operator_field('trust', 200)
        self.request['selection'].update(skill_active=False, operator_panel={
            'stats': {'atk': 1000, 'def': 500, 'max_hp': 3000, 'res': 10, 'attack_speed': 100},
            'included': ['trust', 'potential', 'module']})
        self.request['session']['inventories']['relics']['items'] = []
        result = self.calculate()
        self.assertEqual(result['result']['hp_damage'], 1000)
        self.assertFalse(result['compiled_request']['attacker']['atk'].get('base_add'))
        self.assertTrue(result['audit']['excluded'])

    def test_unknown_middle_level_not_interpolated(self):
        self.operator_field('level', 20)
        result = self.calculate(); self.assertIsNone(result['result'])
        self.assertIn('unsupported_level_interpolation', [r['code'] for r in result['audit']['missing']])

    def test_panel_excluding_trust_adds_it_once_and_included_unknown_trust_is_allowed(self):
        self.operator_field('trust', 200)
        self.request['selection'].update(skill_active=False, operator_panel={'stats': {'atk': 1000}, 'included': ['potential', 'module']})
        self.request['session']['inventories']['relics']['items'] = []
        self.assertEqual(self.calculate()['result']['hp_damage'], 1050)
        self.operator_field('trust', None)
        self.request['selection']['operator_panel']['included'].append('trust')
        self.assertEqual(self.calculate()['result']['hp_damage'], 1000)

    def test_enemy_is_stage_and_level_specific_and_difficulty_not_ignored(self):
        self.request['selection']['enemy_level'] = 99
        self.assertIsNone(self.calculate()['result'])
        self.request['selection']['enemy_level'] = 0; self.request['selection']['difficulty'] = 15
        self.assertIn('unsupported_difficulty', [r['code'] for r in self.calculate()['audit']['missing']])

    def test_trait_requires_attack_context_and_unknown_is_not_zero(self):
        self.request['selection'].pop('attack_mode')
        self.assertIn('missing_attack_mode', [r['code'] for r in self.calculate()['audit']['missing']])
        self.request['selection']['attack_mode']='ranged'
        self.assertIsNone(self.calculate()['result'])

    def test_enemy_ability_fields_are_not_silently_ignored(self):
        from copy import copy
        catalog=copy(self.catalog);catalog.entries=dict(self.catalog.entries)
        entry=deepcopy(next(e for e in catalog.entries.values() if e['category']=='敌人技能与天赋' and e['name']=='战术猎犬'))
        entry['source_parameters']['fields']['能力']='测试护盾机制'
        catalog.entries[entry['id']]=entry
        result=calculate_session(self.request,catalog)
        self.assertIsNone(result['result'])
        self.assertIn('enemy_mechanics',[r['code'] for r in result['audit']['unsupported']])

    def test_enemy_elemental_channels_match_nonzero_prts_source_fields(self):
        entry=next(e for e in self.catalog.entries.values() if e['category']=='敌人关卡参数'
                   and e['name']=='转译基底·α' and e['context']['attributes'].get('epResistance')==10)
        stage,enemy,level=entry['subject_id'].split(':')
        self.request['selection'].update(stage_id=stage,enemy_id=enemy,enemy_level=int(level))
        compiled=compile_session(self.request,self.catalog)['request']
        self.assertEqual(compiled['defender']['ep_resistance']['base']['value'],10)
        self.assertEqual(compiled['defender']['element_res']['base']['value'],0)

    def test_talent_replacement_and_module_levels_not_accumulated(self):
        self.operator_field('elite', 2); self.operator_field('level', 90)
        self.operator_field('module_id', 'LOR-X'); self.operator_field('module_level', 3)
        self.request['allow_partial'] = True; self.request['selection']['skill_active'] = False
        result = self.calculate()
        self.assertIn('module_talent_replacement', [r['code'] for r in result['audit']['missing']])
        module_rules = [r for r in result['audit']['rules'] if r['source']['kind'] == 'module']
        self.assertEqual(sorted((r['stat'], r['value']) for r in module_rules), [('atk', 70), ('max_hp', 330)])

    def test_context_and_effect_ids_cannot_activate_unconfirmed_effects(self):
        self.request['context'] = {'confirmed': True, 'approval_status': 'complete', 'difficulty': 0}
        self.request['selection']['effect_ids'] = ['not-active']
        result = self.calculate(); self.assertIsNone(result['result'])
        self.assertIn('effect_pin_mismatch', [r['code'] for r in result['audit']['missing']])

    def test_schema_and_runtime_reject_invalid_controls(self):
        for field, value in [('skill_active', 'false'), ('operator_id', {}), ('damage_type', []), ('hits', True)]:
            request = deepcopy(self.request); request['selection'][field] = value
            with self.subTest(field=field), self.assertRaises(CalculationError): calculate_session(request, self.catalog)
        request = deepcopy(self.request); request['session']['inventories']['parts']['items'] = [{'id': 'x', 'fields': []}]
        with self.assertRaises(CalculationError): calculate_session(request, self.catalog)
        operations={row.get('properties',{}).get('operation',{}).get('const') for row in contract()['oneOf']}
        self.assertIn('session_damage',operations);self.assertIn('session_dps',operations)

    def test_python_cli_http_equal_and_rules_endpoints(self):
        server, thread = start_server(0)
        try:
            url = 'http://127.0.0.1:' + str(server.server_port)
            results = [calculate_request(self.request, transport, url=url) for transport in ('python', 'cli', 'http')]
            self.assertEqual(results[0], results[1]); self.assertEqual(results[1], results[2])
            identity = self.catalog.select('rogue_6_relic_legacy_15')['id']
            with urlopen(url + '/v1/rules/' + identity) as response: rule_data = json.load(response)
            self.assertEqual(rule_data['status'], 'compiled')
            self.request['selection']['skill_active'] = 'false'
            self.assertIn('error', calculate_request(self.request, 'http', url=url))
        finally: server.shutdown(); server.server_close(); thread.join(5)


if __name__ == '__main__': unittest.main()

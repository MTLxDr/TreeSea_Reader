"""Build one auditable single-hit request from a confirmed Reader snapshot.

This module reads snapshots without persisting or confirming them. Unsupported
mechanics block a complete result by default; partial arithmetic is opt-in.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import re

from .calculation import calculate, check_keys, number, CalculationError, SCHEMA_VERSION, ALGORITHM_VERSION
from .effect_rules import (RuleCatalog, compile_entry, rule, contribution, apply_rules,
                           PROFESSIONS, COMPILER_VERSION)
from .skill_timing import trait_in_zero_target

SELECTION_FIELDS = {'operator_id', 'stage_id', 'enemy_id', 'enemy_level', 'difficulty', 'squad_id',
                    'skill_active', 'damage_type', 'hits', 'operator_panel', 'effect_ids', 'attack_mode', 'target_mode'}


def field(item, name):
    value = item.get('fields', {}).get(name, {})
    return value.get('value') if isinstance(value, dict) else None


def integer(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise CalculationError(f'{name} must be an integer in {lo}..{hi}')
    return value


def session_contract():
    """Envelope schema; inner source and core input schemas are unchanged."""
    return {'type': 'object', 'required': ['operation', 'session', 'selection'], 'additionalProperties': False,
        'properties': {'schema_version': {'const': SCHEMA_VERSION}, 'operation': {'const': 'session_damage'},
            'session': {'type': 'object'}, 'allow_partial': {'type': 'boolean', 'default': False}, 'context': {},
            'selection': {'type': 'object', 'additionalProperties': False, 'properties': {
                **{k: {'type': 'string', 'maxLength': 200} for k in ('operator_id', 'stage_id', 'enemy_id')},
                'enemy_level': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                'difficulty': {'type': 'integer', 'minimum': 0, 'maximum': 99},
                'squad_id': {'type': ['string', 'null']}, 'skill_active': {'type': 'boolean'},
                'damage_type': {'enum': ['physical', 'arts', 'true', 'element']},
                'attack_mode': {'enum': ['melee', 'ranged']},
                'target_mode': {'enum': ['enemy', 'zero'], 'default': 'enemy'},
                'hits': {'type': 'integer', 'minimum': 1, 'maximum': 10000},
                'effect_ids': {'type': 'array', 'maxItems': 128, 'uniqueItems': True, 'items': {'type': 'string'}},
                'operator_panel': {'type': 'object', 'required': ['stats', 'included'], 'additionalProperties': False,
                    'properties': {'stats': {'type': 'object', 'additionalProperties': False,
                        'properties': {k: {'type': 'number'} for k in ('atk', 'def', 'res', 'max_hp', 'attack_speed')},
                        'required': ['atk']}, 'included': {'type': 'array', 'uniqueItems': True,
                        'items': {'enum': ['trust', 'potential', 'module']}}}}}}}}


def compile_session(envelope, catalog=None):
    check_keys(envelope, {'schema_version', 'operation', 'session', 'selection', 'allow_partial', 'context'}, 'session request')
    if envelope.get('schema_version', SCHEMA_VERSION) != SCHEMA_VERSION:
        raise CalculationError('session_damage requires schema_version 1.2')
    if envelope.get('operation') != 'session_damage': raise CalculationError('Expected session_damage')
    if not isinstance(envelope.get('allow_partial', False), bool): raise CalculationError('allow_partial must be boolean')
    selection = envelope.get('selection', {}); check_keys(selection, SELECTION_FIELDS, 'selection')
    target_mode = selection.get('target_mode', 'enemy')
    if target_mode not in ('enemy', 'zero'): raise CalculationError('Unknown target_mode')
    for key in ('operator_id', 'stage_id', 'enemy_id'):
        if key in selection and (not isinstance(selection[key], str) or not 0 < len(selection[key]) <= 200):
            raise CalculationError(key + ' must be a nonempty bounded string')
    if 'damage_type' in selection and selection['damage_type'] not in ('physical', 'arts', 'true', 'element'):
        raise CalculationError('Unknown damage_type')
    if 'skill_active' in selection and not isinstance(selection['skill_active'], bool):
        raise CalculationError('skill_active must be boolean')
    if 'attack_mode' in selection and selection['attack_mode'] not in ('melee', 'ranged'):
        raise CalculationError('attack_mode must be melee or ranged')
    snapshot = envelope.get('session')
    if not isinstance(snapshot, dict): raise CalculationError('session must be an object')
    inventories = snapshot.get('inventories', {})
    if not isinstance(inventories, dict): raise CalculationError('session.inventories must be an object')
    catalog = catalog or RuleCatalog()
    audit = {'compiler_version': COMPILER_VERSION, 'rules': [], 'sources': [], 'missing': [],
             'unsupported': [], 'excluded': [], 'scope': 'single_hit_base_damage',
             'notes': ['只读本局快照；不模拟命中、索敌、技能循环、附伤、元素爆发和特殊吸收。']}
    request = {'schema_version': SCHEMA_VERSION, 'operation': 'damage', 'attacker': {}, 'defender': {},
               'hit': {}, 'context': deepcopy(envelope.get('context', {}))}
    if target_mode == 'zero':
        source = {'kind': 'manual', 'name': '固定0防0抗计算目标', 'detail': '用户指定预设，不代表实际敌人'}
        request['defender'] = {stat: {'base': {'value': 0, 'source': source}}
                               for stat in ('def', 'res', 'element_res', 'ep_resistance')}
        audit['notes'].append('固定单目标0防0抗，无敌人、关卡、护盾与免疫机制；未选择的难度及分队增益不计入。')
    def missing(code, detail): audit['missing'].append({'code': code, 'detail': detail})
    def use(entry, side='attacker', subject=None, included=(), stacks=None):
        audit['sources'].append({'effect_id': entry['id'], 'name': entry['name'], 'variant': entry['variant'],
                                 'subject_id': entry['subject_id'], 'source': entry['source']})
        if entry['category'] == '干员潜能' and re.fullmatch(r'(?:部署费用[+-]\d+|再部署时间[+-]\d+(?:秒|s)?)', entry['raw_effect'].strip()):
            audit['excluded'].append({'effect_id': entry['id'], 'reason': '部署费用/再部署时间不参与本次基础单段伤害'})
            return
        if target_mode == 'zero' and entry['category'] in {'干员技能', '干员天赋'}:
            from .skill_timing import compile_zero_target_effect
            compiled = compile_zero_target_effect(entry)
        elif stacks is not None:
            from .effect_rules import compile_counted_entry
            compiled=compile_counted_entry(entry,stacks)
        else:
            compiled = compile_entry(entry)
        audit['excluded'].extend({'effect_id': entry['id'], **row} for row in compiled.get('excluded_clauses', []))
        if target_mode == 'zero':
            offensive = []
            for r in compiled['rules']:
                if r['stat'] in {'atk', 'attack_speed', None}:
                    offensive.append(r)
                else:
                    audit['excluded'].append({'effect_id': entry['id'], 'stat': r['stat'],
                        'reason': '该我方白值不参与本次基于攻击力的单目标输出'})
            compiled['rules'] = offensive
        audit['unsupported'].extend(compiled['unsupported'])
        apply_rules(request, compiled, side, subject or {}, audit, included)
    def inventory(kind):
        bucket = inventories.get(kind, {})
        if not isinstance(bucket, dict) or not isinstance(bucket.get('items', []), list) or len(bucket.get('items', [])) > 2000:
            raise CalculationError('Invalid inventory: ' + kind)
        rows = []
        for item in bucket.get('items', []):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str): raise CalculationError('Invalid inventory item')
            if not isinstance(item.get('fields', {}), dict): raise CalculationError('Invalid inventory fields')
            if 'relic_state' in item and not isinstance(item['relic_state'],dict):
                raise CalculationError('Invalid relic state')
            for key in ('confirmed', 'excluded'):
                if key in item and not isinstance(item[key], bool): raise CalculationError(key + ' must be boolean')
            if any(not isinstance(v, dict) for v in item.get('fields', {}).values()):
                raise CalculationError('Inventory fields must use {value:...} records')
            if item.get('excluded') is True:
                if kind=='relics' and (item.get('relic_state') or {}).get('trigger_count'):
                    missing('retained_one_use_effect',item['id']+' 已触发的效果需核对持续时间与作用对象')
                audit['excluded'].append({'kind': kind, 'id': item['id'], 'reason': '人工移除'}); continue
            if item.get('confirmed') is not True:
                missing('unconfirmed_item', kind + ':' + item['id']); continue
            rows.append(item)
        ids = [r['id'] for r in rows]
        if len(ids) != len(set(ids)): raise CalculationError('Duplicate session inventory identity: ' + kind)
        return bucket, rows

    _, operators = inventory('operators')
    operator_id = selection.get('operator_id')
    operator = next((o for o in operators if o['id'] == operator_id), None)
    if operator is None: missing('operator_not_confirmed', '请选择已人工确认的本局干员')
    detail = catalog.operators.get(operator_id, {})
    if operator_id and not detail: missing('missing_operator_source', '活动来源中没有该干员的完整资料')
    profile = catalog.profiles.get(operator_id, {})
    subject = {'profession': PROFESSIONS.get(profile.get('data-profession')),
               'position': {'近战位': 'MELEE', '远程位': 'RANGED'}.get(profile.get('data-position'))}
    trait = catalog.operator_trait(operator_id)
    audit['operator_trait'] = trait
    if trait is None:
        missing('missing_operator_trait_source', '职业特性来源不完整，不能假设无效果')
    elif trait['text'] == '可以进行远程攻击，但此时攻击力降低至80%':
        if selection.get('attack_mode') == 'melee':
            audit['notes'].append('来源明确的领主远程攻击降低在本次近战攻击中不触发。')
        elif selection.get('attack_mode') is None:
            missing('missing_attack_mode', '领主特性需要明确本次是近战还是远程攻击')
        else:
            missing('unsupported_ranged_trait', '远程特性与技能覆盖尚未编译，未静默套用或忽略80%')
    elif target_mode == 'zero' and trait_in_zero_target(trait):
        audit['notes'].append('已明确职业特性在单目标持续攻击假设下不改变单段伤害：' + trait['text'])
    else:
        audit['unsupported'].append({'code':'operator_trait','detail':trait['text'],'source':trait['source'],
                                    'name':detail.get('name',operator_id)})
    operator = operator or {'fields': {}}
    for name, lo, hi in [('elite', 0, 2), ('level', 1, 90), ('trust', 0, 200), ('potential', 1, 6),
                         ('skill_index', 1, 3), ('skill_level', 1, 7), ('mastery', 0, 3),
                         ('skill_1_mastery', 0, 3), ('skill_2_mastery', 0, 3), ('skill_3_mastery', 0, 3),
                         ('module_level', 0, 3)]:
        if field(operator, name) is not None: integer(field(operator, name), name, lo, hi)
    panel = selection.get('operator_panel')
    attributes = detail.get('components', {}).get('attributes', [])
    fields = attributes[0]['fields'] if len(attributes) == 1 else {}
    included = []
    if panel is not None:
        check_keys(panel, {'stats', 'included'}, 'operator_panel')
        stats = panel.get('stats', {}); check_keys(stats, {'atk', 'def', 'res', 'max_hp', 'attack_speed'}, 'panel.stats')
        included = panel.get('included')
        if not isinstance(included, list) or any(not isinstance(v, str) or v not in {'trust', 'potential', 'module'} for v in included) or len(included) != len(set(included)):
            raise CalculationError('panel.included must explicitly list trust/potential/module already in the panel')
        for stat, value in stats.items():
            if isinstance(value, bool) or not isinstance(value, (float, int)): raise CalculationError('panel stats must be numbers')
            request['attacker'][stat] = {'base': {'value': number(value, 'panel.' + stat, 0),
                'source': {'kind': 'manual', 'name': (detail.get('name') or operator_id or '干员') + ' · 人工确认起始面板',
                           'detail': '已包含：' + ', '.join(included)}}}
        if 'atk' not in stats: missing('missing_attack', 'operator_panel.stats.atk')
    else:
        elite, level = field(operator, 'elite'), field(operator, 'level')
        if elite is None or level is None: missing('missing_level', '缺少干员精英阶段/等级')
        else:
            integer(elite, 'elite', 0, 2); integer(level, 'level', 1, 90)
            prefix = f'精英{elite}_1级_' if elite == 0 and level == 1 else f'精英{elite}_满级_' if str(level) == fields.get(f'精英{elite}_满级') else None
            if prefix is None: missing('unsupported_level_interpolation', '当前只支持来源明确列出的等级端点；可补入人工面板')
            else:
                for cn, stat in [('攻击', 'atk'), ('防御', 'def'), ('生命上限', 'max_hp'), ('法术抗性', 'res')]:
                    value = fields.get(prefix + cn)
                    if value is None: missing('missing_base_stat', prefix + cn); continue
                    try: value = float(value)
                    except (ValueError, TypeError): missing('non_scalar_base_stat', prefix + cn); continue
                    source = {'kind': 'level', 'name': detail.get('name', operator_id) + f' · 精英{elite}等级{level}',
                              'id': operator_id, 'url': detail.get('source_url', ''), 'revision': str(detail.get('revision_id', 'unknown'))}
                    request['attacker'][stat] = {'base': {'value': value, 'source': source}}
                request['attacker']['attack_speed'] = {'base': {'value': 100, 'source': {'kind': 'base', 'name': '基础攻击速度100'}}}
    if 'trust' not in included:
        trust = field(operator, 'trust')
        if trust is None: missing('missing_trust', '信赖未知')
        else:
            integer(trust, 'trust', 0, 200)
            if 0 < trust < 100: missing('unsupported_trust_interpolation', '未建模中间信赖成长；可使用包含信赖的人工面板')
            elif trust >= 100:
                for cn, stat in [('攻击', 'atk'), ('防御', 'def'), ('生命上限', 'max_hp')]:
                    value = fields.get('信赖加成_' + cn)
                    if value is None and stat in request['attacker']:
                        missing('missing_trust_parameter', cn)
                    if value is not None and stat in request['attacker']:
                        try: value = float(value)
                        except (ValueError, TypeError): missing('invalid_trust_parameter', cn); continue
                        request['attacker'][stat].setdefault('base_add', []).append({'value': value,
                            'source': {'kind': 'trust', 'name': detail.get('name', '') + ' · 满信赖平值',
                                       'url': detail.get('source_url', ''), 'revision': str(detail.get('revision_id', 'unknown'))}})

    relevant = [e for e in catalog.entries.values() if e.get('context', {}).get('operator_id') == operator_id]
    potential = field(operator, 'potential')
    if potential is None: missing('missing_potential', '潜能未知，不能选择天赋档位')
    else:
        integer(potential, 'potential', 1, 6)
        for e in relevant:
            if e['category'] == '干员潜能' and e['context']['potential'] <= potential:
                use(e, subject=subject, included=included)

    module_id, module_level = field(operator, 'module_id'), field(operator, 'module_level')
    if module_id is not None and not isinstance(module_id, str): raise CalculationError('module_id must be a string')
    if module_id is None: missing('missing_module', '装备模组未知')
    elif module_id != 'none':
        if module_level is None: missing('missing_module_level', '模组等级未知')
        else:
            integer(module_level, 'module_level', 1, 3)
            modules = [e for e in relevant if e['category'] == '干员模组' and e['context'].get('level') == module_level
                       and module_id in {e['context'].get('module_type'), e.get('source_parameters', {}).get('fields', {}).get('名称')}]
            if len(modules) != 1: missing('ambiguous_module', '无法匹配唯一模组档位')
            else:
                e = modules[0]; rules = []
                for cn, stat in [('生命', 'max_hp'), ('攻击', 'atk'), ('防御', 'def'), ('攻速', 'attack_speed'), ('法抗', 'res')]:
                    value = e['context']['attributes'].get(cn)
                    if value is None: continue
                    try: value = float(value)
                    except (ValueError, TypeError): missing('non_scalar_module_stat', cn); continue
                    rules.append(rule(e, 'module_flat:' + stat, stat, 'base_add', value,
                                      evidence={'kind': 'module_level_replacement', 'level': module_level}))
                apply_rules(request, {'rules': rules}, 'attacker', subject, audit, included)
                audit['sources'].append({'effect_id': e['id'], 'source': e['source'], 'variant': e['variant']})
                if e['raw_effect']:
                    audit['unsupported'].append({'code': 'module_mechanics', 'effect_id': e['id'],
                        'detail': '模组平值已分开处理；天赋替换、附伤与特性尚未完整建模', 'source': e['source']})

    elite = field(operator, 'elite')
    talent_groups = defaultdict(list)
    for e in relevant:
        if e['category'] == '干员天赋': talent_groups[e['context'].get('exclusive_group')].append(e)
    for group, entries in talent_groups.items():
        if elite is None or potential is None:
            missing('missing_talent_condition', str(group)); continue
        eligible = []
        for e in entries:
            condition = e['context'].get('condition') or ''
            match = re.fullmatch(r'精英([012])', condition)
            if match and elite >= int(match[1]): eligible.append(e)
            elif not match:
                minimum_elite = re.search(r'精英([012])', condition)
                inactive = (minimum_elite and elite < int(minimum_elite[1])) or ('模组' in condition and module_id == 'none')
                audit['excluded'].append({'effect_id': e['id'], 'name': e['name']+' · '+e['variant'],
                    'reason': '不满足解锁条件：'+condition if inactive else '特殊天赋条件未支持：'+condition})
                if not inactive: missing('unsupported_talent_condition', condition or '天赋解锁条件未列明')
        if eligible:
            rank = max(int(e['context']['condition'][-1]) for e in eligible)
            eligible = [e for e in eligible if int(e['context']['condition'][-1]) == rank]
            enhanced = [e for e in eligible if e['context'].get('potential_enhanced') and str(e['context'].get('potential_threshold', '')).isdigit()
                        and potential >= int(e['context']['potential_threshold'])]
            chosen = enhanced or [e for e in eligible if not e['context'].get('potential_enhanced')]
            if len(chosen) == 1:
                if module_id not in {None, 'none'} and module_level in {2, 3}:
                    missing('module_talent_replacement', '模组可能替换天赋，未叠加基础天赋 ' + str(group))
                else: use(chosen[0], subject=subject)
                for e in entries:
                    if e['id'] != chosen[0]['id']: audit['excluded'].append({'effect_id': e['id'], 'reason': '互斥天赋档位未选中'})
            else: missing('ambiguous_talent', str(group))

    skill_active = selection.get('skill_active')
    if not isinstance(skill_active, bool): missing('missing_skill_state', '请明确是否处于技能生效状态')
    elif skill_active:
        skill_index, skill_level = field(operator, 'skill_index'), field(operator, 'skill_level')
        mastery = field(operator, f'skill_{skill_index}_mastery')
        if mastery is None: mastery = field(operator, 'mastery') if field(operator, 'skill_index') == skill_index else None
        if None in (skill_index, skill_level, mastery): missing('missing_skill_rank', '技能编号、等级或对应技能专精未知')
        else:
            integer(skill_index, 'skill_index', 1, 3); integer(skill_level, 'skill_level', 1, 7); integer(mastery, 'mastery', 0, 3)
            if skill_level != 7 and mastery: raise CalculationError('Mastery requires skill level 7')
            if elite is None or elite < skill_index - 1 or (mastery and elite != 2):
                missing('skill_not_unlocked', '精英阶段不足以解锁所选技能或专精')
            else:
                candidates = [e for e in relevant if e['category'] == '干员技能' and all(e['context'].get(k) == v
                    for k, v in [('skill_index', skill_index), ('level', skill_level), ('mastery', mastery)])]
                if len(candidates) == 1: use(candidates[0], subject=subject)
                else: missing('ambiguous_skill', '无法匹配唯一技能档位')

    for kind, categories in [('relics', {'路标藏品', 'PRTS藏品'}), ('parts', {'路标零件', 'PRTS零件'})]:
        bucket, items = inventory(kind)
        selected_subjects = set()
        if bucket.get('complete') is not True: missing('incomplete_inventory', kind + ' 未确认已录完整')
        for item in items:
            stacks=None
            if kind=='relics' and ('relic_state' in item or field(item,'stacks') is not None):
                from .relic_state import rule_for
                metadata=rule_for(item['id'])
                if metadata['mode']=='counter':
                    stacks=field(item,'stacks')
                    if stacks is None:
                        missing('missing_relic_counter',item['id']);continue
                    integer(stacks,'stacks',0,metadata.get('maximum') or 9999)
                elif metadata['mode'] in {'one_use','on_acquire'}:
                    missing('one_use_effect_target',item['id']+' 按独立生效记录核对，不能乘以累计次数');continue
                elif metadata['mode']=='unknown':
                    missing('unknown_relic_counter_mode',item['id']);continue
                elif field(item,'stacks') is not None:
                    integer(field(item,'stacks'),'continuous relic stacks',1,1)
            else:
                quantity = field(item, 'quantity')
                if quantity is None:
                    missing('missing_quantity', item['id']); continue
                integer(quantity, 'quantity', 1, 999)
                if quantity != 1:
                    missing('unsupported_item_stacking', item['id'] + ' 数量大于1，尚无重复持有效果依据'); continue
            try: entry = catalog.select(item['id'], categories)
            except CalculationError as error: missing('unresolved_owned_effect', str(error)); continue
            if entry['subject_id'] in selected_subjects:
                raise CalculationError('Duplicate game effect through multiple source IDs: ' + entry['subject_id'])
            selected_subjects.add(entry['subject_id'])
            use(entry, subject=subject, stacks=stacks)

    difficulty = selection.get('difficulty')
    if difficulty is None: missing('missing_difficulty', '难度未知')
    elif integer(difficulty, 'difficulty', 0, 99) != 0:
        missing('unsupported_difficulty', '难度倍率/符文尚未编译，不能套用普通关卡裸参数')
    if 'squad_id' not in selection: missing('missing_squad', '分队未知；显式null表示本次不使用分队加成')
    elif selection['squad_id'] is not None:
        if not isinstance(selection['squad_id'], str): raise CalculationError('squad_id must be a string or null')
        try: use(catalog.select(selection['squad_id'], {'路标分队'}), subject=subject)
        except CalculationError as error: missing('unresolved_squad', str(error))

    stage_id, enemy_id, enemy_level = (selection.get(k) for k in ('stage_id', 'enemy_id', 'enemy_level'))
    enemy = None
    if target_mode == 'zero':
        if stage_id or enemy_id or enemy_level is not None:
            audit['excluded'].append({'reason': '固定0防0抗模式不使用指定敌人及关卡参数'})
    elif not stage_id or not enemy_id or enemy_level is None: missing('missing_enemy_selection', '需明确关卡ID、敌人ID及档位')
    else:
        integer(enemy_level, 'enemy_level', 0, 100)
        try: enemy = catalog.select(f'{stage_id}:{enemy_id}:{enemy_level}', {'敌人关卡参数'})
        except CalculationError as error: missing('unresolved_enemy_variant', str(error))
    if enemy:
        audit['sources'].append({'effect_id': enemy['id'], 'source': enemy['source'], 'variant': enemy['variant']})
        if enemy['context'].get('reference_only') or enemy['context'].get('conditional'):
            missing('conditional_enemy', '敌人是条件或参考档位；需核实实际出场条件')
        attributes = enemy['context'].get('attributes', {})
        for key, stat in [('atk', 'atk'), ('def', 'def'), ('maxHp', 'max_hp'), ('magicResistance', 'res'),
                          ('epDamageResistance', 'element_res'), ('epResistance', 'ep_resistance')]:
            if key in attributes:
                request['defender'][stat] = {'base': {'value': attributes[key], 'source': contribution(enemy)}}
        for stat in ('def', 'res'):
            if stat not in request['defender']: missing('missing_enemy_stat', stat)
        # Keep enemy special skills visible; stage parameters do not prove immunity/absorption absent.
        specials = [e for e in catalog.entries.values() if e['category'] == '敌人技能与天赋'
                    and (e['name'] == enemy['name'] or e['context'].get('enemy_name') == enemy['name'])]
        if not specials: missing('missing_enemy_mechanism_source', '敌人变体缺少独立机制页，不能证明没有特殊机制')
        for e in specials:
            fields = e.get('source_parameters', {}).get('fields', {})
            mechanics = {k: v for k, v in fields.items() if any(term in k for term in ('效果','能力','天赋','技能')) and v}
            if mechanics:
                audit['unsupported'].append({'code': 'enemy_mechanics', 'effect_id': e['id'],
                    'detail': '敌人技能/天赋未编译', 'source': e['source']})
        stages = [e for e in catalog.by_subject.get(stage_id, []) if e['category'] == '关卡环境与规则']
        if len(stages) != 1: missing('missing_stage_rules', '关卡环境规则来源不唯一')
        elif stages[0].get('source_parameters', {}).get('rules'):
            use(stages[0], side='defender', subject={})
        else:
            audit['sources'].append({'effect_id': stages[0]['id'], 'source': stages[0]['source'], 'variant': stages[0]['variant']})
        if len(stages) == 1 and stages[0]['source_parameters'].get('hasOptionalRules'):
            missing('optional_stage_rules', '关卡含可选规则，需要明确实际激活条件')
    kind = selection.get('damage_type')
    if kind not in ('physical', 'arts', 'true', 'element'): missing('missing_damage_type', '请明确本次伤害类型')
    else: request['hit']['type'] = kind
    if kind == 'element' and 'element_res' not in request['defender']: missing('missing_element_resistance', '敌人元素抗性未知')
    request['hit']['hits'] = integer(selection.get('hits', 1), 'hits', 1, 10000)
    effect_ids = selection.get('effect_ids', [])
    if not isinstance(effect_ids, list) or len(effect_ids) > 128 or any(not isinstance(v, str) for v in effect_ids):
        raise CalculationError('effect_ids must be a bounded string array')
    if len(effect_ids) != len(set(effect_ids)): raise CalculationError('Duplicate effect_ids')
    # Exact pins may only reference effects already confirmed in this request.
    chosen_ids = {s['effect_id'] for s in audit['sources']}
    for effect_id in effect_ids:
        if effect_id not in chosen_ids: missing('effect_pin_mismatch', '不能用effect_ids激活未确认效果或历史修订：' + effect_id)
    audit['status'] = 'incomplete' if audit['missing'] or audit['unsupported'] else 'complete_for_supported_scope'
    return {'request': request, 'audit': audit}


def calculate_session(envelope, catalog=None):
    compiled = compile_session(envelope, catalog)
    audit = compiled['audit']
    result = None
    if audit['status'] == 'complete_for_supported_scope' or envelope.get('allow_partial') is True:
        if 'atk' in compiled['request']['attacker'] and 'type' in compiled['request']['hit']:
            result = calculate(compiled['request'])
            result['limitations'].append('此结果只覆盖编译审计中列出的已知贡献；必须同时检查audit.status与缺项。')
    return {'schema_version': SCHEMA_VERSION, 'algorithm_version': ALGORITHM_VERSION,
            'operation': 'session_damage', 'status': audit['status'], 'partial': audit['status'] == 'incomplete',
            'compiled_request': compiled['request'], 'audit': audit, 'result': result}


def dispatch(request):
    if isinstance(request, dict) and request.get('operation') == 'session_dps':
        from .session_dps import calculate_session_dps
        return calculate_session_dps(request)
    if isinstance(request, dict) and request.get('operation') == 'session_damage': return calculate_session(request)
    return calculate(request)

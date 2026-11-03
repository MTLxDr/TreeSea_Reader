"""Zero-defense session DPH and finite skill-window average DPS."""
from copy import deepcopy

from .calculation import CalculationError, calculate, check_keys, number, SCHEMA_VERSION, ALGORITHM_VERSION
from .effect_rules import RuleCatalog
from .session_calculation import compile_session, field, integer, session_contract
from .skill_timing import finite_window, seconds, skill_profile

OPERATOR_FIELDS = {'elite', 'level', 'trust', 'potential', 'module_id', 'module_level',
                   'skill_index', 'skill_level', 'mastery', 'skill_1_mastery', 'skill_2_mastery', 'skill_3_mastery'}
MODEL_FIELDS = {'duration', 'base_interval', 'interval_add', 'interval_factor', 'first_hit',
                'hits_per_attack', 'frame_align'}


def dps_contract():
    schema = deepcopy(session_contract())
    schema['properties']['operation'] = {'const': 'session_dps'}
    properties = schema['properties']['selection']['properties']
    properties['target_mode'] = {'const': 'zero', 'default': 'zero'}
    properties['operator_fields'] = {'type': 'object', 'additionalProperties': False, 'properties': {
        name: {'type': ['string', 'null']} if name == 'module_id' else {'type': ['integer', 'null']}
        for name in sorted(OPERATOR_FIELDS)}}
    properties['model'] = {'type': 'object', 'additionalProperties': False, 'properties': {
        'duration': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 36000},
        'base_interval': {'type': 'number', 'exclusiveMinimum': 0},
        'interval_add': {'type': 'number'}, 'interval_factor': {'type': 'number', 'exclusiveMinimum': 0},
        'first_hit': {'type': 'number', 'minimum': 0, 'default': 0},
        'hits_per_attack': {'type': 'integer', 'minimum': 1, 'maximum': 32},
        'frame_align': {'type': 'boolean', 'default': False}}}
    return schema


def selected_skill(catalog, operator):
    index, level = field(operator, 'skill_index'), field(operator, 'skill_level')
    mastery = field(operator, f'skill_{index}_mastery')
    if mastery is None:
        mastery = field(operator, 'mastery')
    rows = [e for e in catalog.entries.values() if e['category'] == '干员技能'
            and e.get('context', {}).get('operator_id') == operator.get('id')
            and all(e['context'].get(k) == v for k, v in [('skill_index', index), ('level', level), ('mastery', mastery)])]
    return rows[0] if len(rows) == 1 else None


def calculate_session_dps(envelope, catalog=None):
    check_keys(envelope, {'schema_version', 'operation', 'session', 'selection', 'allow_partial', 'context'}, 'session_dps')
    if envelope.get('operation') != 'session_dps':
        raise CalculationError('Expected session_dps')
    catalog = catalog or RuleCatalog()
    request = deepcopy(envelope)
    selection = request.get('selection', {})
    if not isinstance(selection, dict):
        raise CalculationError('selection must be an object')
    overrides = selection.pop('operator_fields', {})
    model = selection.pop('model', {})
    check_keys(overrides, OPERATOR_FIELDS, 'operator_fields')
    check_keys(model, MODEL_FIELDS, 'model')
    snapshot = request.get('session', {})
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('inventories', {}), dict):
        raise CalculationError('session.inventories must be an object')
    bucket = snapshot.get('inventories', {}).get('operators', {})
    if not isinstance(bucket, dict) or not isinstance(bucket.get('items', []), list):
        raise CalculationError('Invalid inventory: operators')
    operators = bucket.get('items', [])
    if len(operators) > 2000 or any(not isinstance(o, dict) or not isinstance(o.get('fields', {}), dict) for o in operators):
        raise CalculationError('Invalid operator item')
    operator = next((o for o in operators if o.get('id') == selection.get('operator_id')), {'fields': {}})
    for name, value in overrides.items():
        if value is not None:
            if name == 'module_id':
                if not isinstance(value, str): raise CalculationError('module_id must be a string')
            elif isinstance(value, bool) or not isinstance(value, int):
                raise CalculationError(name + ' must be an integer')
        operator.setdefault('fields', {})[name] = {'value': value, 'manual': True, 'source': '计算页本次补录'}
    request['operation'] = 'session_damage'
    selection['target_mode'] = 'zero'
    selection['hits'] = 1
    selection.setdefault('skill_active', True)
    selection.setdefault('difficulty', 0)
    selection.setdefault('squad_id', None)
    request['selection'] = selection
    compiled = compile_session(request, catalog)
    audit = compiled['audit']
    damage_request = compiled['request']
    if 'attack_speed' in damage_request['attacker']:
        damage_request['attacker']['attack_speed']['maximum'] = None
    dph_result = None
    if (audit['status'] == 'complete_for_supported_scope' or request.get('allow_partial') is True) and (
            'atk' in damage_request['attacker'] and 'type' in damage_request['hit']):
        dph_result = calculate(damage_request)
    detail = catalog.operators.get(selection.get('operator_id'), {})
    attributes = detail.get('components', {}).get('attributes', [])
    base_interval = seconds(attributes[0]['fields'].get('攻击速度')) if len(attributes) == 1 else None
    entry = selected_skill(catalog, operator) if selection['skill_active'] else None
    profile = skill_profile(entry) if entry else {}
    gaps = []
    if not entry:
        gaps.append('未选择唯一技能档位；普攻不冒充技能期间平均DPS')
    if profile.get('timing_gaps'):
        gaps.extend('技能攻击时序未支持：' + s for s in profile['timing_gaps'])
    if profile.get('compiled', {}).get('unsupported'):
        gaps.append('技能仍有未编译语句，不能确定完整伤害段和攻击时序；未输出技能期间DPS')
    defaults = {'duration': profile.get('duration'), 'base_interval': base_interval,
        'interval_add': profile.get('interval_add', 0.), 'interval_factor': profile.get('interval_factor', 1.),
        'first_hit': 0., 'hits_per_attack': profile.get('hits_per_attack', 1), 'frame_align': False}
    defaults.update(model)
    # A manually entered finite duration cannot turn a one-shot/maintained skill
    # into a uniform sequence. Its timing mechanism must first be supported.
    if entry and not profile.get('duration'):
        gaps.append('技能没有明确有限持续时长，瞬发/永续/切换技能不能直接套用普通持续攻击模型')
    if defaults['duration'] is None:
        gaps.append('缺少技能持续时间')
    if defaults['base_interval'] is None:
        gaps.append('缺少基础攻击间隔，可在计算参数中补录')
    for name in ('duration', 'base_interval', 'interval_factor'):
        if defaults[name] is not None and number(defaults[name], name, 0) <= 0:
            raise CalculationError(name + ' 必须大于0')
    number(defaults['first_hit'], 'first_hit', 0)
    number(defaults['interval_add'], 'interval_add')
    integer(defaults['hits_per_attack'], 'hits_per_attack', 1, 32)
    if not isinstance(defaults['frame_align'], bool): raise CalculationError('frame_align must be boolean')
    if 'base_interval' in model or 'duration' in model:
        audit['notes'].append('持续时间/基础间隔中显式提供的值为本次人工输入，不能冒充自动读取。')
    window = None
    if dph_result is not None and not gaps:
        window = finite_window(damage_request, **defaults, source=profile.get('source'))
    complete = audit['status'] == 'complete_for_supported_scope' and not gaps
    return {'schema_version': SCHEMA_VERSION, 'algorithm_version': ALGORITHM_VERSION,
        'operation': 'session_dps', 'timing_model_version': '1.0-uniform',
        'status': 'complete_for_supported_scope' if complete else 'incomplete',
        'partial': not complete, 'audit': audit, 'timing_missing': list(dict.fromkeys(gaps)),
        'compiled_request': damage_request, 'dph_result': dph_result, 'window_result': window,
        'result': {'dph': dph_result['per_hit_before_absorption'] if dph_result else None,
                   'skill_average_dps': window['skill_average_dps'] if window else None},
        'resolved_model': defaults, 'operator_fields': deepcopy(operator.get('fields', {})),
        'assumptions': ['固定0防0抗单目标；不应用敌人和关卡规则',
            '未选择的分队/难度额外效果不计入；无法编译的已持有效果仍列缺项',
            '首击默认0秒是理想连续攻击假设，可修改；默认不进行帧对齐',
            'DPS为技能期间平均伤害/秒，不是每一个自然秒内的实际伤害，也不含技能外充能空档']}

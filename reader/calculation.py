"""Pure, side-effect-free calculation primitives with named multiplier groups.

Rates are decimal deltas (+20% -> .2); factor lists contain actual multipliers
(reduce by 70% -> .3). No natural-language effect is silently auto-applied.
"""
from __future__ import annotations

from copy import deepcopy
import math
from .calculation_trace import unpack_operand,operands,fmt,sum_text,product_text

SCHEMA_VERSION='1.2'
SUPPORTED_SCHEMA_VERSIONS={'1.0','1.1',SCHEMA_VERSION}
ALGORITHM_VERSION='0.3-prts'
APPROVAL_STATUS='source_aligned'
SOURCE_URL='https://prts.wiki/w/游戏数据基础'
ELEMENT_SOURCE_URL='https://prts.wiki/w/元素'

LIST_FIELDS={'base_add','stage_add','stage_factors','relic_add','relic_mul_delta','relic_post_factors',
             'direct_add','direct_mul_delta','final_add','final_factors'}
FACTOR_FIELDS={'stage_factors','relic_post_factors','final_factors'}
ATTRIBUTE_FIELDS=LIST_FIELDS|{'base','minimum','maximum','final_groups'}
STAT_FIELDS={'atk','def','res','element_res','ep_resistance','max_hp','attack_speed'}
HIT_FIELDS={'type','attack_scale','attack_flat_add','def_penetration_flat','def_penetration_ratio',
            'res_penetration_flat','res_penetration_ratio','min_damage_ratio','post_scale_factors','post_scale_groups','attack_scale_groups',
            'hits','shield','barrier_charges','immune'}


class CalculationError(ValueError):pass


def number(value,label='value',minimum=None,maximum=None):
    try:value=unpack_operand(value,label)['value']
    except ValueError as error:raise CalculationError(str(error)) from error
    if minimum is not None and value<minimum:raise CalculationError(label+f' must be >= {minimum}')
    if maximum is not None and value>maximum:raise CalculationError(label+f' must be <= {maximum}')
    return value


def check_keys(obj,allowed,label):
    if not isinstance(obj,dict):raise CalculationError(label+' must be an object')
    extra=set(obj)-allowed
    if extra:raise CalculationError(label+' has unknown fields: '+', '.join(sorted(extra)))


def values(spec,key):
    result=spec.get(key,[])
    if not isinstance(result,list) or len(result)>128:raise CalculationError(key+' must be an array of at most 128 numbers')
    return [number(v,key,0 if key in FACTOR_FIELDS else None) for v in result]


def decode_game_final_scalar(encoded):
    """Explicit adapter for source FINAL_SCALER values; not implicit API input."""
    value=number(encoded,'encoded scalar')
    return number(1+value if value<0 else value,'decoded factor',0)


def multiplier_groups(records,label='groups'):
    """Merge identical mechanic IDs before multiplying independent groups.

    The ID and stacking rule must come from a verified mechanic, not prose names.
    Repeated IDs are merged and must declare the same rule. Deltas are rates.
    """
    if not isinstance(records,list) or len(records)>128:raise CalculationError(label+' must be an array of at most 128 groups')
    merged={}
    for record in records:
        check_keys(record,{'group_id','deltas','stacking'},label)
        key=record.get('group_id')
        if not isinstance(key,str) or not key.strip() or len(key)>128:raise CalculationError(label+'.group_id must be a nonempty string of at most 128 characters')
        if key!=key.strip():raise CalculationError(label+'.group_id cannot contain surrounding whitespace')
        rule=record.get('stacking','multiply')
        if not isinstance(rule,str) or rule not in {'add','max','min','multiply'}:raise CalculationError('Unknown stacking rule')
        deltas=record.get('deltas')
        if not isinstance(deltas,list) or not 1<=len(deltas)<=128:raise CalculationError(label+'.deltas must contain 1..128 rates')
        normalized=[number(value,label+'.deltas') for value in deltas]
        contributions=[unpack_operand(value,label+'.deltas') for value in deltas]
        deltas=normalized
        if key in merged and merged[key]['stacking']!=rule:raise CalculationError('Conflicting stacking rules for group '+key)
        group=merged.setdefault(key,{'group_id':key,'stacking':rule,'deltas':[],'contributors':[]})
        group['deltas'].extend(deltas)
        group['contributors'].extend(contributions)
        if len(group['deltas'])>128:raise CalculationError('Too many rates for group '+key)
    result=[]
    for group in merged.values():
        rates=group['deltas'];rule=group['stacking']
        if rule=='multiply':factor=math.prod(number(1+v,'group factor',0) for v in rates)
        else:
            delta=sum(rates) if rule=='add' else max(rates) if rule=='max' else min(rates)
            factor=max(0.,1+delta)
        group['factor']=number(factor,'group factor',0);result.append(group)
        chosen=next((index for index,value in enumerate(rates) if value==(max(rates) if rule=='max' else min(rates))),None) if rule in {'max','min'} else None
        for index,term in enumerate(group['contributors']):term['applied']=chosen is None or index==chosen
    return result


def post_multipliers(value,spec,trace):
    for group in multiplier_groups(spec.get('post_scale_groups',[]),'post_scale_groups'):
        before=value
        value=number(value*group['factor'],'post-mitigation group damage',0)
        trace.append({'step':'post_group_'+group['group_id'],'title':'后置乘区：'+group['group_id'],
                      'components':group,'contributors':group['contributors'],'formula':f"{fmt(before)} × {group_formula(group)} = {fmt(value)}",'value':value})
    factors=spec.get('post_scale_factors',[])
    if not isinstance(factors,list) or len(factors)>128:raise CalculationError('post_scale_factors must be a bounded array')
    for index,factor in enumerate(factors):
        term=unpack_operand(factor,'post_scale_factors');factor=number(factor,'post_scale_factors',0);before=value
        value=number(value*factor,'post-mitigation damage',0)
        trace.append({'step':f'post_scale_{index}','title':'已解析的独立后置倍率','factor':factor,'contributors':[term],
                      'formula':f'{fmt(before)} × {fmt(factor)} = {fmt(value)}','value':value})
    return value


def group_formula(group):
    rates=group['deltas'];rule=group['stacking']
    if rule=='multiply':return '('+' × '.join(f'(1 + {fmt(v)})' for v in rates)+')'
    expression=sum_text(rates) if rule=='add' else rule+'('+', '.join(fmt(v) for v in rates)+')'
    return 'max(0, 1 + '+expression+')'


def calculate_buildup(request):
    check_keys(request,{'schema_version','operation','amount','defender','min_damage_ratio','post_scale_groups','post_scale_factors','hits','context'},'request')
    amount=number(request.get('amount'),'amount',0)
    defender=request.get('defender',{});check_keys(defender,{'ep_resistance'},'defender')
    resistance=calculate_attribute(defender.get('ep_resistance',{'base':0}),'ep_resistance')
    ratio=number(request.get('min_damage_ratio',.05),'min_damage_ratio',0,1)
    effective=max(0.,resistance['value'])
    before=amount*max(0.,1-effective/100)
    value=max(ratio*amount,before) if effective>0 else amount
    trace=[{'step':'buildup_resistance','title':'损伤抵抗及损伤下限（不使用元素抗性或贯穿）',
            'components':{'amount':amount,'ep_resistance':effective,'before_floor':before,'minimum_buildup':ratio*amount},
            'contributors':operands(request,['amount','min_damage_ratio']),
            'formula':f'max({fmt(ratio)} × {fmt(amount)}, {fmt(amount)} × max(0, 1 − {fmt(effective)}/100)) = {fmt(value)}','value':value}]
    value=post_multipliers(value,request,trace)
    hits=request.get('hits',1)
    if isinstance(hits,bool) or not isinstance(hits,int) or not 1<=hits<=10000:raise CalculationError('hits is outside its integer range')
    return {'schema_version':SCHEMA_VERSION,'algorithm_version':ALGORITHM_VERSION,'approval_status':APPROVAL_STATUS,
        'operation':'elemental_buildup','attributes':{'defender':{'ep_resistance':resistance}},
        'buildup_per_hit':value,'total_buildup':number(value*hits,'total buildup',0),'hits':hits,'trace':trace,
        'context':deepcopy(request.get('context',{})),'source_references':[SOURCE_URL,ELEMENT_SOURCE_URL],
        'limitations':['只计算本次元素损伤累积，不自动推演爆发阈值、冷却、恢复或爆发伤害。',
                       '损伤抵抗与元素抗性分别使用；本通道无贯穿、生命护盾和扣血结算。',
                       '损伤下限采用游戏数据基础的详细损伤公式；不模拟定点误差。']}


def calculate_attribute(spec,stat='attribute'):
    check_keys(spec,ATTRIBUTE_FIELDS,stat)
    if 'base' not in spec:raise CalculationError(stat+'.base is required')
    base=number(spec['base'],stat+'.base')
    groups={key:values(spec,key) for key in LIST_FIELDS}
    trace=[]
    def step(key,title,before,after,components,formula,fields=(),contributors=None):
        if not math.isfinite(after):raise CalculationError(stat+' overflow at '+key)
        trace.append({'step':key,'title':title,'before':before,'components':components,'after':after,
                      'formula':formula+' = '+fmt(after),'contributors':contributors if contributors is not None else operands(spec,fields)})
        return after
    value=step('base_panel','基础白值及面板平值补充',base,base+sum(groups['base_add']),groups['base_add'],
        f"{fmt(base)} + {sum_text(groups['base_add'])}",['base','base_add'])
    value=step('stage_runes','关卡符文：先加后逐项乘',value,
        (value+sum(groups['stage_add']))*math.prod(groups['stage_factors']),
        {'add':groups['stage_add'],'factors':groups['stage_factors']},
        f"({fmt(value)} + {sum_text(groups['stage_add'])}) × {product_text(groups['stage_factors'])}",['stage_add','stage_factors'])
    value=step('relic_runes','藏品符文：平值、加成率求和、后置乘算',value,
        (value+sum(groups['relic_add']))*(1+sum(groups['relic_mul_delta']))*math.prod(groups['relic_post_factors']),
        {k:groups[k] for k in ('relic_add','relic_mul_delta','relic_post_factors')},
        f"({fmt(value)} + {sum_text(groups['relic_add'])}) × (1 + {sum_text(groups['relic_mul_delta'])}) × {product_text(groups['relic_post_factors'])}",
        ['relic_add','relic_mul_delta','relic_post_factors'])
    value=step('direct_add','直接加算',value,value+sum(groups['direct_add']),groups['direct_add'],f"{fmt(value)} + {sum_text(groups['direct_add'])}",['direct_add'])
    value=step('direct_mul','直接乘算：攻击提升/降低等加成率相加',value,value*max(0.,1+sum(groups['direct_mul_delta'])),groups['direct_mul_delta'],
        f"{fmt(value)} × max(0, 1 + {sum_text(groups['direct_mul_delta'])})",['direct_mul_delta'])
    value=step('final_add','最终加算：鼓舞等',value,value+sum(groups['final_add']),groups['final_add'],f"{fmt(value)} + {sum_text(groups['final_add'])}",['final_add'])
    named=multiplier_groups(spec.get('final_groups',[]),'final_groups')
    for group in named:
        value=step('final_group_'+group['group_id'],'最终乘区：'+group['group_id'],value,value*group['factor'],group,
            f"{fmt(value)} × {group_formula(group)}",contributors=group['contributors'])
    value=step('final_mul','最终乘算：已解析的独立倍率',value,value*math.prod(groups['final_factors']),groups['final_factors'],
        f"{fmt(value)} × {product_text(groups['final_factors'])}",['final_factors'])
    lo=spec.get('minimum',20 if stat=='attack_speed' else 0)
    hi=spec.get('maximum',600 if stat=='attack_speed' else 100 if stat=='res' else None)
    lo=number(lo,'minimum') if lo is not None else None
    hi=number(hi,'maximum') if hi is not None else None
    if lo is not None and hi is not None and lo>hi:raise CalculationError('minimum exceeds maximum')
    bounded=max(lo,value) if lo is not None else value
    bounded=min(hi,bounded) if hi is not None else bounded
    value=step('bounds','白值上下限',value,bounded,{'minimum':lo,'maximum':hi},
        f"clamp({fmt(value)}, {fmt(lo) if lo is not None else '−∞'}, {fmt(hi) if hi is not None else '+∞'})")
    return {'value':value,'trace':trace,'formula':'\n'.join(row['formula'] for row in trace)}


def _calculate(request):
    if isinstance(request,dict):
        version=request.get('schema_version',SCHEMA_VERSION)
        if not isinstance(version,str) or version not in SUPPORTED_SCHEMA_VERSIONS:raise CalculationError('Unsupported schema_version')
        if version in {'1.0','1.1'}:
            request=deepcopy(request)
            def preserve_old_groups(value):
                if isinstance(value,dict):
                    for key,items in value.items():
                        if key in {'final_groups','post_scale_groups'} and isinstance(items,list):
                            for item in items:
                                if isinstance(item,dict):item.setdefault('stacking','add')
                        else:preserve_old_groups(items)
                elif isinstance(value,list):
                    for item in value:preserve_old_groups(item)
            preserve_old_groups(request)
    if isinstance(request,dict) and request.get('operation')=='elemental_buildup':return calculate_buildup(request)
    if isinstance(request,dict) and request.get('operation')=='attribute':
        check_keys(request,{'schema_version','operation','stat','attribute','context'},'request')
        stat=request.get('stat','attribute')
        if not isinstance(stat,str):raise CalculationError('stat must be a string')
        return {'schema_version':SCHEMA_VERSION,'algorithm_version':ALGORITHM_VERSION,'approval_status':APPROVAL_STATUS,
            'operation':'attribute','stat':stat,**calculate_attribute(request.get('attribute',{}),stat),
            'source_references':[SOURCE_URL],'context':deepcopy(request.get('context',{}))}
    check_keys(request,{'schema_version','operation','attacker','defender','hit','timing','context'},'request')
    if request.get('operation','damage')!='damage':raise CalculationError('Unknown operation')
    attacker=request.get('attacker',{});defender=request.get('defender',{})
    allowed=STAT_FIELDS
    check_keys(attacker,allowed,'attacker');check_keys(defender,allowed,'defender')
    if 'atk' not in attacker:raise CalculationError('attacker.atk is required')
    attributes={side:{key:calculate_attribute(value,key) for key,value in data.items()}
                for side,data in [('attacker',attacker),('defender',defender)]}
    hit=request.get('hit',{});check_keys(hit,HIT_FIELDS,'hit')
    kind=hit.get('type','physical')
    if not isinstance(kind,str) or kind not in {'physical','arts','true','element'}:raise CalculationError('Unknown hit.type')
    attack=number(attributes['attacker']['atk']['value'],'resolved attack',0)
    scale=number(hit.get('attack_scale',1),'attack_scale',0)
    base_scale=scale
    scale_terms=operands(hit,['attack_scale'])
    scale_groups=multiplier_groups(hit.get('attack_scale_groups',[]),'attack_scale_groups')
    for group in scale_groups:scale*=group['factor'];scale_terms.extend(group['contributors'])
    attack_add=number(hit.get('attack_flat_add',0),'attack_flat_add')
    basic=number(max(0.,attack*scale+attack_add),'basic damage',0)
    floor=number(hit.get('min_damage_ratio',.05),'min_damage_ratio',0,1)*basic
    trace=[{'step':'basic_damage','title':'基本伤害：攻击×攻击倍率＋附加攻击','value':basic,
            'components':{'attack':attack,'attack_scale':scale,'attack_scale_groups':scale_groups,'attack_flat_add':attack_add},
            'contributors':scale_terms+operands(hit,['attack_flat_add']),
            'formula':f"max(0, {fmt(attack)} × {fmt(base_scale)}"+''.join(' × '+group_formula(group) for group in scale_groups)+f' + {fmt(attack_add)}) = {fmt(basic)}'}]
    stat='def' if kind=='physical' else 'element_res' if kind=='element' else 'res'
    defense=attributes['defender'].get(stat,{}).get('value',0.)
    flat_key='def_penetration_flat' if kind=='physical' else 'res_penetration_flat'
    ratio_key='def_penetration_ratio' if kind=='physical' else 'res_penetration_ratio'
    for key in ('def_penetration_flat','res_penetration_flat'):number(hit.get(key,0),key,0)
    for key in ('def_penetration_ratio','res_penetration_ratio'):number(hit.get(key,0),key,0,1)
    penetration_flat=number(hit.get(flat_key,0),flat_key,0)
    penetration_ratio=number(hit.get(ratio_key,0),ratio_key,0,1)
    # Ordinary RES reductions are attribute modifiers above, before the RES cap.
    # These hit fields mean ignore RES, a final subtraction after the cap.
    capped_defense=min(100.,max(0.,defense)) if kind=='arts' else defense
    effective=max(0.,capped_defense-penetration_flat)*(1-penetration_ratio)
    if kind=='element':
        if any(number(hit.get(key,0),key)!=0 for key in ('def_penetration_flat','def_penetration_ratio','res_penetration_flat','res_penetration_ratio')):
            raise CalculationError('Element damage has no penetration; remove nonzero penetration fields')
        effective=max(0.,defense);penetration_flat=penetration_ratio=0.
    if kind in {'arts','physical','element'}:
        if kind=='element':ignore_formula=f'max(0, 元素抗性 {fmt(defense)}) = {fmt(effective)}'
        else:ignore_formula=f'max(0, {fmt(capped_defense)} − {fmt(penetration_flat)}) × (1 − {fmt(penetration_ratio)}) = {fmt(effective)}'
        trace.append({'step':'effective_resistance','title':'有效元素抗性（无贯穿）' if kind=='element' else '有效法抗：属性封顶后无视' if kind=='arts' else '有效防御：固定后百分比无视',
            'components':{'white_value':defense,'stat_before_ignore':capped_defense if kind!='element' else defense},
            'contributors':operands(hit,[flat_key,ratio_key]) if kind!='element' else [],'formula':ignore_formula,'value':effective})
        mitigated=basic-effective if kind=='physical' else basic*max(0.,1-effective/100)
        damage=max(floor,mitigated)
        trace.append({'step':'mitigation','title':'元素抗性及伤害下限（无贯穿）' if kind=='element' else '固定贯穿后百分比贯穿，再结算防御/抗性及伤害下限',
            'components':{'target_stat':defense,'stat_before_ignore':capped_defense if kind!='element' else defense,
                          'flat_penetration':penetration_flat,'ratio_penetration':penetration_ratio,
                          'effective_stat':effective,'before_floor':mitigated,'minimum_damage':floor},'value':damage})
        trace[-1]['formula']=(f'max({fmt(floor)}, {fmt(basic)} − {fmt(effective)}) = {fmt(damage)}' if kind=='physical' else
                              f'max({fmt(floor)}, {fmt(basic)} × max(0, 1 − {fmt(effective)}/100)) = {fmt(damage)}')
        trace[-1]['contributors']=operands(hit,[flat_key,ratio_key,'min_damage_ratio']) if kind!='element' else operands(hit,['min_damage_ratio'])
    else:
        damage=basic;trace.append({'step':'mitigation','title':'真实伤害使用基本伤害','formula':f'H = {fmt(damage)}','value':damage})
    damage=post_multipliers(damage,hit,trace)
    immune=hit.get('immune',False)
    if not isinstance(immune,bool):raise CalculationError('immune must be boolean')
    if immune:damage=0.;trace.append({'step':'immune','title':'明确的伤害免疫','formula':'免疫 → 0','value':0.})
    hits=hit.get('hits',1);barrier=hit.get('barrier_charges',0)
    for value,label,lo,hi in [(hits,'hits',1,10000),(barrier,'barrier_charges',0,10000)]:
        if isinstance(value,bool) or not isinstance(value,int) or not lo<=value<=hi:raise CalculationError(label+' is outside its integer range')
    shield=number(hit.get('shield',0),'shield',0)
    blocked_hits=min(hits,barrier) if damage>0 else 0
    total_damage=number(damage*hits,'total damage',0)
    blocked=blocked_hits*damage
    remaining_damage=(hits-blocked_hits)*damage
    absorbed=min(shield,remaining_damage)
    hp_damage=remaining_damage-absorbed
    trace.append({'step':'absorption','title':'独立屏障次数与共享数值护盾','components':{
        'hits':hits,'blocked_hits':blocked_hits,'barrier_blocked_damage':blocked,'shield_absorbed':absorbed},
        'contributors':operands(hit,['shield']),
        'formula':f'max(0, ({hits} − {blocked_hits}) × {fmt(damage)} − {fmt(shield)}) = {fmt(hp_damage)}','value':hp_damage})
    result={'schema_version':SCHEMA_VERSION,'algorithm_version':ALGORITHM_VERSION,'approval_status':APPROVAL_STATUS,'operation':'damage',
        'attributes':attributes,'white_values':{side:{key:value['value'] for key,value in stats.items()} for side,stats in attributes.items()},
        'damage_type':kind,'basic_damage':basic,'per_hit_before_absorption':damage,
        'total_before_absorption':total_damage,'barrier_blocked_damage':blocked,'shield_absorbed':absorbed,
        'hp_damage':hp_damage,'shield_remaining':shield-absorbed,'barrier_remaining':barrier-blocked_hits,'trace':trace,
        'context':deepcopy(request.get('context',{})),'source_references':[SOURCE_URL],
        'limitations':['显式乘区基础演算；未自动解释或套用藏品、技能及敌人机制原文。',
                       '命名乘区按group_id合并；同组规则须有机制依据。旧倍率列表仅接收已解析的独立倍率，不可重复填写同一效果。',
                       '使用浮点计算，不模拟游戏定点误差、逐帧取整、前后摇、技能循环和特殊护盾机制。',
                       '屏障与数值护盾为本接口明确定义的简化吸收模型，特殊机制需独立规则。']}
    if 'timing' in request:
        timing=request['timing'];check_keys(timing,{'base_interval'},'timing')
        interval=number(timing.get('base_interval'),'base_interval',0)
        if interval==0:raise CalculationError('base_interval must be greater than zero')
        speed=attributes['attacker'].get('attack_speed',{}).get('value',100.)
        actual=interval/(min(600.,max(10.,speed))/100)
        dps=number(total_damage/actual,'theoretical DPS',0)
        result['timing']={'actual_interval':actual,'theoretical_dps_before_absorption':dps,
                          'interval_formula':f'{fmt(interval)} / (clamp({fmt(speed)}, 10, 600) / 100) = {fmt(actual)} 秒',
                          'dps_formula':f'{fmt(total_damage)} / {fmt(actual)} = {fmt(dps)}',
                          'contributors':operands(timing,['base_interval']),
                          'note':'按一次攻击的给定段数估计；不含动画帧、弹道、技能覆盖率与循环。'}
    return result


def calculate(request):
    result=_calculate(request)
    version=request.get('schema_version',SCHEMA_VERSION)
    result['input_schema_version']=version
    if version in {'1.0','1.1'}:
        result['compatibility_notes']=['旧版请求中未声明stacking的命名乘区按旧约定相加；新请求默认逐项乘。接入方应按来源显式指定组内规则。']
    return result

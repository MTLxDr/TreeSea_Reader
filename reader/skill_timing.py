"""Finite skill-window arithmetic and bounded PRTS description interpretation.

The timeline is an explicit uniform-attack model, not a game-animation simulator.
Unknown clauses and missing durations are never converted into zero effects.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import re

from .calculation import CalculationError, number, calculate
from .effect_rules import compile_entry, contribution

PRTS_SPEED_URL = 'https://prts.wiki/w/游戏数据基础#攻速公式'
PRTS_FRAMES_URL = 'https://prts.wiki/w/作战机制/sandbox#帧对齐机制对攻速的影响'


def source_text(raw):
    """Render only known display templates; never execute wiki markup."""
    text = str(raw)
    pattern = re.compile(r'\{\{([^{}]+)\}\}')
    def replace(match):
        parts = match[1].split('|')
        if parts[0] == 'color' and len(parts) == 3:
            return parts[2]
        if parts[0] in {'+', '*', '**', 'x', 'X'} and len(parts) >= 3:
            return parts[-1]
        return match[0]
    for _ in range(12):
        new = pattern.sub(replace, text)
        if new == text:
            break
        text = new
    text = re.sub(r'<br\s*/?>', '，', text, flags=re.I)
    return re.sub(r'<[^>]*>', '', text).strip()


def seconds(value):
    if isinstance(value, bool):
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*', str(value))
    return float(match[1]) if match else None


def skill_profile(entry):
    """Recognize uniform attacks, explicit hit counts and interval modifiers."""
    text = source_text(entry.get('raw_effect', ''))
    profile = {'duration': seconds(entry.get('context', {}).get('duration')),
        'hits_per_attack': 1, 'interval_add': 0., 'interval_factor': 1.,
        'damage_type': None, 'timing_gaps': [], 'excluded_clauses': [],
        'source': contribution(entry), 'raw_effect': entry.get('raw_effect', '')}
    remaining = []
    for clause in filter(None, (c.strip() for c in re.split(r'[，,；;。\n]+', text))):
        match = re.fullmatch(r'(?:下次)?攻击变为(\d+)连射', clause)
        if match:
            count = int(match[1])
            if not 1 <= count <= 32:
                profile['timing_gaps'].append('连射段数超出支持范围')
            else:
                profile['hits_per_attack'] = count
            continue
        match = re.fullmatch(r'攻击间隔(?:.*?缩短|.*?增大|延长|缩短)\(([+-]?\d+(?:\.\d+)?)\)', clause)
        if match:
            profile['interval_add'] += float(match[1])
            continue
        match = re.fullmatch(r'攻击间隔(?:缩短至|变为)(\d+(?:\.\d+)?)%', clause)
        if match:
            profile['interval_factor'] *= float(match[1])/100
            continue
        if re.fullmatch(r'攻击范围(?:扩大|缩小)|技能会自动开启|优先攻击未被阻挡的目标', clause) or re.fullmatch(
                r'同时攻击(?:至多|最多)\d+个目标(?:（视为近距离攻击）)?', clause):
            profile['excluded_clauses'].append({'text': clause, 'reason': '固定单目标、持续可攻击的计算假设'})
            continue
        match = re.fullmatch(r'(?:每次射击|攻击|下次攻击)造成相当于攻击力(\d+(?:\.\d+)?)%的(物理|法术|真实)?伤害', clause)
        if match:
            if match[2]:
                profile['damage_type'] = {'物理': 'physical', '法术': 'arts', '真实': 'true'}[match[2]]
            remaining.append('下次攻击的攻击力提高至' + match[1] + '%')
            continue
        match = re.fullmatch(r'攻击力(?:提升至|提高至)(\d+(?:\.\d+)?)%', clause)
        if match:
            remaining.append('下次攻击的攻击力提高至' + match[1] + '%')
            continue
        # Unknown timing behavior may not silently use ordinary attack speed.
        if any(term in clause for term in ('每秒', '每隔', '攻击间隔', '停止攻击', '攻击速度逐渐',
                                           '弹药', '蓄力', '持续攻击', '逐渐', '随机', '概率')):
            profile['timing_gaps'].append(clause)
        remaining.append(clause)
    normalized = deepcopy(entry)
    normalized['raw_effect'] = '，'.join(remaining)
    compiled = compile_entry(normalized)
    for r in compiled['rules']:
        r['evidence']['source_raw'] = entry.get('raw_effect', '')
        r['evidence']['normalization'] = 'Known display templates only; source revision retained'
    compiled['excluded_clauses'] = profile['excluded_clauses']
    if re.search(r'(?:攻击力|攻击速度)\{\{(?:\*\*|x|X)\|', entry.get('raw_effect', '')):
        compiled['unsupported'].append({'code': 'unsupported_operation_stage', 'effect_id': entry['id'],
            'name': entry['name'], 'detail': '此攻击/攻速乘区需要独立来源规则，未当成普通加成', 'source': entry['source']})
    profile['compiled'] = compiled
    return profile


def compile_zero_target_effect(entry):
    if entry['category'] == '干员技能':
        return skill_profile(entry)['compiled']
    if entry['category'] == '干员天赋':
        # Exact stat clauses only. Non-damage deployment clauses are explicitly
        # excluded from a skill-active, continuously attacking window.
        clauses, excluded = [], []
        for clause in re.split(r'[，,；;。\n]+', source_text(entry['raw_effect'])):
            if re.fullmatch(r'(?:所有友方|自身)?再部署时间[+-]\d+(?:\.\d+)?%?', clause.strip()):
                excluded.append({'text': clause, 'reason': '技能持续期间的输出不包含再部署循环'})
            elif clause.strip():
                clauses.append(clause.strip())
        copy = deepcopy(entry); copy['category'] = '干员技能'; copy['raw_effect'] = '，'.join(clauses)
        result = compile_entry(copy)
        for r in result['rules']:
            r['source'] = contribution(entry)
            r['evidence']['source_raw'] = entry['raw_effect']
        result['excluded_clauses'] = excluded
        return result
    return compile_entry(entry)


def trait_in_zero_target(trait):
    """No blanket removal of offensive class traits."""
    text = source_text((trait or {}).get('text', ''))
    safe = {'能够阻挡一个敌人', '能够阻挡两个敌人', '能够阻挡三个敌人',
            '优先攻击空中单位', '攻击造成法术伤害', '攻击造成群体法术伤害', '攻击造成群体物理伤害'}
    return text in safe


def finite_window(damage_request, *, duration, base_interval, interval_add=0., interval_factor=1.,
                  first_hit=0., hits_per_attack=1, frame_align=False, source=None):
    """Count actual model hit events in [0,duration), then divide their sum by duration."""
    duration = number(duration, '技能持续时间', 0)
    base_interval = number(base_interval, '基础攻击间隔', 0)
    interval_add = number(interval_add, '攻击间隔加算')
    interval_factor = number(interval_factor, '攻击间隔倍率', 0)
    first_hit = number(first_hit, '首次命中时间', 0)
    if duration <= 0 or duration > 36000:
        raise CalculationError('技能持续时间必须在 0 到 36000 秒之间且不能为0')
    if base_interval <= 0 or (base_interval+interval_add)*interval_factor <= 0:
        raise CalculationError('修正后的攻击间隔必须大于0')
    if isinstance(hits_per_attack, bool) or not isinstance(hits_per_attack, int) or not 1 <= hits_per_attack <= 32:
        raise CalculationError('每次攻击段数必须为1到32的整数')
    if not isinstance(frame_align, bool):
        raise CalculationError('frame_align must be boolean')
    request = deepcopy(damage_request)
    request['hit']['hits'] = 1
    request.pop('timing', None)
    request['attacker'].setdefault('attack_speed', {'base': 100})['maximum'] = None
    per_hit = calculate(request)
    speed = per_hit['attributes']['attacker']['attack_speed']['value']
    effective_speed = min(600., max(10., speed))
    modified_interval = number((base_interval+interval_add)*interval_factor, '修正后的攻击间隔', 0)
    continuous_interval = modified_interval/(effective_speed/100)
    if continuous_interval <= 0:
        raise CalculationError('攻击间隔过小，超出可计算范围')
    interval = continuous_interval
    frames = None
    if frame_align:
        frames = max(1, int((Decimal(str(continuous_interval))*30).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
        interval = frames/30
    # Decimal arithmetic avoids inventing an extra hit at the excluded endpoint.
    remaining = Decimal(str(duration))-Decimal(str(first_hit))
    slots = remaining*30/Decimal(frames) if frame_align else remaining/Decimal(str(interval))
    count = max(0, int(slots.to_integral_value(rounding='ROUND_CEILING'))) if remaining > 0 else 0
    if count*hits_per_attack > 1_000_000:
        raise CalculationError('技能期间命中次数超过100万，请核对攻击间隔和时长')
    dph = per_hit['per_hit_before_absorption']
    per_attack = dph*hits_per_attack
    total = number(per_attack*count, '技能期间总伤害', 0)
    average = number(total/duration, '技能期间平均DPS', 0)
    trace = [
        {'step': 'interval_modifiers', 'title': '先结算攻击间隔修正',
         'formula': f'({base_interval:g} + {interval_add:g}) × {interval_factor:g} = {modified_interval:g} 秒', 'value': modified_interval},
        {'step': 'attack_speed', 'title': '攻速仅在间隔换算时限制至10–600',
         'formula': f'{modified_interval:g} / (clamp({speed:g}, 10, 600) / 100) = {continuous_interval:.12g} 秒',
         'value': continuous_interval, 'contributors': per_hit['attributes']['attacker']['attack_speed']['trace']},
        {'step': 'frame_alignment', 'title': '30帧四舍五入近似' if frame_align else '不进行帧对齐（理论间隔）',
         'formula': f'{frames}/30 = {interval:.12g} 秒' if frame_align else f'T = {interval:.12g} 秒', 'value': interval},
        {'step': 'attack_count', 'title': '技能期间攻击次数，结束时刻不计入',
         'formula': f't = {first_hit:g} + k × {interval:.12g}, 0 ≤ t < {duration:g}；共 {count} 次', 'value': count},
        {'step': 'skill_total', 'title': '技能期间总伤害（计算中间值）',
         'formula': f'{dph:.12g} × {hits_per_attack} 段 × {count} 次 = {total:.12g}', 'value': total},
        {'step': 'skill_average_dps', 'title': '技能期间平均DPS',
         'formula': f'{total:.12g} / {duration:g} = {average:.12g} 伤害/秒', 'value': average}]
    return {'dph': dph, 'damage_per_attack': per_attack, 'skill_average_dps': average,
        'skill_total_damage': total, 'duration': duration, 'attack_count': count,
        'hit_count': count*hits_per_attack, 'hits_per_attack': hits_per_attack,
        'attack_speed': speed, 'effective_attack_speed': effective_speed,
        'base_interval': base_interval, 'modified_interval': modified_interval,
        'continuous_interval': continuous_interval, 'actual_interval': interval,
        'first_hit': first_hit, 'frame_align': frame_align, 'trace': trace, 'source': source,
        'assumptions': ['0防御、0法抗、0元素抗性；单目标始终可被攻击；无护盾、免疫与弹道飞行',
            f'首次命中时间按输入假设 {first_hit:g} 秒；不是动画实测',
            '同一次攻击内的多段伤害按同一时刻命中，不模拟段内时差',
            '仅普通均匀攻击模型；不把固定伤害频率、弹药与动态攻击过程套用普通攻速']}

"""Auditable, conservative compiler. Cataloguing prose never activates a rule.

Only primitive rune parameters, explicit source operation labels and strictly
bounded PRTS attribute clauses are accepted. Unknown selectors/mechanics remain
visible; the original catalog and historical documents are never rewritten.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .calculation import CalculationError, number, STAT_FIELDS

ROOT = Path(__file__).resolve().parents[1]
COMPILER_VERSION = '0.1'
BB_STATS = {'atk': 'atk', 'def': 'def', 'max_hp': 'max_hp',
            'magic_resistance': 'res', 'attack_speed': 'attack_speed'}
CN_STATS = {'攻击力': 'atk', '防御力': 'def', '生命上限': 'max_hp',
            '生命值': 'max_hp', '法术抗性': 'res', '攻击速度': 'attack_speed'}
PROFESSIONS = dict(zip(('先锋', '近卫', '重装', '狙击', '术师', '医疗', '辅助', '特种'),
                       ('PIONEER', 'WARRIOR', 'TANK', 'SNIPER', 'CASTER', 'MEDIC', 'SUPPORT', 'SPECIAL')))
PROFESSIONS['召唤物'] = 'TOKEN'
RUNE_FIELDS = {'char_attribute_add': ('relic_add', 'flat'),
               'char_attribute_mul': ('relic_mul_delta', 'ratio_delta')}
MOD_FIELDS = {'relic_add': 'relic_add', 'relic_mul': 'relic_mul_delta',
              'direct_add': 'direct_add', 'direct_mul': 'direct_mul_delta',
              'final_add': 'final_add'}


def stable_id(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plain(text):
    # Only presentation templates; never evaluate arbitrary wiki syntax.
    text = re.sub(r'\{\{color\|[^|{}]+\|([^{}]+)\}\}', r'\1', text)
    text = re.sub(r'<br\s*/?>', '，', text, flags=re.I)
    return re.sub(r'<[^>]+>', '', text).strip()


def contribution(entry, kind=None, detail=''):
    category = entry['category']
    kind = kind or next((v for k, v in [('藏品', 'relic'), ('零件', 'part'), ('技能', 'skill'),
        ('天赋', 'talent'), ('潜能', 'potential'), ('模组', 'module'), ('敌人', 'enemy')] if k in category), 'map')
    source = entry['source']
    result = {'kind': kind, 'name': entry['name'] + (' · ' + entry['variant'] if entry.get('variant') else ''),
              'id': entry['id'], 'url': source['url'],
              'revision': str(source.get('revision_id') or source['sha256']),
              'detail': detail or '来源参数；证据哈希 ' + source['sha256']}
    return result


def rule(entry, key, stat, field, value, conditions=(), evidence=None, target='friendly'):
    number(value, 'compiled rule')
    if entry['category'].startswith('干员'): target = 'self'
    return {'rule_id': stable_id([entry['id'], key, stat, field]), 'effect_id': entry['id'],
            'subject_id': entry['subject_id'], 'source': contribution(entry),
            'source_sha256': entry['source']['sha256'],
            'applicable_version': {'content_version': entry['source'].get('content_version', 'unknown'),
                'dataset_version': entry.get('context', {}).get('dataset_version', 'unspecified')},
            'target': target, 'conditions': list(conditions), 'stat': stat, 'damage_type': None,
            'stage': field, 'unit': 'ratio_delta' if field.endswith('delta') or field=='attack_scale_groups' else 'flat',
            'value': value, 'group_id': 'prts:' + field + ':' + str(stat), 'stacking': 'add', 'evidence': evidence or {}}


def compile_entry(entry):
    """Return supported subrules and explicit gaps, never a guessed zero effect."""
    rules, unsupported = [], []
    payload = entry.get('source_parameters', {})
    category = entry['category']
    def gap(code, detail):
        unsupported.append({'code': code, 'detail': detail, 'effect_id': entry['id'],
                            'name': entry['name'], 'source': entry['source']})
    runes = payload.get('topicBuffs', [])
    if runes:
        for index, rune in enumerate(runes):
            key = rune.get('key')
            if key not in RUNE_FIELDS:
                gap('unsupported_rune', str(key)); continue
            board = rune.get('blackboard', [])
            keys = [b.get('key') for b in board]
            if len(keys) != len(set(keys)):
                gap('duplicate_parameter', str(key)); continue
            conditions = []
            unknown = set(keys) - set(BB_STATS) - {'selector.profession', 'selector.buildable'}
            if unknown:
                gap('unsupported_parameter', ', '.join(sorted(unknown)))
            selectors_valid = True
            for b in board:
                if b['key'] not in {'selector.profession', 'selector.buildable'}: continue
                values = str(b.get('valueStr', '')).upper().split('|')
                allowed = set(PROFESSIONS.values()) if b['key'].endswith('profession') else {'MELEE', 'RANGED', 'ALL'}
                if not values or not set(values) <= allowed:
                    selectors_valid = False; gap('unsupported_selector', str(b))
                else:
                    conditions.append({'field': 'profession' if b['key'].endswith('profession') else 'position',
                                       'op': 'in', 'values': values})
            if not selectors_valid or unknown: continue
            field, unit = RUNE_FIELDS[key]
            for b in board:
                stat = BB_STATS.get(b['key'])
                if not stat: continue
                # A source tooltip may repeat the same rune: validate, don't add twice.
                peers = [m for m in entry.get('modifiers', []) if m['evidence'] == 'source_tooltip'
                         and m['stat'] == stat and MOD_FIELDS.get(m['bucket']) == field]
                if peers and any(m['value'] != b['value'] for m in peers):
                    gap('conflicting_evidence', stat + ' tooltip/rune disagreement'); continue
                rules.append(rule(entry, f'rune:{index}', stat, field, b['value'], conditions,
                                  {'kind': 'source_rune_parameter', 'rune_index': index, 'rune_key': key,
                                   'parameter': b, 'corroborating_tooltips': peers}))
    elif category in {'干员技能', '干员潜能'}:
        # Entire clauses only: no extraction of numbers from ambiguous sentences.
        clauses = re.split(r'[，,；;。\n]+', plain(entry['raw_effect']))
        for index, clause in enumerate(filter(None, clauses)):
            match = re.fullmatch(r'(攻击力|防御力|生命上限|生命值|法术抗性|攻击速度)([+-]\d+(?:\.\d+)?)(%)?', clause.strip())
            if match:
                stat, value, percent = CN_STATS[match[1]], float(match[2]), bool(match[3])
                if percent and stat == 'attack_speed':
                    gap('unsupported_unit', clause); continue
                field = 'base_add' if category == '干员潜能' else 'direct_mul_delta' if percent else 'direct_add'
                if category == '干员潜能' and percent:
                    gap('unsupported_unit', clause); continue
                rules.append(rule(entry, f'clause:{index}', stat, field, value / 100 if percent else value,
                                  evidence={'kind': 'exact_attribute_clause', 'text': clause}))
            else:
                match = re.fullmatch(r'下次攻击的攻击力提高至(\d+(?:\.\d+)?)%', clause.strip())
                if match and category == '干员技能':
                    r = rule(entry, f'clause:{index}', None, 'attack_scale_groups', float(match[1]) / 100 - 1,
                             evidence={'kind': 'exact_attack_scale_clause', 'text': clause})
                    r.update(group_id='skill:' + entry['subject_id'], stacking='multiply', damage_type='any')
                    rules.append(r)
                else: gap('unsupported_clause', clause)
    else:
        # Annotated tooltip fallback is limited to an explicit unconditional scope.
        for index, mod in enumerate(entry.get('modifiers', [])):
            field = MOD_FIELDS.get(mod['bucket'])
            text = mod.get('raw', {}).get('text', '') if isinstance(mod.get('raw'), dict) else ''
            safe = re.fullmatch(r'所有我方单位的(?:攻击力|防御力|生命值|生命上限|攻击速度|法术抗性)[+-]\d+(?:\.\d+)?%?', text)
            expected = 'ratio_delta' if field and field.endswith('delta') else 'flat'
            if field and mod['stat'] in STAT_FIELDS and mod['value'] is not None and safe and mod['unit'] == expected:
                rules.append(rule(entry, f'annotation:{index}', mod['stat'], field, mod['value'],
                                  evidence={'kind': 'source_tooltip', 'annotation': mod}))
        if not rules: gap('uncompiled_source', '没有可安全执行的参数或完整语义规则')
        elif entry.get('conditions_raw') or len(rules) != len(entry.get('modifiers', [])):
            gap('uncompiled_remainder', '仅编译明确无条件的运算标注，原文其余部分仍待建模')
    return {'compiler_version': COMPILER_VERSION, 'effect_id': entry['id'], 'rules': rules,
            'status': 'partial' if rules and unsupported else 'compiled' if rules else 'unsupported',
            'unsupported': unsupported}


class RuleCatalog:
    """Read only active revisions. Exact IDs beat names; variants never sum."""
    def __init__(self, database=ROOT / 'data/reader.sqlite3'):
        self.database = Path(database)
        with closing(sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            self.entries = {r['id']: r for (raw,) in db.execute('SELECT e.payload_json FROM effect_entries e '
                'JOIN active_documents a ON a.document_id=e.document_id') for r in [json.loads(raw)]}
            self.operators = {oid: json.loads(raw) for oid, raw in db.execute('SELECT operator_id,payload_json FROM active_operator_details')}
            self.profiles = {oid: json.loads(raw).get('attributes', {}) for oid, raw in db.execute("SELECT e.entity_id,e.payload_json FROM entities e "
                "JOIN active_documents a ON a.document_id=e.document_id WHERE e.kind='prts_operator'")}
            self.stage_names = dict(db.execute("SELECT e.entity_id,json_extract(e.payload_json,'$.name') FROM entities e "
                "JOIN active_documents a ON a.document_id=e.document_id WHERE e.kind='stage'"))
        self.by_subject = defaultdict(list)
        for entry in self.entries.values(): self.by_subject[entry['subject_id']].append(entry)

    def select(self, identity, categories=None):
        rows = [self.entries[identity]] if identity in self.entries else self.by_subject.get(identity, [])
        rows = [r for r in rows if categories is None or r['category'] in categories]
        # The PRTS prose copy corroborates a numeric source, not another effect.
        numeric = [r for r in rows if r.get('source_parameters', {}).get('topicBuffs')]
        if numeric: rows = numeric
        if len(rows) != 1:
            raise CalculationError('效果不存在、已非活动修订或档位不唯一，请指定效果ID：' + str(identity))
        return rows[0]

    def compile(self, identity):
        if identity not in self.entries: raise CalculationError('Unknown or inactive effect_id: ' + str(identity))
        return compile_entry(self.entries[identity])

    def operator_trait(self, operator_id):
        detail = self.operators.get(operator_id, {})
        base = (ROOT / 'data/sources/prts/operator_details').resolve()
        raw_path = detail.get('raw_path')
        if not isinstance(raw_path, str): return None
        path = (base / raw_path).resolve()
        if not path.is_relative_to(base) or not path.is_file(): return None
        text = path.read_text(encoding='utf-8')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if digest != detail.get('content_sha256'): return None
        match = re.search(r'^\|特性=([^\n]+)', text, re.M)
        if not match: return None
        return {'text': plain(match[1]), 'raw': match[1], 'source': {'url': detail['source_url'],
            'revision': detail.get('revision_id'), 'content_sha256': digest, 'raw_path': str(path.relative_to(ROOT))}}

    def coverage(self):
        statuses, categories, compiled = Counter(), defaultdict(Counter), []
        rule_count = 0
        for entry in self.entries.values():
            result = compile_entry(entry); statuses[result['status']] += 1
            categories[entry['category']][result['status']] += 1
            rule_count += len(result['rules'])
            if result['rules']: compiled.append({'effect_id': entry['id'], 'name': entry['name'],
                'category': entry['category'], 'status': result['status'], 'rule_count': len(result['rules'])})
        return {'compiler_version': COMPILER_VERSION, 'active_source_entries': len(self.entries),
                'entry_status': dict(statuses), 'rule_count': rule_count,
                'by_category': {k: dict(v) for k, v in categories.items()}, 'entries_with_rules': compiled,
                'boundary': '按活动来源记录统计；partial只表示部分可执行，重复来源不是不同游戏效果；不代表完整游戏机制覆盖率。'}


def apply_rules(request, compiled, side, subject, audit, included=()):
    """Resolve selectors and append attributed operands to the existing core request."""
    for original in compiled['rules']:
        r = deepcopy(original); reason = None; missing = None
        for condition in r['conditions']:
            actual = subject.get(condition['field'])
            if actual is None: missing = '缺少作用对象字段 ' + condition['field']; break
            if 'ALL' not in condition['values'] and actual not in condition['values']:
                reason = '作用对象不满足 ' + condition['field']; break
        if r['source']['kind'] in included: reason = '起始面板已包含该类平值来源'
        r.update(side=side, applied=not reason and not missing, reason=reason or missing)
        audit['rules'].append(r)
        if missing:
            audit['missing'].append({'code': 'missing_selector', 'detail': missing, 'rule_id': r['rule_id']})
        if not r['applied']: continue
        operand = {'value': r['value'], 'source': r['source']}
        if r['stage'] == 'attack_scale_groups':
            request['hit'].setdefault('attack_scale_groups', []).append({'group_id': r['group_id'],
                'stacking': r['stacking'], 'deltas': [operand]})
        else:
            if r['stat'] not in request[side]:
                audit['missing'].append({'code': 'missing_base_stat', 'detail': side + '.' + r['stat'], 'rule_id': r['rule_id']})
                r['applied'] = False; r['reason'] = '没有对应基础属性'; continue
            request[side][r['stat']].setdefault(r['stage'], []).append(operand)

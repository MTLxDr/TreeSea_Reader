"""Per-relic counters and acquisition lifetimes, separate from screenshot counts."""
from copy import deepcopy
import json
from functools import lru_cache
from .catalog import ROOT


@lru_cache(maxsize=1)
def rules():
    path=ROOT/'assets/relics/rules.json'
    return json.loads(path.read_text(encoding='utf-8'))['rules'] if path.exists() else {}


def rule_for(identity):
    return rules().get(identity, {'mode':'unknown','counter':False,'one_use':False})


def initialize(row):
    rule=rule_for(row['id'])
    state=row.setdefault('relic_state',dict(held=not row.get('excluded',False),generation=1,
                                           trigger_count=None,events=[]))
    state.setdefault('mode',rule['mode'])
    return state


def lose(row, reason, fingerprint=None):
    state=initialize(row)
    state.update(held=False,lost_page=fingerprint)
    state['events'].append(dict(type='lost',reason=reason,generation=state['generation']))
    row['excluded']=True
    row['exclusion_reason']=reason


def merge(old, incoming, fingerprint):
    """Only an observed ownership transition can establish a new acquisition."""
    if old is None:
        state=initialize(incoming)
    else:
        state=deepcopy(initialize(old))
        if old.get('excluded'):
            if old.get('exclusion_reason') != 'lost' or state.get('lost_page') == fingerprint:
                return None
            state['held']=True
            state['generation']+=1
            state['events'].append(dict(type='reacquired',generation=state['generation'],page=fingerprint))
        incoming.update(excluded=False,relic_state=state)
    rule=rule_for(incoming['id'])
    state['held']=True
    fields=incoming.setdefault('fields',{})
    if rule['mode']=='continuous':
        fields['stacks']=dict(value=1,source='held_continuous_effect',confidence=1.,manual=False)
    elif rule['mode'] in {'one_use','on_acquire'}:
        previous_used=state.get('used',False)
        used=True if rule['mode']=='on_acquire' else fields.get('used',{}).get('value')
        generation=state['generation']
        counted=state.setdefault('triggered_generations',[])
        if used is True and generation not in counted:
            counted.append(generation)
            state['trigger_count']=state.get('trigger_offset',0)+len(counted)
            state['events'].append(dict(type='used',generation=generation,page=fingerprint))
        state['used']=used if used is not None else (previous_used if old and state['generation']==initialize(old)['generation'] else None)
        if state.get('trigger_count') is not None:
            fields['stacks']=dict(value=state['trigger_count'],source='confirmed_one_use_triggers',confidence=1.,manual=False)
    return incoming

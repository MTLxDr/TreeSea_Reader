"""Resident occupancy is separate from the node hidden beneath it."""
from collections import deque

FORBIDDEN={'诡意行商','秘境行商','险路小径','险路尽头','曲折密道','命运所指','居民据点','“居民”据点'}
RULE_URL='https://prts.wiki/w/沉沦者的黑流树海'


def graph_distances(region, start):
    graph={tuple(n['slot']):set() for n in region.get('nodes',[])}
    for a,b in region.get('edges',[]):
        a,b=tuple(a),tuple(b)
        if a in graph and b in graph:graph[a].add(b);graph[b].add(a)
    start=tuple(start);out={start:0};queue=deque([start])
    while queue:
        a=queue.popleft()
        for b in graph.get(a,[]):
            if b not in out:out[b]=out[a]+1;queue.append(b)
    return out


def infer_position(result, explicit=None):
    if explicit is not None:
        result['current_slot']=list(explicit);result['position_evidence']='人物标记'
    else:
        missing=[n for n in result.get('nodes',[]) if n['type']=='unobserved' and n.get('evidence')=='template_only' and not n.get('resident')]
        if len(missing)==1 and not result.get('needs_review'):
            result['current_slot']=missing[0]['slot'];result['position_evidence']='唯一无法匹配节点（用户指定规则）'
        else:result['current_slot']=None;result['position_evidence']='等待人物标记'
    result['at_start']=bool(result.get('current_slot') is not None and result['current_slot']==result.get('start_slot'))
    return result.get('current_slot')


def update_resident_constraints(result, initial_observations=None):
    residents=[n['slot'] for n in result.get('nodes',[]) if n.get('resident')]
    initial=list(initial_observations or (residents if result.get('at_start') else []))
    for n in result.get('nodes',[]):
        if n.get('resident') or n['slot'] in initial:n['resident_visited']=True
    candidates=[];empty_checks={}
    if initial:
        distances=[graph_distances(result,slot) for slot in initial]
        for n in result.get('nodes',[]):
            if n['slot']==result.get('start_slot') or n.get('resident_visited'):continue
            if n['type'] not in {'unobserved','unknown-ferocity','settlement'}:continue
            if not all(d.get(tuple(n['slot']),999)<=5 for d in distances):continue
            if len(initial)<=2 and not result.get('fourth_floor_memory'):
                nearby=graph_distances(result,n['slot']);known=possible=0
                for tile in result['nodes']:
                    if tile['slot']==result.get('start_slot') or nearby.get(tuple(tile['slot']),999)>5:continue
                    definite=tile['slot'] in initial or tile.get('resident_initial') or (tile['type']=='empty' and (tile.get('manual') or tile.get('confidence',0)>=.85))
                    if definite:known+=1
                    elif tile['type']=='unobserved' or tile.get('evidence')=='small_marker_candidate' or tile.get('resident_visited'):possible+=1
                empty_checks[','.join(map(str,n['slot']))]={'known':known,'possible':possible,'required':len(initial)}
                if not known<=len(initial)<=known+possible:continue
            candidates.append(n['slot'])
    result['resident_constraints']={'visible':residents,'initial':initial,
        'visited':[n['slot'] for n in result.get('nodes',[]) if n.get('resident_visited')],
        'settlement_candidates':candidates,
        'has_initial_evidence':bool(initial),'empty_count_checks':empty_checks,'source':RULE_URL}
    for n in result.get('nodes',[]):
        n['settlement_candidate']=n['slot'] in candidates
        # Initial residents start on original empty tiles, not on hidden events.
        if n['slot'] in initial:n['underlying_type']='empty';n['resident_initial']=True
        if n.get('resident_visited'):
            n['excluded_types']=sorted(FORBIDDEN)
            n['resident_evidence']='出生点初始生成' if n.get('resident_initial') else ('居民移动占领' if n.get('resident') else '居民曾经进入')
        elif not n.get('resident_initial'):n.pop('excluded_types',None)
    return result['resident_constraints']

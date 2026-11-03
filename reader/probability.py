"""Observed frequency priors conditioned on the remaining floor count budget.

This is a model built from marginal source counts, not recovered generator code.
Small joint models are enumerated; larger models use bounded rejection sampling.
"""
from collections import Counter, OrderedDict
from copy import deepcopy
import hashlib
import itertools
import json
import math

import numpy as np


def rounded_percentages(values):
    """Largest remainder rounding: displayed integer units add to 100."""
    total=sum(values)
    if total<=0:return [0]*len(values)
    exact=[100*v/total for v in values]
    units=[math.floor(v) for v in exact]
    order=sorted(range(len(values)),key=lambda i:(exact[i]-units[i],-i),reverse=True)
    for index in order[:100-sum(units)]: units[index]+=1
    return units


def fixed_terminals(result):
    if result.get("needs_review") or result.get("floor") not in (1,2,4):return
    terminals=set(map(tuple,result.get("terminal_slots",[])))
    for node in result.get("nodes",[]):
        if tuple(node["slot"]) not in terminals or node.get("manual"):continue
        node.setdefault("observed_type",node["type"])
        node.update(type="danger-end",label="险路尽头",confidence=1.,evidence="template_fixed_exit",
                    fixed_by_template=True,probabilities=None)


def fixed_nodes(result, catalog):
    fixed_terminals(result)
    if result.get('needs_review'):return
    template=next((t for t in getattr(catalog,'templates',[]) if t['template_id']==result.get('template_id')),None)
    values=template.get('fixedNodes',[]) if template else result.get('fixed_nodes',[])
    result['fixed_nodes']=deepcopy(values)
    from .catalog import NODE_NAMES
    by_slot={tuple(n['slot']):n for n in result.get('nodes',[])}
    for row in values:
        node=by_slot.get(tuple(row['slot']))
        if node is None or node.get('manual'):continue
        node.setdefault('observed_type',node['type'])
        node.update(type=row['type'],label=NODE_NAMES[row['type']],confidence=1.,evidence='template_fixed_node',
                    fixed_by_template=True,probabilities=None)


def known_counts(result):
    counts=Counter()
    for node in result.get("nodes",[]):
        if node["slot"]==result.get("start_slot"):continue
        if node["type"] in {"unobserved","unknown-ferocity","unknown-mystery"}:continue
        if node.get("manual") or node.get("evidence") in {"template_fixed_exit","template_fixed_node","initial_resident_empty"} or (
            node.get("evidence")=="ocr_label" and node.get("confidence",0)>=.85):
            counts[node["label"]]+=1
    return counts


def conditional_marginals(priors, bounds, exact_budget=80000, sample_budget=100000):
    """Each variable is a dict of type->weight; bounds are remaining min/max."""
    choices=[list(p) for p in priors]
    active={name:pair for name,pair in bounds.items() if pair[0]>0 or pair[1]<sum(name in p for p in priors)}
    if any(lo>hi or hi<0 for lo,hi in active.values()):return None,{"status":"inconsistent"}
    all_names=set().union(*priors)
    if sum(min(bounds.get(name,(0,len(priors)))[1],sum(name in p for p in priors)) for name in all_names)<len(priors):
        return None,{"status":"inconsistent"}
    if not active:return priors,{"method":"distance_frequency","status":"unconstrained"}
    marginals=[dict.fromkeys(p,0.) for p in priors]
    product_size=math.prod(len(p) for p in priors)
    if product_size<=exact_budget:
        mass=0.
        for assignment in itertools.product(*choices):
            counts=Counter(assignment)
            if not all(lo<=counts[name]<=hi for name,(lo,hi) in active.items()):continue
            weight=math.prod(priors[i][name] for i,name in enumerate(assignment))
            mass+=weight
            for i,name in enumerate(assignment):marginals[i][name]+=weight
        if mass<=0:return None,{"status":"inconsistent","method":"quota_exact"}
        return [{k:v/mass for k,v in p.items()} for p in marginals],{"status":"ok","method":"quota_exact","prior_feasible_mass":mass}
    signature=json.dumps([priors,active],sort_keys=True).encode()
    rng=np.random.default_rng(int.from_bytes(hashlib.sha256(signature).digest()[:8],"big"))
    names=sorted(set().union(*priors))
    name_index={n:i for i,n in enumerate(names)}
    accepted=drawn=0
    while drawn<sample_budget and accepted<2500:
        batch=min(5000,sample_budget-drawn)
        assignments=np.empty((batch,len(priors)),dtype=np.int16)
        for index,p in enumerate(priors):
            labels=list(p);cdf=np.cumsum(list(p.values()));cdf[-1]=1.
            samples=np.searchsorted(cdf,rng.random(batch))
            assignments[:,index]=np.asarray([name_index[n] for n in labels])[samples]
        valid=np.ones(batch,dtype=bool)
        for name,(lo,hi) in active.items():
            counts=(assignments==name_index[name]).sum(axis=1)
            valid&=(counts>=lo)&(counts<=hi)
        kept=assignments[valid]
        accepted+=len(kept);drawn+=batch
        for index,p in enumerate(marginals):
            for name in p:p[name]+=int((kept[:,index]==name_index[name]).sum())
    if accepted<100:return None,{"status":"insufficient_sampling","method":"quota_sampling","accepted":accepted,"drawn":drawn}
    return [{k:v/accepted for k,v in p.items()} for p in marginals],{
        "status":"ok","method":"quota_sampling","accepted":accepted,"drawn":drawn,
        "max_standard_error":.5/math.sqrt(accepted)}


def _cache_key(result, catalog):
    floor = str(result.get("floor"))
    if floor=="4" and getattr(catalog,"fourth_floor_memory",False):floor="追忆"
    observations = sorted((tuple(n["slot"]), n["type"], n.get("label"), n.get("distance"),
        bool(n.get("manual")), n.get("evidence"), n.get("confidence", 0) >= .85)
        for n in result.get("nodes", []))
    source = sorted((distance, values) for (fid, distance), values in catalog.distance_counts.items()
                    if fid == str(floor))
    values = ["map-probability-6", bool(getattr(catalog,"use_observed_ranges",True)), result.get("fixed_nodes"), floor, result.get("template_id"), observations,
        [(n['slot'],bool(n.get('resident')),n.get('excluded_types'),n.get('resident_initial'),n.get('resident_visited')) for n in result.get('nodes',[])],
        result.get('resident_constraints',{}),
        sorted(tuple(sorted((tuple(a), tuple(b)))) for a, b in result.get("edges", [])),
        result.get("start_slot"), sorted(map(tuple, result.get("terminal_slots", []))),
        bool(result.get("needs_review")), bool(result.get("needs_node_review")),
        catalog.count_limits.get(str(floor), {}), source]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def refresh_map_probabilities(result, catalog):
    """Reuse results only when observations and source counts are unchanged."""
    if result.get("page_type") != "map":
        return
    fixed_nodes(result,catalog)
    result['fourth_floor_memory']=result.get('floor')==4 and getattr(catalog,'fourth_floor_memory',False)
    if 'resident_constraints' in result or any(n.get('resident') or n.get('resident_visited') for n in result.get('nodes',[])):
        from .resident_tracking import update_resident_constraints
        update_resident_constraints(result,result.get('resident_constraints',{}).get('initial'))
    for node in result.get('nodes',[]):
        if node.get('resident_initial') and not node.get('manual') and not node.get('fixed_by_template'):
            node.update(type='empty',label='林间空地',underlying_type='empty',evidence='initial_resident_empty',confidence=1.)
    key = _cache_key(result, catalog)
    cache = getattr(catalog, "_map_probability_cache", None)
    if cache is None:
        cache = catalog._map_probability_cache = OrderedDict()
    cached = cache.get(key)
    if cached is not None:
        cache.move_to_end(key)
        for node in result.get("nodes", []):
            saved = cached["nodes"][tuple(node["slot"])]
            node.pop("probability_error", None)
            node.update(deepcopy(saved))
        for name in ("floor_node_counts", "probability_model"):
            result.pop(name, None)
            if name in cached:
                result[name] = deepcopy(cached[name])
    else:
        result.pop("floor_node_counts", None)
        result.pop("probability_model", None)
        _calculate_map_probabilities(result, catalog)
        saved = {"nodes": {tuple(n["slot"]): {k: deepcopy(n[k]) for k in
                    ("probabilities", "probability_error") if k in n}
                    for n in result.get("nodes", [])}}
        for name in ("floor_node_counts", "probability_model"):
            if name in result:
                saved[name] = deepcopy(result[name])
        cache[key] = saved
        if len(cache) > 32:
            cache.popitem(last=False)
    result["probability_cache"] = {"hit": cached is not None, "key": key}
    for node in result.get('nodes',[]):
        node['high_value']=[dict(name=r['name'],probability=r['probability']) for r in (node.get('probabilities') or {}).get('estimates',[])
                            if r['name'] in {'秘境行商','误入奇境'} and r['probability']>0]


def _calculate_map_probabilities(result,catalog):
    if result.get("page_type")!="map":return
    fixed_terminals(result)
    statistics_floor="追忆" if result["floor"]==4 and getattr(catalog,"fourth_floor_memory",False) else str(result["floor"])
    if result.get("needs_node_review"):
        for node in result.get("nodes", []):
            node["probabilities"] = None
            node["probability_error"] = "连续识别的节点类型冲突，请手动确认对应节点后再计算。"
        result["probability_model"] = {"status": "needs_node_review"}
        return
    nodes=[]
    resident_rules=result.get('resident_constraints',{})
    for node in result.get("nodes",[]):
        node.pop("probability_error",None)
        node["probabilities"]=catalog.probabilities(result["floor"],node.get("distance"),node["type"])
        if node.get('resident_visited') and node['type']=='unobserved':
            alternatives=[catalog.probabilities(result['floor'],node.get('distance'),k) for k in ('unknown-mystery','unknown-ferocity')]
            estimates=[deepcopy(r) for p in alternatives if p for r in p['estimates']]
            # Residents may also enter original empty tiles and viewpoints.
            # Neither belongs to the two hidden event families above.
            distance_sources=catalog.distance_counts.get((statistics_floor,node.get('distance')), {})
            if len(distance_sources)==1:
                document,counts=next(iter(distance_sources.items()))
                allowed={r['name'] for r in estimates}|{'林间空地','羽瞰点'}
                estimates=[{'name':name,'count':count,'probability':0.} for name,count in counts.items()
                    if name in allowed and count>0]
            else:document=None
            total_count=sum(r['count'] for r in estimates)
            sources={document} if document is not None else {p['document_id'] for p in alternatives if p}
            if total_count and len(sources)==1:
                for row in estimates:row['probability']=row['count']/total_count
                node['probabilities']={'estimates':estimates,'conditions':'居民覆盖、楼层及距起点距离',
                    'sample_count':total_count,'document_id':next(iter(sources)),
                    'limitation':'按可进入类型的楼层距离计数估算，包含林间空地及羽瞰点；尚无居民移动选择的联合频率。'}
        if node.get('probabilities'):
            excluded=set(node.get('excluded_types',[]))
            if resident_rules.get('has_initial_evidence') and node['slot'] not in resident_rules.get('settlement_candidates',[]):
                excluded.update({'居民据点','“居民”据点'})
            rows=node['probabilities']['estimates']
            for r in rows:
                if r['name'] in excluded:r['probability']=0;r['resident_excluded']=True
            total=sum(r['probability'] for r in rows)
            if total:
                for r in rows:r['probability']/=total
            else:node['probabilities']=None;node['probability_error']='居民规则下没有符合条件的节点类型，核对位置或节点标记'
        if node["probabilities"]:nodes.append(node)
    if result.get("needs_review") or not nodes:return
    limits=catalog.count_limits.get(statistics_floor,{})
    counts=known_counts(result)
    result["floor_node_counts"]={"confirmed":dict(counts),"limits":deepcopy(limits),
        "scope":"main map excluding start; OCR >= .85 or manual/template confirmed", "limit_source":"lubiao observed ranges"}
    bounds={name:(0,row["maximum"]-counts[name]) for name,row in limits.items()}
    # Lower bounds are only valid when all remaining positions are represented.
    unmodelled=any(n["type"]=="unobserved" or n.get("evidence")=="small_marker_candidate" for n in result["nodes"]
                    if n["slot"]!=result.get("start_slot"))
    if not unmodelled:
        bounds={name:(max(0,row["minimum"]-counts[name]),row["maximum"]-counts[name]) for name,row in limits.items()}
    priors=[{r["name"]:r["probability"] for r in n["probabilities"]["estimates"] if r["probability"]>0} for n in nodes]
    posterior,model=conditional_marginals(priors,bounds) if getattr(catalog,'use_observed_ranges',True) else (priors,{'method':'distance_frequency','status':'ranges_disabled'})
    if posterior is None:
        model.update(range_status=model['status'],status='distance_fallback',range_bounds=deepcopy(bounds))
        posterior=priors
    model['bounds_kind']='observed_range'
    model['statistics_floor']=statistics_floor
    model.update(confirmed_counts=dict(counts),lower_bounds_applied=not unmodelled,
        limitation="按各节点距离频率乘积构造先验，再按本层剩余数量约束条件化；不是已证实的游戏生成概率。")
    result["probability_model"]=model
    for node,p in zip(nodes,posterior or [None]*len(nodes)):
        old=node["probabilities"]
        if p is None:
            node["probabilities"]=None
            node["probability_error"]="数量约束与当前观察冲突，或有效联合样本不足；请核对节点及本层模式。"
            continue
        for row in old["estimates"]:
            row["prior_probability"]=row["probability"]
            row["probability"]=p.get(row["name"],0.)
            row["confirmed_count"]=counts[row["name"]]
            row["maximum"]=limits.get(row["name"],{}).get("maximum")
            row["range_model_zero"]=row["name"] in bounds and bounds[row["name"]][1]==0 and row["probability"]==0
            row["hard_zero"]=False
        old["estimates"].sort(key=lambda r:-r["probability"])
        old["model"]=deepcopy(model)
        old["conditions"]="楼层、距起点距离、未知类别、已确认数量及观测数量范围（估算）"
        old["limitation"]=model["limitation"]

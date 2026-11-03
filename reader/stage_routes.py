"""Expose source route commands and scheduler-relative spawn times."""


def stage_events(stage,names):
    level=stage['level'];rows=[]
    def actions(actions,scope,pre_delay):
        for action in actions:
            key=action.get('key','')
            if action.get('actionType') not in {'SPAWN','ACTIVATE_PREDEFINED'}:continue
            delay=action.get('preDelay',0)
            name=names.get(key,names.get(key.split('#')[0],key)) or '未指定单位'
            prefix=f'{scope}（前置{pre_delay:g}s）' if pre_delay else scope
            rows.append({'scope':scope,'delay':delay,'fragment_pre_delay':pre_delay,'time':f'{prefix} + {delay:g}s',
                'key':key,'name':name,'count':action.get('count',1),'interval':action.get('interval',0),
                'route':action.get('routeIndex',0),'hidden_group':action.get('hiddenGroup'),
                'random_group':action.get('randomSpawnGroupKey'),'random_pack':action.get('randomSpawnGroupPackKey'),
                'weight':action.get('weight'),'block_fragment':action.get('blockFragment',False),
                'action_type':action['actionType'],'proto':'shsdgo' in key or 'proto' in name.lower(),
                'npc':any(s in name for s in ('探险','冒险')),
                'raw':action})
    for i,wave in enumerate(level.get('waves') or [],1):
        for j,fragment in enumerate(wave.get('fragments',[]),1):
            actions(fragment.get('actions',[]),f'第{i}波第{j}片段',fragment.get('preDelay',0))
    for branch,value in (level.get('branches') or {}).items():
        for i,phase in enumerate(value.get('phases',[]),1):
            actions(phase.get('actions',[]),f'触发分支 {branch} 第{i}段',phase.get('preDelay',0))
    return rows


def placed_units(stage,names):
    rows=[]
    activations=stage_events(stage,names)
    for group in ('predefines','hardPredefines'):
        for kind in ('tokenInsts','characterInsts'):
            for unit in (stage['level'].get(group) or {}).get(kind,[]) or []:
                identity=unit.get('inst',{}).get('characterKey','');name=names.get(identity,identity)
                conditions=[r for r in activations if r['action_type']=='ACTIVATE_PREDEFINED' and r['key'] in {identity,unit.get('alias')}]
                rows.append({'id':identity,'name':name,'position':unit.get('position'),'conditions':conditions,
                    'alias':unit.get('alias'),'hidden':unit.get('hidden'),
                    'npc':any(s in name for s in ('探险','冒险')),'group':group,'raw':unit})
    return rows


def route_points(route):
    points=[route.get('startPosition',{})]
    points.extend(c['position'] for c in route.get('checkpoints',[]) or [] if c.get('type') in {'MOVE','APPEAR_AT_POS'} and c.get('position'))
    points.append(route.get('endPosition',{}))
    return [p for p in points if 'row' in p and 'col' in p]


def route_segments(route):
    """Keep disappearance/appearance distinct from continuous movement."""
    current=route.get('startPosition');hidden=False;segments=[]
    for checkpoint in route.get('checkpoints') or []:
        kind=checkpoint.get('type')
        if kind=='DISAPPEAR':hidden=True
        elif kind in {'MOVE','APPEAR_AT_POS'}:
            position=checkpoint.get('position')
            if not position or 'row' not in position or 'col' not in position:continue
            if current is not None:
                segments.append({'start':current,'end':position,
                    'kind':'teleport' if kind=='APPEAR_AT_POS' else 'move' if not hidden else 'hidden'})
            current=position;hidden=False
    end=route.get('endPosition')
    if current is not None and end is not None and current!=end and not hidden:
        segments.append({'start':current,'end':end,'kind':'move'})
    return segments


def portal_links(level):
    from .stage_tiles import tile_at
    pairs={}
    for name in ('routes','extraRoutes'):
        for index,route in enumerate(level.get(name) or []):
            for segment in route_segments(route):
                if segment['kind']!='teleport':continue
                a,b=segment['start'],segment['end'];ta,tb=tile_at(level,a),tile_at(level,b)
                if not ta or not tb or ta['tileKey']!='tile_telin' or tb['tileKey']!='tile_telout':continue
                key=(a['row'],a['col'],b['row'],b['col'])
                pairs.setdefault(key,{'entry':a,'exit':b,'routes':[]})['routes'].append((name,index))
    return [dict(value,id=i) for i,(_,value) in enumerate(sorted(pairs.items()),1)]

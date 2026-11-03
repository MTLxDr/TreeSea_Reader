"""Catalog every sourced Blackstream effect before compiling calculation rules.

Catalogued means that the complete source effect, conditions and parameters are
retained. It does not mean every prose mechanic has been verified as executable.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from collect_prts import template_blocks,split_top_level
from collect_operator_details import record

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/effects'
BUCKETS={'直接加算':'direct_add','直接乘算':'direct_mul','最终加算':'final_add','最终乘算':'final_mul',
    '藏品符文：加算':'relic_add','藏品符文：乘算':'relic_mul','藏品符文：后置乘算':'relic_post_mul'}
STATS={'目标生命上限':'player_life','目标生命值':'player_life','攻击速度':'attack_speed','攻击间隔':'attack_interval','再部署时间':'respawn_time','生命上限':'max_hp','最大生命':'max_hp',
    '生命值':'max_hp','攻击力':'atk','防御力':'def','法术抗性':'res','法抗':'res','移动速度':'move_speed','技力':'sp','伤害':'damage',
    '护盾':'shield','阻挡数':'block_count','源石锭':'ingots','希望':'hope','目标生命':'player_life'}
BB_STATS={'atk':'atk','def':'def','max_hp':'max_hp','maxHp':'max_hp','attack_speed':'attack_speed','respawn_time':'respawn_time',
          'magic_resistance':'res','magicResistance':'res','move_speed':'move_speed','sp_recovery_per_sec':'sp_recovery'}
CONDITION=re.compile(r'(?:若|如果|当|每(?:次|有|名|使|经|隔|过|拥有)|仅(?:在|对|当|限)|首次|初始|持有|携带|(?<!再)部署(?:时|后)|进入|击倒|击杀|完成|触发|技能(?:开启|结束|期间)|生命(?:值)?(?:低于|高于)|阻挡(?:时|后)|周围|附近|持续\d)[^。；\n]{0,100}')


def dumps(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(dumps(value).encode()).hexdigest()
def text_value(value):
    text=str(value or '')
    text=re.sub(r'<br\s*/?>','\n',text,flags=re.I)
    return re.sub(r'<[^>]*>','',text)


def prose_notes(section):
    selected=[];end=0
    for offset,raw in template_blocks(section):
        if offset<end:continue
        name=split_top_level(raw[2:-2],'|')[0].strip()
        if name in {'技能','技能2','技能3','模组','模组2','属性'} or name.startswith('天赋列表'):
            selected.append((offset,raw));end=offset+len(raw)
    for offset,raw in reversed(selected):section=section[:offset]+section[offset+len(raw):]
    return section


def scalar(value):
    match=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(%)?',str(value).strip())
    if not match:return None
    return float(match[1])/(100 if match[2] else 1)


def annotations(text,payload):
    """Only explicit source labels or primitive rune keys get operation types."""
    rows=[]
    for line in payload.get('semanticEffect',{}).get('lines',[]):
        tooltip=line.get('tooltip','')
        bucket=next((value for key,value in sorted(BUCKETS.items(),key=lambda p:-len(p[0])) if key in tooltip),None)
        if not bucket:continue
        label=line.get('label',line.get('text',''))
        stat=next((value for key,value in STATS.items() if key in label),None)
        value=scalar(line.get('value',''))
        rows.append({'stat':stat,'bucket':bucket,'value':value,'raw':line,'evidence':'source_tooltip',
                     'unit':'ratio_delta' if '%' in line.get('value','') else 'flat'})
    rune_buckets={'char_attribute_add':'relic_add','char_attribute_mul':'relic_mul'}
    for rune in payload.get('topicBuffs',[]):
        if rune.get('key') not in rune_buckets:continue
        for item in rune.get('blackboard',[]):
            if item.get('key') in BB_STATS and isinstance(item.get('value'),(int,float)):
                rows.append({'stat':BB_STATS[item['key']],'bucket':rune_buckets[rune['key']],'value':item['value'],
                    'raw':item,'rune_key':rune['key'],'evidence':'source_rune_parameter','unit':'ratio_delta' if rune['key'].endswith('_mul') else 'flat'})
    # Display annotations are kept as evidence, never inferred solely from '+%'.
    for name,bucket in BUCKETS.items():
        if name in text and not any(r['bucket']==bucket for r in rows):
            rows.append({'stat':None,'bucket':bucket,'value':None,'raw':name,'evidence':'source_prose','unit':'unknown'})
    unique={dumps(r):r for r in rows}
    return list(unique.values())


class Builder:
    def __init__(self,db):
        self.db=db;self.entries=[];self.expected=Counter();self.actual=Counter();self.missing=[]

    def add(self,category,key,name,text,source,variant='',notes='',payload=None,context=None):
        payload=payload or {};context=context or {}
        body=text_value(text);notes=text_value(notes)
        source=dict(source)
        whole=body+'\n'+notes
        mods=annotations(whole,payload)
        friendly=bool(re.search('我方|干员|友方|友军',whole)) or category.startswith('干员')
        enemy=bool(re.search('敌方|敌人|敌军|目标敌',whole)) or category.startswith('敌人')
        side='both' if friendly and enemy else 'friendly' if friendly else 'enemy' if enemy else 'unspecified'
        number_text=re.sub(r'<!--.*?-->','',body,flags=re.S)
        number_text=re.sub(r'#[0-9A-Fa-f]{3,8}\b','',number_text)
        tokens=[{'text':m[0],'number':float(m[1]),'unit':m[2] or 'unspecified'} for m in
                re.finditer(r'([+-]?\d+(?:\.\d+)?)\s*(%|秒|s\b|层|次|点|格|倍)?',number_text)]
        conditions=list(dict.fromkeys(CONDITION.findall(body)))
        conditions+=payload.get('semanticEffect',{}).get('conditionTags',[])
        bounds=[m[0] for m in re.finditer(r'(?:最多|最高|至多|上限|不超过|可叠加)[^，。；\n]{0,45}',whole)]
        status='source_annotated' if mods else 'source_parameters' if payload.get('topicBuffs') else 'catalogued'
        entry={'id':digest([source['provider'],source['url'],source['sha256'],category,key,variant,context.get('source_record_id')]),'category':category,
            'subject_id':key,'name':name,'variant':str(variant),'side':side,'raw_effect':body,'raw_notes':notes,
            'source':source,'context':context,'conditions_raw':conditions,'stack_limits_raw':bounds,
            'targets_raw':list(dict.fromkeys(re.findall(r'【[^】]+】',body))),
            'numeric_tokens':tokens,'modifiers':mods,'source_parameters':payload,'review_status':status,
            'execution_status':'not_compiled' if tokens or mods or payload.get('topicBuffs') or re.search('免疫|隐匿|沉睡|召唤|伤害|技能|生命|防御|攻击|治疗|状态|？？',body) else 'non_numeric',
            'buckets':sorted({r['bucket'] for r in mods}),'has_numeric':bool(tokens or mods)}
        self.entries.append(entry);self.actual[category]+=1
        return entry

    def source(self,row):
        return {'document_id':row['document_id'],'provider':row['provider'],'url':row['source_url'],
                'sha256':row['sha256'],'revision_id':row['revision_id'],'content_version':row['content_version']}

    def entities(self):
        query='''SELECT e.*,d.provider,d.source_url,d.sha256,d.revision_id,d.content_version FROM entities e
            JOIN active_documents a ON a.document_id=e.document_id JOIN documents d ON d.id=e.document_id'''
        allowed={'藏品','零件','分队','模式','乌托邦','实托邦','stage_detail','stage_enemy','event'}
        for row in self.db.execute(query):
            if row['kind'] not in allowed:continue
            data=json.loads(row['payload_json']);kind=row['kind'];source=self.source(row)
            if kind=='stage_enemy':
                self.expected['敌人关卡参数']+=1
                self.add('敌人关卡参数',row['entity_id'],row['name'],data.get('description',''),source,
                    variant=f"关卡 {row['entity_id'].split(':')[0]} / 等级 {data.get('level',0)}",payload=data,
                    context={'stage_variant':row['entity_id'],'attributes':data.get('attributes',{}),
                        'reference_only':data.get('referenceOnly',False),'conditional':data.get('conditional',False)})
            elif kind=='stage_detail':
                self.expected['关卡环境与规则']+=1
                self.add('关卡环境与规则',row['entity_id'],row['name'],dumps(data.get('rules',[])),source,payload=data,
                    context={'options':data.get('options',{}),'devices':data.get('devices',[]),'level_id':data.get('levelId')})
            elif kind=='event':
                self.expected['事件及节点选择']+=1
                self.add('事件及节点选择',row['entity_id'],row['name'],dumps(data),source,payload=data)
            else:
                category='路标'+kind
                self.expected[category]+=1
                self.add(category,data.get('itemId',row['entity_id']),data.get('name',row['name']),data.get('rawEffect',''),source,
                    variant=data.get('variant',data.get('effectNumber','')),payload=data,
                    context={'game_item_id':data.get('itemId'),'dataset_version':data.get('introducedVersion','unspecified'),
                        'source_record_id':row['entity_id'],'calculation_type_raw':data.get('calculationType')})
        for row in self.db.execute('''SELECT c.*,d.provider,d.source_url,d.sha256,d.revision_id,d.content_version FROM current_prts_catalog c JOIN documents d ON d.id=c.document_id'''):
            data=json.loads(row['payload_json']);fields=data['fields'];category='PRTS藏品' if row['kind']=='prts_relic' else 'PRTS零件'
            self.expected[category]+=1
            self.add(category,row['entity_id'],row['name'],fields.get('效果',''),self.source(row),
                notes=fields.get('备注',''),payload=data,context={'rarity':fields.get('稀有度'),'tags':fields.get('角标'),'source_template':data['template']})

    def operators(self):
        query='''SELECT t.*,d.provider,d.source_url,d.sha256,d.revision_id,d.content_version FROM active_operator_details t JOIN documents d ON d.id=t.document_id'''
        for row in self.db.execute(query):
            data=json.loads(row['payload_json']);source=self.source(row);components=data['components'];oid=row['operator_id'];name=row['name']
            notes={k:prose_notes(v) for k,v in data.get('sections',{}).items() if k in {'技能','天赋','模组'}}
            for kind,label in [('skills','干员技能'),('talents','干员天赋'),('potentials','干员潜能'),('modules','干员模组'),('attributes','干员基础面板')]:
                for index,component in enumerate(components.get(kind,[])):
                    f=component['fields'];key=f'{oid}:{kind}:{index}'
                    if kind=='skills':
                        for level in component.get('levels',[]):
                            self.expected[label]+=1
                            v=f"技能{component['index']} {f.get('技能名','')} / 等级{level['level']} / 专精{level['mastery']}"
                            self.add(label,key,name,level['description'],source,variant=v,payload={'level_values':level,'source_component_ref':f'components.skills.{index}'},
                                notes=notes.get('技能',''),context={'operator_id':oid,'skill_index':component['index'],
                                    'level':level['level'],'mastery':level['mastery'],'sp_cost':level.get('sp_cost'),
                                    'initial_sp':level.get('initial_sp'),'duration':level.get('duration'),
                                    'sp_type':f.get('技能类型1'),'activation':f.get('技能类型2'),
                                    'exclusive_group':f'{oid}:skill:{component["index"]}'})
                    elif kind=='talents':
                        variants=[k for k in f if re.fullmatch(r'(?:潜能增强_)?天赋\d+效果',k)]
                        for field in variants or ['__all__']:
                            self.expected[label]+=1
                            base=field[:-2] if field!='__all__' else ''
                            self.add(label,key,name,f.get(field,dumps(f)),source,variant=f'{index+1} / '+base,
                                notes=notes.get('天赋',''),payload={'fields':{k:v for k,v in f.items() if k.startswith(base)},'source_component_ref':f'components.talents.{index}'},
                                context={'operator_id':oid,'condition':f.get(base+'条件'),'potential_threshold':f.get('潜能增强'),
                                    'potential_enhanced':field.startswith('潜能增强_'),'exclusive_group':f'{oid}:talent:{index}'})
                    elif kind=='potentials':
                        for field,value in f.items():
                            if not re.fullmatch('潜能[2-6]',field):continue
                            self.expected[label]+=1
                            self.add(label,key,name,value,source,variant=field,payload={'source_component_ref':f'components.potentials.{index}'},context={'operator_id':oid,'potential':int(field[-1]),'cumulative':True})
                    elif kind=='modules':
                        for level in (1,2,3):
                            self.expected[label]+=1
                            attrs={k:f.get(k+(str(level) if level>1 else ''),None) for k in ['生命','攻击','防御','攻速','再部署','法抗','攻击速度','阻挡数']}
                            words=[f.get('特性',''),f.get('特性'+str(level),''),f.get('天赋'+str(level),''),f.get('潜能天赋'+str(level),'')]
                            self.add(label,key,name,'\n'.join(w for w in words if w),source,
                                variant=f"{f.get('名称','')} / {f.get('类型','')} / 等级{level}",payload={'fields':{k:v for k,v in f.items() if not any(s in k for s in ['基础信息','材料消耗','任务'])},'source_component_ref':f'components.modules.{index}'},
                                context={'operator_id':oid,'module_type':f.get('类型'),'level':level,'attributes':attrs,
                                    'unlock_level':f.get('解锁等级'),'unlock_trust':f.get('解锁信赖'),
                                    'exclusive_group':f'{oid}:equipped_module','level_values_are_replacements':True})
                    else:
                        self.expected[label]+=1
                        self.add(label,key,name,dumps(f),source,payload=component,context={'operator_id':oid})
                if not components.get(kind) and kind in {'skills','potentials','modules'}:
                    self.expected[label+'说明']+=1
                    section={'skills':'技能','potentials':'潜能提升','modules':'模组'}[kind]
                    self.add(label+'说明',oid,name,data.get('sections',{}).get(section,'来源页面未列出此类参数；不等于已证明效果为零。'),source,context={'operator_id':oid})

    def combat_pages(self):
        folder=ROOT/'data/sources/combat_prts';manifest=json.loads((folder/'latest_manifest.json').read_text(encoding='utf-8'))
        self.missing=manifest.get('failures',[])
        for page in manifest['pages']:
            data=json.loads((folder/page['path']).read_text(encoding='utf-8'));text=data['wikitext'];title=page['title']
            doc=self.db.execute('SELECT id FROM documents WHERE provider=? AND source_url=? AND sha256=?',('prts_combat',page['url'],page['sha256'])).fetchone()
            if not doc:raise ValueError('Combat source is not imported: '+title)
            source={'document_id':doc[0],'provider':'prts_combat','url':page['url'],'sha256':page['sha256'],
                'revision_id':page.get('revision_id'),'content_version':'unknown'}
            if title in {'游戏数据基础','作战机制'}:
                heads=list(re.finditer(r'^={2,3}\s*([^=\n]+)\s*={2,3}\s*$',text,re.M))
                for i,m in enumerate(heads):
                    section=text[m.end():heads[i+1].start() if i+1<len(heads) else len(text)]
                    if not any(k in m[1] for k in ['属性','伤害','符文','损伤','数值','攻击','防御','抗性','治疗','状态','生命','修饰','增益','免疫']):continue
                    self.expected['基础结算规则']+=1
                    self.add('基础结算规则',title+':'+m[1],m[1],section,source,context={'page':title})
                continue
            if title.startswith('沉沦者的黑流树海'):
                if title.endswith(('拟造物质编目','引擎配件目录','黑流数据库')):continue
                heads=list(re.finditer(r'^={2,4}\s*([^=\n]+?)\s*={2,4}\s*$',text,re.M))
                for i,m in enumerate(heads):
                    body=text[m.end():heads[i+1].start() if i+1<len(heads) else len(text)]
                    if not re.search(r'\d|效果|加成|增益|降低|提高|伤害|生命|攻击|防御',body):continue
                    if any(k in m[1] for k in ['音乐','剧情','更新记录','数据库','蚀刻章','委托','兑换','注释']):continue
                    self.expected['主题机制与养成']+=1
                    self.add('主题机制与养成',title+':'+str(i),title+' / '+m[1],body,source,context={'heading':m[1]})
                continue
            found=False
            for index,(offset,raw) in enumerate(template_blocks(text)):
                name=split_top_level(raw[2:-2],'|')[0].strip()
                if not name.startswith('敌人信息/'):continue
                component=record(raw,offset);f=component['fields'];found=True
                self.expected['敌人技能与天赋']+=1
                effects={k:v for k,v in f.items() if any(word in k for word in ['效果','能力','天赋','技能'])}
                self.add('敌人技能与天赋',title+':'+str(index),title,dumps(effects),source,
                    variant='级别'+str(f.get('index','基础信息')),payload=component,
                        context={'enemy_name':f.get('名称',title),'level':f.get('index'),'attributes':f,
                        'all_source_notes_ref':'wikitext'})
            if not found:
                self.expected['敌人机制补充']+=1
                self.add('敌人机制补充',title,title,text,source,context={'no_enemy_template':True})

    def save(self):
        if self.expected!=self.actual:raise ValueError('Coverage mismatch')
        ids=[e['id'] for e in self.entries]
        if len(set(ids))!=len(ids):raise ValueError('Duplicate effect IDs')
        OUT.mkdir(parents=True,exist_ok=True)
        summary={'scope':'黑流树海本体及DLC；当前已获取的所有来源记录','source_coverage_complete':self.expected==self.actual,
            'entry_count':len(self.entries),'expected_by_category':dict(self.expected),'actual_by_category':dict(self.actual),
            'review_status':dict(Counter(e['review_status'] for e in self.entries)),
            'execution_status':dict(Counter(e['execution_status'] for e in self.entries)),
            'numeric_entries':sum(e['has_numeric'] for e in self.entries),
            'unresolved_reference_pages':self.missing,
            'boundary':'Source coverage is complete for enumerated datasets. Missing standalone enemy variant pages and uncompiled prose remain explicit; no zero-effect assumptions.'}
        data={'schema_version':1,'summary':summary,'entries':self.entries}
        data['content_sha256']=digest(data)
        temp=OUT/'catalog.json.tmp';temp.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8');temp.replace(OUT/'catalog.json')
        (ROOT/'reports/effect_coverage.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    db=sqlite3.connect(ROOT/'data/reader.sqlite3');db.row_factory=sqlite3.Row
    builder=Builder(db);builder.entities();builder.operators();builder.combat_pages();builder.save();db.close()


if __name__=='__main__':main()

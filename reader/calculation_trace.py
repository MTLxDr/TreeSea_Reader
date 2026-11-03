"""Source-labelled operands and readable arithmetic, shared by API and UI."""
from copy import deepcopy
import math

SOURCE_LABELS={'base':'基础面板','skill':'技能','talent':'天赋','relic':'藏品','part':'零件',
    'map':'地图/关卡','buff':'增减益','enemy':'敌人机制','module':'模组','potential':'潜能',
    'trust':'信赖','level':'等级','manual':'手工输入','unknown':'未确认来源'}


def unpack_operand(raw,label='value'):
    source={'kind':'manual','name':'未标注来源'}
    value=raw
    if isinstance(raw,dict):
        if set(raw)-{'value','source'} or 'value' not in raw:raise ValueError(label+' requires value and optional source')
        value=raw['value'];supplied=raw.get('source')
        if 'source' in raw and supplied is None:raise ValueError(label+'.source cannot be null')
        if supplied is not None:
            if not isinstance(supplied,dict) or set(supplied)-{'kind','name','id','url','revision','detail'}:
                raise ValueError(label+'.source has unknown fields or is not an object')
            if not isinstance(supplied.get('kind'),str) or supplied['kind'] not in SOURCE_LABELS:raise ValueError(label+'.source.kind is unknown')
            if not isinstance(supplied.get('name'),str) or not supplied['name'].strip():raise ValueError(label+'.source.name is required')
            source=deepcopy(supplied)
            for key,item in source.items():
                if key in {'id','revision'} and isinstance(item,int) and not isinstance(item,bool):source[key]=str(item);continue
                if not isinstance(item,str) or len(item)>4000:raise ValueError(label+'.source.'+key+' must be a string of at most 4000 characters')
    if isinstance(value,bool) or not isinstance(value,(int,float)):raise ValueError(label+' must be a finite JSON number')
    try:value=float(value)
    except OverflowError as error:raise ValueError(label+' is outside the finite numeric range') from error
    if not math.isfinite(value):raise ValueError(label+' must be a finite JSON number')
    return {'value':value,'source':source,'field':label}


def operands(spec,fields):
    result=[]
    for key in fields:
        raw=spec.get(key,[])
        if not isinstance(raw,list):raw=[raw]
        result.extend(unpack_operand(item,key) for item in raw)
    return result


def fmt(value):return f'{value:.12g}'


def sum_text(values):return ' + '.join('('+fmt(v)+')' if v<0 else fmt(v) for v in values) or '0'


def product_text(values):return ' × '.join(fmt(v) for v in values) or '1'


def explain(row):
    lines=[row.get('title',''),row.get('formula','')]
    for term in row.get('contributors',[]):
        source=term['source'];state='（本组未选中）' if term.get('applied') is False else ''
        lines.append(f"{fmt(term['value'])} ← {SOURCE_LABELS.get(source['kind'],source['kind'])}：{source['name']}{state}")
        for key in ('detail','url','revision'):
            if source.get(key):lines.append(f"  { {'detail':'说明','url':'来源','revision':'修订'}[key] }: {source[key]}")
    return '\n'.join(line for line in lines if line)

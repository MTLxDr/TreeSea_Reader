"""Compare source-attributed arithmetic against saved PRTS / ArkDPS references.

Reads public source snapshots as text only; never executes downloaded JavaScript.
This checks single packets and attribute ordering, not full skill timelines.
"""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reader.calculation import calculate,calculate_attribute,ALGORITHM_VERSION
from reader.calculation_ui import example_request


def main():
    folder=ROOT/'reports/calculation/reference/ark-dps'
    main_script=next(folder.glob('main-*.js'))
    source=main_script.read_text(encoding='utf-8')
    snippet=next(folder.glob('formula-snippet-*.js'))
    formulas=snippet.read_text(encoding='utf-8')
    html=(folder/'index.html').read_text(encoding='utf-8')
    # Check that the preserved website actually contains the formula and preset.
    if '* (1 + B) + C) * D' not in source:raise AssertionError('ArkDPS attribute formula changed')
    base=float(re.search(r'JC01X:\{attack:(\d+)',source).group(1))
    start=formulas.index('JC01_X_skill3:')
    if '3.25+' not in formulas[start:start+1200]:raise AssertionError('SilverAsh coefficient changed')
    visible=None
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',html,re.S):
        text=re.sub(r'\s+',' ',re.sub('<[^>]+>',' ',row)).strip()
        if text.startswith('银灰3真银斩 '):visible=text;break
    if not visible or '2707 (物理)' not in visible or '271 (法术)' not in visible:raise AssertionError('Displayed ArkDPS preset changed')
    request=example_request()
    if request['attacker']['atk']['base']['value']!=base:raise AssertionError('Local sample base differs from site')
    cases=[]
    for name,kind,scale,expected,displayed in [('silverash_s3_physical','physical',1,base*3.25,2707),
            ('silverash_s3_module_arts','arts',.1,base*3.25*.1,271)]:
        data=deepcopy(request);data['hit'].update(type=kind,attack_scale={'value':scale,'source':{
            'kind':'skill' if kind=='physical' else 'module','name':'真银斩普通伤害包' if kind=='physical' else 'X模附加法伤（站点固定配置）','url':'https://ark-dps.com/'}})
        result=calculate(data);actual=result['hp_damage']
        if abs(actual-expected)>1e-8 or round(actual)!=displayed:raise AssertionError((name,actual,expected,displayed))
        cases.append({'name':name,'expected':expected,'actual':actual,'website_rounded_dph':displayed,'passed':True})
        path=ROOT/'reports/calculation'/name
        path.with_suffix('.request.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        path.with_suffix('.result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    order=[]
    for base,flat,rate,added,factor in [(1000,0,1.6,500,.6),(1000,50,1.6,500,.6),(500,0,.5,100,1.2),(500,40,-.4,200,.8)]:
        spec={'base':base,'direct_add':[flat],'direct_mul_delta':[rate],'final_add':[added],'final_factors':[factor]}
        expected=((base+flat)*max(0,1+rate)+added)*factor
        alternate=(base+flat)*max(0,1+rate)*factor+added
        actual=calculate_attribute(spec,'atk')['value']
        if abs(actual-expected)>1e-8 or abs(actual-alternate)<1e-8:raise AssertionError('Final-add ordering is not distinguishable')
        order.append({'input':spec,'prts_and_arkdps_formula':expected,'swapped_order':alternate,'actual':actual,'passed':True})
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'algorithm_version':ALGORITHM_VERSION,
        'sources':['https://ark-dps.com/','https://prts.wiki/w/游戏数据基础'],
        'reference_scope':'Two displayed SilverAsh S3 damage packets in the website fixed preset, plus four arithmetic cases against its published attribute formula. No full DPS/timeline validation.',
        'conclusion':'PRTS and ArkDPS both apply FINAL_ADDITION before FINAL_SCALER. Attack-scale coefficients are before DEF/RES and do not change white values.',
        'single_packet_cases':cases,'order_comparison_cases':order,
        'website_display_preset_verified':True,'snapshot_hashes':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in [folder/'index.html',main_script,snippet]},
        'partial_snapshots':[path.name for path in folder.glob('*') if path.stat().st_size==1000000]}
    output=ROOT/'reports/calculation/prts_arkdps_comparison.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'single_packets':len(cases),'order_cases':len(order),'all_passed':True,'report':str(output)},ensure_ascii=False))


if __name__=='__main__':main()

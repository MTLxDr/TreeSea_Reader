"""Compare Python, CLI and loopback HTTP results; export reviewable examples."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reader.calculation import calculate,SCHEMA_VERSION,ALGORITHM_VERSION,APPROVAL_STATUS
from reader.calculation_api import contract,start_server


def example():
    return {'schema_version':SCHEMA_VERSION,'operation':'damage','attacker':{'atk':{'base':500,'relic_mul_delta':[.2],
        'direct_add':[50],'direct_mul_delta':[.5,.2],'final_add':[100],'final_factors':[.8,1.1]}},
        'defender':{'def':{'base':300},'res':{'base':30}},'hit':{'type':'physical','post_scale_factors':[1.2,.75]},
        'context':{'purpose':'算法核对样例'}}


def main():
    folder=ROOT/'reports/calculation';folder.mkdir(parents=True,exist_ok=True)
    docs=ROOT/'docs';docs.mkdir(exist_ok=True)
    (docs/'calculation.schema.json').write_text(json.dumps(contract(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    physical=example();arts=deepcopy(physical);arts['hit']['type']='arts'
    grouped={'attacker':{'atk':{'base':1000}},'defender':{'res':{'base':0}},'hit':{'type':'arts',
        'post_scale_groups':[{'group_id':'arts_bonus','deltas':[.2],'stacking':'add'},{'group_id':'arts_bonus','deltas':[.3],'stacking':'add'},
                             {'group_id':'other','deltas':[.1]}]}}
    ignore={'attacker':{'atk':{'base':1000}},'defender':{'res':{'base':90,'direct_add':[50]}},'hit':{'type':'arts','res_penetration_flat':20}}
    reduction=deepcopy(ignore);reduction['hit'].pop('res_penetration_flat');reduction['defender']['res']['direct_add'].append(-20)
    def term(value,kind,name):return {'value':value,'source':{'kind':kind,'name':name}}
    attributed={'operation':'attribute','stat':'atk','attribute':{'base':term(1000,'base','测试面板'),
        'direct_mul_delta':[term(2,'skill','真银斩'),term(-.4,'enemy','挠伤')],
        'final_add':[term(500,'skill','战斗之歌')],'final_factors':[term(.6,'enemy','寸劲')]}}
    ratio={'attacker':{'atk':{'base':1000}},'defender':{'def':{'base':500}},'hit':{'attack_scale':term(2,'skill','攻击力比例200%')}}
    chosen={'attacker':{'atk':{'base':100}},'hit':{'post_scale_groups':[{'group_id':'weakening','stacking':'max',
        'deltas':[term(.2,'talent','较弱效果'),term(.3,'skill','较强效果')]}]}}
    legacy={'schema_version':'1.1','attacker':{'atk':{'base':100}},'hit':{'post_scale_groups':[{'group_id':'old','deltas':[.2,.3]}]}}
    cases=[('review_physical',physical,684.36),('review_arts',arts,668.052),
        ('basic_physical',{'attacker':{'atk':{'base':1000}},'defender':{'def':{'base':300}}},700),
        ('physical_floor',{'attacker':{'atk':{'base':100}},'defender':{'def':{'base':10000}}},5),
        ('attribute_only',{'operation':'attribute','stat':'atk','attribute':{'base':500,'direct_mul_delta':[.5]}},750),
        ('named_damage_groups',grouped,1650),
        ('named_attribute_groups',{'operation':'attribute','stat':'atk','attribute':{'base':100,'final_groups':[{'group_id':'same','deltas':[.2,.3],'stacking':'add'}]}},150),
        ('res_overflow_ignore',ignore,200),('res_overflow_reduction',reduction,50),
        ('element_damage',{'attacker':{'atk':{'base':1000}},'defender':{'element_res':{'base':30},'ep_resistance':{'base':99}},'hit':{'type':'element'}},700),
        ('elemental_buildup',{'operation':'elemental_buildup','amount':1000,'defender':{'ep_resistance':{'base':30}},
            'post_scale_groups':[{'group_id':'buildup_bonus','deltas':[.2,.3],'stacking':'add'}],'hits':2},2100),
        ('attributed_white_value',attributed,1860),('attributed_attack_ratio',ratio,1500),
        ('attributed_max_group',chosen,130),('legacy_group_compatibility',legacy,150)]
    server,thread=start_server(0);base=f'http://127.0.0.1:{server.server_address[1]}'
    report={'interfaces':{},'cases':[],'schema_version':SCHEMA_VERSION,'algorithm_version':ALGORITHM_VERSION,'approval_status':APPROVAL_STATUS}
    try:
        for name,request,expected in cases:
            local=calculate(request)
            actual=local.get('hp_damage',local.get('value',local.get('total_buildup')))
            if abs(actual-expected)>1e-8:raise AssertionError((name,actual,expected))
            path=folder/(name+'.request.json');path.write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            cli=subprocess.run([sys.executable,str(ROOT/'scripts/calculate.py'),'--input',str(path)],capture_output=True,check=True)
            cli_result=json.loads(cli.stdout)
            req=Request(base+'/v1/calculate',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'},method='POST')
            http=json.load(urlopen(req,timeout=10))
            if local!=cli_result or local!=http:raise AssertionError('Interface mismatch: '+name)
            (folder/(name+'.result.json')).write_text(json.dumps(local,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            report['cases'].append({'name':name,'expected':expected,'actual':actual,'python_cli_http_equal':True})
        health=json.load(urlopen(base+'/health',timeout=10));schema=json.load(urlopen(base+'/v1/schema',timeout=10))
        if health['approval_status']!=APPROVAL_STATUS or schema!=contract():raise AssertionError('Contract metadata mismatch')
        effects=json.load(urlopen(base+'/v1/effects?'+urlencode({'search':'锈刃','limit':3}),timeout=10))
        if not effects['entries']:raise AssertionError('Effect lookup failed')
        item=json.load(urlopen(base+'/v1/effects/'+effects['entries'][0]['id'],timeout=10))
        if item['execution_status']!='not_compiled':raise AssertionError('Unreviewed effect unexpectedly compiled')
        invalid_cases=[b'{"wrong":1}',b'{"attacker":{"atk":{"base":{"value":1,"source":null}}}}',('['*1100+'0'+']'*1100).encode()]
        for invalid in invalid_cases:
            try:
                urlopen(Request(base+'/v1/calculate',data=invalid,headers={'Content-Type':'application/json'}),timeout=10)
                raise AssertionError('Invalid request accepted')
            except HTTPError as error:
                if error.code!=400 or 'error' not in json.load(error):raise
        report['interfaces']={'python':True,'cli_json':True,'http_calculation':True,'http_schema':True,'http_effect_lookup':True,'structured_errors':True}
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
    (folder/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""JSON CLI and local HTTP adapter for the pure calculation core."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import sqlite3
from urllib.parse import urlparse,parse_qs,unquote

from .calculation import calculate,CalculationError,SCHEMA_VERSION,SUPPORTED_SCHEMA_VERSIONS,ALGORITHM_VERSION,APPROVAL_STATUS,LIST_FIELDS,FACTOR_FIELDS,HIT_FIELDS,STAT_FIELDS
from .calculation_trace import SOURCE_LABELS
from .session_calculation import dispatch, session_contract
from .session_dps import dps_contract

MAX_BYTES=1_000_000


def no_duplicates(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise CalculationError('Duplicate JSON key: '+key)
        result[key]=value
    return result


def parse_json(raw):
    def reject(value):raise CalculationError('Non-finite JSON constant: '+value)
    try:return json.loads(raw,parse_constant=reject,object_pairs_hook=no_duplicates)
    except (json.JSONDecodeError,UnicodeDecodeError,RecursionError) as error:raise CalculationError(str(error)) from error


def contract():
    source={'type':'object','required':['kind','name'],'additionalProperties':False,'properties':{
        'kind':{'enum':list(SOURCE_LABELS)},'name':{'type':'string','minLength':1,'maxLength':4000},
        **{key:{'type':['string','integer'] if key in {'id','revision'} else 'string','maxLength':4000} for key in ('id','url','revision','detail')}}}
    def operand(minimum=None,maximum=None):
        scalar={'type':'number',**({'minimum':minimum} if minimum is not None else {}),**({'maximum':maximum} if maximum is not None else {})}
        return {'oneOf':[scalar,{'type':'object','required':['value'],'additionalProperties':False,'properties':{'value':scalar,'source':{'$ref':'#/$defs/source'}}}]}
    attr_properties={'base':operand(),'minimum':{'type':['number','null']},'maximum':{'type':['number','null']}}
    for key in sorted(LIST_FIELDS):
        attr_properties[key]={'type':'array','maxItems':128,'items':operand(0 if key in FACTOR_FIELDS else None)}
    groups={'type':'array','maxItems':128,'items':{'$ref':'#/$defs/multiplier_group'}}
    factors={'type':'array','maxItems':128,'items':operand(0)}
    attr_properties['final_groups']=groups
    attribute={'type':'object','required':['base'],'additionalProperties':False,'properties':attr_properties}
    entity={'type':'object','additionalProperties':False,'properties':{key:{'$ref':'#/$defs/attribute'} for key in sorted(STAT_FIELDS)}}
    hit_properties={key:operand(0) for key in HIT_FIELDS-{'type','hits','barrier_charges','immune','post_scale_factors','post_scale_groups','attack_scale_groups','attack_flat_add'}}
    hit_properties['attack_flat_add']=operand()
    for key in ('def_penetration_ratio','res_penetration_ratio','min_damage_ratio'):hit_properties[key]=operand(0,1)
    hit_properties.update(type={'enum':['physical','arts','true','element']},hits={'type':'integer','minimum':1,'maximum':10000},
        barrier_charges={'type':'integer','minimum':0,'maximum':10000},immune={'type':'boolean'},
        post_scale_factors=factors,post_scale_groups=groups,attack_scale_groups=groups)
    version={'enum':sorted(SUPPORTED_SCHEMA_VERSIONS)}
    hit={'type':'object','additionalProperties':False,'properties':hit_properties,'allOf':[{
        'if':{'required':['type'],'properties':{'type':{'const':'element'}}},
        'then':{'properties':{key:operand(0,0) for key in ('def_penetration_flat','def_penetration_ratio','res_penetration_flat','res_penetration_ratio')}}}]}
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema','title':'TreeSea Reader calculation request',
        '$defs':{'source':source,'attribute':attribute,'entity':entity,'multiplier_group':{'type':'object','required':['group_id','deltas'],'additionalProperties':False,
            'properties':{'group_id':{'type':'string','minLength':1,'maxLength':128,'pattern':r'^\S(?:[\s\S]*\S)?$'},
                          'deltas':{'type':'array','minItems':1,'maxItems':128,'items':operand()},
                          'stacking':{'enum':['add','max','min','multiply'],'default':'multiply'}}}},'oneOf':[
        {'type':'object','required':['attacker'],'additionalProperties':False,'properties':{
            'schema_version':version,'operation':{'const':'damage'},'attacker':dict(entity,required=['atk']),
            'defender':entity,'hit':hit,
            'timing':{'type':'object','required':['base_interval'],'additionalProperties':False,'properties':{'base_interval':{
                'oneOf':[{'type':'number','exclusiveMinimum':0},{'type':'object','required':['value'],'additionalProperties':False,'properties':{
                    'value':{'type':'number','exclusiveMinimum':0},'source':{'$ref':'#/$defs/source'}}}]}}},'context':{}}},
        {'type':'object','required':['operation','attribute'],'additionalProperties':False,'properties':{
            'schema_version':version,'operation':{'const':'attribute'},'stat':{'type':'string'},
            'attribute':{'$ref':'#/$defs/attribute'},'context':{}}},
        {'type':'object','required':['operation','amount'],'additionalProperties':False,'properties':{
            'schema_version':version,'operation':{'const':'elemental_buildup'},'amount':operand(0),
            'defender':{'type':'object','additionalProperties':False,'properties':{'ep_resistance':{'$ref':'#/$defs/attribute'}}},
            'min_damage_ratio':operand(0,1),'post_scale_groups':groups,'post_scale_factors':factors,
            'hits':{'type':'integer','minimum':1,'maximum':10000},'context':{}}}],
        'x-algorithm-version':ALGORITHM_VERSION,'x-approval-status':APPROVAL_STATUS,
        'x-unit-notes':{'ratio_delta':'+20% must be 0.2; direct/relic delta arrays are summed',
            'factors':'Actual independent multipliers only; do not duplicate effects from named groups',
            'groups':'PRTS final multipliers multiply by default; use add/max/min only with mechanic evidence. Explicit 1.0/1.1 requests preserve the old add default.',
            'contributions':'Every numeric operand can carry {value,source:{kind,name,id,url,revision,detail}}; output formulas and source-labelled terms explain the calculation.',
            'white_value_order':'PRTS: direct add -> direct multiplier -> final add -> final scaler; attack ratio is before mitigation and does not change white values',
            'res':'Resistance in points; default RES cap 100. Ordinary RES reductions apply before cap; res_penetration_* means ignore RES after cap.',
            'element':'element_res reduces elemental HP damage; ep_resistance reduces elemental_buildup. Neither channel supports penetration.',
            'automatic_effects':'No source effect is automatically active merely because it is catalogued'}}
    schema['oneOf'].append(session_contract())
    schema['oneOf'].append(dps_contract())
    schema['x-unit-notes']['session_damage']='Read-only confirmed session snapshot; inspect audit and status. Incomplete requests return result:null unless allow_partial is explicitly true.'
    schema['x-unit-notes']['session_dps']='Zero DEF/RES single target. DPH and finite skill-window average DPS are independently nullable; inspect audit, timing_missing and resolved_model. First-hit default 0s, theoretical intervals, end-exclusive window; not all skill timing is supported.'
    return schema


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send_json(self,status,data):
        body=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path=='/health':self.send_json(200,{'status':'ok','schema_version':SCHEMA_VERSION,'algorithm_version':ALGORITHM_VERSION,'approval_status':APPROVAL_STATUS})
        elif parsed.path=='/v1/schema':self.send_json(200,contract())
        elif parsed.path=='/v1/rules/coverage':
            from .effect_rules import RuleCatalog
            try:self.send_json(200,RuleCatalog().coverage())
            except (OSError,ValueError,sqlite3.Error) as error:self.send_json(400,{'error':{'code':'catalog_error','message':str(error)}})
        elif parsed.path.startswith('/v1/rules/'):
            from .effect_rules import RuleCatalog
            try:self.send_json(200,RuleCatalog().compile(unquote(parsed.path.removeprefix('/v1/rules/'))))
            except (OSError,ValueError,sqlite3.Error) as error:self.send_json(400,{'error':{'code':'rule_error','message':str(error)}})
        elif parsed.path.startswith('/v1/effects'):
            try:
                from .catalog import Catalog
                catalog=Catalog();query=parse_qs(parsed.query)
                if parsed.path=='/v1/effects/coverage':self.send_json(200,catalog.effect_summary())
                elif parsed.path=='/v1/effects':
                    self.send_json(200,catalog.search_effects(query.get('search',[''])[0],category=query.get('category',[None])[0],
                        limit=int(query.get('limit',['200'])[0]),offset=int(query.get('offset',['0'])[0])))
                else:
                    result=catalog.effect_detail(unquote(parsed.path.removeprefix('/v1/effects/')))
                    self.send_json(200 if result else 404,result or {'error':{'code':'not_found','message':'Unknown effect'}})
            except (OSError,ValueError,OverflowError,sqlite3.Error) as error:self.send_json(400,{'error':{'code':'catalog_error','message':str(error)}})
        else:self.send_json(404,{'error':{'code':'not_found','message':'Unknown endpoint'}})
    def do_POST(self):
        if self.path!='/v1/calculate':self.send_json(404,{'error':{'code':'not_found','message':'Unknown endpoint'}});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=MAX_BYTES:raise CalculationError('Body must be 1..1000000 bytes')
            request=parse_json(self.rfile.read(length).decode('utf-8'))
            self.send_json(200,dispatch(request))
        except (ValueError,CalculationError,UnicodeDecodeError,OverflowError,OSError,sqlite3.Error) as error:
            self.send_json(400,{'error':{'code':'invalid_request','message':str(error)}})


def start_server(port=8765):
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    return server,thread


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',default='-',help='UTF-8 JSON input file, or - for stdin')
    parser.add_argument('--output',default='-',help='JSON output file, or - for stdout')
    parser.add_argument('--schema',action='store_true')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args(argv)
    if args.serve:
        if not 1<=args.port<=65535:parser.error('port must be 1..65535')
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        print(f'http://127.0.0.1:{args.port} | {APPROVAL_STATUS}',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
        return 0
    exit_code=0
    try:
        if args.schema:result=contract()
        else:
            if args.input=='-':raw=sys.stdin.buffer.read(MAX_BYTES+1)
            else:
                with Path(args.input).open('rb') as stream:raw=stream.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise CalculationError('Input exceeds size limit')
            result=dispatch(parse_json(raw.decode('utf-8-sig')))
    except (OSError,ValueError,CalculationError,OverflowError,sqlite3.Error) as error:
        result={'error':{'code':'invalid_request','message':str(error)}};exit_code=2
    body=json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n'
    if args.output=='-':
        if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
        sys.stdout.write(body)
    else:
        try:Path(args.output).write_text(body,encoding='utf-8')
        except OSError as error:
            sys.stdout.write(json.dumps({'error':{'code':'output_error','message':str(error)}})+'\n');return 2
    return exit_code


if __name__=='__main__':raise SystemExit(main())

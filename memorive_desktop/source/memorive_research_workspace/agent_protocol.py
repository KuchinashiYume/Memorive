"""MCP stdio and a single-call CLI share the exact read/propose service surface.

JSON-RPC 2.0, newline-delimited UTF-8 per MCP 2025-11-25. No listener, no
credentials, no agent execution, no automatic confirmation or Vault writes.
"""
import json,sys
from .service import PUBLIC_METHODS

def schema(properties,required):
    return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
TEXT={'type':'string'};IDS={'type':'array','items':TEXT,'maxItems':24}
SCHEMAS={
    'memo.capabilities':schema({},[]),
    'memo.search':schema({'query':TEXT,'project':TEXT,'limit':{'type':'integer','minimum':1,'maximum':24},'artifact_ids':IDS},['query','project']),
    'memo.read_evidence':schema({'evidence_id':TEXT,'project':TEXT,'expected_hash':TEXT},['evidence_id','project']),
    'memo.prepare_tension':schema({'project':TEXT,'evidence_ids':IDS},['project','evidence_ids']),
    'memo.job_status':schema({'job_id':TEXT},['job_id']),
}
for method in ('memo.propose_feedback','memo.submit_draft'):
    SCHEMAS[method]=schema({k:TEXT for k in ('project','title','claim','scope','limitations')}|{'evidence_ids':IDS},['project','title','claim','scope','limitations','evidence_ids'])
DESCRIPTIONS={
    'memo.capabilities':'List Memo research tools and available project IDs.',
    'memo.search':'Search one selected project using the saved Memo retrieval weights and configured models, which may call a provider. Returns versioned citations. No permission to transmit results to other destinations is implied.',
    'memo.read_evidence':'Read an exact evidence excerpt and verify that its source remains unchanged.',
    'memo.prepare_tension':'Prepare source-bound RESEARCH_OPPORTUNITIES comparison facets for at least two papers. Scientific interpretation remains unassessed.',
    'memo.propose_feedback':'Propose cited knowledge with scope and limitations. This cannot approve or activate it.',
    'memo.submit_draft':'Return an agent research draft to the human review queue in Memorive.',
    'memo.job_status':'Read the status of a known job; does not start or retry any call.',
}

def tools_list():
    return [{'name':m.replace('.','_'),'description':DESCRIPTIONS[m],'inputSchema':SCHEMAS[m],
        'annotations':{'readOnlyHint':m not in {'memo.propose_feedback','memo.submit_draft'},'destructiveHint':False,
                       'idempotentHint':m not in {'memo.propose_feedback','memo.submit_draft'},'openWorldHint':m=='memo.search'}} for m in sorted(PUBLIC_METHODS)]

def validate_arguments(method,args):
    spec=SCHEMAS[method]
    if not isinstance(args,dict) or set(args)-set(spec['properties']) or set(spec['required'])-set(args):raise ValueError('TOOL_ARGUMENTS_INVALID')
    for name,value in args.items():
        s=spec['properties'][name];kind=s['type']
        if kind=='string' and (not isinstance(value,str) or not value or len(value)>16000):raise ValueError('TOOL_ARGUMENT_INVALID')
        if kind=='integer' and (type(value) is not int or not s['minimum']<=value<=s['maximum']):raise ValueError('TOOL_ARGUMENT_INVALID')
        if kind=='array' and (not isinstance(value,list) or len(value)>s['maxItems'] or any(not isinstance(x,str) or len(x)>200 for x in value)):raise ValueError('TOOL_ARGUMENT_INVALID')
    return args

class Protocol:
    def __init__(self,workspace):self.workspace=workspace;self.initialized=False
    def handle(self,request):
        if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid Request'}}
        identity=request.get('id');method=request.get('method');params=request.get('params',{})
        if identity is None:
            if method=='notifications/initialized':self.initialized=True
            return None
        try:
            if method=='initialize':
                version=params.get('protocolVersion')
                if version not in {'2024-11-05','2025-03-26','2025-06-18','2025-11-25'}:version='2025-11-25'
                result={'protocolVersion':version,'capabilities':{'tools':{'listChanged':False}},
                    'serverInfo':{'name':'memorive-research','version':'0.8.116'},'instructions':'Use project-scoped evidence. Scientific conclusions require human review in Memorive; these tools cannot approve.'}
            elif method=='ping':result={}
            elif not self.initialized:raise ValueError('INITIALIZE_FIRST')
            elif method=='tools/list':result={'tools':tools_list()}
            elif method=='tools/call':
                name=params.get('name','');mapping={m.replace('.','_'):m for m in PUBLIC_METHODS}
                if name not in mapping:raise ValueError('METHOD_NOT_ALLOWED')
                target=mapping[name]
                try:
                    output=self.workspace.call(target,validate_arguments(target,params.get('arguments',{})))
                    result={'content':[{'type':'text','text':json.dumps(output,ensure_ascii=False)}],'isError':False}
                except (ValueError,TypeError,OSError) as exc:
                    result={'content':[{'type':'text','text':str(exc)[:180]}],'isError':True}
            else:return {'jsonrpc':'2.0','id':identity,'error':{'code':-32601,'message':'Method not found'}}
            return {'jsonrpc':'2.0','id':identity,'result':result}
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            return {'jsonrpc':'2.0','id':identity,'error':{'code':-32602,'message':str(exc)[:180]}}

def serve(workspace,stdin=None,stdout=None):
    input_stream=stdin or sys.stdin;output_stream=stdout or sys.stdout;protocol=Protocol(workspace)
    while True:
        line=input_stream.readline(1024*1024+1)
        if not line:break
        if len(line)>1024*1024:raise ValueError('MCP_MESSAGE_TOO_LARGE')
        try:response=protocol.handle(json.loads(line))
        except json.JSONDecodeError:response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
        if response is not None:output_stream.write(json.dumps(response,ensure_ascii=False)+'\n');output_stream.flush()

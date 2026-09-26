"""Restricted MCP view over the stable API; reuses the adopted MCP framing."""
import json,sys
from .agent_protocol import Protocol
from .developer_api import VERSION,SCHEMAS,METHOD_SCOPE

class ScopedProtocol(Protocol):
    def __init__(self,workspace,client_id):
        super().__init__(workspace);self.client_id=client_id
    def handle(self,request):
        if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':return super().handle(request)
        identity=request.get('id');method=request.get('method')
        if method not in {'tools/list','tools/call'} or identity is None:
            out=super().handle(request)
            if method=='initialize' and out and 'result' in out:out['result']['serverInfo']['version']=VERSION
            return out
        try:
            if not self.initialized:raise ValueError('INITIALIZE_FIRST')
            api=self.workspace.developer
            if method=='tools/list':
                methods=api.call(self.client_id,'memo.capabilities')['methods']
                result={'tools':[{'name':m.replace('.','_'),'description':'Memorive API '+VERSION+' · '+m,'inputSchema':s,
                    'annotations':{'readOnlyHint':METHOD_SCOPE[m] in {'read','events'},'destructiveHint':m=='memo.job_cancel','openWorldHint':m in {'memo.search','memo.job_submit'}}} for m,s in methods.items()]}
            else:
                params=request.get('params') or {};mapping={m.replace('.','_'):m for m in SCHEMAS}
                try:
                    name=mapping.get(params.get('name'))
                    if name is None:raise ValueError('API_METHOD_NOT_FOUND')
                    value=api.call(self.client_id,name,params.get('arguments') or {})
                    result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'isError':False}
                except (ValueError,KeyError,TypeError,OSError) as e:
                    result={'content':[{'type':'text','text':str(e)[:180]}],'isError':True}
            return {'jsonrpc':'2.0','id':identity,'result':result}
        except (ValueError,TypeError,KeyError) as e:return {'jsonrpc':'2.0','id':identity,'error':{'code':-32602,'message':str(e)[:180]}}

def serve(ws,client_id,stdin=None,stdout=None):
    inp=stdin or sys.stdin;out=stdout or sys.stdout;p=ScopedProtocol(ws,client_id)
    while True:
        line=inp.readline(4*1024*1024+1)
        if not line:break
        if len(line)>4*1024*1024:raise ValueError('MCP_MESSAGE_TOO_LARGE')
        try:r=p.handle(json.loads(line))
        except json.JSONDecodeError:r={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
        if r is not None:out.write(json.dumps(r,ensure_ascii=False)+'\n');out.flush()

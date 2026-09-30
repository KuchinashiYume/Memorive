"""Versioned local developer surface. Client grants are policy, not an OS sandbox.

Only the trusted desktop edits grants. Existing local integrations remain
compatible; --client selects the restricted surface. No HTTP listener or keys.
"""
import copy,json,re,sys,time
from pathlib import Path
from .store import uid,digest,now,packed
from .agent_protocol import SCHEMAS as RESEARCH_SCHEMAS,validate_arguments

VERSION='1.0.0'
SCOPES=('read','events','import','draft','export','writeback','automation','model')
DESKTOP=frozenset({'memo.developer_state','memo.developer_save','memo.automation_save','memo.developer_config'})
AUTOMATION_DEFAULT={'enabled':False,'max_queued':20}
TEXT={'type':'string','minLength':1,'maxLength':16000}
IDS={'type':'array','items':TEXT,'maxItems':2000}
def spec(properties,required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
SCHEMAS={
    'memo.capabilities':spec({}),
    'memo.projects':spec({}),
    'memo.connections':spec({'project':TEXT},['project']),
    **{k:v for k,v in RESEARCH_SCHEMAS.items() if k in {'memo.search','memo.read_evidence','memo.prepare_tension','memo.submit_draft'}},
    'memo.import_preview':spec({'connection_id':TEXT,'paths':IDS,'items':{'type':'array','items':{'type':'object'},'maxItems':2000},'bibliography':TEXT}),
    'memo.import_commit':spec({'connection_id':TEXT,'preview_id':TEXT,'preview_hash':TEXT,'selected_ids':IDS},['connection_id','preview_id','preview_hash']),
    'memo.outputs':spec({'connection_id':TEXT,'source_id':TEXT},['connection_id','source_id']),
    'memo.export':spec({'connection_id':TEXT,'source_id':TEXT,'output_ids':IDS},['connection_id','source_id']),
    'memo.writeback_prepare':spec({'connection_id':TEXT,'source_id':TEXT,'external_note_id':TEXT,'existing_text':{'type':'string','maxLength':2097152},'output_ids':IDS,'format':{'enum':['markdown','html']}},['connection_id','source_id','external_note_id']),
    'memo.writeback_ack':spec({'connection_id':TEXT,'plan_id':TEXT,'text_hash':TEXT,'host_text':{'type':'string','maxLength':2097152},'host_note_id':TEXT},['connection_id','plan_id','text_hash']),
    'memo.job_submit':spec({'connection_id':TEXT,'kind':{'enum':['ask','agent','process','process_retry']},'source_id':TEXT,'question':TEXT,'thread_id':TEXT,'request_id':TEXT},['connection_id','kind','request_id']),
    'memo.job_status':spec({'job_id':TEXT},['job_id']),
    'memo.job_cancel':spec({'job_id':TEXT},['job_id']),
    'memo.events':spec({'cursor':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':200}},[]),
}
SCHEMAS['memo.import_preview']['required']=['connection_id']
METHOD_SCOPE={m:'read' for m in SCHEMAS}
METHOD_SCOPE.update({'memo.events':'events','memo.import_preview':'import','memo.import_commit':'import',
    'memo.submit_draft':'draft','memo.export':'export','memo.writeback_prepare':'writeback',
    'memo.writeback_ack':'writeback','memo.job_submit':'automation','memo.job_cancel':'automation'})

def validate(method,p):
    if method not in SCHEMAS:raise ValueError('API_METHOD_NOT_FOUND')
    # jsonschema is already part of the source/runtime dependencies.
    import jsonschema
    try:jsonschema.validate(p,SCHEMAS[method])
    except jsonschema.ValidationError:raise ValueError('API_ARGUMENTS_INVALID') from None
    return p

class DeveloperAPI:
    def __init__(self,ws):self.ws=ws;self.store=ws.store
    def automation(self):
        return self.store.get('developer_settings','automation') or dict(AUTOMATION_DEFAULT,id='automation',revision=0)
    def save_automation(self,config,expected_revision):
        if not isinstance(config,dict) or set(config)!=set(AUTOMATION_DEFAULT) or type(config['enabled']) is not bool or type(config['max_queued']) is not int or not 1<=config['max_queued']<=100:
            raise ValueError('AUTOMATION_SETTINGS_INVALID')
        return self.store.put('developer_settings','automation','',config,expected=expected_revision)
    def state(self):
        return {'api_version':VERSION,'transport':'stdio','clients':self.store.list('developer_client'),
            'projects':[{'id':p['id'],'name':p['name']} for p in self.store.list('project')],
            'connections':[{'id':c['id'],'name':c['name'],'project':c['project'],'root':c['root'],'enabled':c['enabled']} for c in self.store.list('connection')],
            'scopes':list(SCOPES),'automation':self.automation(),'jobs':[self.job_projection(j) for j in self.store.list('connector_action') if j.get('client_id')][:40]}
    def save(self,config,client_id=None,expected_revision=0):
        fields={'name','enabled','projects','connection_ids','scopes'}
        if not isinstance(config,dict) or set(config)!=fields:raise ValueError('CLIENT_SETTINGS_INVALID')
        if not isinstance(config['name'],str) or not config['name'].strip() or len(config['name'])>100 or type(config['enabled']) is not bool:raise ValueError('CLIENT_SETTINGS_INVALID')
        for k in ('projects','connection_ids','scopes'):
            if not isinstance(config[k],list) or len(config[k])>100 or any(not isinstance(i,str) for i in config[k]):raise ValueError('CLIENT_SETTINGS_INVALID')
        if not config['projects'] or set(config['scopes'])-set(SCOPES):raise ValueError('CLIENT_SCOPE_INVALID')
        for project in config['projects']:
            if not self.store.get('project',project):raise ValueError('PROJECT_NOT_FOUND')
        for identity in config['connection_ids']:
            c=self.ws.connections.connection(identity,False)
            if c['project'] not in config['projects']:raise ValueError('CLIENT_CONNECTION_SCOPE_INVALID')
        identity=client_id or uid('client_')
        if client_id and not self.store.get('developer_client',client_id):raise ValueError('CLIENT_NOT_FOUND')
        return self.store.put('developer_client',identity,'',dict(config,name=config['name'].strip()),expected=expected_revision)
    def client(self,identity):
        c=self.store.get('developer_client',identity)
        if not c or not c['enabled']:raise ValueError('CLIENT_DISABLED_OR_UNKNOWN')
        return c
    def require(self,c,scope):
        if scope not in c['scopes']:raise ValueError('CLIENT_SCOPE_DENIED:'+scope)
    def project(self,c,project):
        if project not in c['projects']:raise ValueError('CLIENT_PROJECT_DENIED')
    def connection(self,c,identity):
        if identity not in c['connection_ids']:raise ValueError('CLIENT_CONNECTION_DENIED')
        row=self.ws.connections.connection(identity)
        self.project(c,row['project']);return row
    def authorize_action(self,client_id,connection_id,kind,revision=None):
        c=self.client(client_id);self.require(c,'automation');self.require(c,'model')
        self.connection(c,connection_id)
        if not self.automation()['enabled']:raise ValueError('AUTOMATION_DISABLED')
        if revision is not None and revision!=c['revision']:raise ValueError('CLIENT_GRANT_CHANGED')
        return c
    def config(self,client_id):
        c=self.client(client_id)
        command=([sys.executable,'--memo-agent'] if getattr(sys,'frozen',False) else
                 [sys.executable,str(Path(__file__).with_name('agent_entry.py'))])
        command+=['--workspace',str(self.store.root),'--client',client_id]
        return {'api_version':VERSION,'command':command,
            'mcp':{'mcpServers':{'memorive':{'command':command[0],'args':command[1:]+['--mcp']}}},
            'sdk_relative_path':'integrations/developer','client_id':client_id}
    def job_projection(self,action):
        value={k:action[k] for k in ('id','project','connection_id','client_id','kind','status','created_at','error') if k in action}
        value['cancellation']='queued_or_research_job'
        result=action.get('result') or {}
        if result.get('job_id') and action['kind'] in {'ask','agent'}:
            job=self.store.get('job',result['job_id'])
            if not job:return dict(value,status='UNAVAILABLE',error='JOB_HISTORY_REMOVED')
            value.update(status=job['status'],result=result,usage=job.get('usage'),elapsed_ms=job.get('elapsed_ms'),error=job.get('error'))
            if job['status']=='COMPLETE':
                thread=self.store.get('thread',result['thread_id'])
                value['answer']=next((m for m in (thread or {}).get('messages',[]) if m.get('id')==job.get('answer_id') and m['role']=='assistant'),None)
                if value['answer'] is None:value['result_unavailable']='ANSWER_HISTORY_REMOVED'
        elif result:
            value['result']=result
            if action['kind'] in {'process','process_retry'}:
                d=self.store.get('connector_document',action.get('source_id',''))
                processing=(d or {}).get('processing') or result
                value['processing']=processing
                # DISPATCHED means handed to the mainline, never completed.
                value['status']={'SUCCEEDED':'COMPLETE','FAILED':'ERROR','CANCELLED':'CANCELLED','RUNNING':'RUNNING','PAUSED':'PAUSED','CANCEL_REQUESTED':'CANCELLING'}.get(processing.get('control_state'),
                    'ERROR' if processing.get('state')=='ERROR' else action['status'])
        return value
    def own_job(self,c,identity):
        a=self.store.get('connector_action',identity)
        if not a or a.get('client_id')!=c['id']:raise ValueError('CLIENT_JOB_DENIED')
        self.connection(c,a['connection_id']);return a
    def cancel(self,c,identity):
        with self.store.tx() as db:
            a=self.store.get('connector_action',identity,db=db)
            if not a or a.get('client_id')!=c['id']:raise ValueError('CLIENT_JOB_DENIED')
            if a['status']=='QUEUED':
                return self.store.put('connector_action',a['id'],a['project'],dict(a,status='CANCELLED'),db=db)
        if a['status'] in {'ERROR','CANCELLED','INTERRUPTED'}:return self.job_projection(a)
        result=a.get('result') or {}
        if a['kind'] in {'ask','agent'} and result.get('job_id'):
            self.ws.cancel(result['job_id']);return self.job_projection(a)
        raise ValueError('JOB_USE_CURRENT_TASK_CONTROLS' if a['kind'].startswith('process') else 'JOB_DISPATCH_IN_PROGRESS')
    def events(self,c,cursor=0,limit=100):
        projects=c['projects'];marks=','.join('?' for _ in projects)
        with self.store.tx() as db:
            maximum=db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
            if cursor>maximum:raise ValueError('EVENT_CURSOR_AHEAD')
            rows=db.execute('SELECT seq,event,object_id,at,body FROM events WHERE seq>? AND event LIKE ? AND json_extract(body,?) IN ('+marks+') ORDER BY seq LIMIT ?',
                [cursor,'MEMO_PUBLIC_%','$.project',*projects,limit+1]).fetchall()
        more=len(rows)>limit;rows=rows[:limit]
        return {'events':[{'cursor':r['seq'],'type':r['event'][12:].lower(),'id':r['object_id'],'at':r['at'],**json.loads(r['body'])} for r in rows],
            'next_cursor':rows[-1]['seq'] if more else maximum,'has_more':more}
    def call(self,client_id,method,p=None):
        p=validate(method,dict(p or {}));c=self.client(client_id)
        if method!='memo.capabilities':self.require(c,METHOD_SCOPE[method])
        if 'project' in p:self.project(c,p['project'])
        connection=None
        if 'connection_id' in p:connection=self.connection(c,p['connection_id'])
        if method=='memo.capabilities':
            return {'api_version':VERSION,'transport':'stdio','trust_boundary':'local_same_os_user','client_id':c['id'],
                'methods':{m:s for m,s in SCHEMAS.items() if METHOD_SCOPE[m] in c['scopes'] or m=='memo.capabilities'},
                'scopes':c['scopes'],'automation':self.automation()['enabled'],'model_calls':'explicit model grant; existing gateway and cache',
                'cancellation':{'queued':True,'research':True,'mainline':'current_task_controls'},'scientific_approval':False}
        if method=='memo.projects':return {'projects':[{'id':p['id'],'name':p['name']} for p in self.store.list('project') if p['id'] in c['projects']]}
        if method=='memo.connections':return {'connections':[dict(id=r['id'],name=r['name'],provider=r['provider']) for r in self.store.list('connection',p['project']) if r['id'] in c['connection_ids'] and r['enabled']]}
        if method=='memo.search':
            config=None
            if 'model' not in c['scopes']:
                config=copy.deepcopy(self.ws.weights.settings(p['project'])['config'])
                config.update(semantic_enabled=False,semantic_profile_ref=None,embedding_profile_ref=None,rule_enrichment=False)
            return self.ws.index.search(**p,policy_config=config)
        if method in {'memo.read_evidence','memo.prepare_tension','memo.submit_draft'}:return self.ws.call(method,p)
        if method=='memo.events':return self.events(c,**p)
        if method in {'memo.job_status','memo.job_cancel'}:
            a=self.own_job(c,p['job_id'])
            return self.job_projection(a) if method.endswith('status') else self.cancel(c,p['job_id'])
        if method=='memo.job_submit':
            c=self.authorize_action(client_id,connection['id'],p['kind'])
            if not re.fullmatch(r'[\w-]{8,96}',p['request_id']):raise ValueError('REQUEST_ID_INVALID')
            return self.job_projection(self.ws.connections.queue(**p,client_id=client_id,grant_revision=c['revision']))
        routes={'memo.import_preview':'connector.preview','memo.import_commit':'connector.commit','memo.outputs':'connector.outputs',
            'memo.export':None,'memo.writeback_prepare':'connector.writeback_prepare','memo.writeback_ack':'connector.writeback_ack'}
        if method=='memo.export':return self.ws.connections.export(**p)
        return self.ws.connections.companion(routes[method],p)
    def desktop(self,method,p):
        return {'memo.developer_state':self.state,'memo.developer_save':self.save,'memo.automation_save':self.save_automation,'memo.developer_config':self.config}[method](**p)

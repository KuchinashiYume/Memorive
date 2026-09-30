"""Optional, bounded progress observations. Business/control errors stay outside the sink."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib,json,logging,re,time,uuid

_current=ContextVar('memorive_node_progress',default=None)
BASES={'VALIDATED_OUTPUTS','PROCESSED_ITEMS'}
PHASES={'DOCUMENT_STRUCTURE':'STAGE','OCR_RESCUE':'PAGE','VECTOR_GENERATION':'CHUNK','CARD_SEGMENTS':'SEGMENT',
        'CARD_REVIEW':'ROUND','JUDGMENT_REVIEW':'CLAIM','RAW_EXTRACTION':'LINE',
        'RULE_EVALUATION':'RULE','LOCAL_LOGIC':'SEGMENT','PDF_ASSIST':'PAGE',
        'SOURCE_BRANCHES':'BRANCH','CANDIDATE_RELATIONS':'ITEM','REPORT_ITEMS':'ITEM'}
MAX_UNITS=2**31-1
NODE_PHASES={'01_DOCUMENT_INGEST':{'OCR_RESCUE','DOCUMENT_STRUCTURE'},'02_CHUNK_EMBEDDING':{'VECTOR_GENERATION'},
 '03_CARD_DISTILL':{'CARD_SEGMENTS'},'04_CARD_CROSS_CHECK':{'CARD_REVIEW'},
 '08_JUDGMENT_CROSS_CHECK':{'JUDGMENT_REVIEW'},
 'E2_DATA_REVIEW':{'RAW_EXTRACTION','RULE_EVALUATION','PDF_ASSIST'},'E2_LOGIC_REVIEW':{'LOCAL_LOGIC'},
 'COLLECTING':{'SOURCE_BRANCHES','REPORT_ITEMS'},'RESOLVING_IDENTITIES':{'CANDIDATE_RELATIONS'}}
def integer(value):return type(value) is int and 0<=value<=MAX_UNITS
def identity(value):return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,192}',value) is not None
def validate(value):
    try:return _validate(value)
    except (TypeError,KeyError,ValueError):return None

def _validate(value):
    if not isinstance(value,dict):return None
    required={'schema_version','job_id','attempt_id','node_id','node_execution_id','scope_id','scope_kind','phase','unit','basis','completed_units','total_units','plan_hash','progress_revision','scope_state'}
    if set(value)-required-{'limited_units'} or not required<=set(value):return None
    if value['schema_version']!='NodeProgress-v1' or value['scope_kind']!='SUBPHASE':return None
    if any(not identity(value[k]) for k in ('job_id','attempt_id','node_id','node_execution_id','scope_id')):return None
    if value['phase'] not in PHASES or PHASES[value['phase']]!=value['unit'] or value['basis'] not in BASES:return None
    if value['phase'] not in NODE_PHASES.get(value['node_id'],set()):return None
    expected='PROCESSED_ITEMS' if value['phase'] in {'RAW_EXTRACTION','RULE_EVALUATION','SOURCE_BRANCHES','CANDIDATE_RELATIONS','REPORT_ITEMS'} else 'VALIDATED_OUTPUTS'
    if value['basis']!=expected:return None
    if not all(integer(value[k]) for k in ('completed_units','total_units','progress_revision')):return None
    if not 0<=value['completed_units']<=value['total_units'] or value['total_units']==0 or value['progress_revision']==0:return None
    if not integer(value.get('limited_units',0)) or value.get('limited_units',0)>value['completed_units']:return None
    if not isinstance(value['plan_hash'],str) or not re.fullmatch('[0-9a-f]{64}',value['plan_hash']):return None
    if value['scope_state'] not in {'ACTIVE','CLOSED'}:return None
    return deepcopy(value)

def apply_event(rows,event,*,job_id,attempt_id,control_state):
    """Called inside the existing serialized store transaction; no caller sequence."""
    if not isinstance(event,dict):return rows
    if not isinstance(rows,dict):rows={}
    if event.get('job_id')!=job_id or event.get('attempt_id')!=attempt_id:return rows
    node=event.get('node_id');execution=event.get('node_execution_id')
    if not identity(node) or not identity(execution):return rows
    old=rows.get(node);action=event.get('action')
    if not isinstance(old,dict):old=None
    if control_state not in {'RUNNING','PAUSE_REQUESTED'} and action not in {'SUSPEND','CLOSE'}:
        if action!='END' or control_state not in {'PAUSED','RESUME_REQUESTED','CANCEL_REQUESTED'}:return rows
    updated=deepcopy(rows)
    if action=='BEGIN':
        if (old or {}).get('node_execution_id')!=event.get('previous_execution_id'):return rows
        updated[node]={'node_execution_id':execution,'attempt_id':attempt_id,'activity':'RUNNING','progress':None}
        return updated
    if not old or old.get('node_execution_id')!=execution or old.get('attempt_id')!=attempt_id:return rows
    if old['activity']=='CLOSED':return rows
    if action in {'ACTIVE','SUSPEND','CLOSE'}:
        updated[node]['activity']={'ACTIVE':'RUNNING','SUSPEND':'SUSPENDED','CLOSE':'CLOSED'}[action]
    elif action in {'START','UPDATE','END'}:
        p=validate(event.get('progress'))
        if not p or any(p[k]!=event[k] for k in ('job_id','attempt_id','node_id','node_execution_id')):return rows
        prior=old.get('progress')
        if action=='START':
            if old['activity']!='RUNNING' or p['scope_id']==(prior or {}).get('scope_id'):return rows
            if (prior or {}).get('scope_id')!=event.get('previous_scope_id') or p['completed_units']!=0 or p['scope_state']!='ACTIVE':return rows
        else:
            if not prior or prior['scope_state']=='CLOSED' or prior['scope_id']!=p['scope_id']:return rows
            if any(prior[k]!=p[k] for k in ('total_units','plan_hash','phase','unit','basis')):return rows
            if p['progress_revision']<=prior['progress_revision'] or p['completed_units']<prior['completed_units']:return rows
            if action=='UPDATE' and (old['activity']!='RUNNING' or p['scope_state']!='ACTIVE'):return rows
            if action=='END' and p['scope_state']!='CLOSED':return rows
        updated[node]['progress']=p
    else:return rows
    return updated

def project(rows,node_id,*,job_id,attempt_id,active,live=True):
    if not active or not live or not isinstance(rows,dict):return None
    row=rows.get(node_id)
    if not isinstance(row,dict) or row.get('activity')!='RUNNING' or row.get('attempt_id')!=attempt_id:return None
    p=validate(row.get('progress'))
    if not p or p['scope_state']!='ACTIVE' or p['completed_units']==0:return None
    if any(p[k]!=v for k,v in [('job_id',job_id),('attempt_id',attempt_id),('node_id',node_id),('node_execution_id',row.get('node_execution_id'))]):return None
    return p

def is_live(job,node):
    rows=job.get('node_progress');leases=job.get('node_progress_live')
    return isinstance(rows,dict) and isinstance(leases,dict) and isinstance(rows.get(node),dict) and bool(leases.get(node)) and leases[node]==rows[node].get('node_execution_id')

class Session:
    def __init__(self,job_id,attempt_id,sink,*,previous=None,invalidate=None,clock=time.monotonic):
        self.job_id=job_id;self.attempt_id=attempt_id;self.sink=sink;self.previous=previous or {};self.invalidate=invalidate
        self.clock=clock;self.nodes={};self.node_id=None;self.failed=False;self.held=False
    def emit(self,action,node,**payload):
        if self.failed:return
        try:self.sink(dict(action=action,job_id=self.job_id,attempt_id=self.attempt_id,node_id=node,node_execution_id=self.nodes[node]['id'],**payload))
        except Exception as error:
            # Only optional observer transport is caught, never a business callback.
            self.failed=True
            if self.invalidate:
                try:self.invalidate()
                except Exception:pass
            logging.getLogger(__name__).warning('NODE_PROGRESS_UNAVAILABLE:%s',type(error).__name__)
    def activity(self,state,node=None):
        node=node or self.node_id
        if not node:return
        self.node_id=node
        if node not in self.nodes:
            if state!='RUNNING':return
            self.nodes[node]={'id':uuid.uuid4().hex,'scope':None,'activity':'RUNNING','requested':'RUNNING'}
            self.emit('BEGIN',node,previous_execution_id=(self.previous.get(node) or {}).get('node_execution_id'))
        elif self.nodes[node]['activity']!='CLOSED':
            self.nodes[node]['activity']=state
            self.nodes[node]['requested']=state
            self.emit('ACTIVE' if state=='RUNNING' else 'CLOSE' if state=='CLOSED' else 'SUSPEND',node)
    def control(self,job):
        node=self.node_id
        if not node or node not in self.nodes:return
        rows=job.get('node_progress')
        row=rows.get(node) if isinstance(rows,dict) else None
        if not isinstance(row,dict):return
        state=job.get('control_state')
        desired='RUNNING' if state=='RUNNING' and not self.held and self.nodes[node]['requested']=='RUNNING' else 'SUSPENDED'
        if row.get('node_execution_id')==self.nodes[node]['id'] and row.get('activity')!=desired and row.get('activity')!='CLOSED':
            self.nodes[node]['activity']=desired
            self.emit('ACTIVE' if desired=='RUNNING' else 'SUSPEND',node)
    def close(self):
        for node in tuple(self.nodes):self.activity('CLOSED',node)

@contextmanager
def session_scope(session):
    token=_current.set(session)
    try:yield session
    finally:
        session.close();_current.reset(token)
def activity(state,node=None):
    s=_current.get()
    if s:s.activity(state,node)
def control(job):
    s=_current.get()
    if s:s.control(job)

def hold(value):
    s=_current.get()
    if s:s.held=bool(value)

def stage(job_id,node,active=True):
    s=_current.get()
    if not s or s.job_id!=job_id:return
    if s.node_id and s.node_id!=node:s.activity('CLOSED',s.node_id)
    s.activity('RUNNING' if active else 'CLOSED',node)

class Scope:
    def __init__(self,phase,units,basis='VALIDATED_OUTPUTS'):
        self.session=_current.get();self.row=None;self.done=set();self.limited=set();self.last_sent=0.0
        if not self.session or self.session.failed or not self.session.node_id:return
        s=self.session;self.node=s.node_id;node=s.nodes.get(self.node)
        if not node or node['activity']!='RUNNING':return
        if phase not in NODE_PHASES.get(self.node,set()):return
        self.units=tuple(str(v) for v in units)
        if not self.units or len(self.units)>MAX_UNITS or len(set(self.units))!=len(self.units):return
        if phase not in PHASES or basis not in BASES:return
        self.allowed=set(self.units);previous=node['scope'];scope=uuid.uuid4().hex;node['scope']=scope
        self.row=dict(schema_version='NodeProgress-v1',job_id=s.job_id,attempt_id=s.attempt_id,node_id=self.node,node_execution_id=node['id'],scope_id=scope,scope_kind='SUBPHASE',phase=phase,unit=PHASES[phase],basis=basis,completed_units=0,total_units=len(self.units),plan_hash=hashlib.sha256(json.dumps(self.units,separators=(',',':')).encode()).hexdigest(),progress_revision=1,scope_state='ACTIVE',limited_units=0)
        s.emit('START',self.node,progress=self.row,previous_scope_id=previous)
    def complete(self,unit,*,limited=False):
        if not self.row or self.row['scope_state']!='ACTIVE':return
        key=str(unit)
        if key not in self.allowed or key in self.done:return
        self.done.add(key)
        if limited:self.limited.add(key)
        now=self.session.clock()
        # First useful result immediately; subsequent changes are <=4Hz.
        if self.row['completed_units']==0 or now-self.last_sent>=.25:
            self.last_sent=now;self._send('UPDATE')
    def _send(self,action):
        self.row.update(completed_units=len(self.done),limited_units=len(self.limited),progress_revision=self.row['progress_revision']+1)
        self.session.emit(action,self.node,progress=self.row)
    def close(self):
        if self.row and self.row['scope_state']=='ACTIVE':self.row['scope_state']='CLOSED';self._send('END')
    def __enter__(self):return self
    def __exit__(self,*error):self.close()

def scope(phase,units,basis='VALIDATED_OUTPUTS'):return Scope(phase,units,basis)

def facade_session(facade,job_id,attempt_id):
    try:prior=facade.store.read_job(job_id).get('node_progress',{})
    except Exception as error:
        logging.getLogger(__name__).warning('NODE_PROGRESS_UNAVAILABLE:%s',type(error).__name__)
        session=Session(job_id,attempt_id,lambda event:None);session.failed=True
        return session
    session=Session(job_id,attempt_id,lambda event:facade.record_node_progress(job_id,event),previous=prior)
    session.invalidate=lambda:facade.suppress_node_progress(job_id,{node:row['id'] for node,row in session.nodes.items()})
    return session

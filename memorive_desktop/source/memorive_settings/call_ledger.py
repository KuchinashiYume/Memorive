"""Durable native call evidence. Never stores prompts, headers, outputs or secrets."""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

_scope=ContextVar('memo_call_scope',default={})
_execution=ContextVar('memo_call_execution',default=None)
_control=ContextVar('memo_execution_control',default=None)
_interrupt=ContextVar('memo_execution_interrupt',default=None)

class ExecutionControlSignal(BaseException):
    """Cooperative stop, deliberately outside model-error retries and fallbacks."""
    def __init__(self,state):
        super().__init__('EXECUTION_'+state)
        self.state=state
        self.execution_receipt=None

@contextmanager
def execution_control(checkpoint, *, interrupt=None):
    token=_control.set(checkpoint)
    interrupt_token=_interrupt.set(interrupt or checkpoint)
    try:yield
    finally:
        _interrupt.reset(interrupt_token)
        _control.reset(token)

def execution_checkpoint():
    checkpoint=_control.get()
    if checkpoint is not None:checkpoint()

def execution_interrupt_callback():
    """Return the current task stop callback without changing checkpoint policy."""
    return _interrupt.get()

def _observed_usage(raw):
    # Retain numeric accounting only, including interrupted embedding batches.
    try:value=json.loads(raw)
    except (ValueError,TypeError):return {}
    if not isinstance(value,dict):return {}
    usage=value.get('usage')
    if not isinstance(usage,dict):
        usage={'prompt_tokens':value.get('prompt_eval_count'),'completion_tokens':value.get('eval_count')}
    return {k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens'}
            and isinstance(v,int) and not isinstance(v,bool) and v>=0}

def digest(value):
    if not isinstance(value,bytes):
        value=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode('utf-8')
    return hashlib.sha256(value).hexdigest().upper()

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def call_scope(**fields):
    allowed={'job_id','node_id','profile_ref','task_type'}
    if set(fields)-allowed or any(v is not None and (not isinstance(v,str) or not v or len(v)>192) for v in fields.values()):
        raise ValueError('MODEL_CALL_SCOPE_INVALID')
    token=_scope.set({**_scope.get(),**{k:v for k,v in fields.items() if v is not None}})
    try:yield
    finally:_scope.reset(token)

class CallLedger:
    def __init__(self,root):self.root=Path(root)
    def write(self,folder,identity,phase,value):
        target=self.root/folder
        target.mkdir(parents=True,exist_ok=True)
        # Creation and fsync happen before dispatch; storage failure prevents send.
        with (target/(identity+'.'+phase+'.json')).open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,sort_keys=True,allow_nan=False)
            f.flush();os.fsync(f.fileno())

def recorded_execution(fn):
    signature=inspect.signature(fn)
    @wraps(fn)
    def run(self,*args,**kwargs):
        execution_checkpoint()
        ledger=getattr(self,'_call_ledger',None)
        if ledger is None:return fn(self,*args,**kwargs)
        from copy import deepcopy
        bound=signature.bind(self,*args,**kwargs)
        for key in ('service','cli_service','model'):
            if key in bound.arguments:bound.arguments[key]=deepcopy(bound.arguments[key])
        args=bound.args[1:];kwargs=bound.kwargs;values=bound.arguments
        service=values.get('service') or values.get('cli_service') or {}
        model=values.get('model') or service
        profile=(_scope.get().get('profile_ref') or model.get('profile_ref') or service.get('config_id') or service.get('profile_ref'))
        kind=values.get('profile_kind') or ('CLI' if 'cli' in fn.__name__ else 'API')
        identity='execution-'+uuid4().hex
        pre={'schema_version':'MemoModelExecution-v1','execution_id':identity,'recorded_at':now(),
             'profile_ref':profile,'profile_kind':kind,'requested_model':model.get('model_name'),
             'provider_label':service.get('provider') or service.get('adapter_id') or kind,
             'purpose':values.get('purpose') or fn.__name__,**_scope.get(),
             'behavior_sha256':digest({k:v for k,v in values.items() if k!='self'}),
             'status':'PREPARED','region':'PROVIDER_MANAGED_UNDISCLOSED' if kind!='LOCAL' else 'LOCAL',
             'route':kind,'egress':'LOCAL' if kind=='LOCAL' else 'PROVIDER_MANAGED'}
        from .provider_pricing import snapshot, estimate
        price = snapshot(service, model, pre['recorded_at']) if kind == 'API' else None
        if price is not None:
            pre['price_snapshot'] = price
        ledger.write('executions',identity,'pre',pre)
        state={'ledger':ledger,'pre':pre,'physical_call_ids':[],'observed_usage':{}}
        token=_execution.set(state);started=time.monotonic()
        try:
            from .task_scheduling import CLI_RESOURCES
            with CLI_RESOURCES.enter(self,service) if kind=='CLI' else nullcontext():
                result=dict(fn(self,*args,**kwargs))
            receipt=dict(result.get('execution_receipt') or {})
            from .provider_catalog_aliases import catalog_alias
            alias=catalog_alias(service, model.get('model_name'), [result.get('returned_model')]) if kind=='API' else None
            if alias is not None and result.get('returned_model') != model.get('model_name'):
                receipt['provider_model_alias']={**alias,'endpoint_origin':'https://api.deepseek.com'}
            receipt.update(execution_id=identity,physical_call_ids=list(state['physical_call_ids']),
                           profile_ref=profile,**{k:v for k,v in _scope.get().items() if k!='profile_ref'})
            if kind=='LOCAL' and receipt.get('cost_evidence')=='LOCAL_COMPUTE_NO_PROVIDER_API_COST':
                receipt.update(actual_cost_cny=0, cost_scope='PROVIDER_API_FEE_ONLY')
            if price is not None:
                receipt['price_snapshot'] = price
                cost = estimate(price, receipt.get('token_usage'))
                if cost is not None:
                    currency = str(price.get('currency') or '').lower()
                    if currency in {'cny','usd','jpy'}:
                        receipt.update({f'estimated_cost_{currency}':cost,
                                        'cost_evidence':price.get('price_basis') or 'PROJECT_PRICE_ESTIMATE'})
                    metrics=(receipt.get('transport_diagnostic') or {}).get('prompt_cache_metrics')
                    if isinstance(metrics,dict):
                        tokens=(receipt.get('token_usage') or {}).get('completion_tokens')
                        metrics['cost_per_1000_output_tokens']=cost*1000/tokens if tokens else None
                        metrics['cost_basis']='ESTIMATED'
                        metrics['cost_currency']=price.get('currency')
            result['execution_receipt']=receipt
            post={**pre,'recorded_at':now(),'status':result.get('status','UNKNOWN'),
                  'physical_call_ids':list(state['physical_call_ids']),
                  'duration_ms':round((time.monotonic()-started)*1000),'execution_receipt':receipt}
            ledger.write('executions',identity,'post',post)
            return result
        except BaseException as error:
            # The pre record remains evidence of any incomplete post write.
            post={**pre,'recorded_at':now(),'status':'ERROR','error_type':type(error).__name__,
                  'physical_call_ids':list(state['physical_call_ids']),
                  'duration_ms':round((time.monotonic()-started)*1000)}
            if isinstance(error,ExecutionControlSignal):
                receipt={'status':error.state,'execution_id':identity,'profile_ref':profile,
                         'physical_call_ids':list(state['physical_call_ids']),
                         'token_usage':dict(state['observed_usage']) or None,
                         'usage_scope':'COMPLETED_PHYSICAL_RESPONSES','actual_cost_cny':None}
                if kind=='LOCAL':receipt.update(actual_cost_cny=0,cost_scope='PROVIDER_API_FEE_ONLY')
                if price is not None:
                    receipt['price_snapshot']=price
                    cost=estimate(price,receipt['token_usage'])
                    currency=str(price.get('currency') or '').lower()
                    if cost is not None and currency in {'cny','usd','jpy'}:
                        receipt.update({f'estimated_cost_{currency}':cost,
                                        'cost_evidence':price.get('price_basis') or 'PROJECT_PRICE_ESTIMATE'})
                error.execution_receipt=receipt
                post.update(status=error.state,execution_receipt=receipt)
            if not (ledger.root/'executions'/(identity+'.post.json')).exists():ledger.write('executions',identity,'post',post)
            raise
        finally:_execution.reset(token)
    return run

class RecordingTransport:
    def __init__(self,delegate):self.delegate=delegate
    def __getattr__(self,name):return getattr(self.delegate,name)
    def request(self,**kwargs):return self._run('request',kwargs)
    def run(self,**kwargs):return self._run('run',kwargs)
    def run_template(self,**kwargs):return self._run('run_template',kwargs)
    def run_verification(self, **kwargs):
        if callable(getattr(self.delegate, 'run_verification', None)):
            return self._run('run_verification', kwargs)
        return self.run(**kwargs), {}
    def _run(self,method,kwargs):
        execution_checkpoint()
        state=_execution.get()
        if state is None:return getattr(self.delegate,method)(**kwargs)
        identity='call-'+uuid4().hex;state['physical_call_ids'].append(identity)
        pre={**state['pre'],'call_id':identity,'recorded_at':now(),'transport':'run' if method in {'run_verification','run_template'} else method,'status':'PREPARED'}
        if method=='request':
            url=urlsplit(kwargs['url'])
            pre.update(http_method=kwargs['method'],endpoint_origin=url.scheme+'://'+url.hostname,
                       endpoint_path_sha256=digest(url.path),request_sha256=digest(kwargs.get('body') or b''),
                       proxy_mode=kwargs.get('proxy_mode'),proxy_address_sha256=digest(kwargs.get('proxy_address') or ''))
        else:
            pre.update(command_sha256=digest(kwargs.get('argv') or []),request_sha256=digest(kwargs.get('stdin_bytes') or b''))
        state['ledger'].write('transport',identity,'pre',pre)
        started=time.monotonic()
        try:
            result=getattr(self.delegate,method)(**kwargs)
            observed = result[0] if method in {'run_verification','run_template'} else result
            raw=observed.body if method=='request' else observed.stdout
            post={**pre,'recorded_at':now(),'status':'RETURNED','response_sha256':digest(raw),
                  'response_bytes':len(raw),'duration_ms':round((time.monotonic()-started)*1000)}
            if method=='request':post['http_status']=result.status_code
            else:post.update(returncode=observed.returncode,started=observed.started,timed_out=observed.timed_out)
            usage=_observed_usage(raw)
            if usage:
                post['token_usage']=usage
                for key,value in usage.items():state['observed_usage'][key]=state['observed_usage'].get(key,0)+value
            state['ledger'].write('transport',identity,'post',post)
            return result
        except BaseException as error:
            if not (state['ledger'].root/'transport'/(identity+'.post.json')).exists():
                state['ledger'].write('transport',identity,'post',{**pre,'recorded_at':now(),'status':'ERROR',
                    'error_type':type(error).__name__,'network_error_code':__import__('memorive_settings.network_compat',fromlist=['error_code']).error_code(error) if method=='request' else None,'duration_ms':round((time.monotonic()-started)*1000),
                    'remote_result':'UNKNOWN'})
            raise

def recording_urlopen(opener):
    """Adapt the existing loopback opener without changing its network policy."""
    def open_recorded(request, *, timeout=None):
        from io import BytesIO
        from types import SimpleNamespace
        class Native:
            def request(self, **_kwargs):
                from .cancellable_http import read_response
                return read_response(opener, request, timeout=timeout, interrupt=_interrupt.get())
        response=RecordingTransport(Native()).request(method=request.get_method(),url=request.full_url,
            body=request.data,proxy_mode='DIRECT',proxy_address='')
        stream=BytesIO(response.body)
        stream.status=response.status_code
        return stream
    return open_recorded

"""Console-only product service entry points; no production mode registration."""
from __future__ import annotations
import threading, hashlib, json
from memorive_app.contracts import utc_now

class ConsoleServiceAdapter:
    adapter_kind='real_application_facade_console_session'
    def __init__(self, delegate, worker, session_id):
        self.delegate,self.worker,self.session_id=delegate,worker,session_id
        self.facade=delegate.facade
        self.lock=threading.RLock()
        self.thread=None
        self.manual_receipt=None
        self.owned_jobs=set()

    def parity_projection(self):
        result=self.delegate.parity_projection()
        result['allowed_methods'] += ['console.inject_event','console.control_task','console.dispatch','console.worker_status','console.execute_pending']
        return result

    def _job(self,jid):
        # Native service store belongs exclusively to the verified DATA_ROOT.
        job=self.facade.get_job(jid)
        return job

    def _execute_pending(self):
        try:self.manual_receipt=self.worker.run_pending_once()
        except Exception as exc:self.manual_receipt={'status':'FAILED','error_code':getattr(exc,'code','Core_EXECUTION_FAILED')}

    def call(self, method, p):
        if method=='inbox.set_auto_run' and p.get('enabled') is not False:
            raise ValueError('AUTOMATIC_EXECUTION_LOCKED')
        if not method.startswith('console.'):
            return self.delegate.call(method,p)
        with self.lock:
            if method=='console.worker_status':
                return {'active':bool(self.thread and self.thread.is_alive()),'receipt':self.manual_receipt}
            if method=='console.execute_pending':
                if p != {'allow_model_calls':True}:raise ValueError('MANUAL_MODEL_PERMISSION_REQUIRED')
                if self.thread and self.thread.is_alive():raise ValueError('MANUAL_TASK_ALREADY_RUNNING')
                self.manual_receipt=None
                self.thread=threading.Thread(target=self._execute_pending,name='console-manual-core',daemon=True)
                self.thread.start()
                return {'execution_state':'RUNNING','business_job_complete':False}
            if method=='console.inject_event':
                if set(p)!={'event','note','request_id'} or p['event'] not in {'import','complete','fail','review','cancel','stall'}:
                    raise ValueError('CONSOLE_EVENT_INVALID')
                # Use the existing facade/lifecycle and synthetic event mechanism,
                # but identify the event as a console request, not a production run.
                from memorive_current_task.product_controller import _request
                request=_request(p['request_id'],'[控制台模拟] '+p['event'])
                request['job_type']='memorive-console-simulation'
                request['payload_ref']='console:'+self.session_id+':'+p['request_id']
                request['resolved_task_input']['console_session_id']=self.session_id
                request['input_hashes']={'input':hashlib.sha256(json.dumps(p,ensure_ascii=False,sort_keys=True).encode()).hexdigest().upper()}
                request['profile_snapshot']['source_hashes']=request['input_hashes']
                request['profile_snapshot']['created_at']=utc_now()
                handle=self.facade.start_job(request,idempotency_key='console:'+p['request_id'])
                jid=handle['job_id'];self.owned_jobs.add(jid)
                job=self._job(jid)
                if job['control_state']=='QUEUED':
                    status=self.facade.mark_running(jid,expected_version=job['version'])
                    status=self.facade.set_safe_checkpoint(jid,expected_version=status['observed_version'],safe=True)
                    version=status['observed_version']
                    target={'complete':'SUCCEEDED','fail':'FAILED','review':'WAITING_HUMAN','cancel':'CANCELLED'}.get(p['event'])
                    if target=='CANCELLED':self.facade.cancel_job(jid,expected_version=version,idempotency_key='console-cancel:'+p['request_id'])
                    elif target:
                        if target=='WAITING_HUMAN':
                            row=self.facade.record_action_request(jid,expected_version=version,action_request_id='console-action-'+p['request_id'],action='REVIEW_CONSOLE_EVENT',payload={'test_data':True})
                            version=row['observed_version']
                        if target != 'WAITING_HUMAN':
                            self.facade._transition(jid,version,target,'CONSOLE_SIMULATED_'+p['event'].upper())
                return {'job_id':jid,'job':self.facade.get_job(jid),'test_data':True,'native_store':True}
            if method=='console.dispatch':
                if set(p)!={'item_id','request_id','allow_model_calls'} or p['allow_model_calls'] is not True:
                    raise ValueError('MANUAL_MODEL_PERMISSION_REQUIRED')
                if self.thread and self.thread.is_alive():raise ValueError('MANUAL_TASK_ALREADY_RUNNING')
                # Never pick an unrelated pending item through the worker's FIFO.
                pending=[x for x in self.worker.inbox.store.list_intents() if x.get('state')=='PENDING']
                if any(x['item_id']!=p['item_id'] for x in pending):raise ValueError('UNRELATED_PENDING_DISPATCH')
                result=self.worker.inbox.dispatch(p['item_id'],idempotency_key='console:'+p['request_id'])
                self.manual_receipt=None
                self.thread=threading.Thread(target=self._execute_pending,name='console-manual-core',daemon=True)
                self.thread.start()
                return {'dispatch_receipt':result,'execution_state':'RUNNING','business_job_complete':False}
            if method=='console.control_task':
                if set(p)!={'job_id','action','request_id','allow_model_calls'}:raise ValueError('CONTROL_FIELDS_INVALID')
                action=p['action'];job=self._job(p['job_id'])
                if action not in {'pause','resume','cancel','retry'}:raise ValueError('CONTROL_ACTION_INVALID')
                if action == 'cancel':
                    prior = self.facade.store.lookup_idempotency('control:cancel', 'console:'+p['request_id'])
                    if prior is not None:
                        if prior['job_id'] != p['job_id']:raise ValueError('CONTROL_IDEMPOTENCY_CONFLICT')
                        return {'action_receipt':prior,'business_job_complete':False,
                                'test_data':job.get('request',{}).get('job_type')=='memorive-console-simulation'}
                if action in {'resume','retry'} and p['allow_model_calls'] is not True:raise ValueError('MANUAL_MODEL_PERMISSION_REQUIRED')
                if action=='retry':
                    if job.get('request',{}).get('job_type')!='memorive-console-simulation':
                        raise ValueError('PRODUCTION_RETRY_REQUIRES_WORKFLOW_CONTROLLER')
                    receipt=self.facade.retry_job(p['job_id'],retry_policy={'request_id':'retry-'+p['request_id'],'correlation_id':self.session_id},idempotency_key='console:'+p['request_id'])
                    if p['job_id'] in self.owned_jobs:self.owned_jobs.add(receipt['job_id'])
                    if job.get('request',{}).get('job_type')=='memorive-console-simulation':
                        started=self.facade.mark_running(receipt['job_id'],expected_version=receipt['observed_version'])
                        self.facade._transition(receipt['job_id'],started['observed_version'],'SUCCEEDED','CONSOLE_SIMULATION_REPLAY_COMPLETED')
                else:
                    receipt=getattr(self.facade,action+'_job')(p['job_id'],expected_version=job['version'],idempotency_key='console:'+p['request_id'])
                return {'action_receipt':receipt,'business_job_complete':False,'test_data':job.get('request',{}).get('job_type')=='memorive-console-simulation'}
        raise ValueError('CONSOLE_SERVICE_OPERATION_UNKNOWN')

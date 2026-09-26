"""Session-scoped adapters to native product stores and inherited Phase engines."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, threading, uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest().upper()


class ProductConsoleAdapter:
    def __init__(self, api, launch, stop):
        if not Path(api._state_dir).resolve().is_relative_to(launch.data_root):
            raise ValueError('PRODUCT_PROFILE_OUTSIDE_SESSION')
        self.api, self.launch, self.stop = api, launch, stop
        self.lock = threading.RLock()
        self.root = Path(api._state_dir) / 'console-research'
        self.root.mkdir(exist_ok=True)
        self.reports = []
        self.api.call('inbox.set_auto_run', {'enabled':False})

    def snapshot(self):
        with self.lock:
            messages = self.api.call('messages.list', {})['rows']
            inbox = self.api.call('inbox.projection', {})['cards']
            jobs = self.api._service.call('list_jobs', {})['jobs']
            reports=[self.api._research.call('research.get',{'run_id':r['run_id']}) for r in self.api._research.state()['runs'] if r['kind']=='report' and r['status'] in {'SUCCEEDED','PARTIAL'}]
            return {'messages':messages, 'inbox':inbox, 'jobs':jobs, 'reports':reports}

    def _message(self, kind, title, body, request_id, *, job_id=None, report_period=None):
        from pr_os_messages.projection import MessageProjectionEngine
        from pr_os_messages.contracts import message_dedupe_key, message_id_for_dedupe
        severity = 'RED' if kind in {'error','report_failed','stalled','target_missing'} else 'YELLOW' if kind in {'warning','review'} else 'GREEN' if kind in {'success','imported'} else 'BLUE'
        event_type = {'success':'JOB_COMPLETED', 'error':'JOB_FAILED', 'report_failed':'REPORT_FAILED', 'daily':'REPORT_READY','weekly':'REPORT_READY','monthly':'REPORT_READY', 'review':'ACTION_REQUIRED'}.get(kind,'USER_INFORMATION')
        store=self.api._messages.store
        with self.api._messages._runtime_event_lock:
            event={'sequence':int(store.load(recover_corruption=False)['state']['projection']['cursor'])+1,
                'event_id':'console-event-'+uuid.uuid4().hex,'event_type':event_type,
                'job_id':job_id or self.launch.session_id,'run_id':self.launch.session_id,'attempt_id':request_id,
                'node_id':None,'root_cause':'CONSOLE_'+kind.upper()+'_'+sha(request_id)[:16],'occurred_at':now(),'severity':severity,
                'message_required':True,'safe_title':title,'safe_summary':body[:320],'safe_body':body,
                'target_locator':'pr-os://job/'+(job_id or self.launch.session_id),
                'protected_bulk_read':severity in {'RED','YELLOW'} or kind in {'daily','weekly','monthly','report_failed','radar'},'attachment_refs':[],
                'status_axes':{'lifecycle_status':'completed','verification_result':'NOT_ASSESSED','acceptance_verdict':'NOT_ASSESSED','capability_status':'AVAILABLE'},
                'report_period':report_period}
            receipt=MessageProjectionEngine(store,synthetic_only=False).ingest([event])
            mid=message_id_for_dedupe(message_dedupe_key(event))
        return {'message_id':mid, 'native_receipt':receipt, 'test_data':True}

    def _require_target(self, category, value):
        if category == 'jobs':
            # A control must not first enumerate reports/messages/inbox.
            # The verified service is already confined to this console DATA_ROOT.
            job = self.api._service.call('get_job', {'job_id': value})
            if job.get('job_id') != value:raise ValueError('TARGET_NOT_IN_SESSION')
            return
        key={'messages':'message_id','inbox':'item_id','jobs':'job_id'}[category]
        if not any(row[key]==value for row in self.snapshot()[category]):
            raise ValueError('TARGET_NOT_IN_SESSION')

    def execute(self, operation, params, request_id, allow_model_calls):
        if self.stop.is_set():
            raise ValueError('SESSION_CLOSING')
        if operation=='message.test':
            return self._message(params['kind'], '[测试] '+params['title'],params['body'],request_id)
        if operation=='event.inject':
            result=self.api._service.call('console.inject_event', {'event':params['event'],'note':params['note'],'request_id':request_id})
            kind={'import':'imported','complete':'success','fail':'error','review':'review','cancel':'cancelled','stall':'stalled'}[params['event']]
            return {**result, **self._message(kind,'[模拟事件] '+params['event'],params['note'],request_id,job_id=result.get('job_id'))}
        if operation in {'message.confirm','message.star'}:
            self._require_target('messages',params['message_id'])
            return self.api.call('messages.confirm' if operation.endswith('confirm') else 'messages.toggle_star',
                dict(params, from_detail=True) if operation.endswith('confirm') else params)
        if operation=='inbox.import':
            result=self.api.call('inbox.import_paths',{'paths':params['paths'],'request_id':request_id,'source_kind':'picker'})
            self._message('imported' if result.get('status') not in {'ERROR','PARTIAL_ERROR','FAILED'} else 'warning',
                '控制台导入结果','导入回执已生成，请查看收件箱；自动执行关闭。',request_id)
            return result
        if operation.startswith('task.'):
            self._require_target('jobs',params['job_id'])
            if operation=='task.retry':
                job=self.api._service.call('get_job',{'job_id':params['job_id']})
                if job.get('request',{}).get('job_type')!='pr-os-console-simulation':
                    if allow_model_calls is not True:raise ValueError('MANUAL_MODEL_PERMISSION_REQUIRED')
                    worker=self.api._service.call('console.worker_status',{})
                    if worker['active']:raise ValueError('MANUAL_TASK_ALREADY_RUNNING')
                    receipt=self.api.call('current_task.retry',{'job_id':params['job_id'],'request_id':request_id})
                    execution=self.api._service.call('console.execute_pending',{'allow_model_calls':True})
                    return {'retry_receipt':receipt,**execution}
            control = {'action':operation.split('.')[1], 'job_id':params['job_id'],'request_id':request_id,'allow_model_calls':allow_model_calls}
            try:
                return self.api._service.call('console.control_task',control)
            except Exception as error:
                from pr_os_desktop_service.errors import ConnectionLost, RequestTimeout
                if operation != 'task.cancel' or not isinstance(error,(ConnectionLost,RequestTimeout)):
                    raise
                # Cancel is idempotent and never invokes a model. Reconnect once
                # with the SAME command key; the service returns its durable
                # control receipt if the reply, rather than the command, was lost.
                return self.api._service.call('console.control_task',control)
        if operation=='inbox.dispatch':
            self._require_target('inbox',params['item_id'])
            return self.api._service.call('console.dispatch',dict(params,request_id=request_id,allow_model_calls=allow_model_calls))
        if operation=='report.run':
            return {'report':self.api._research.run_sync('report',request_id,period=params['period'])}
        if operation=='radar.recommend':
            return {'radar':self.api._research.run_sync('radar',request_id,topic=params['topic'],count=params['count'])}
        raise ValueError('OPERATION_NOT_SUPPORTED')

    def _bound_input(self, name):
        p=self.launch.data_root/'research-inputs'/name
        if not p.is_file():
            raise ValueError('RESEARCH_INPUT_BINDING_REQUIRED')
        resolved=p.resolve()
        if not resolved.is_relative_to(self.launch.data_root):
            raise ValueError('RESEARCH_INPUT_OUTSIDE_SESSION')
        return resolved, json.loads(resolved.read_text(encoding='utf8'))

    def _report(self, period, request_id):
        # The frozen M15 CLI performs all source/authority/hash checks and calls
        # the original digest builder, domain validator and fixed renderer.
        from m15_research_changelog.cli import run
        from argparse import Namespace
        path,binding=self._bound_input('report-'+period+'.json')
        if binding.get('digest_kind')!=period:
            raise ValueError('REPORT_PERIOD_BINDING_MISMATCH')
        for key in ('run_context','source_manifest','output_dir'):
            if not Path(binding[key]).resolve().is_relative_to(self.launch.data_root):
                raise ValueError('RESEARCH_INPUT_OUTSIDE_SESSION')
        result=run(Namespace(**binding))
        out=Path(binding['output_dir'])
        markdown=(out/'research_change_log.md').read_text(encoding='utf8')
        report={'report_id':'report-'+request_id,'period':period,'created_at':now(), 'native_receipt':result,
                'body':markdown,'source_binding_sha256':hashlib.sha256(path.read_bytes()).hexdigest().upper(),
                'output_dir':str(out.relative_to(self.launch.data_root)), 'engine':'m15_research_changelog.cli.run'}
        with self.lock:self.reports.append(report)
        self._message(period,'周期报告 · '+period,markdown[:3900],request_id,report_period=period)
        return {'report':report}

    def _radar(self, params, request_id):
        from scripts.p04_t09_cross_acceptance_runner import run
        from scripts.p04_t09_fixed_renderer import render
        path,binding=self._bound_input('radar.json')
        if params['topic']!=binding.get('topic') or params['count']!=binding.get('count'):
            raise ValueError('RADAR_SCOPE_BINDING_MISMATCH')
        for key in ('manifest','input_root'):
            if not Path(binding[key]).resolve().is_relative_to(self.launch.data_root):
                raise ValueError('RESEARCH_INPUT_OUTSIDE_SESSION')
        out=self.root/request_id
        out.mkdir(exist_ok=False)
        result=run(Path(binding['manifest']),Path(binding['input_root']),work_root=out/'work')
        markdown=render(result['recommendations'])
        (out/'integrated_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        (out/'recommendations.md').write_text(markdown,encoding='utf8')
        self._message('radar','外部雷达推荐',markdown[:3900],request_id)
        return {'radar':result,'engine':'scripts.p04_t09_cross_acceptance_runner.run', 'mode':'FROZEN_SOURCE_REPLAY', 'live_fetch_performed':False}

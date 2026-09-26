"""User-facing, persistent research execution over native desktop state."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import copy, re, threading, time, json
from .common import sha, now, write, read, sealed, validate_config, defaults, CHANNELS
from .transport import MetadataClient

METHODS=frozenset({'research.state','research.config_get','research.config_save','research.start','research.get','research.cancel',
    'research.source_refresh','research.feedback_state','research.expose','research.feedback','research.revoke',
    'research.repair_delivery','research.task_list','research.interaction_state','research.schedule_get','research.schedule_save',
    'research.workflow_get','research.workflow_save'})

class ResearchRuntime:
    def __init__(self,api):
        self.api=api
        self.root=api._profile_path('WORKSPACE')/'research'
        self.root.mkdir(parents=True,exist_ok=True)
        from .feedback import FeedbackStore
        self.feedback=FeedbackStore(self.root)
        self.lock=threading.RLock();self.stop=threading.Event();self.thread=None
        self.client=MetadataClient(self.root,lambda:self.api._service.call('settings.get_state',{})['settings']['preferences'],
            source_config=lambda:self.api._service.call('settings.external_sources_get',{})['literature']['slots'])
        self.current=None;self.closed=False
        self.report_clock=None
        self.scheduler_stop = threading.Event()
        self.scheduler = None
        # A previous process can leave a receipt, never an invisible running task.
        for path in (self.root/'runs').glob('*/state.json'):
            item=read(path)
            if item['status'] in {'QUEUED','RUNNING'}:
                write(path,sealed(dict(item,status='INTERRUPTED',failed_stage=item.get('stage'),error_code='PROCESS_INTERRUPTED',finished_at=now())))
            elif item['status'] in {'SUCCEEDED','PARTIAL'} and (path.parent/'result.json').exists():
                try:self._publish_library(item['run_id'], item['kind'], read(path.parent/'result.json'))
                except (OSError,ValueError,KeyError):
                    # One damaged historical report must not prevent desktop
                    # startup or hide other papers. Never overwrite that file.
                    self._state(item['run_id'],notification_error='RESEARCH_EXISTING_RESULT_NEEDS_REVIEW',status='PARTIAL')
        self.scheduler = threading.Thread(target=self._schedule_loop, name='desktop-passive-discovery', daemon=True)
        self.scheduler.start()

    def library_rows(self):
        from .library_projection import rows
        return rows(self.root)

    def _publish_library(self, run_id, kind, result):
        from .library_projection import publish
        if sealed(result)['sha256']!=result.get('sha256'):
            raise ValueError('RESEARCH_RESULT_HASH_MISMATCH')
        readable=self._run_path(run_id)/'result.md'
        if not readable.is_file() or readable.read_text(encoding='utf8')!=result['markdown']:
            raise ValueError('RESEARCH_READABLE_RESULT_MISMATCH')
        state = read(self._run_path(run_id)/'state.json')
        row = publish(self.root, run_id, kind, state['title'], result, state['created_at'])
        self._state(run_id, library_artifact_id=row['artifact_id'], library_view=row['mode'],
                    library_locator=row['stable_locator'], result_sha256=result['sha256'])
        return row

    def _schedule_loop(self):
        next_report_check = 0.0
        while not self.scheduler_stop.wait(2):
            if time.monotonic() >= next_report_check:
                next_report_check = time.monotonic() + 60
                try:self.report_tick()
                except Exception:self.periodic_status = 'REPORT_STATE_NEEDS_REVIEW'
            try:self.passive_tick()
            except Exception as exc:
                self.passive_status = 'WAITING_CONFIGURATION'

    def report_tick(self, current=None):
        from .periodic import tick
        return tick(self, current)

    def _has_active_run(self):
        # Result publication is the transaction boundary. The completed
        # worker may still be posting a UI notification; that is not another
        # research execution and must not reject the user's next request.
        with self.lock:
            return bool(self.current and read(self._run_path(self.current)/'state.json')['status'] in {'QUEUED','RUNNING'})

    def passive_tick(self, current_time=None):
        import os
        clock = time.time() if current_time is None else current_time
        with self.lock:
            if self.closed:return {'status':'CLOSED'}
            if os.environ.get('MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION') == 'DISABLED':
                return {'status':'PAUSED_CONSOLE_SESSION'}
            config = self.api._service.call('settings.research_get',{})['config']
            if not config['passive_enabled']:return {'status':'DISABLED'}
            if not config['topic'].strip() and not config.get('directions'):return {'status':'TOPIC_REQUIRED'}
            if not self.api._service.call('settings.external_sources_get',{})['literature']['selected']:
                return {'status':'SOURCE_REQUIRED'}
            if self._has_active_run():return {'status':'RUNNING','run_id':self.current}
            path = self.root/'passive_schedule.json'
            saved = read(path) if path.exists() else {}
            if saved.get('request_id'):
                previous_id='research-'+sha(saved['request_id'])[:24]
                previous_path=self._run_path(previous_id)
                previous=read(previous_path/'state.json') if (previous_path/'state.json').exists() else {}
                if (previous_path/'result.json').exists():
                    # Publication may have failed after the immutable result.
                    # Never refetch merely to repair its delivery.
                    read(previous_path/'result.json')
                    due=saved.get('next_due_unix',saved['claimed_at_unix']+saved.get('interval_seconds',config['interval_hours']*3600))
                    if saved.get('next_due_unix')!=due:
                        saved=sealed(dict(saved,next_due_unix=due));write(path,saved)
                    if clock<due:return dict(saved,status='WAITING_NEXT_RUN')
                elif previous.get('status') in {'QUEUED','RUNNING'}:
                    return {'status':'RUNNING','run_id':previous_id}
                elif previous.get('status') not in {None,'FAILED','INTERRUPTED'} or any((self.root/'requests'/previous_id).glob('*.presend.json')):
                    # An emitted request is not a proven pre-provider failure.
                    # No automatic resend, even after the former due time.
                    return {'status':'RETRY_REQUIRES_REVIEW','run_id':previous_id}
            request_id = 'passive-' + sha({'previous':saved.get('request_id'),'at':clock})[:32]
            # Claim before starting, but only a durable result consumes a period.
            # Failed claims remain traceable and may retry if no request was sent.
            schedule = sealed({'request_id':request_id,'claimed_at_unix':clock,
                               'interval_seconds':config['interval_hours']*3600})
            write(path,schedule)
            item = self.start(kind='discovery',request_id=request_id,trigger='PASSIVE_OPT_IN')
            return {'status':'RUNNING','run_id':item['run_id'],'next_due_unix':None}

    def _run_path(self,run_id):
        if not isinstance(run_id,str) or not re.fullmatch(r'research-[a-f0-9]{24}',run_id):raise ValueError('RESEARCH_ID_INVALID')
        return self.root/'runs'/run_id

    def _state(self,identity,**changes):
        with self.lock:
            path=self._run_path(identity)/'state.json'
            prior=read(path) if path.exists() else {}
            value=sealed(dict(prior,**changes));write(path,value);return value

    def state(self):
        items=[]
        for path in (self.root/'runs').glob('*/state.json'):
            item=read(path);items.append(item)
        from .report_identity import project
        items=project(self.root,items)
        return {'schema_version':'DesktopResearchState-v1','runs':sorted(items,key=lambda x:x['created_at'],reverse=True),
                'active_run_id':self.current if self._has_active_run() else None,
                'sources':self.api._service.call('settings.external_sources_get',{})['literature'],
                'execution_mode':'MANUAL_AND_OPT_IN_PASSIVE_METADATA',
                'paid_model_calls':sum(r.get('paid_model_calls',0) for r in items),
                'passive_config':self.api._service.call('settings.research_get',{})['config'],
                'passive_console_locked':__import__('os').environ.get('MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION')=='DISABLED'}

    def call(self,method,p):
        if method not in METHODS:raise ValueError('RESEARCH_METHOD_INVALID')
        if method=='research.workflow_get':
            if p:raise ValueError('RESEARCH_PARAMS_INVALID')
            from .report_models import projection
            return projection(self)
        if method=='research.workflow_save':
            if set(p)!={'config','expected_revision'}:raise ValueError('RESEARCH_PARAMS_INVALID')
            from .report_models import save
            return save(self,**p)
        if method=='research.schedule_get':
            if p:raise ValueError('RESEARCH_PARAMS_INVALID')
            from .report_schedule import get
            with self.lock:return get(self.root)
        if method=='research.schedule_save':
            if set(p)!={'config','expected_revision'}:raise ValueError('RESEARCH_PARAMS_INVALID')
            from .report_schedule import save
            return save(self,**p)
        if method=='research.state':
            if p:raise ValueError('RESEARCH_PARAMS_INVALID')
            return self.state()
        if method=='research.feedback_state':
            if p:raise ValueError('RESEARCH_PARAMS_INVALID')
            return self.feedback.snapshot()
        if method=='research.task_list':
            if p:raise ValueError('RESEARCH_PARAMS_INVALID')
            return self.task_list()
        if method=='research.source_refresh':
            if set(p)!={'source','request_id'}:raise ValueError('RESEARCH_PARAMS_INVALID')
            if not self.api._service.call('settings.research_get',{})['config']['passive_enabled']:
                raise ValueError('RESEARCH_RECEIVING_DISABLED')
            return self.start(kind='discovery',request_id=p['request_id'],source_filter=p['source'])
        if method=='research.expose':
            if set(p)!={'run_id','candidate_id','request_id'}:raise ValueError('RESEARCH_PARAMS_INVALID')
            result=read(self._run_path(p['run_id'])/'result.json')
            if sealed(result)['sha256']!=result.get('sha256'):raise ValueError('RESEARCH_RESULT_HASH_MISMATCH')
            item=next((x for x in result['recommendations'] if x['candidate_id']==p['candidate_id']),None)
            if item is None:raise ValueError('RESEARCH_CANDIDATE_NOT_FOUND')
            return self.feedback.expose(p['run_id'],result.get('slate_sha256',result['sha256']),item,p['request_id'])
        if method=='research.feedback':
            if not {'exposure_event_id','decision_type','request_id'}.issubset(p):raise ValueError('RESEARCH_PARAMS_INVALID')
            if set(p)-{'exposure_event_id','decision_type','request_id','cooldown_until','global_confirmation','manifestation_id','candidate_card_ref'}:raise ValueError('RESEARCH_PARAMS_INVALID')
            return self.feedback.decide(**p)
        if method=='research.revoke':
            if set(p)!={'decision_event_id','request_id'}:raise ValueError('RESEARCH_PARAMS_INVALID')
            return self.feedback.revoke(**p)
        if method=='research.repair_delivery':
            if set(p)!={'run_id'}:raise ValueError('RESEARCH_PARAMS_INVALID')
            return self.repair_delivery(p['run_id'])
        if method=='research.interaction_state':
            if p:raise ValueError('RESEARCH_PARAMS_INVALID')
            from .interactions import current_digest
            return current_digest(self)
        if method in {'research.config_get','research.config_save'}:
            return self.api._service.call(method.replace('research.config','settings.research'),p)
        if method=='research.start':
            if any(key in p for key in ('trigger','source_filter','report_window','material_progress','report_cutoff')):raise ValueError('RESEARCH_PARAMS_INVALID')
            return self.start(**p)
        if set(p)!={'run_id'}:raise ValueError('RESEARCH_PARAMS_INVALID')
        path=self._run_path(p['run_id'])
        if method=='research.get':
            result=read(path/'state.json')
            if (path/'result.json').exists():
                body=read(path/'result.json')
                if sealed(body)['sha256']!=body.get('sha256'):raise ValueError('RESEARCH_RESULT_HASH_MISMATCH')
                result['result']=body
            return result
        with self.lock:
            if self.current==p['run_id'] and self._has_active_run():
                self.stop.set();return {'run_id':p['run_id'],'status':'CANCELLING'}
            return read(path/'state.json')

    def start(self,*,kind,request_id,topic=None,count=None,period=None,trigger='MANUAL',source_filter=None,report_window=None,material_progress=None,report_cutoff=None,regenerate_from=None):
        if kind not in {'discovery','report'} or not isinstance(request_id,str) or not 1<=len(request_id)<=160:raise ValueError('RESEARCH_START_INVALID')
        implicit_report=kind=='report' and trigger=='MANUAL' and report_window is None and report_cutoff is None and material_progress is None
        config=self.api._service.call('settings.research_get',{})['config']
        if kind=='discovery':
            config=validate_config(dict(config,topic=config['topic'] if topic is None else topic,count=config['count'] if count is None else count))
            if not config['topic'].strip() and not config.get('directions'):raise ValueError('RESEARCH_TOPIC_REQUIRED')
            literature=self.api._service.call('settings.external_sources_get',{})['literature']
            sources=literature['selected']
            if source_filter is not None:
                if source_filter not in sources:raise ValueError('RESEARCH_SOURCE_NOT_ENABLED')
                sources=[source_filter]
            if not sources:raise ValueError('RESEARCH_SOURCE_REQUIRED')
        else:
            if period not in {'daily','weekly','monthly'}:raise ValueError('RESEARCH_PERIOD_INVALID')
            sources=[]
            from .report_identity import previous_window,project
            from research_reports.policy import ZONE,validate_window
            current=self.report_clock() if self.report_clock else datetime.now(ZONE)
            if regenerate_from is not None:
                original_path=self._run_path(regenerate_from)
                original=read(original_path/'state.json')
                if original.get('kind')!='report' or original.get('period')!=period or original['status'] in {'QUEUED','RUNNING'}:
                    raise ValueError('REPORT_REGENERATION_INVALID')
                original=project(self.root,[original])[0]
                report_window=original.get('report_window')
                if not report_window:raise ValueError('REPORT_WINDOW_UNAVAILABLE')
                previous=read(original_path/'request.json')
                if 'material_progress' in previous:
                    from .periodic import material_progress as progress_rows,windows
                    from .report_identity import window_key
                    matches=[row for row in windows(progress_rows(self.api),current) if window_key(row['window'])==window_key(report_window)]
                    material_progress=matches[0]['progress'] if matches else previous['material_progress']
            report_window=report_window or previous_window(period,current)
            report_cutoff=report_cutoff or current.isoformat()
            validate_window(period,report_window['start'],report_window['end'],report_cutoff)
        if kind!='report' and regenerate_from is not None:raise ValueError('REPORT_REGENERATION_INVALID')
        params={'kind':kind,'config':config,'sources':sources,'period':period}
        if kind=='report':
            params.update(report_window=report_window,report_cutoff=report_cutoff)
            if material_progress is not None:params['material_progress']=material_progress
            if regenerate_from is not None:params['regenerate_from']=regenerate_from
        if kind=='discovery':params['source_snapshot']=[s for s in literature['slots'] if s['provider'] in sources]
        if trigger == 'PASSIVE_MATERIAL_PROGRESS':
            if kind!='report' or not report_window or not material_progress:raise ValueError('REPORT_MATERIAL_PROGRESS_REQUIRED')
            params.update(trigger=trigger,report_window=report_window,material_progress=material_progress,report_cutoff=report_cutoff)
        elif trigger != 'MANUAL':
            if trigger != 'PASSIVE_OPT_IN' or kind != 'discovery' or not config['passive_enabled']:
                raise ValueError('RESEARCH_PASSIVE_AUTHORIZATION_INVALID')
            params['trigger'] = trigger
        run_id='research-'+sha(request_id)[:24];path=self._run_path(run_id)
        with self.lock:
            if self.closed:raise ValueError('RESEARCH_CLOSED')
            if self.scheduler_stop.is_set():raise ValueError('RESEARCH_CLOSED')
            if (path/'state.json').exists():
                item=read(path/'state.json')
                replay=dict(params)
                if kind=='report':
                    frozen_request=read(path/'request.json')
                    for key in ('report_model','report_language'):
                        if key in frozen_request:replay[key]=frozen_request[key]
                if implicit_report:
                    prior=read(path/'request.json')
                    # A repeated click keeps the first request's frozen clock and
                    # source snapshot, even across midnight or an app restart.
                    for key in ('report_window','report_cutoff','material_progress','report_model','report_language'):
                        if key in prior:replay[key]=prior[key]
                        else:replay.pop(key,None)
                if item['request_sha256']!=sha(replay):raise ValueError('RESEARCH_IDEMPOTENCY_CONFLICT')
                return item
            if self._has_active_run():raise ValueError('RESEARCH_ALREADY_RUNNING')
            if kind=='report':
                from .report_models import freeze
                from .periodic import material_progress as progress_rows,windows
                from .report_identity import window_key
                if not params.get('material_progress'):
                    matches=[row for row in windows(progress_rows(self.api),current) if window_key(row['window'])==window_key(report_window)]
                    params['material_progress']=matches[0]['progress'] if matches else []
                if not params['material_progress']:raise ValueError('REPORT_NO_LIBRARY_CHANGE')
                params['report_model']=freeze(self,run_id,period)
                params['report_language']=self.api._service.call('settings.get_state',{})['settings']['preferences']['language']
            self.stop=threading.Event();self.current=run_id
            item=self._state(run_id,run_id=run_id,kind=kind,title=(config['topic'] or ' / '.join(d['name'] for d in config['directions'])) if kind=='discovery' else {'daily':'日报','weekly':'周报','monthly':'月报'}[period],created_at=now(),status='QUEUED',stage='WAITING',request_sha256=sha(params),period=period)
            if report_window:
                from .report_identity import next_revision,title
                revision=next_revision(self.root,report_window)
                item=self._state(run_id,title=title(report_window,revision),report_window=report_window,
                    report_revision=revision,regenerate_from=regenerate_from,report_model=params.get('report_model'))
            write(path/'request.json',sealed(params))
            self.thread=threading.Thread(target=self._execute,args=(run_id,params),name='desktop-research',daemon=True)
            self.thread.start();return item

    def run_sync(self,kind,request_id,**kwargs):
        item=self.start(kind=kind,request_id=request_id,**kwargs)
        while True:
            result=self.call('research.get',{'run_id':item['run_id']})
            if result['status'] not in {'QUEUED','RUNNING'}:
                if result['status'] not in {'SUCCEEDED','PARTIAL'}:raise ValueError(result.get('error_code',result['status']))
                return result
            time.sleep(.1)

    def _execute(self,run_id,params):
        try:
            from memorive_settings.task_scheduling import TASK_CATEGORIES
            def checkpoint():
                if self.stop.is_set():
                    raise ValueError('RESEARCH_CANCELLED')
            with TASK_CATEGORIES.enter('RESEARCH_' + params['kind'].upper(), checkpoint=checkpoint):
                self._state(run_id,status='RUNNING',stage='COLLECTING')
                if 'material_progress' in params:
                    from .periodic import render
                    result=render(self,run_id,params)
                else:
                    result=self._discovery(run_id,params) if params['kind']=='discovery' else self._report(run_id,params['period'],params.get('report_window'),params.get('report_cutoff'))
                if params.get('report_model'):
                    from .report_models import compose
                    result=compose(self,run_id,params,result)
                    self._state(run_id,model_calls=result['model_calls'],paid_model_calls=result['paid_model_calls'])
                if self.stop.is_set():raise ValueError('RESEARCH_CANCELLED')
                result=sealed(result);write(self._run_path(run_id)/'result.json',result)
                (self._run_path(run_id)/'result.md').write_text(result['markdown'],encoding='utf8')
                self._state(run_id,stage='PUBLISHING',result_sha256=result['sha256'])
                self._publish_library(run_id, params['kind'], result)
                notification_failed=False
                try:
                    if params['kind']=='report':self._state(run_id,stage='NOTIFYING')
                    if params['kind'] == 'discovery' and (not params['config']['push_enabled'] or not result.get('recommendations')):
                        self._state(run_id, message_id=None, notification_status='SUPPRESSED_BY_USER')
                    else:
                        mid=self._message(run_id,params['kind'],result,params['period'])
                        self._state(run_id,message_id=mid,notification_status='MESSAGE_CREATED')
                except Exception:
                    notification_failed=True
                    self._state(run_id,notification_error='RESEARCH_MESSAGE_PROJECTION_FAILED')
                self._state(run_id,status='PARTIAL' if notification_failed else result.get('status','SUCCEEDED'),stage='COMPLETE',finished_at=now())
                if self.api._window is not None and not getattr(self.api,'_system_session_ending',False):
                    try:self.api._window.evaluate_js("window.dispatchEvent(new CustomEvent('desktop:research-published'))")
                    except Exception:pass
        except Exception as exc:
            code=str(exc) if re.fullmatch(r'[A-Z0-9_: -]{1,180}',str(exc)) else 'RESEARCH_EXECUTION_FAILED'
            previous=read(self._run_path(run_id)/'state.json')
            stopped_status='CANCELLED' if self.stop.is_set() else 'FAILED'
            if params['kind']=='report' and self.stop.is_set() and self.closed:
                stopped_status='INTERRUPTED';code='PROCESS_INTERRUPTED'
            self._state(run_id,status=stopped_status,failed_stage=previous.get('stage'),stage='STOPPED',finished_at=now(),error_code=code)
            if not self.stop.is_set() and params['config']['push_enabled']:
                try:self._failure_message(run_id,previous.get('stage'))
                except Exception:self._state(run_id,notification_error='RESEARCH_ERROR_MESSAGE_PENDING')
            self._notify_ui()

    def _notify_ui(self):
        if self.api._window is not None and not getattr(self.api,'_system_session_ending',False):
            try:self.api._window.evaluate_js("window.dispatchEvent(new CustomEvent('desktop:research-published'))")
            except Exception:pass

    def task_list(self):
        clock=datetime.now(timezone.utc);rows=[]
        for row in self.state()['runs']:
            end=row.get('finished_at')
            history=bool(end and (clock-datetime.fromisoformat(end.replace('Z','+00:00'))).total_seconds()>=900)
            rows.append(dict(row,history=history,workflow_kind='RESEARCH_DISCOVERY' if row['kind']=='discovery' else 'RESEARCH_REPORT'))
        return {'rows':rows,'archive_after_seconds':900,'automatic_history_is_not_deletion':True}

    def repair_delivery(self,run_id):
        # Result publication retry, not model/network retry. No new run or result hash.
        with self.lock:
            path=self._run_path(run_id);state=read(path/'state.json')
            if state['status'] in {'RUNNING','QUEUED'}:raise ValueError('RESEARCH_STILL_RUNNING')
            result=read(path/'result.json');params=read(path/'request.json')
            if sealed(result)['sha256']!=result.get('sha256'):raise ValueError('RESEARCH_RESULT_HASH_MISMATCH')
            if not (path/'result.md').exists():(path/'result.md').write_text(result['markdown'],encoding='utf8')
            self._publish_library(run_id,params['kind'],result)
            suppress=params['kind']=='discovery' and (not params['config']['push_enabled'] or not result.get('recommendations'))
            if not suppress and not state.get('message_id'):
                mid=self._message(run_id,params['kind'],result,params['period'])
                self._state(run_id,message_id=mid,notification_status='MESSAGE_CREATED')
            repaired=self._state(run_id,status=result.get('status','SUCCEEDED'),stage='COMPLETE',
                finished_at=state.get('finished_at') or now(),notification_error=None,delivery_repaired_at=now())
            self._notify_ui();return repaired

    def _discovery(self,run_id,params):
        from .discovery import run
        return run(self,run_id,params)

    def _report(self,run_id,period,window=None,cutoff=None):
        from .digest import build_digest
        from research_reports.renderer import render_markdown
        from research_reports.policy import ZONE
        from .report_identity import previous_window
        current=datetime.fromisoformat(cutoff) if cutoff else self.report_clock() if self.report_clock else datetime.now(ZONE)
        window=window or previous_window(period,current)
        start=datetime.fromisoformat(window['start']);end=datetime.fromisoformat(window['end'])
        events=[];seen=set();members=[]
        jobs=self.api._service.call('list_jobs',{})['jobs']
        for job in jobs:
            if self.stop.is_set():raise ValueError('RESEARCH_CANCELLED')
            payload=self.api._service.call('get_job_events',{'job_id':job['job_id']})
            rows=payload.get('events',[]) if isinstance(payload,dict) else payload
            members.append({'source_system':'Desktop_APPLICATION_EVENTS','source_ref':'memorive://job/'+job['job_id'],'sha256':sha(rows).upper()})
            for row in rows:
                stamp=row.get('recorded_at') or row.get('occurred_at') or row.get('timestamp') or row.get('created_at')
                if not stamp:continue
                key=sha(row);eid='RE-'+key[:32].upper()
                if eid in seen:continue
                seen.add(eid);state=row.get('control_state') or row.get('to_state') or row.get('state') or row.get('event_type') or '记录'
                category='失败' if any(x in str(state) for x in ('FAIL','ERROR')) else '待审' if 'WAITING' in str(state) else '新增' if 'CREAT' in str(state) or state=='QUEUED' else '修改'
                events.append({'event_id':eid,'event_kind':str(row.get('event_type','DESKTOP_JOB_EVENT')),'occurred_at':stamp,'observed_at':stamp,'source_system':'Desktop_APPLICATION_EVENTS','source_record_ref':'event:'+str(row.get('event_id',key)),'source_content_hash':key.upper(),'subject_refs':['memorive://job/'+job['job_id']],'evidence_refs':[key.upper()],'status_axis':'control_state','to_state':str(state),'verification_status':'not_applicable','display_text':str(job.get('display_name') or job.get('title') or job['job_id'])+' · '+str(state),'daily_category':category,'weekly_categories':['风险与待办'] if category in {'失败','待审'} else ['研究进展'],'monthly_categories':['风险与未评估维度'],'explicit_tags':[]})
        work=self.api.call('work_log.list_entries',{'time_range':'all','page':1,'page_size':500})
        work_rows=list(work.get('rows',[]))
        for page in range(2,(work.get('total_count',len(work_rows))+499)//500+1):
            if self.stop.is_set():raise ValueError('RESEARCH_CANCELLED')
            work_rows.extend(self.api.call('work_log.list_entries',{'time_range':'all','page':page,'page_size':500})['rows'])
        work['rows']=work_rows
        members.append({'source_system':'Desktop_WORK_LOG_PROJECTION','source_ref':'desktop-work-log','sha256':sha(work).upper()})
        for row in work.get('records',work.get('rows',[])):
            if row.get('job_id') in {j['job_id'] for j in jobs}:continue
            key=sha(row);stamp=row['event_time_utc'];category='失败' if row.get('severity')=='RED' else '修改'
            events.append({'event_id':'RE-'+key[:32].upper(),'event_kind':'DESKTOP_ACTIVITY','occurred_at':stamp,'observed_at':stamp,'source_system':'Desktop_WORK_LOG_PROJECTION','source_record_ref':row['log_entry_id'],'source_content_hash':key.upper(),'subject_refs':[row['source_locator']],'evidence_refs':[key.upper()],'status_axis':'control_state','to_state':row.get('public_payload',{}).get('control_state','RECORDED'),'verification_status':'not_applicable','display_text':row['title']+' · '+row['summary'],'daily_category':category,'weekly_categories':['风险与待办'] if category=='失败' else ['研究进展'],'monthly_categories':['风险与未评估维度'],'explicit_tags':[]})
        snapshot={'events':events,'members':members,'manifest_sha256':sha({'members':members,'events':events}).upper(),'issues':[],'conflicts':[]}
        write(self._run_path(run_id)/'source_snapshot.json',sealed(snapshot))
        self._state(run_id,stage='COMPOSING')
        digest=build_digest(snapshot,digest_kind=period,window_start=start.isoformat(),window_end=end.isoformat(),cutoff_at=current.isoformat())
        markdown=render_markdown(digest)
        return {'schema_version':'DesktopPeriodicReport-v1','period':period,'window_start':start.isoformat(),'window_end':end.isoformat(),'source_event_count':len(events),'digest':digest,'markdown':markdown,'source_scope':['CURRENT_DESKTOP_APPLICATION_EVENTS','CURRENT_DESKTOP_WORK_LOG'],'status':'SUCCEEDED','engine':'Desktop RESEARCH_REPORTS runtime successor + unchanged RESEARCH_REPORTS templates','paid_model_calls':0}

    def _message(self,run_id,kind,result,period):
        from memorive_messages.projection import MessageProjectionEngine
        from memorive_messages.contracts import message_dedupe_key,message_id_for_dedupe
        store=self.api._messages.store
        summary = (f"{'部分来源未完成；已' if result.get('status')=='PARTIAL' else '检索完成，'}保存 {len(result.get('recommendations',[]))} 条文献候选。"
                   if kind == 'discovery' else "周期报告已生成并保存。")
        body = summary + "\n请前往资料库查看正文。\n结果校验值：" + result['sha256']
        with self.api._messages._runtime_event_lock:
            title=read(self._run_path(run_id)/'state.json')['title']
            if kind=='discovery':title='外部文献·'+title
            event={'sequence':int(store.load(recover_corruption=False)['state']['projection']['cursor'])+1,'event_id':'event-'+run_id,'event_type':'REPORT_READY' if kind=='report' else 'USER_INFORMATION','job_id':run_id,'run_id':run_id,'attempt_id':run_id,'node_id':None,'root_cause':'RESEARCH_RESULT','occurred_at':now(),'severity':'BLUE','message_required':True,'safe_title':title,'safe_summary':body[:320],'safe_body':body[:3900],'target_locator':'memorive://artifact/'+run_id+'/result','protected_bulk_read':True,'attachment_refs':[],'status_axes':{'lifecycle_status':'completed','verification_result':'NOT_ASSESSED','acceptance_verdict':'NOT_ASSESSED','capability_status':'AVAILABLE'},'report_period':period}
            MessageProjectionEngine(store,synthetic_only=False).ingest([event]);return message_id_for_dedupe(message_dedupe_key(event))

    def _failure_message(self,run_id,stage):
        from memorive_messages.projection import MessageProjectionEngine
        from .task_projection import STEPS
        state=read(self._run_path(run_id)/'state.json');place=dict(STEPS).get(stage,'文献检索')
        problem='结果已生成，但文件、索引或消息投递未完成；请修复投递，无需重新检索。' if stage=='PUBLISHING' else '来源未返回可用数据；请检查来源配置、凭据或网络后重新检索。'
        if state['kind']=='report':
            place='周期报告投递' if stage=='PUBLISHING' else '周期报告生成'
            problem='报告已生成，但文件、索引或消息投递未完成；无需重复处理文献或对话。' if stage=='PUBLISHING' else '无法从已完成的文献或精炼成品生成报告，请检查相关成果记录。'
        store=self.api._messages.store
        with self.api._messages._runtime_event_lock:
            event={'sequence':int(store.load(recover_corruption=False)['state']['projection']['cursor'])+1,
                'event_id':'failure-'+run_id,'event_type':'USER_INFORMATION','job_id':run_id,'run_id':run_id,
                'attempt_id':run_id,'node_id':None,'root_cause':'RESEARCH_FAILURE','occurred_at':state['finished_at'],
                'severity':'RED','message_required':True,'safe_title':('外部文献·' if state['kind']=='discovery' else '')+state['title'],
                'safe_summary':'出错位置：'+place+'。'+problem,'safe_body':'出错位置：'+place+'。'+problem,
                'target_locator':'memorive://job/'+run_id,'protected_bulk_read':True,'attachment_refs':[],
                'status_axes':{'lifecycle_status':'failed','verification_result':'NOT_ASSESSED','acceptance_verdict':'NOT_ASSESSED','capability_status':'AVAILABLE'}}
            MessageProjectionEngine(store,synthetic_only=False).ingest([event])

    def close(self):
        self.scheduler_stop.set()
        if self.scheduler and self.scheduler.is_alive():self.scheduler.join(timeout=5)
        with self.lock:self.closed=True;self.stop.set();thread=self.thread
        pending_report=bool(self.current and (self._run_path(self.current)/'model_request.json').exists())
        if thread and thread.is_alive():thread.join(timeout=2 if pending_report else 35)
        if thread and thread.is_alive():
            if pending_report:
                # The provider may still finish outside our process. Persist an
                # interruption before the owner closes the IPC service; the
                # durable request claim prevents an automatic paid resend.
                item=read(self._run_path(self.current)/'state.json')
                self._state(self.current,status='INTERRUPTED',failed_stage=item.get('stage'),
                    error_code='PROCESS_INTERRUPTED',finished_at=now())
                return {'status':'STOPPING_REPORT','request_outcome':'PENDING_REVIEW'}
            raise RuntimeError('RESEARCH_EXECUTION_STILL_QUIESCING')
        return {'status':'QUIESCED'}

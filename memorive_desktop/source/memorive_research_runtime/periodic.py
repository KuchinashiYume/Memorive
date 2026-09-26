"""Passive completed-window reports, triggered by material outputs, not log noise."""
from datetime import datetime, timedelta
from research_reports.policy import ZONE, original_window_ref, validate_window
from research_reports.renderer import render_markdown
from .common import sha, sealed, write, read
from .digest import build_digest


def material_progress(api):
    completed = {r['job_id'] for r in api._core_library_job_rows() if r.get('control_state') == 'SUCCEEDED'}
    papers = [dict(r,material_kind='PAPER') for r in api._library.core_activity_rows() if r['task_id'] in completed]
    conversations = [dict(r,material_kind='REFINEMENT') for r in api._library.refinement_activity_rows()]
    # Review clicks, reading, reports and discovery metadata do not count as a new
    # processed paper or newly refined conversation.
    return papers + conversations


def windows(progress, current):
    current = current.astimezone(ZONE)
    result = {}
    for item in progress:
        stamp = datetime.fromisoformat(item['updated_at'].replace('Z','+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('REPORT_MATERIAL_TIMESTAMP_UNZONED')
        for period in ('daily','weekly','monthly'):
            reference = original_window_ref(period, stamp)
            start,end = reference.split(':',1)[1].split('..')
            if datetime.fromisoformat(end) > current:
                continue
            window = {'period':period,'start':start,'end':end}
            result.setdefault(reference, {'window':window,'progress':[]})['progress'].append(item)
    # No invented lookback cap: only windows with real, retained material exist.
    return [result[key] for key in sorted(result,key=lambda key:(result[key]['window']['end'],key))]


def tick(runtime, current=None):
    import os
    if os.environ.get('MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION') == 'DISABLED':
        return {'status':'PAUSED_BY_CONSOLE'}
    current = current or (runtime.report_clock() if runtime.report_clock else datetime.now(ZONE))
    with runtime.lock:
        if runtime.closed:return {'status':'CLOSED'}
        if getattr(runtime,'scheduler_stop',None) and runtime.scheduler_stop.is_set():return {'status':'CLOSED'}
        if runtime._has_active_run():return {'status':'BUSY'}
        from .report_schedule import get,due_at
        from .report_identity import project,window_key
        schedule=get(runtime.root)['config']
        from .report_models import config,catalog
        mapping=config(runtime.root)['config']
        available={r['profile_ref'] for r in catalog(runtime) if r['eligible']}
        produced={window_key(row['report_window']) for row in project(runtime.root)
                  if row.get('report_window') and (runtime._run_path(row['run_id'])/'result.json').exists()}
        progress = material_progress(runtime.api)
        for candidate in windows(progress,current):
            window = candidate['window']
            if mapping[window['period']] not in available:continue
            if current.astimezone(ZONE)<due_at(window,schedule) or window_key(window) in produced:continue
            base_id = 'periodic-v1-' + sha(window)
            attempt=0
            while True:
                request_id=base_id if not attempt else base_id+'-retry-'+str(attempt)
                run_id='research-'+sha(request_id)[:24]
                path=runtime._run_path(run_id)
                if (path/'result.json').exists():
                    # Rendering succeeded, even if publication later failed.
                    # Delivery repair reuses this result; never render it twice.
                    read(path/'result.json')
                    break
                if not (path/'state.json').exists():
                    return runtime.start(kind='report', request_id=request_id, period=window['period'],
                        trigger='PASSIVE_MATERIAL_PROGRESS',report_window=window,
                        material_progress=candidate['progress'], report_cutoff=current.isoformat())
                prior=read(path/'state.json')
                if (path/'model_request.json').exists():break
                if prior['status'] not in {'FAILED','INTERRUPTED'}:break
                # This path is the local deterministic material renderer, not
                # a provider/model call. Preserve failed attempts, mint a new
                # identity, and hold runtime.lock across selection and claim.
                attempt+=1
        return {'status':'NO_NEW_COMPLETED_WINDOW'}


def render(runtime, run_id, params):
    window=params['report_window'];period=window['period'];progress=params['material_progress']
    validate_window(period,window['start'],window['end'],params['report_cutoff'])
    if not progress:raise ValueError('REPORT_MATERIAL_PROGRESS_REQUIRED')
    events=[]
    for item in progress:
        key=sha(item);stamp=item['updated_at'];paper=item['material_kind']=='PAPER'
        text=item['display_name'] + (' · 文献处理完成，成果待人工复核' if paper else ' · 对话精炼完成，成果待人工复核')
        events.append({'event_id':'RE-'+key[:32].upper(),'event_kind':'MATERIAL_OUTPUT_READY',
            'occurred_at':stamp,'observed_at':stamp,'source_system':'Desktop_MATERIAL_OUTPUT',
            'source_record_ref':item['stable_locator'],'source_content_hash':key.upper(),
            'subject_refs':[item['stable_locator']],'evidence_refs':[key.upper()],
            'status_axis':'artifact_registry','to_state':'RESULT_AVAILABLE','verification_status':'not_applicable',
            'display_text':text,'daily_category':'新增','weekly_categories':['研究进展'],
            'monthly_categories':['知识库结构变化'],'explicit_tags':[]})
    members=[{'source_system':'Desktop_MATERIAL_OUTPUT','source_ref':r['stable_locator'],'sha256':sha(r).upper()} for r in progress]
    snapshot={'events':events,'members':members,'manifest_sha256':sha({'members':members,'events':events}).upper(),'issues':[],'conflicts':[]}
    write(runtime._run_path(run_id)/'source_snapshot.json',sealed(snapshot))
    runtime._state(run_id,stage='COMPOSING')
    digest=build_digest(snapshot,digest_kind=period,window_start=window['start'],window_end=window['end'],cutoff_at=params['report_cutoff'])
    return {'schema_version':'DesktopPeriodicReport-v1','period':period,'window_start':window['start'],
        'window_end':window['end'],'source_event_count':len(events),'material_progress_count':len(progress),
        'digest':digest,'markdown':render_markdown(digest),'source_scope':['COMPLETED_PAPERS','REFINED_CONVERSATIONS'],
        'status':'SUCCEEDED','engine':'Desktop RESEARCH_REPORTS material-progress successor','paid_model_calls':0}

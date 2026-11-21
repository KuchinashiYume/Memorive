"""Targeted sample regression; no model or live provider calls."""
from pathlib import Path
import json
import re
from urllib.parse import unquote
from types import SimpleNamespace
from threading import RLock
from copy import deepcopy
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]



def _scheduler(root,config,requests):
    from memorive.research.runtime import ResearchRuntime
    instance=ResearchRuntime.__new__(ResearchRuntime)
    instance.root=root;instance.lock=RLock();instance.closed=False;instance.current=None
    def call(method,params):
        return {'config':deepcopy(config)} if method=='settings.research_get' else {'literature':{'selected':['arxiv']}}
    instance.host=SimpleNamespace(service=SimpleNamespace(call=call))
    def start(**params):requests.append(params);return {'run_id':'test-only-'+str(len(requests))}
    instance.start=start
    return instance


def test_disabling_receiving_does_not_rewrite_schedule(tmp_path,monkeypatch):
    from memorive.research.common import defaults
    monkeypatch.delenv('MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION',raising=False)
    config=dict(defaults(),passive_enabled=True,topic='fixture',interval_hours=168)
    runtime=_scheduler(tmp_path,config,[]);runtime.passive_tick(100)
    snapshot=(tmp_path/'passive_schedule.json').read_bytes();config['passive_enabled']=False
    assert runtime.passive_tick(1000000)['status']=='DISABLED'
    assert (tmp_path/'passive_schedule.json').read_bytes()==snapshot

def test_report_windows_only_come_from_material_progress(tmp_path):
    from memorive.research.reports.periodic import windows,material_progress
    current=datetime(2026,9,8,tzinfo=timezone.utc)
    assert windows([],current)==[]
    library=SimpleNamespace(core_activity_rows=lambda:[{'task_id':'ok','updated_at':'2026-09-01T10:00:00Z'},
        {'task_id':'failed','updated_at':'2026-09-01T10:00:00Z'}],refinement_activity_rows=lambda:[])
    host=SimpleNamespace(library=library,library_job_rows=lambda:[{'job_id':'ok','control_state':'SUCCEEDED'},
        {'job_id':'failed','control_state':'FAILED'}],profile_path=lambda area:tmp_path,library_failed_job_rows=lambda:[])
    progress=material_progress(host)
    assert len(progress)==1 and progress[0]['task_id']=='ok'
    assert {r['window']['period'] for r in windows(progress,current)}=={'daily','weekly'}


def test_a_failed_processing_job_is_reported_under_failures(tmp_path):
    # The report used to list completed papers, refined conversations and research opportunities only, so its
    # table read 失败 0 and its summary said there were no failures on a day with three. A day with only
    # failures has a report without a model summary (there is no library change to describe).
    from memorive.research.reports.periodic import material_progress,windows,render
    from memorive.research.reports.models import compose
    library=SimpleNamespace(core_activity_rows=lambda:[],refinement_activity_rows=lambda:[])
    host=SimpleNamespace(library=library,library_job_rows=lambda:[],profile_path=lambda area:tmp_path,
        library_failed_job_rows=lambda:[{'job_id':'job-1','control_state':'FAILED','display_name':'Zeiler2014.pdf',
                                         'updated_at':'2026-09-01T10:00:00Z'}])
    [daily]=[w for w in windows(material_progress(host),datetime(2026,9,8,tzinfo=timezone.utc)) if w['window']['period']=='daily']
    (tmp_path/'run-1').mkdir()
    states=[]
    runtime=SimpleNamespace(run_path=lambda run_id:tmp_path/run_id,update_state=lambda run_id,**fields:states.append(fields))
    result=render(runtime,'run-1',{'report_window':daily['window'],'material_progress':daily['progress'],
                                   'report_cutoff':'2026-09-08T00:00:00+00:00'})
    sections={s['title']:s['items'] for s in result['digest']['sections']}
    assert [item['text'] for item in sections['失败']]==['《Zeiler2014.pdf》文献处理失败，没有产出成果']
    assert sections['新增']==[] and '需要处理' in result['markdown']
    composed=compose(runtime,'run-1',{'report_model':{'profile_kind':'API','model_name':'m'},'period':'daily'},result)
    assert (composed['narrative_status'],composed['model_calls'],composed['paid_model_calls'])==('NOT_REQUESTED_NO_LIBRARY_CHANGE',0,0)


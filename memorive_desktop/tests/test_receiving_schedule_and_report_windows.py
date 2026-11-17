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

def test_report_windows_only_come_from_material_progress():
    from memorive.research.reports.periodic import windows,material_progress
    current=datetime(2026,9,8,tzinfo=timezone.utc)
    assert windows([],current)==[]
    library=SimpleNamespace(core_activity_rows=lambda:[{'task_id':'ok','updated_at':'2026-09-01T10:00:00Z'},
        {'task_id':'failed','updated_at':'2026-09-01T10:00:00Z'}],refinement_activity_rows=lambda:[])
    host=SimpleNamespace(library=library,library_job_rows=lambda:[{'job_id':'ok','control_state':'SUCCEEDED'},
        {'job_id':'failed','control_state':'FAILED'}])
    progress=material_progress(host)
    assert len(progress)==1 and progress[0]['task_id']=='ok'
    assert {r['window']['period'] for r in windows(progress,current)}=={'daily','weekly'}


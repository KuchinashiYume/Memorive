"""Real IPC/worker tests. All model responses here are controlled fixtures."""
import asyncio
from copy import deepcopy
import threading
import time
import pytest

from memorive_settings.exam_jobs import WorkflowExamJobs, observed_call
from memorive_settings.store import SettingsStore
from memorive_settings.service_adapter import SettingsServiceAdapter
from memorive_desktop_service.service import ApplicationServiceHost
from memorive_desktop_service.client import ApplicationServiceClient
from test_cli_settings_usability import _controller


def wait_done(jobs, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        row = jobs.status(job_id=job_id)
        if row['state'] in {'COMPLETE','ERROR','CANCELLED','INTERRUPTED'}:
            return row
        time.sleep(.01)
    pytest.fail('worker did not finish')


@pytest.mark.parametrize('kind',['structured_chat','embedding','ocr','rerank'])
def test_cancel_preserves_current_return_and_stops_next_call(tmp_path,kind):
    store = SettingsStore(tmp_path); snapshot=store.load(); started=threading.Event(); release=threading.Event(); physical=[]
    def transport():
        physical.append(1);started.set();assert release.wait(10);return {'preserved':'answer'}
    def execute(**_):
        observed_call(transport,kind)()
        observed_call(transport,kind)()
        return {'status':'PASS','score':100}
    jobs=WorkflowExamJobs(store,execute)
    plan=dict(settings_sha256=snapshot['settings_sha256'],maximum_model_calls=3)
    row=jobs.start(params={'node_id':'analysis'},plan=plan,idempotency_key='fixture-cancel-0001')
    assert started.wait(5)
    assert jobs.status(job_id=row['job_id'])['calls_started']==1
    jobs.cancel(job_id=row['job_id']);release.set()
    result=wait_done(jobs,row['job_id'])
    assert result['state']=='CANCELLED' and result['calls_returned']==1 and len(physical)==1


def test_duplicate_start_and_different_start_cannot_double_call(tmp_path):
    store=SettingsStore(tmp_path);snapshot=store.load();gate=threading.Event();begun=threading.Event();sent=[]
    def execute(**_):
        sent.append(1);begun.set();assert gate.wait(10);return {'status':'PASS','score':87}
    jobs=WorkflowExamJobs(store,execute);plan=dict(settings_sha256=snapshot['settings_sha256'])
    kw=dict(params={'node_id':'analysis'},plan=plan,idempotency_key='fixture-start-0001')
    first=jobs.start(**kw);assert begun.wait(5)
    assert jobs.start(**kw)['job_id']==first['job_id']
    with pytest.raises(ValueError,match='ALREADY_RUNNING'):
        WorkflowExamJobs(store,execute).start(**{**kw,'idempotency_key':'fixture-start-0002'})
    gate.set();assert wait_done(jobs,first['job_id'])['receipt']['score']==87
    assert jobs.start(**kw)['state']=='COMPLETE' and len(sent)==1


def test_lost_worker_is_not_model_failure_or_automatic_rerun(tmp_path):
    store=SettingsStore(tmp_path);store.load();sent=[]
    jobs=WorkflowExamJobs(store,lambda **_:sent.append(1));row=dict(job_id='a'*32,node_id='analysis',state='RUNNING',started_at='2026-09-05T00:00:00Z')
    jobs._write(row)
    result=WorkflowExamJobs(store,lambda **_:sent.append(1)).status(job_id=row['job_id'])
    assert result['state']=='INTERRUPTED' and result['reason'].endswith('NOT_ASSESSED') and not sent


@pytest.mark.parametrize('channel',['API','CLI','LOCAL'])
def test_real_ipc_short_requests_survive_slow_exam_and_reconnect(tmp_path,channel):
    controller=_controller(tmp_path);seen=[];entered=threading.Event()
    def execute(**_):
        def slow_model():
            seen.append(channel);entered.set();time.sleep(5.3);return {}
        observed_call(slow_model,'embedding' if channel=='LOCAL' else 'structured_chat')()
        return {'status':'PASS','score':87,'persistent_mutation':False}
    controller.exam_jobs.execute=execute
    def plan(**_):
        snapshot=controller.store.load()
        return dict(status='READY',plan_sha256='a'*64,maximum_model_calls=2,settings_sha256=snapshot['settings_sha256'])
    controller.test_workflow_node=plan
    async def scenario():
        secret=b'isolated-fixture-secret-for-ipc-092'
        host=ApplicationServiceHost(SettingsServiceAdapter(controller),secret=secret)
        await host.start()
        client=ApplicationServiceClient('127.0.0.1',host.bound_port,secret=secret)
        try:
            await client.connect();start=time.monotonic()
            row=await client.call('settings.start_workflow_exam',dict(node_id='analysis',idempotency_key='fixture-ipc-'+channel+'-0001',expected_plan_sha256='a'*64))
            assert time.monotonic()-start < 3 and row['state']=='RUNNING'
            await client.close()
            client=ApplicationServiceClient('127.0.0.1',host.bound_port,secret=secret);await client.connect()
            states=[]
            while True:
                result=await client.call('settings.workflow_exam_status',{'job_id':row['job_id']});states.append(result)
                if result['state']=='COMPLETE':break
                assert time.monotonic()-start<15
                await asyncio.sleep(.2)
            assert time.monotonic()-start>5 and any(x['calls_started']==1 and x['calls_returned']==0 for x in states)
            assert result['receipt']['score']==87 and result['calls_returned']==1 and seen==[channel]
        finally:
            await client.close();await host.close()
    asyncio.run(scenario())

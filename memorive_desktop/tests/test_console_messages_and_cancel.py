"""sample supervised failures; synthetic state and loopback only, no providers."""
from pathlib import Path
import concurrent.futures
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get('MEMORIVE_TESTS_TARGET', Path(__file__).resolve().parents[1]))
for folder in (ROOT / 'source', ROOT / 'product/desktop'):
    sys.path.insert(0, str(folder))
from runtime import ProductApi
from memorive_research_runtime.common import defaults
from memorive_test_console_bridge.product_adapter import ProductConsoleAdapter


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION', 'DISABLED')
    monkeypatch.setenv('MEMORIVE_TEST_CONSOLE_SESSION_ID', 'review-sample-regression')
    instance = ProductApi(tmp_path / 'localappdata/state', ROOT / 'source', ROOT / 'source/facade_method_binding.json',
        session_source_roots={'codex_home': tmp_path / 'empty-codex', 'claude_home': tmp_path / 'empty-claude'})
    yield instance
    instance.close()


def test_message_frame_and_error_motion_are_explicit():
    html = (ROOT / 'product/desktop/bundle_assistant.html').read_text(encoding='utf8')
    assert "MESSAGE: 'interaction'" in html
    assert 'assistant_ack_delivery' in html
    assert 'error-shake' in html


def test_delivery_is_retained_until_ack(tmp_path):
    from memorive_desktop_assistant.delivery import AssistantDeliveryQueue
    queue = AssistantDeliveryQueue(tmp_path / 'delivery.json')
    presentation = {'animation_state': 'ERROR', 'reason_code': 'MESSAGE_NEEDS_ATTENTION', 'locator': 'memorive://job/test'}
    queue.enqueue('message-1', presentation)
    assert queue.peek()['delivery_id'] == 'message-1'
    restarted = AssistantDeliveryQueue(tmp_path / 'delivery.json')
    assert restarted.peek()['animation_state'] == 'ERROR'
    restarted.ack('message-1')
    restarted.enqueue('message-1', presentation)
    assert restarted.peek() is None


def test_three_replaceable_source_slots(api):
    initial = api.call('settings.external_sources_get', {})['literature']
    assert len(initial['slots']) == 3
    for provider in ('arxiv', 'crossref', None):
        changed = api.call('settings.external_sources_save', {'source': {'slot_id': 'literature-1', 'provider': provider}, 'expected_revision': initial['revision']})
        initial = api.call('settings.external_sources_get', {})['literature']
        assert initial['slots'][0]['provider'] == provider
    with pytest.raises(Exception):
        api.call('settings.external_sources_save', {'source': {'slot_id': 'literature-1', 'provider': 'openalex'}, 'expected_revision': initial['revision']})


def test_passive_and_push_are_independent_preferences():
    config = defaults()
    assert config['passive_enabled'] is False
    assert config['push_enabled'] is True


def test_real_console_cancel_during_snapshot_keeps_bridge_alive(api):
    adapter = ProductConsoleAdapter(api, SimpleNamespace(data_root=api._state_dir.parent.parent, session_id='review-sample-regression'), threading.Event())
    jobs = [adapter.execute('event.inject', {'event': 'stall', 'note': 'synthetic cancellation'}, 'event-' + str(i), False)['job_id'] for i in range(6)]
    def snapshots():
        return [adapter.snapshot() for _ in range(12)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        readers = [pool.submit(snapshots) for _ in range(2)]
        for index, job in enumerate(jobs):
            result = adapter.execute('task.cancel', {'job_id': job}, 'cancel-' + str(index), False)
            assert result['action_receipt']['state'] == 'CANCELLED'
            assert api._service.call('get_job', {'job_id': job})['control_state'] == 'CANCELLED'
        for reader in readers:
            assert reader.result()
    assert api._service.call('service.health', {})['protocol_version'] == '1.0'
    with pytest.raises(Exception):
        api.call('inbox.set_auto_run', {'enabled': True})


@pytest.mark.parametrize('dispatch_completed',[False,True])
def test_cancel_lost_reply_reconciles_same_logical_key(api, monkeypatch, dispatch_completed):
    from memorive_desktop_service.errors import ConnectionLost
    adapter = ProductConsoleAdapter(api, SimpleNamespace(data_root=api._state_dir.parent.parent, session_id='review-sample-regression'), threading.Event())
    job = adapter.execute('event.inject',{'event':'stall','note':'loopback failure injection'},'cancel-disconnect-event',False)['job_id']
    original = api._service.call
    lost = False
    def dropping(method, params=None):
        nonlocal lost
        if method == 'console.control_task' and not lost:
            lost = True
            if dispatch_completed:original(method,params)
            raise ConnectionLost('INJECTED_RESPONSE_LOSS')
        return original(method,params)
    monkeypatch.setattr(api._service,'call',dropping)
    result = adapter.execute('task.cancel',{'job_id':job},'same-cancel-key',False)
    assert result['action_receipt']['state']=='CANCELLED'
    assert original('get_job',{'job_id':job})['state_history'].count('CANCELLED') == 1
    assert adapter.execute('task.cancel',{'job_id':job},'same-cancel-key',False)==result


def test_cancel_unavailable_never_reports_success(api, monkeypatch):
    from memorive_desktop_service.errors import ConnectionLost
    adapter = ProductConsoleAdapter(api, SimpleNamespace(data_root=api._state_dir.parent.parent, session_id='review-sample-regression'), threading.Event())
    job = adapter.execute('event.inject',{'event':'stall','note':'synthetic'},'unavailable-event',False)['job_id']
    original=api._service.call
    def unavailable(method,params=None):
        if method=='console.control_task':raise ConnectionLost('INJECTED_UNAVAILABLE')
        return original(method,params)
    monkeypatch.setattr(api._service,'call',unavailable)
    with pytest.raises(ConnectionLost):
        adapter.execute('task.cancel',{'job_id':job},'unavailable-cancel',False)
    assert original('get_job',{'job_id':job})['control_state']=='RUNNING'


def test_actual_message_poll_holds_until_render_ack(api):
    preferences=api._desk_assistant.load_preferences()
    api._write_json_file(api._desk_assistant.preferences_path,dict(preferences,reminder_scope='INCLUDE_ORDINARY'))
    adapter=ProductConsoleAdapter(api, SimpleNamespace(data_root=api._state_dir.parent.parent,session_id='review-sample-regression'), threading.Event())
    for kind,animation in [('info','MESSAGE'),('error','ERROR')]:
        message=adapter.execute('message.test',{'kind':kind,'title':'Synthetic reminder','body':'No remote calls'},'msg-'+kind,False)
        first=api.assistant_bootstrap({})
        assert first['presentation']['animation_state']==animation
        delivery=first['presentation']['delivery_id']
        assert delivery=='message:'+message['message_id']
        for _ in range(3):
            assert api.assistant_bootstrap({})['presentation']['delivery_id']==delivery
        api.assistant_ack_delivery({'delivery_id':delivery})
        assert api.assistant_bootstrap({})['presentation']['animation_state']=='STATIC'


def test_unacknowledged_message_claim_can_recover_after_presenter_restart(api):
    params={'locator':'memorive://job/recovery','reason_code':'MESSAGE_NEEDS_ATTENTION','animation_state':'ERROR','delivery_id':'message:recovery','reduced_motion':False}
    api._desk_assistant.deliveries.enqueue(params['delivery_id'],params)
    assert api.claim_assistant_presentation_delivery(params)['status']=='CLAIMED'
    assert api.claim_assistant_presentation_delivery(params)['status']=='ALREADY_CONSUMED'
    api._desk_assistant.delivery_session='new-presenter-session'
    assert api.claim_assistant_presentation_delivery(params)['status']=='CLAIMED'



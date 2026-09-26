from pathlib import Path
import json
import os
import subprocess
import sys
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest

from bridge_client import BridgeClient
from common import MESSAGE_KINDS, EVENT_KINDS, delete_session, read_json, validate_command
from manager import Manager, build_info
from server import make_server


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / 'console'
    root.mkdir()
    for name in ['reference_bridge.py', 'common.py']:
        shutil.copy2(Path(__file__).parent / name, root / name)
    manager = Manager(root)
    yield manager
    manager.close()


def command(operation, params, request_id=None, allow=False):
    return {'operation': operation, 'params': params, 'request_id': request_id or 'test-' + uuid.uuid4().hex, 'allow_model_calls': allow}


def complete(manager, value):
    record = manager.submit(value)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        row = next(r for r in manager.snapshot()['commands'] if r['command_id'] == record['command_id'])
        if row['state'] in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
            assert row['state'] == 'SUCCEEDED', row
            return row
        time.sleep(.05)
    pytest.fail('Command did not settle')


def test_no_connection_never_implies_memorive(manager):
    assert manager.snapshot()['state'] == 'WAITING_FOR_BUILD'
    assert manager.snapshot()['connected_to_memorive'] is False
    with pytest.raises(ValueError, match='BUILD_NOT_CONNECTED'):
        manager.submit(command('diagnostics', {}))


def test_current_uninstrumented_executable_does_not_launch(manager, tmp_path):
    fake = tmp_path / 'Memorive.exe'
    fake.write_bytes(b'fixture-not-executable')
    (tmp_path / 'release_identity_binding.json').write_text(json.dumps({
        'schema_version': 'DesktopReleaseIdentityBinding-v1',
        'release_version': '1.01', 'display_version': 'v1.01',
        'channel': 'TEST_ONLY', 'main_executable': 'Memorive.exe',
        'release_authorized': False, 'acceptance_verdict': 'NOT_ASSESSED',
    }), encoding='utf-8')
    assert build_info(fake)['bridge_declared'] is False
    with pytest.raises(ValueError, match='BUILD_BRIDGE_NOT_IMPLEMENTED'):
        manager.start('build', fake)
    assert manager.owner is None
    assert not list((manager.root / 'sessions').iterdir())


def test_reference_full_protocol_and_exact_cleanup(manager, tmp_path):
    unrelated = tmp_path / 'user-data.txt'
    unrelated.write_text('preserve user data', encoding='utf-8')
    initial = manager.start('reference')
    assert initial['state'] == 'REFERENCE_ONLY'
    assert initial['connected_to_memorive'] is False
    session = initial['session']
    for kind in MESSAGE_KINDS:
        complete(manager, command('message.test', {'kind': kind, 'title': kind, 'body': 'Temporary test body'}))
    for kind in EVENT_KINDS:
        complete(manager, command('event.inject', {'event': kind, 'note': 'temporary event'}))
    for period in ['daily', 'weekly', 'monthly']:
        complete(manager, command('report.run', {'period': period}))
    assert len(list((Path(session['data_root']) / 'reference_reports').glob('*.md'))) == 3
    receipt = manager.end()
    assert receipt['status'] == 'CLEANED'
    assert receipt['remaining_entries'] == 0
    assert receipt['native_quiesce_ack'] is True
    assert receipt['executable_unchanged'] is True
    assert not Path(session['data_root']).exists()
    assert unrelated.read_text(encoding='utf-8') == 'preserve user data'
    persisted = read_json(manager.root / 'monitor' / 'latest_snapshot.json')
    assert persisted['native'] == {} and persisted['commands'] == [] and persisted['events'] == []
    assert 'Temporary test body' not in json.dumps(persisted)
    assert 'Temporary test body' not in json.dumps(receipt)


def test_idempotency_and_permission_cannot_drift(manager):
    manager.start('reference')
    value = command('message.test', {'kind': 'info', 'title': 'one', 'body': 'one'}, 'same-request')
    first = complete(manager, value)
    replay = manager.submit(value)
    assert replay['command_id'] == first['command_id'] and replay['replayed'] is True
    with pytest.raises(ValueError, match='IDEMPOTENCY_CONFLICT'):
        manager.submit({**value, 'allow_model_calls': True})
    assert len(manager.client.snapshot()['messages']) == 1


@pytest.mark.parametrize('operation,params', [('discovery.recommend', {'topic': 'test', 'count': 2}), ('inbox.dispatch', {'item_id': 'item-1'}), ('task.resume', {'job_id': 'job-1'}), ('task.retry', {'job_id': 'job-1'})])
def test_model_permission_is_per_command(operation, params):
    with pytest.raises(ValueError, match='MANUAL_MODEL_PERMISSION_REQUIRED'):
        validate_command(command(operation, params))
    assert validate_command(command(operation, params, allow=True))


@pytest.mark.parametrize('bad', ['../outside', 'review-20260906T160000-abcd', '', 'C:/Users'])
def test_cleanup_rejects_unowned_paths(manager, bad):
    with pytest.raises(ValueError):
        delete_session(manager.root, bad)


def test_cleanup_rejects_altered_ownership(manager):
    session = manager.start('reference')['session']
    root = Path(session['data_root'])
    marker = root / '.console-session.json'
    original = marker.read_bytes()
    marker.write_text('{"session_id":"wrong","owner":"MEMORIVE_INDEPENDENT_CONSOLE"}', encoding='utf-8')
    try:
        with pytest.raises(ValueError, match='MARKER_MISMATCH'):
            delete_session(manager.root, session['session_id'])
        assert root.exists()
    finally:
        marker.write_bytes(original)


def test_bridge_failure_still_removes_only_owned_payload(manager):
    session = manager.start('reference')['session']
    complete(manager, command('report.run', {'period': 'weekly'}))
    manager.owner.close()
    receipt = manager.end('BROKEN_BRIDGE')
    assert receipt['status'] == 'CLEANED' and receipt['native_quiesce_ack'] is False
    assert not Path(session['data_root']).exists()


@pytest.mark.parametrize('descriptor', [
    {'protocol_version': '2.0', 'host': '127.0.0.1', 'port': 1, 'session_id': 'test'},
    {'protocol_version': '1.0', 'host': 'example.com', 'port': 1, 'session_id': 'test'},
    {'protocol_version': '1.0', 'host': '127.0.0.1', 'port': True, 'session_id': 'test'},
    {'protocol_version': '1.0', 'host': '127.0.0.1', 'port': 1, 'session_id': 'wrong'},
])
def test_descriptor_rejects_version_external_host_and_wrong_session(descriptor):
    with pytest.raises(ValueError):
        BridgeClient(descriptor, 'not-a-real-token', 'test', '0' * 64, 'MEMORIVE_BUILD')


def test_http_rejects_cross_origin_and_unmarked_reads(manager):
    server = make_server(manager, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f'http://127.0.0.1:{server.server_port}/api/snapshot'
    try:
        for headers in [{}, {'X-Memorive-Console': '1', 'Origin': 'https://example.com'}, {'X-Memorive-Console': '1', 'Sec-Fetch-Site': 'cross-site'}]:
            with pytest.raises(urllib.error.HTTPError) as result:
                opener.open(urllib.request.Request(url, headers=headers))
            assert result.value.code == 403
        with opener.open(urllib.request.Request(url, headers={'X-Memorive-Console': '1'})) as response:
            assert json.load(response)['connected_to_memorive'] is False
    finally:
        server.shutdown()
        server.server_close()


def test_second_session_never_reuses_first_data(manager):
    first = manager.start('reference')['session']
    complete(manager, command('message.test', {'kind': 'info', 'title': 'first-session', 'body': 'discard-me'}))
    manager.end()
    second = manager.start('reference')['session']
    assert first['session_id'] != second['session_id']
    assert manager.client.snapshot()['messages'] == []
    assert not Path(first['data_root']).exists()


def test_reference_replies_validate_against_published_schema(manager):
    import jsonschema
    schema = read_json(Path(__file__).parent / 'bridge.schema.json')
    jsonschema.Draft202012Validator.check_schema(schema)
    manager.start('reference')
    client = manager.client
    def validate(definition, value):
        jsonschema.Draft202012Validator({**schema, '$ref': '#/$defs/' + definition}).validate(value)
    validate('sessionReady', client.handshake(Path(manager.session['data_root'])))
    validate('snapshot', client.snapshot())
    request = command('report.run', {'period': 'weekly'})
    validate('command', {**request, 'session_id': manager.session['session_id']})
    validate('commandReceipt', client.command(request))
    validate('eventBatch', client.events(0))
    validate('sessionQuiesced', client.end())


def test_guard_loss_terminates_test_session(manager):
    session = manager.start('reference')['session']
    original = manager.client.snapshot
    manager.client.snapshot = lambda: {**original(), 'automatic_execution': True}
    deadline = time.monotonic() + 5
    while manager.session and time.monotonic() < deadline:
        time.sleep(.05)
    assert manager.session is None
    assert not Path(session['data_root']).exists()
    assert manager.snapshot()['error_code'] == 'AUTOMATIC_EXECUTION_GUARD_LOST'


def test_handshake_rejects_numeric_guard_values(manager):
    manager.start('reference')
    client = manager.client
    result = client.handshake(Path(manager.session['data_root']))
    request = client.request
    client.request = lambda *a, **k: {**result, 'automatic_execution_locked': 1}
    try:
        with pytest.raises(ValueError, match='SAFETY_HANDSHAKE_REJECTED'):
            client.handshake(Path(manager.session['data_root']))
    finally:
        client.request = request


def test_abrupt_console_exit_watchdog_removes_test_reports(tmp_path):
    root = tmp_path / 'crash-console'
    root.mkdir()
    source = Path(__file__).parent
    for name in ['reference_bridge.py', 'common.py']:
        shutil.copy2(source / name, root / name)
    output = (tmp_path / 'host.log').open('wb')
    host = subprocess.Popen([sys.executable, str(source / 'main.py'), '--headless', '--port', '0', '--state-root', str(root)],
        stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        endpoint = root / 'console_endpoint.json'
        deadline = time.monotonic() + 8
        while not endpoint.exists() and time.monotonic() < deadline:
            assert host.poll() is None
            time.sleep(.1)
        url = read_json(endpoint)['url']
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def post(path, value):
            with opener.open(urllib.request.Request(url + '/api/' + path, data=json.dumps(value).encode(), headers={'Content-Type': 'application/json', 'X-Memorive-Console': '1'}), timeout=10) as response:
                return json.load(response)
        session = post('session/start', {'mode': 'reference'})['session']
        post('commands', command('report.run', {'period': 'weekly'}))
        deadline = time.monotonic() + 5
        while not list((Path(session['data_root']) / 'reference_reports').glob('*.md')) and time.monotonic() < deadline:
            time.sleep(.1)
        assert list((Path(session['data_root']) / 'reference_reports').glob('*.md'))
        host.kill()
        host.wait(timeout=5)
        deadline = time.monotonic() + 10
        receipt = root / 'receipts' / (session['session_id'] + '.json')
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert read_json(receipt)['status'] == 'CLEANED'
        assert not Path(session['data_root']).exists()
        assert read_json(root / 'monitor' / 'latest_snapshot.json')['business_content_retained'] is False
    finally:
        if host.poll() is None:
            host.kill()
            host.wait(timeout=5)
        output.close()


def test_short_ui_cache_is_owned_and_deleted(tmp_path):
    from common import create_ui_profile, delete_ui_profile
    from process_owner import process_identity
    root = tmp_path / 'control'
    root.mkdir()
    profile_id, path = create_ui_profile(root, os.getpid(), process_identity(os.getpid()))
    assert len(str(path / 'webview')) < 96
    (path / 'example_ui_cache.txt').write_text('transient UI data')
    receipt = delete_ui_profile(root, profile_id)
    assert receipt['status'] == 'CLEANED'
    assert not path.exists()

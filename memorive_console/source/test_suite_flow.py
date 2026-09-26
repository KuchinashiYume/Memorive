from pathlib import Path
import json
import shutil
import time

import pytest

from common import OPERATIONS, build_suite_catalog, build_suite_plan, read_json
from manager import Manager


def manager_fixture(tmp_path):
    root = tmp_path / 'console'
    root.mkdir()
    for name in ['reference_bridge.py', 'common.py']:
        shutil.copy2(Path(__file__).parent / name, root / name)
    return Manager(root)


def wait_for_suite(manager, suite_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        suite = next(row for row in manager.snapshot()['suites'] if row['suite_id'] == suite_id)
        if suite['state'] in {'COMPLETED', 'CANCELLED'}:
            return suite
        time.sleep(.05)
    pytest.fail('Suite did not settle')


def test_full_safe_suite_is_complete_and_model_free():
    catalog = {row['preset_id']: row for row in build_suite_catalog(list(OPERATIONS), connected=True)}
    assert list(catalog) == ['full_safe', 'smoke', 'messages', 'workflow', 'documents', 'diagnostic_scenarios']
    assert catalog['full_safe']['case_count'] == 29
    assert catalog['full_safe']['available_case_count'] == 29
    assert catalog['full_safe']['status'] == 'READY'
    plan = build_suite_plan('full_safe')
    assert len(plan) == 29
    assert len({row['case_id'] for row in plan}) == 29
    assert all(row['allow_model_calls'] is False for row in plan)
    assert all(OPERATIONS[row['operation']]['model'] is False for row in plan)
    assert {'message.star', 'message.confirm', 'task.pause', 'task.cancel', 'inbox.import'} <= {row['operation'] for row in plan}
    assert catalog['diagnostic_scenarios']['case_count'] == 6


def test_full_safe_suite_runs_sequentially_and_writes_codex_review_bundle(tmp_path):
    manager = manager_fixture(tmp_path)
    try:
        manager.start('reference')
        started = manager.start_suite({'preset_id': 'full_safe'})
        suite = wait_for_suite(manager, started['suite_id'])
        assert suite['state'] == 'COMPLETED'
        assert suite['verdict'] == 'PASS'
        assert suite['summary'] == {'total': 29, 'passed': 29, 'failed': 0, 'skipped': 0}
        assert suite['progress']['completed'] == 29
        assert suite['model_calls_allowed'] is False
        assert suite['automatic_execution'] is False
        assert manager.client.snapshot()['model_calls_started'] == 0
        bundle = suite['review_bundle']
        json_path, markdown_path = Path(bundle['json_path']), Path(bundle['markdown_path'])
        assert json_path.is_file() and markdown_path.is_file()
        receipt = read_json(json_path)
        assert receipt['schema_version'] == 'Memorive-ConsoleCodexReview-v1'
        assert receipt['verdict'] == 'PASS'
        assert receipt['summary']['passed'] == 29
        assert receipt['automatic_execution'] is False
        assert receipt['model_calls_allowed'] is False
        assert receipt['business_content_recorded'] is False
        serialized = json.dumps(receipt, ensure_ascii=False)
        markdown = markdown_path.read_text(encoding='utf-8')
        for forbidden in ['此消息仅属于当前临时审核会话', '接口参考报告', '由控制台内置测试包注入']:
            assert forbidden not in serialized
            assert forbidden not in markdown
        assert read_json(manager.root / 'receipts' / 'suites' / 'latest.json')['suite_id'] == suite['suite_id']
    finally:
        manager.close()


def test_suite_fixture_and_business_outputs_are_deleted_but_review_survives(tmp_path):
    manager = manager_fixture(tmp_path)
    try:
        session = manager.start('reference')['session']
        suite = wait_for_suite(manager, manager.start_suite({'preset_id': 'workflow'})['suite_id'])
        review_path = Path(suite['review_bundle']['json_path'])
        data_root = Path(session['data_root'])
        assert list(data_root.rglob('console-suite-import.txt'))
        receipt = manager.end('SUITE_CLEANUP_TEST')
        assert receipt['status'] == 'CLEANED' and receipt['remaining_entries'] == 0
        assert not data_root.exists()
        assert review_path.is_file()
        assert read_json(review_path)['business_content_recorded'] is False
    finally:
        manager.close()


def test_missing_build_capability_is_reported_instead_of_silently_dropped(monkeypatch, tmp_path):
    import manager as manager_module
    original = manager_module.BridgeClient.handshake

    def partial(client, data_root):
        value = original(client, data_root)
        value['operations'] = ['diagnostics']
        return value

    monkeypatch.setattr(manager_module.BridgeClient, 'handshake', partial)
    manager = manager_fixture(tmp_path)
    try:
        manager.start('reference')
        suite = wait_for_suite(manager, manager.start_suite({'preset_id': 'smoke'})['suite_id'])
        assert suite['verdict'] == 'FAIL'
        assert suite['summary'] == {'total': 6, 'passed': 1, 'failed': 0, 'skipped': 5}
        assert {row['state'] for row in suite['cases']} == {'PASSED', 'SKIPPED'}
        assert {row.get('error_code') for row in suite['cases'] if row['state'] == 'SKIPPED'} == {'OPERATION_NOT_SUPPORTED_BY_BUILD'}
    finally:
        manager.close()


def test_manual_commands_are_blocked_while_suite_owns_the_queue(tmp_path):
    manager = manager_fixture(tmp_path)
    try:
        manager.start('reference')
        original = manager._wait_suite_command

        def delayed(*args, **kwargs):
            time.sleep(.2)
            return original(*args, **kwargs)

        manager._wait_suite_command = delayed
        started = manager.start_suite({'preset_id': 'smoke'})
        with pytest.raises(ValueError, match='TEST_SUITE_RUNNING'):
            manager.submit({'request_id': 'manual-during-suite', 'operation': 'diagnostics', 'params': {}, 'allow_model_calls': False})
        manager.cancel_suite({'suite_id': started['suite_id']})
        assert wait_for_suite(manager, started['suite_id'])['state'] == 'CANCELLED'
    finally:
        manager.close()


def test_release_discovery_only_uses_exact_copackaged_locations(monkeypatch, tmp_path):
    import manager as manager_module

    root = tmp_path / 'release-bundle'
    root.mkdir()
    executable = root / 'Memorive.exe'
    executable.write_bytes(b'copackaged')
    (root / 'release_identity_binding.json').write_text(json.dumps({
        'schema_version': 'DesktopReleaseIdentityBinding-v1',
        'release_version': '1.02', 'display_version': 'v1.02',
        'channel': 'PUBLIC', 'main_executable': 'Memorive.exe',
        'release_authorized': True, 'acceptance_verdict': 'PASS',
    }), encoding='utf-8')
    (root / 'memorive-console-capabilities.json').write_text(json.dumps({
        'schema_version': 'Memorive-ConsoleBuildCapabilities-v1',
        'protocol_version': '1.0', 'launch_transport': 'ENV_V1',
        'session_mode': 'EPHEMERAL_ONLY',
    }), encoding='utf-8')
    unrelated = root / 'unrelated' / 'Memorive.exe'
    unrelated.parent.mkdir()
    unrelated.write_bytes(b'must not be scanned')
    monkeypatch.setattr(manager_module.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(manager_module.sys, 'executable', str(root / 'Memorive-Test-Console.exe'))
    rows = manager_module.discover_builds()
    assert [row['label'] for row in rows] == ['v1.02']
    assert rows[0]['path'] == str(executable.resolve())

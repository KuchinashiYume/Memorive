from pathlib import Path
import shutil

from manager import Manager


def manager_fixture(tmp_path):
    root = tmp_path / 'console'
    root.mkdir()
    for name in ['reference_bridge.py', 'common.py']:
        shutil.copy2(Path(__file__).parent / name, root / name)
    return Manager(root)


def test_new_build_handshake_automatically_attaches_builtin_test_pack(tmp_path):
    manager = manager_fixture(tmp_path)
    try:
        snapshot = manager.start('reference')
        attachment = snapshot['test_attachment']
        assert attachment['status'] == 'ATTACHED'
        assert attachment['automatic_attachment'] is True
        assert attachment['automatic_execution'] is False
        assert attachment['model_calls_allowed_in_batch'] is False
        assert attachment['safe_batch_case_count'] == 25
        assert attachment['safe_batch_operations'] == ['message.test', 'event.inject', 'report.run', 'diagnostics',
            'research.qa.simulate']
        assert attachment['one_click_case_count'] == 29
        assert attachment['one_click_operations'] == ['message.test', 'event.inject', 'report.run', 'inbox.import',
            'task.pause', 'task.cancel', 'message.confirm', 'message.star', 'diagnostics']
        assert attachment['manual_operations'] == ['discovery.recommend', 'inbox.dispatch', 'task.resume', 'task.retry',
            'expression.trigger', 'expression.cancel', 'research.qa.simulate']
        assert attachment['missing_operations'] == []
        assert attachment['optional_missing_operations'] == []
        assert manager.snapshot()['test_attachment'] == attachment
    finally:
        manager.close()


def test_partial_build_attaches_only_advertised_tests_without_executing(monkeypatch, tmp_path):
    import manager as manager_module
    original = manager_module.BridgeClient.handshake
    def partial(client, data_root):
        value = original(client, data_root)
        value['operations'] = ['diagnostics', 'message.test', 'discovery.recommend']
        return value
    monkeypatch.setattr(manager_module.BridgeClient, 'handshake', partial)
    manager = manager_fixture(tmp_path)
    try:
        snapshot = manager.start('reference')
        attachment = snapshot['test_attachment']
        assert attachment['status'] == 'PARTIAL'
        assert attachment['safe_batch_case_count'] == 15
        assert attachment['safe_batch_operations'] == ['message.test', 'diagnostics']
        assert attachment['manual_operations'] == ['discovery.recommend']
        assert 'report.run' in attachment['missing_operations']
        assert snapshot['commands'] == []
        assert snapshot['native']['model_calls_started'] == 0
    finally:
        manager.close()


def test_test_attachment_is_removed_with_session(tmp_path):
    manager = manager_fixture(tmp_path)
    try:
        manager.start('reference')
        manager.end()
        snapshot = manager.snapshot()
        assert snapshot['test_attachment']['status'] == 'WAITING_FOR_BUILD'
        assert snapshot['test_attachment']['safe_batch_case_count'] == 0
        assert snapshot['test_attachment']['one_click_case_count'] == 0
        assert snapshot['commands'] == []
    finally:
        manager.close()

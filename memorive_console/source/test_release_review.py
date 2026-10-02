import json
from pathlib import Path

import pytest

from common import OPERATIONS
from manager import Manager
from release_review import review_document, closed_review, markdown


def data(reference=False):
    return {'session': {'session_id': 'review-unit-001', 'mode': 'reference' if reference else 'build',
        'build': {'release_version': '1.02', 'path': 'SECRET_LOCAL_PATH', 'label': 'SECRET_TITLE'},
        'build_sha256': 'a' * 64, 'token': 'SECRET_TOKEN'},
        'state': 'REFERENCE_ONLY' if reference else 'CONNECTED', 'business_operations_enabled': True,
        'supported_operations': list(OPERATIONS), 'native': {'body': 'SECRET_BODY'},
        'suites': [{'session_id': 'review-unit-001', 'suite_id': 'suite-unit', 'preset_id': 'full_safe',
            'state': 'COMPLETED', 'verdict': 'PASS', 'cases': [{'case_id': 'message-info',
                'operation': 'message.test', 'state': 'PASSED', 'result': {'body': 'SECRET_RESULT'}}]}]}


def test_reference_cannot_imply_product_acceptance():
    report = review_document(data(True), {})
    assert report['evidence_scope'] == 'REFERENCE_PROTOCOL'
    assert report['automated_suites'][0]['evidence_scope'] == 'REFERENCE_PROTOCOL'
    assert not report['manual_editable']
    assert report['release_verdict'] == 'NOT_ASSESSED'
    assert all(row['status'] == 'NOT_RUN' for row in report['manual_checks'])


def test_export_is_allowlisted_and_manual_checks_do_not_change_automatic_verdict():
    report = review_document(data(), {'research': 'FAIL'})
    assert report['manual_checks'][2]['status'] == 'FAIL'
    assert report['automated_suites'][0]['verdict'] == 'PASS'
    assert report['release_verdict'] == 'NOT_ASSESSED'
    assert 'SECRET_' not in json.dumps(report) + markdown(report)


def test_gated_and_disconnected_capabilities_are_not_runnable():
    source = data()
    source['state'] = 'WAITING_FOR_ACCESS'
    report = review_document(source, {})
    assert report['safe_suite'] == {'total': 29, 'declared': 29, 'runnable': 0, 'model_calls_allowed': False}
    assert not report['manual_editable']
    source['state'] = 'DISCONNECTED'
    assert not review_document(source, {})['manual_editable']


def test_cleanup_does_not_relabel_evidence_or_lose_failures():
    original = review_document(data(), {'updates': 'FAIL'})
    result = closed_review(original, {'status': 'CLEANED', 'data_root': 'SECRET_PATH'}, 'CLEANED')
    assert result['cleanup_status'] == 'CLEANED'
    assert result['evidence_scope'] == 'APPLICATION_BRIDGE'
    assert result['manual_checks'][-1]['status'] == 'FAIL'
    assert not result['manual_editable']
    assert closed_review(original, None, 'CLEANUP_FAILED')['cleanup_status'] == 'CLEANUP_FAILED'
    assert original['cleanup_status'] == 'PENDING_SESSION_CLOSE'


def test_stale_session_and_reference_manual_updates_rejected(tmp_path):
    manager = Manager(tmp_path)
    try:
        manager.start('reference')
        sid = manager.session['session_id']
        with pytest.raises(ValueError, match='REVIEW_SESSION_NOT_EDITABLE'):
            manager.update_review_check({'session_id': sid, 'check_id': 'research', 'status': 'PASS'})
        manager.session['mode'] = 'build'  # unit fixture, no product is launched
        with pytest.raises(ValueError, match='REVIEW_SESSION_NOT_EDITABLE'):
            manager.update_review_check({'session_id': 'review-previous', 'check_id': 'research', 'status': 'PASS'})
        saved = manager.update_review_check({'session_id': sid, 'check_id': 'research', 'status': 'FAIL'})
        assert saved['manual_checks'][2]['status'] == 'FAIL'
        manager.session['mode'] = 'reference'
        manager.end()
        exported = manager.export_review({})
        assert exported['report']['cleanup_status'] == 'CLEANED'
        assert json.loads(Path(exported['json_path']).read_text('utf-8'))['manual_checks'][2]['status'] == 'FAIL'
        assert not any((tmp_path / 'sessions').iterdir())
        manager.start('reference')
        assert all(r['status'] == 'NOT_RUN' for r in manager.release_review()['manual_checks'])
        assert manager.session['session_id'] != sid
    finally:
        manager.close()


def test_failed_cleanup_is_written_to_review_before_retry(tmp_path, monkeypatch):
    import manager as module
    manager = Manager(tmp_path)
    original = module.delete_session
    try:
        manager.start('reference')
        sid = manager.session['session_id']
        def fail(*args):
            raise PermissionError('fixture busy')
        monkeypatch.setattr(module, 'delete_session', fail)
        with pytest.raises(RuntimeError, match='CLEANUP_FAILED'):
            manager.end()
        path = tmp_path / 'receipts' / 'reviews' / (sid + '.json')
        assert json.loads(path.read_text('utf-8'))['cleanup_status'] == 'CLEANUP_FAILED'
        monkeypatch.setattr(module, 'delete_session', original)
        manager.end()
        assert json.loads(path.read_text('utf-8'))['cleanup_status'] == 'CLEANED'
    finally:
        monkeypatch.setattr(module, 'delete_session', original)
        manager.close()

import json
from pathlib import Path
import re
import threading
import urllib.request

import pytest

from localization import RESOURCES, LANGUAGES, translate
from manager import Manager
from server import make_server


def test_all_catalogs_have_complete_keys_and_identical_placeholders():
    for language in LANGUAGES:
        assert RESOURCES[language].keys() == RESOURCES['zh-CN'].keys()
        for key, text in RESOURCES[language].items():
            assert text.strip(), (language, key)
            assert sorted(re.findall(r'\$\{\d+\}', key)) == sorted(re.findall(r'\$\{\d+\}', text)), (language, key)
    assert not any(re.search('[\u3400-\u9fff]', text) for text in RESOURCES['en-US'].values())


def test_preferences_persist_without_touching_session_or_model_permission(tmp_path):
    manager = Manager(tmp_path)
    try:
        manager.start('reference')
        sid, owner = manager.session['session_id'], manager.owner
        manager.set_preferences({'language': 'en-US'})
        assert manager.session['session_id'] == sid and manager.owner is owner
        assert manager.language == 'en-US'
        assert manager.snapshot()['automatic_execution'] is False
        with pytest.raises(ValueError):
            manager.set_preferences({'language': '../../secret'})
        with pytest.raises(ValueError):
            manager.set_preferences({'language': 'ja-JP', 'allow_model_calls': True})
        report = manager.export_review({})['report']
        assert report['report_language'] == 'en-US'
        assert report['manual_checks'][0]['title'] == 'Messages and pet'
        assert report['release_verdict'] == 'NOT_ASSESSED'
    finally:
        manager.close()
    again = Manager(tmp_path)
    try:
        assert again.language == 'en-US'
        assert not list((tmp_path / 'sessions').iterdir())
    finally:
        again.close()


def test_language_and_review_http_use_local_origin_boundary(tmp_path):
    manager = Manager(tmp_path)
    server = make_server(manager, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    root = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(root + '/i18n-data.js') as response:
            assert b'window.CONSOLE_I18N' in response.read()
        req = urllib.request.Request(root + '/api/preferences', data=b'{"language":"ja-JP"}',
            headers={'Content-Type': 'application/json', 'X-Memorive-Console': '1'})
        with urllib.request.urlopen(req) as response:
            assert json.load(response)['language'] == 'ja-JP'
        req.add_header('Origin', 'https://untrusted.invalid')
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        assert caught.value.code == 403
    finally:
        server.shutdown(); server.server_close(); manager.close()


def test_raw_codes_and_user_text_fall_back_unchanged():
    for language in LANGUAGES:
        assert translate('CONSOLE_LANGUAGE_INVALID', language) == 'CONSOLE_LANGUAGE_INVALID'
        assert translate('A user research answer, not a resource key', language) == 'A user research answer, not a resource key'

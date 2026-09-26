from pathlib import Path
import ctypes
import json
import os
import stat
import subprocess
import threading
import time

import pytest

from common import delete_session, read_json, write_json

SID = 'review-20260906T194800-abcdef123456'


def registered_session(tmp_path):
    root = tmp_path / 'console'
    session = root / 'sessions' / SID
    session.mkdir(parents=True)
    write_json(session / '.console-session.json', {'owner': 'MEMORIVE_INDEPENDENT_CONSOLE', 'session_id': SID})
    write_json(root / 'owned_sessions' / (SID + '.json'), {'session_id': SID, 'data_root': str(session.resolve()),
        'owner_pid': 99999999, 'owner_identity': 1, 'status': 'OWNED', 'disposition': 'DISCARD_ON_CLOSE'})
    return root, session


def junction(link, target):
    assert link.parent.is_dir() and target.is_dir()
    environment = {**os.environ, 'MEMORIVE_TEST_LINK': str(link), 'MEMORIVE_TEST_TARGET': str(target)}
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        'New-Item -ItemType Junction -Path $env:MEMORIVE_TEST_LINK -Target $env:MEMORIVE_TEST_TARGET -ErrorAction Stop | Out-Null'],
        env=environment, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def test_internal_windows_cache_junction_is_deleted_without_following(tmp_path):
    root, session = registered_session(tmp_path)
    cache = session / 'home' / 'AppData' / 'Local' / 'Microsoft' / 'Windows' / 'INetCache'
    target = cache / 'IE'
    target.mkdir(parents=True)
    (target / 'cache.bin').write_bytes(b'owned cache')
    junction(cache / 'Content.IE5', target)
    sentinel = tmp_path / 'daily-data.txt'
    sentinel.write_text('preserve')
    receipt = delete_session(root, SID)
    assert receipt['status'] == 'CLEANED'
    assert receipt['unlinked_internal_links'] == 1
    assert not session.exists()
    assert sentinel.read_text() == 'preserve'


def test_external_junction_is_rejected_before_any_deletion(tmp_path):
    root, session = registered_session(tmp_path)
    external = tmp_path / 'daily-data'
    external.mkdir()
    (external / 'important.txt').write_text('preserve external')
    (session / 'still-owned.txt').write_text('preserve until preflight passes')
    link = session / 'outside'
    junction(link, external)
    try:
        with pytest.raises(ValueError, match='EXTERNAL_LINK_CLEANUP_BLOCKED'):
            delete_session(root, SID)
        assert (session / '.console-session.json').exists()
        assert (session / 'still-owned.txt').exists()
        assert (external / 'important.txt').read_text() == 'preserve external'
        assert read_json(root / 'owned_sessions' / (SID + '.json'))['status'] == 'OWNED'
    finally:
        assert link.parent.resolve() == session.resolve()
        os.rmdir(link)  # Remove only this fixture's link, never its target.


def test_read_only_cache_file_does_not_block_close(tmp_path):
    root, session = registered_session(tmp_path)
    cache = session / 'read-only-cache.bin'
    cache.write_bytes(b'disposable cache')
    cache.chmod(stat.S_IREAD)
    assert delete_session(root, SID)['status'] == 'CLEANED'
    assert not session.exists()


def test_failed_delete_preserves_marker_and_allows_retry(tmp_path, monkeypatch):
    import common
    root, session = registered_session(tmp_path)
    blocked = session / 'locked.db'
    blocked.write_bytes(b'temporary state')
    real_unlink = os.unlink
    def locked(path, *args, **kwargs):
        if Path(path).name == 'locked.db':
            raise PermissionError(13, 'fixture file locked', str(path))
        return real_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'unlink', locked)
        patch.setattr(common.time, 'sleep', lambda *_: None)
        with pytest.raises(PermissionError):
            delete_session(root, SID)
    assert read_json(session / '.console-session.json')['session_id'] == SID
    assert read_json(root / 'owned_sessions' / (SID + '.json'))['status'] == 'OWNED'
    assert delete_session(root, SID)['status'] == 'CLEANED'
    assert not session.exists()


def test_dead_owner_recovery_cleans_internal_cache_links(tmp_path):
    from manager import Manager
    root, session = registered_session(tmp_path)
    target = session / 'IE'
    target.mkdir()
    junction(session / 'Content.IE5', target)
    manager = Manager(root)
    try:
        assert not session.exists()
        assert manager.last_cleanup['status'] == 'CLEANED'
        assert manager.snapshot()['state'] == 'WAITING_FOR_BUILD'
    finally:
        manager.close()


def test_root_junction_cannot_be_used_to_delete_another_tree(tmp_path):
    root, session = registered_session(tmp_path)
    # Replace only this empty fixture after saving its marker; the registered
    # absolute root must never be allowed to redirect cleanup elsewhere.
    (session / '.console-session.json').unlink()
    session.rmdir()
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'keep.txt').write_text('keep')
    junction(session, external)
    try:
        with pytest.raises(ValueError):
            delete_session(root, SID)
        assert (external / 'keep.txt').read_text() == 'keep'
    finally:
        assert session.parent.resolve() == (root / 'sessions').resolve()
        os.rmdir(session)


def test_close_is_async_and_multiple_close_events_run_cleanup_once():
    from window_lifecycle import WindowCloseController
    entered, release, destroyed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class Manager:
        def end(self, reason):
            calls.append(reason)
            entered.set()
            assert release.wait(3)
    class Window:
        def evaluate_js(self, script):
            pass
        def destroy(self):
            destroyed.set()
    controller = WindowCloseController(Manager(), Window())
    started = time.monotonic()
    assert controller.closing() is False
    assert time.monotonic() - started < .2
    assert entered.wait(2)
    assert controller.closing() is False
    assert not destroyed.is_set()
    release.set()
    assert destroyed.wait(3)
    assert controller.closing() is True
    assert calls == ['CONSOLE_WINDOW_CLOSE']


def test_close_failure_keeps_window_open_and_allows_retry():
    from window_lifecycle import WindowCloseController
    destroyed, failed = threading.Event(), threading.Event()
    calls = []
    class Manager:
        def end(self, reason):
            calls.append(reason)
            if len(calls) == 1:
                raise PermissionError(13, 'fixture locked', 'owned-cache.bin')
    class Window:
        def evaluate_js(self, script):
            if 'closingFailed' in script:
                failed.set()
        def destroy(self):
            destroyed.set()
    controller = WindowCloseController(Manager(), Window())
    assert controller.closing() is False
    assert failed.wait(2)
    assert not destroyed.is_set()
    assert controller.closing() is False
    assert destroyed.wait(2)
    assert len(calls) == 2

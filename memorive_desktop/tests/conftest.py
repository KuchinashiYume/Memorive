"""Test setup: import paths, no network access beyond this computer, known stale tests,
and a stop when the real data folders could be touched."""
import sys
import socket
from pathlib import Path

# Test helpers live next to the tests, not in product code. Product code is appended
# too, so a test file that does not set the path itself can run on its own (tests that
# insert paths first still win).
_ROOT = Path(__file__).resolve().parents[1]
for _folder in (Path(__file__).parent, _ROOT / 'source', _ROOT / 'product' / 'desktop'):
    if str(_folder) not in sys.path:
        sys.path.append(str(_folder))

original_connect = socket.socket.connect

def local_only(self, address):
    if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
        raise RuntimeError('TEST_EXTERNAL_NETWORK_FORBIDDEN')
    return original_connect(self, address)

socket.socket.connect = local_only


def _known_stale():
    import json
    import os
    path = Path(__file__).with_name("known_stale.json")
    stale = json.loads(path.read_text(encoding="utf8")) if path.is_file() else {}
    if os.environ.get("MEMORIVE_TESTS_RUN_STALE") == "1":
        # Re-check the stale list: run everything except scripts that break pytest itself.
        return {k: v for k, v in stale.items() if v.startswith("[dev script]")}
    return stale


_IGNORED_FILES = {}


def _stale_key(path):
    # "test_x.py" for files directly in tests/, "modules/<package>/test_x.py" below it.
    path = Path(str(path)).resolve()
    try:
        return path.relative_to(Path(__file__).resolve().parent).as_posix()
    except ValueError:
        return path.name


def pytest_ignore_collect(collection_path, config):
    # Entries "<file>::<collection>" are test files that cannot even be imported
    # (they need dev-only modules or files); they are listed at the end of the run.
    reason = _known_stale().get(_stale_key(collection_path) + "::<collection>")
    if reason:
        _IGNORED_FILES[_stale_key(collection_path)] = reason
        return True
    return None


def pytest_terminal_summary(terminalreporter):
    if _IGNORED_FILES:
        terminalreporter.write_line(f"known stale, not collected: {len(_IGNORED_FILES)} files")
        for name, reason in sorted(_IGNORED_FILES.items()):
            terminalreporter.write_line(f"  {name}: {reason}")


def pytest_collection_modifyitems(config, items):
    import pytest
    stale = _known_stale()
    for item in items:
        name = getattr(item, "originalname", None) or item.name
        reason = stale.get(_stale_key(item.fspath) + "::" + name)
        if reason:
            item.add_marker(pytest.mark.skip(reason="known stale: " + reason))


# These files build session fixtures dated 2026-08-27/28 and the product only
# lists sessions from the last 30 days, so they stopped passing once the dates
# aged out. Pin the sessions module's clock to a day inside that window.
SESSION_CLOCK_FILES = {
    'test_real_sessions_and_layout.py',
    'test_sessions_deepseek_bubbles_and_default_mode.py',
    'test_complete_sessions.py',
}

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _session_fixture_clock(request, monkeypatch):
    if Path(str(request.node.fspath)).name not in SESSION_CLOCK_FILES:
        return
    import datetime as real
    import memorive.sessions.controller as controller

    class FixtureDateTime(real.datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 5, tzinfo=real.timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(controller, 'datetime', FixtureDateTime)


# Tests must never touch the real data folders. run_tests.py, next to this file, points
# the variables below at a throw-away folder; a session without them, or with one inside a
# real data folder, stops before any test runs. Without this, a plain `pytest` run writes
# into the real runtime log.
_DATA_VARIABLES = (
    "MEMORIVE_RUNTIME_LOG_LOG_DIR",
    "MEMORIVE_OPS_ROOT",
    "MEMORIVE_VAULT_ROOT",
    "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_ROOT",
    "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT",
)
# The development machine's data folders, the product's defaults before 2.00. The defaults now lie
# under LOCALAPPDATA (memorive/platform/data_folders.py), which the check below requires to be a throw-away folder.
_REAL_DATA_ROOTS = (r"G:\Memorive", r"G:\Memorive-运维", r"G:\Memorive-沙盒", r"G:\Memorive-ops")


def _inside(path, root):
    import os

    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _real_windows_folders():
    """The user's real AppData folders, read from Windows itself (not from the environment)."""
    import ctypes
    import uuid
    from ctypes import wintypes

    folders = {}
    known = {"LOCALAPPDATA": "F1B32785-6FBA-4FCF-9D55-7B8E7F157091", "APPDATA": "3EB685DB-65F9-4CF6-A03A-E3EF65729F3D"}
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    for name, guid in known.items():
        raw = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        out = wintypes.LPWSTR()
        if shell32.SHGetKnownFolderPath(ctypes.byref(raw), 0, None, ctypes.byref(out)) == 0:
            folders[name] = out.value
            ole32.CoTaskMemFree(out)
    return folders


def pytest_sessionstart(session):
    import os
    import pytest

    problems = []
    for name in _DATA_VARIABLES:
        value = os.environ.get(name, "").strip()
        if not value:
            problems.append(f"{name} is not set")
        elif any(_inside(value, root) for root in _REAL_DATA_ROOTS):
            problems.append(f"{name} points into a real data folder ({value})")
    if os.name == "nt":
        for name, real in _real_windows_folders().items():
            value = os.environ.get(name, real)
            if os.path.normcase(os.path.abspath(value)) == os.path.normcase(os.path.abspath(real)):
                problems.append(f"{name} is the real {real}")
    if problems:
        pytest.exit("tests need sandbox data folders; run them with tests/run_tests.py. "
                    + "; ".join(problems), returncode=4)

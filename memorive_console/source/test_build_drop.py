from pathlib import Path
import json

import pytest

from build_drop import deliver_build_drop, dropped_build_path


def drop(*rows):
    return {'type': 'drop', 'dataTransfer': {'files': list(rows)}}


@pytest.mark.parametrize('event,code', [
    (None, 'DROP_FILE_REQUIRED'),
    (drop(), 'DROP_FILE_REQUIRED'),
    (drop({}, {}), 'DROP_ONE_BUILD_ONLY'),
    (drop({'name': 'Memorive.exe', 'path': 'C:\\fakepath\\Memorive.exe'}), 'DROP_NATIVE_PATH_REQUIRED'),
    (drop({'pywebviewFullPath': 'Memorive.exe'}), 'DROP_NATIVE_PATH_REQUIRED'),
    (drop({'pywebviewFullPath': 'C:\\bad\x00\\Memorive.exe'}), 'DROP_NATIVE_PATH_REQUIRED'),
])
def test_no_partial_or_guessed_locator(event, code):
    with pytest.raises(ValueError, match=code):
        dropped_build_path(event)


def test_shortcut_directory_and_removed_file_never_become_executables(tmp_path):
    shortcut = tmp_path / 'Memorive.lnk'
    shortcut.write_bytes(b'test only')
    with pytest.raises(ValueError, match='DROP_BUILD_EXE_REQUIRED'):
        dropped_build_path(drop({'pywebviewFullPath': str(shortcut)}))
    folder = tmp_path / 'Memorive.exe'
    folder.mkdir()
    with pytest.raises(ValueError, match='DROP_BUILD_EXE_REQUIRED'):
        dropped_build_path(drop({'pywebviewFullPath': str(folder)}))
    with pytest.raises(ValueError, match='DROP_FILE_UNAVAILABLE'):
        dropped_build_path(drop({'pywebviewFullPath': str(tmp_path / 'missing' / 'Memorive.exe')}))


def test_legacy_or_unrelated_executable_names_are_rejected(tmp_path):
    for name in ('Renamed.exe', 'Other.exe'):
        path = tmp_path / name
        path.write_bytes(b'test only')
        with pytest.raises(ValueError, match='DROP_BUILD_EXE_REQUIRED'):
            dropped_build_path(drop({'pywebviewFullPath': str(path)}))


def test_native_locator_round_trips_to_page_without_shell_or_js_interpolation(tmp_path):
    parent = tmp_path / "中文 build ' & $()"
    parent.mkdir()
    exe = parent / 'Memorive.exe'
    exe.write_bytes(b'fixture; never execute')
    class Window:
        def evaluate_js(self, script):
            self.script = script
    window = Window()
    event = drop({'name': exe.name, 'pywebviewFullPath': str(exe)})
    deliver_build_drop(window, event)
    prefix = 'void window.MemoriveBuildConsoleDrop.accept('
    assert window.script.startswith(prefix)
    assert json.loads(window.script[len(prefix):-1]) == {'path': str(exe.resolve())}
    assert exe.read_bytes() == b'fixture; never execute'

"""After Memorive imports a capture from the browser's "Memorive会话桥接" download folder and keeps its own
identical copy, the downloaded file is removed, so captures move out of Downloads instead of piling up there.
Files that failed to import, files outside the extension's own download folder and files Memorive has no
identical copy of stay where they are."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from browser_companion_support import companion  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _api(tmp_path: Path, status: str = "PASS"):
    api = companion()
    api.source_root = ROOT / "source"
    api.profile_root = tmp_path / "profile"
    api.system_effects_enabled = False
    imported: list[str] = []

    def import_session_file(path: Path) -> dict[str, object]:
        imported.append(path.name)
        return {"status": status, "imported_count": 1}

    api.sessions = SimpleNamespace(import_session_file=import_session_file)
    downloads = tmp_path / "Downloads"
    (downloads / "Memorive会话桥接").mkdir(parents=True)
    api._browser_session_download_roots = lambda: [downloads]
    return api, downloads, imported


def _capture(folder: Path, name: str = "memorive-session-library-universal-2026-11-22T07-19-41-946Z.json") -> Path:
    path = folder / name
    path.write_text(json.dumps({"generated_at": "2026-11-22T07:19:41.946Z", "conversations": []}), encoding="utf-8")
    return path


def test_an_imported_capture_moves_out_of_the_download_folder(tmp_path: Path) -> None:
    api, downloads, imported = _api(tmp_path)
    capture = _capture(downloads / "Memorive会话桥接")
    payload = capture.read_bytes()

    receipt = api.sync_browser_sessions_from_downloads()

    assert imported == [capture.name]
    assert receipt["imported_file_count"] == 1
    assert receipt["moved_download_count"] == 1
    assert not capture.exists()
    kept = Path(receipt["managed_directory"]) / capture.name
    assert kept.read_bytes() == payload

    again = api.sync_browser_sessions_from_downloads()
    assert again["imported_file_count"] == 0 and again["failure_count"] == 0


def test_a_capture_imported_earlier_also_moves_out(tmp_path: Path) -> None:
    api, downloads, _imported = _api(tmp_path)
    capture = _capture(downloads / "Memorive会话桥接")
    payload = capture.read_bytes()
    api.sync_browser_sessions_from_downloads()

    capture.write_bytes(payload)  # the same file downloaded again
    receipt = api.sync_browser_sessions_from_downloads()

    assert receipt["imported_file_count"] == 0
    assert receipt["moved_download_count"] == 1
    assert not capture.exists()


def test_a_capture_that_failed_to_import_stays(tmp_path: Path) -> None:
    api, downloads, _imported = _api(tmp_path, status="FAIL")
    capture = _capture(downloads / "Memorive会话桥接")

    receipt = api.sync_browser_sessions_from_downloads()

    assert receipt["failure_count"] == 1
    assert receipt["moved_download_count"] == 0
    assert capture.exists()


def test_a_capture_outside_the_extension_download_folder_stays(tmp_path: Path) -> None:
    api, downloads, imported = _api(tmp_path)
    capture = _capture(downloads)  # a folder the user granted, not the extension's own download folder

    receipt = api.sync_browser_sessions_from_downloads()

    assert imported == [capture.name]
    assert receipt["moved_download_count"] == 0
    assert capture.exists()

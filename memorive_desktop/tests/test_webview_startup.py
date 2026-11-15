from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
for candidate in (
    ROOT / "source",
    ROOT / "product" / "desktop",
    ROOT.parents[1] / "build_runtime_deps",
):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import app  # noqa: E402


def test_long_state_path_selects_stable_short_isolated_webview_profile(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MEMORIVE_WEBVIEW2_STORAGE_PATH", raising=False)
    long_state = Path("R:/") / ("long-startup-segment-" * 14) / "state"

    first = app.webview_storage_selection(long_state)
    second = app.webview_storage_selection(long_state)
    other = app.webview_storage_selection(long_state.parent / "other-state")

    assert first == second
    assert first["strategy"] == "short_temp_isolated"
    assert first["configured_override_present"] is False
    assert first["state_dir_unchanged"] is True
    assert first["requested_path_chars"] > app.WEBVIEW2_STORAGE_PATH_BUDGET
    assert first["storage_path_chars"] <= app.WEBVIEW2_STORAGE_PATH_BUDGET
    assert Path(first["storage_path"]).is_dir()
    assert first["profile_identity"] != other["profile_identity"]
    assert first["storage_path"] != other["storage_path"]


def test_long_explicit_webview_profile_fails_over_without_merging_profiles(
    monkeypatch,
) -> None:
    long_override = Path("R:/") / ("configured-long-segment-" * 14)
    state_a = Path("R:/profiles/a/state")
    state_b = Path("R:/profiles/b/state")
    monkeypatch.setenv("MEMORIVE_WEBVIEW2_STORAGE_PATH", str(long_override))

    selection_a = app.webview_storage_selection(state_a)
    selection_b = app.webview_storage_selection(state_b)

    assert selection_a["strategy"] == "short_temp_isolated"
    assert selection_a["configured_override_present"] is True
    assert selection_a["storage_path_chars"] <= app.WEBVIEW2_STORAGE_PATH_BUDGET
    assert selection_a["storage_path"] != selection_b["storage_path"]


def test_webview_diagnostics_capture_pywebview_initialization_errors(
    tmp_path: Path,
) -> None:
    selection = app.webview_storage_selection(tmp_path / "state")
    handler, log_path = app.install_webview_diagnostics(tmp_path, selection)
    try:
        logging.getLogger("pywebview").error(
            "synthetic WebView2 initialization failure"
        )
        handler.flush()
        text = log_path.read_text(encoding="utf-8")
        assert "webview_storage_selected" in text
        assert "synthetic WebView2 initialization failure" in text
        assert selection["profile_identity"] in text
    finally:
        app.remove_webview_diagnostics(handler)

    assert handler not in logging.getLogger("pywebview").handlers


def test_no_short_storage_root_fails_fast_instead_of_waiting_for_webview(
    monkeypatch,
) -> None:
    long_base = str(Path("R:/") / ("unusable-temp-segment-" * 12))
    monkeypatch.setattr(app.tempfile, "gettempdir", lambda: long_base)
    monkeypatch.setattr(app, "_windows_short_existing_path", lambda path: path)
    monkeypatch.setenv("TEMP", long_base)
    monkeypatch.setenv("TMP", long_base)

    with pytest.raises(
        RuntimeError,
        match="WEBVIEW2_STORAGE_PATH_EXCEEDS_SAFE_BUDGET",
    ):
        app.webview_storage_selection(
            Path("R:/") / ("long-state-segment-" * 12)
        )


def test_startup_source_does_not_hardcode_a_machine_username() -> None:
    source = (ROOT / "product" / "desktop" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "KuchinashiYume" not in source
    assert "KUCHIN~1" not in source
    assert "MEMORIVE_WEBVIEW2_STORAGE_PATH" in source
    assert "tempfile.gettempdir()" in source
    assert os.environ.get("USERNAME", "") not in source


def test_native_screen_capture_stages_through_short_temp_path() -> None:
    source = (ROOT / "product" / "desktop" / "app.py").read_text(
        encoding="utf-8"
    )
    capture_source = source.split("def capture_screen_rectangle", 1)[1].split(
        "def inspect_assistant_desktop_transparency", 1
    )[0]

    assert 'bitmap.Save(str(temporary), ImageFormat.Png)' in capture_source
    assert 'bitmap.Save(str(destination), ImageFormat.Png)' not in capture_source
    assert 'destination.open("xb")' in capture_source
    assert "temporary.unlink(missing_ok=True)" in capture_source


def test_transparency_probe_reads_long_evidence_paths_via_memory_stream() -> None:
    source = (ROOT / "product" / "desktop" / "app.py").read_text(
        encoding="utf-8"
    )
    probe_source = source.split(
        "def inspect_assistant_desktop_transparency", 1
    )[1].split("def bring_product_window_to_front", 1)[0]

    assert "Bitmap(str(" not in probe_source
    assert "MemoryStream(Array[Byte](path.read_bytes()))" in probe_source
    assert "visible_stream.Dispose()" in probe_source

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from memorive_test_console_bridge import (  # noqa: E402
    BASE_PATH,
    ConsoleBridge,
    ConsoleBridgeError,
    ConsoleLaunch,
    SUPPORTED_OPERATIONS,
)


SESSION_ID = "review-20260906T120000-abcdef123456"


def _launch_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    executable = tmp_path / "Memorive.exe"
    executable.write_bytes(b"artifact-location-console-test")
    for name in ("localappdata", "appdata", "temp"):
        (tmp_path / name).mkdir()
    (tmp_path / ".console-session.json").write_text(
        json.dumps(
            {"owner": "MEMORIVE_INDEPENDENT_CONSOLE", "session_id": SESSION_ID}
        ),
        encoding="utf-8",
    )
    values = {
        "MEMORIVE_TEST_CONSOLE_PROTOCOL": "1.0",
        "MEMORIVE_TEST_CONSOLE_SESSION_ID": SESSION_ID,
        "MEMORIVE_TEST_CONSOLE_DATA_ROOT": str(tmp_path),
        "MEMORIVE_TEST_CONSOLE_DESCRIPTOR_PATH": str(
            tmp_path / "bridge-descriptor.json"
        ),
        "MEMORIVE_TEST_CONSOLE_TOKEN": "1" * 64,
        "MEMORIVE_TEST_CONSOLE_BUILD_SHA256": hashlib.sha256(
            executable.read_bytes()
        ).hexdigest(),
        "MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION": "DISABLED",
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "APPDATA": str(tmp_path / "appdata"),
        "TEMP": str(tmp_path / "temp"),
        "TMP": str(tmp_path / "temp"),
    }
    return executable, values


def _request(
    descriptor: dict[str, object],
    *,
    token: str,
    method: str,
    route: str,
    body: dict[str, object] | None = None,
    origin: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "X-Memorive-Session": SESSION_ID,
    }
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(
        f"http://127.0.0.1:{descriptor['port']}{BASE_PATH}{route}",
        data=(
            None
            if body is None
            else json.dumps(body, separators=(",", ":")).encode("utf-8")
        ),
        method=method,
        headers=headers,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_console_launch_is_fail_closed_and_confined(tmp_path: Path) -> None:
    executable, values = _launch_environment(tmp_path)
    assert ConsoleLaunch.from_environment(executable, {}) is None
    with pytest.raises(ConsoleBridgeError, match="CONSOLE_ENVIRONMENT_INCOMPLETE"):
        ConsoleLaunch.from_environment(
            executable, {"MEMORIVE_TEST_CONSOLE_PROTOCOL": "1.0"}
        )

    launch = ConsoleLaunch.from_environment(executable, values)
    assert launch is not None
    verified = launch.verify_write_paths(
        {
            "profile": tmp_path / "localappdata",
            "cache": tmp_path / "temp" / "wv2",
        }
    )
    assert verified == {"profile": "localappdata", "cache": "temp/wv2"}

    outside = tmp_path.parent / "outside"
    with pytest.raises(ConsoleBridgeError, match="CONSOLE_PATH_OUTSIDE_DATA_ROOT"):
        launch.verify_write_paths({"escape": outside})


def test_console_webview_profile_stays_in_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = ROOT / "product" / "desktop"
    if str(desktop) not in sys.path:
        sys.path.insert(0, str(desktop))
    specification = importlib.util.spec_from_file_location(
        "desktop_app_console_test", desktop / "app.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MEMORIVE_TEST_CONSOLE_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("MEMORIVE_WEBVIEW2_STORAGE_PATH", raising=False)
    selection = module.webview_storage_selection(state)
    assert Path(selection["storage_path"]).resolve() == (
        tmp_path / "webview2"
    ).resolve()
    assert selection["strategy"] == "console_data_root"
    assert selection["console_data_root_verified"] is True
    assert selection["path_budget_enforced"] is False

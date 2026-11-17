from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (ATTEMPT_ROOT / "source", ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.api import ProductApi  # noqa: E402
from memorive.desktop.assistant.desktop import AssistantDesktop  # noqa: E402
from browser_companion_support import companion  # noqa: E402
from memorive.desktop.assistant.models import AssistantWindowPlacement  # noqa: E402
from memorive.desktop.assistant.placement import (  # noqa: E402
    WorkArea,
    clamp_placement,
    restore_default_placement,
    visible_bounds,
)


GEMINI_SYNC_BASE = "https://gemini.google.com/app?memorive_bridge_sync=v1#"
PREFLIGHT_URL = f"{GEMINI_SYNC_BASE}memorive-browser-session-preflight-2-v5"
FULL_URL = f"{GEMINI_SYNC_BASE}memorive-browser-session-full-v4"
INCREMENTAL_PREFIX = (
    f"{GEMINI_SYNC_BASE}memorive-browser-session-incremental-v2:"
)
CURRENT_EXTENSION_VERSION = json.loads(
    (
        ATTEMPT_ROOT
        / "source"
        / "memorive" / "sessions" / "companion"
        / "extension"
        / "manifest.json"
    ).read_text(encoding="utf-8")
)["version"]


class _Archive:
    def __init__(self, rows: dict[str, list[dict[str, str]]]) -> None:
        self.rows = rows

    def list_sessions(
        self, *, provider: str, limit: int, search: str = ""
    ) -> list[dict[str, str]]:
        assert limit == 50
        assert search == ""
        return self.rows.get(provider, [])[:limit]

    def get_messages(
        self, provider: str, provider_conversation_id: str
    ) -> list[dict[str, str]]:
        return [
            {"role": "user", "content": f"{provider_conversation_id} 问题😀"},
            {"role": "assistant", "content": f"{provider} 回答"},
        ]


class _SessionController:
    def __init__(self, archive: _Archive) -> None:
        self.session_archive = archive


def _launchable_api(rows: dict[str, list[dict[str, str]]]) -> ProductApi:
    api = companion()
    api.system_effects_enabled = True
    api.sessions = _SessionController(_Archive(rows))
    api.browser_companion_status = lambda: {
        "prepared": True,
        "extension_connected": True,
        "bridge_identity_verified": True,
        "preflight_eligible": True,
        "connected_browser": "EDGE",
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
    }
    api._browser_executables = lambda: [
        (
            "Microsoft Edge",
            Path("C:/Program Files/Edge/msedge.exe"),
            "edge://extensions/",
        )
    ]
    return api


def _row(provider: str, index: int) -> dict[str, str]:
    return {
        "provider_conversation_id": f"private-{provider}-{index}",
        "content_sha256": hashlib.sha256(
            f"content-{provider}-{index}".encode("utf-8")
        ).hexdigest(),
    }


def _decode_cursor(url: str) -> dict[str, object]:
    token = url.removeprefix(INCREMENTAL_PREFIX)
    padded = token + "=" * ((4 - len(token) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def test_first_sync_uses_collision_safe_v5_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[list[str]] = []

    class _Process:
        pid = 43001

    api = _launchable_api({"deepseek": [_row("deepseek", 1)]})
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command) or _Process(),
    )

    receipt = api.run_browser_companion_sync()

    assert launched == [["C:\\Program Files\\Edge\\msedge.exe", PREFLIGHT_URL]]
    assert receipt["sync_mode"] == "PREFLIGHT"
    assert receipt["per_provider_limit"] == 2


def test_three_provider_archive_switches_to_bounded_incremental_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[list[str]] = []

    class _Process:
        pid = 43002

    rows = {
        provider: [_row(provider, index) for index in range(1, 55)]
        for provider in ("deepseek", "gemini", "kimi")
    }
    api = _launchable_api(rows)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command) or _Process(),
    )

    receipt = api.run_browser_companion_sync()

    assert receipt["status"] == "INCREMENTAL_SYNC_TRIGGERED"
    assert receipt["sync_mode"] == "INCREMENTAL"
    assert receipt["per_provider_limit"] == 10
    assert receipt["known_session_count"] == 150
    assert receipt["overlap_fingerprint_count"] == 6
    assert len(launched) == 1 and launched[0][1].startswith(INCREMENTAL_PREFIX)
    assert all(f"private-{provider}" not in launched[0][1] for provider in rows)

    cursor = _decode_cursor(launched[0][1])
    assert cursor["schema_version"] == "MEMORIVE_BROWSER_SESSION_CURSOR_V1"
    assert set(cursor["providers"]) == {"deepseek", "gemini", "kimi"}
    for provider_id, provider_cursor in cursor["providers"].items():
        assert len(provider_cursor["known_session_hashes"]) == 50
        assert len(provider_cursor["overlap_fingerprints"]) == 2
        expected = hashlib.sha256(
            f"{provider_id}\x00private-{provider_id}-1".encode("utf-8")
        ).hexdigest()[:24]
        assert provider_cursor["known_session_hashes"][0] == expected


def test_cursor_helper_is_imported_after_embedded_source_becomes_available() -> None:
    runtime_path = ATTEMPT_ROOT / "source" / "memorive" / "sessions" / "companion" / "desktop.py"
    tree = ast.parse(runtime_path.read_text(encoding="utf-8"))

    module_imports = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert all(
        not (
            isinstance(node, ast.ImportFrom)
            and node.module == "memorive.sessions.companion.incremental_cursor"
        )
        for node in module_imports
    )

    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_browser_companion_incremental_cursor"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "memorive.sessions.companion.incremental_cursor"
        for node in ast.walk(method)
    )


def test_first_assistant_placement_is_persisted_without_restore_action() -> None:
    area = WorkArea(
        monitor_id="display-primary",
        left=0,
        top=0,
        right=1920,
        bottom=1080,
        primary=True,
        dpi_percent=100,
    )

    class _ControlStore:
        def __init__(self) -> None:
            self.filters: dict[str, object] = {}

        def load(self) -> dict[str, object]:
            return {"state": {"filters": dict(self.filters)}}

    class _Assistant:
        def __init__(self, store: _ControlStore) -> None:
            self.store = store
            self.persisted: list[AssistantWindowPlacement] = []

        def load_placement(
            self, areas: tuple[WorkArea, ...]
        ) -> AssistantWindowPlacement:
            if not self.store.filters:
                return restore_default_placement(areas)
            return AssistantWindowPlacement(
                monitor_id=str(self.store.filters["assistant.monitor_id"]),
                x=int(self.store.filters["assistant.x"]),
                y=int(self.store.filters["assistant.y"]),
                width=int(self.store.filters["assistant.width"]),
                height=int(self.store.filters["assistant.height"]),
                dpi_percent=int(self.store.filters["assistant.dpi_percent"]),
            )

        def persist_placement(
            self,
            placement: AssistantWindowPlacement,
            _areas: tuple[WorkArea, ...],
        ) -> dict[str, object]:
            self.persisted.append(placement)
            for field in ("monitor_id", "x", "y", "width", "height", "dpi_percent"):
                self.store.filters[f"assistant.{field}"] = getattr(placement, field)
            return {"placement": placement.to_dict(), "control_store_revision": 1}

    store = _ControlStore()
    assistant = _Assistant(store)
    api = object.__new__(AssistantDesktop)
    api.lock = threading.RLock()
    api.window = None
    api.control_store = store
    api.controller = assistant
    api.areas = (area,)
    api.placement_class = AssistantWindowPlacement
    api.clamp_placement = clamp_placement
    api.visible_bounds = visible_bounds

    first = api.initial_placement()
    second = api.initial_placement()

    assert first == second
    assert len(assistant.persisted) == 1
    assert store.filters["assistant.width"] == 90
    assert store.filters["assistant.height"] == 90

from __future__ import annotations

from pathlib import Path
import io
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Mapping
import urllib.error

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.documents.inbox import InboxController  # noqa: E402
from memorive.models.gateway.local_structured_chat import LocalStructuredChatAdapter  # noqa: E402
from memorive_messages import MessagesProductController  # noqa: E402
from memorive.sessions.refinement.model_channel import _response_schema  # noqa: E402
from memorive.sessions.controller import SessionsProductController  # noqa: E402
from runtime import ServiceProcessManager  # noqa: E402
from tests.test_session_refinement_tasks import (  # noqa: E402
    LOCAL_PROFILE_REF,
    _FacadeService,
    _LocalModelRegistry,
    _browser_bundle,
    _current_task,
)
from tests.test_refinement_inbox_and_local_mapping import (  # noqa: E402
    _BlockingChannel,
    _QueuedSessionsService,
)


def _sessions(
    tmp_path: Path,
    *,
    channel: Any,
    observer: Any | None = None,
) -> tuple[SessionsProductController, InboxController, Any, str]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    inbox = InboxController(source_roots=[source_root], data_root=tmp_path / "inbox")
    current_task = _current_task(tmp_path, _FacadeService(tmp_path / "facade"))
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    sessions = SessionsProductController(
        _QueuedSessionsService(inbox),
        object(),
        ATTEMPT_ROOT / "contracts" / "analysis",
        ATTEMPT_ROOT / "fixtures_t08",
        tmp_path / "sessions-state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        local_model_registry=_LocalModelRegistry(),
        conversation_refinement_channel=channel,
        refinement_task_manager=current_task,
        refinement_lifecycle_observer=observer,
    )
    sessions.import_browser_bundle(_browser_bundle())
    projection_id = sessions.call("sessions.list", {"source": "deepseek"})["rows"][0][
        "local_projection_id"
    ]
    sessions.call(
        "sessions.refinement_settings_save",
        {"profile_ref": LOCAL_PROFILE_REF, "include_tasks": True, "include_risks": True},
    )
    inbox.set_auto_run(True)
    return sessions, inbox, current_task, projection_id


def test_local_refinement_schema_avoids_ollama_unsupported_boolean_items() -> None:
    schema = _response_schema(
        message_ids=("msg-01", "msg-02"),
    )

    item_array = schema["properties"]["items"]

    assert item_array["minItems"] == item_array["maxItems"] == 2
    assert "items" not in item_array


class _JsonResponse:
    def __init__(self, value: Mapping[str, Any]) -> None:
        self._raw = json.dumps(dict(value)).encode("utf-8")

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def test_local_adapter_projects_safe_schema_rejection_reason() -> None:
    digest = "D" * 64

    def urlopen(request: Any, *, timeout: float) -> Any:
        del timeout
        if request.full_url.endswith("/api/tags"):
            return _JsonResponse({"models": [{"model": "fixture-local", "digest": digest}]})
        if request.full_url.endswith("/api/version"):
            return _JsonResponse({"version": "fixture"})
        body = b'{"error":"JSON schema conversion failed: Unrecognized schema: false"}'
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, io.BytesIO(body)
        )

    outcome = LocalStructuredChatAdapter(urlopen=urlopen).execute(
        prompt="Return one structured field.",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        binding={
            "schema_version": "EvaluationAssetsLocalStructuredChatBinding-v1",
            "endpoint": "http://127.0.0.1:11434/api/chat",
            "requested_model": "fixture-local",
            "expected_model_digest": digest,
            "think": False,
            "temperature": 0,
            "seed": 1,
            "num_ctx": 1024,
            "num_predict": 128,
            "cloud_fallback": False,
        },
        purpose="conversation_refinement",
    )

    assert outcome.success is False
    assert outcome.error_code == "LOCAL_STRUCTURED_SCHEMA_UNSUPPORTED"
    assert outcome.receipt["error_code"] == outcome.error_code


class _HealthyService:
    def call(self, method: str, _params: Mapping[str, Any]) -> Mapping[str, Any]:
        if method == "settings.get_state":  # messages read the UI language (v1.02)
            from memorive_settings.contracts import default_settings
            return {"settings": default_settings()}
        assert method == "service.health"
        return {
            "schema_version": "ApplicationServiceHealth-v1",
            "protocol_version": "1.0",
            "adapter_kind": "fixture",
            "request_count": 0,
            "auth_rejection_count": 0,
            "connection_count": 0,
            "uptime_seconds": 0,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
        }


def test_user_state_build_owner_rejects_older_build_after_newer_release(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "state" / "service-runtime"
    profile_root = tmp_path / "state" / "profile"
    source_root = ATTEMPT_ROOT / "source"
    latest = ServiceProcessManager(
        source_root, profile_root, runtime_root, service_revision=60
    )
    latest._acquire_state_owner()
    latest._release_state_owner(status="STOPPED")

    older = ServiceProcessManager(
        source_root, profile_root, runtime_root, service_revision=59
    )
    with pytest.raises(RuntimeError, match="APPLICATION_SERVICE_NEWER_BUILD_OWNS_STATE"):
        older._acquire_state_owner()

    successor = ServiceProcessManager(
        source_root, profile_root, runtime_root, service_revision=61
    )
    successor._acquire_state_owner()
    command = successor._service_command()
    assert command[-4:] == [
        "--parent-pid",
        str(sys.modules["os"].getpid()),
        "--service-revision",
        "61",
    ]
    successor._release_state_owner(status="STOPPED")



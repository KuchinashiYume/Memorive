"""A structured chat request that fails, on each route: API, command-line tool, local model.

LiveModelValidationRunner.execute_structured_chat sends every model request of the app; a route
(_send_api, _send_cli, _send_local) that cannot get a reply returns _ChatAttempt.failed(...). These
tests run each route against a stand-in transport that fails and check the failed result: status,
reason, the receipt that names the same reason, and the call counts. Nothing leaves the computer.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.error import URLError

import pytest

from memorive_settings.model_validation import HttpResponse, LiveModelValidationRunner, ProcessResult

SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
PREFERENCES = {"proxy_mode": "NONE", "proxy_address": "", "request_timeout_seconds": 30}


class _FailingHttp:
    def __init__(self):
        self.calls = []

    def request(self, **request):
        self.calls.append(request)
        return HttpResponse(status_code=500, body=b'{"error": {"message": "server error"}}')


class _FailingProcess:
    def __init__(self):
        self.calls = []

    def run(self, **request):
        self.calls.append(request)
        return ProcessResult(True, 1, b"", b"the tool stopped", duration_ms=5)


def _check_failed(result, requested_model):
    assert result["status"] == "FAILED"
    assert isinstance(result["reason"], str) and result["reason"]
    receipt = result["execution_receipt"]
    assert receipt["status"] == "FAILED" and receipt["failure_reason"] == result["reason"]
    assert result["requested_model"] == receipt["requested_model"] == requested_model
    for count in ("external_network_calls", "provider_calls", "external_model_calls", "external_process_launches"):
        assert type(result[count]) is int and result[count] >= 0


def test_an_api_request_answered_with_a_server_error_fails(tmp_path):
    http = _FailingHttp()
    runner = LiveModelValidationRunner(credential_resolver=lambda _ref: b"fixture-secret",
                                       preferences_loader=lambda: dict(PREFERENCES),
                                       scratch_root=tmp_path / "chat", http_transport=http)
    result = runner.execute_structured_chat(
        profile_kind="API", service={"provider": "ANTHROPIC_API_KEY_00", "credential_ref": "REF:fixture"},
        model={"model_name": "claude-opus-5", "max_output_tokens_mode": "EXPLICIT"},
        prompt="Return one object.", response_schema=SCHEMA, purpose="research_chat")
    _check_failed(result, "claude-opus-5")
    assert len(http.calls) == 1


def test_a_command_line_tool_that_exits_with_an_error_fails(tmp_path):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"codex-cli-fixture")
    process = _FailingProcess()
    runner = LiveModelValidationRunner(credential_resolver=lambda _ref: b"unused",
                                       preferences_loader=lambda: dict(PREFERENCES),
                                       scratch_root=tmp_path / "chat", process_transport=process,
                                       executable_resolver=lambda _value: str(executable.resolve()))
    result = runner.execute_structured_chat(
        profile_kind="CLI",
        service={"adapter_id": "codex_cli", "executable": "codex",
                 "cli_binary_sha256": hashlib.sha256(executable.read_bytes()).hexdigest().upper()},
        model={"model_name": "gpt-5.6-luna", "thinking_mode": "high", "max_output_tokens_mode": "PROVIDER_DEFAULT"},
        prompt="Return one object.", response_schema=SCHEMA, purpose="research_chat")
    _check_failed(result, "gpt-5.6-luna")
    assert len(process.calls) == 1


def test_a_local_model_that_cannot_be_reached_fails(tmp_path, monkeypatch):
    import model_gateway.local_structured_chat as local_chat

    def unreachable(*_args, **_kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(local_chat, "_default_open", unreachable)
    runner = LiveModelValidationRunner(credential_resolver=lambda _ref: b"unused",
                                       preferences_loader=lambda: dict(PREFERENCES),
                                       scratch_root=tmp_path / "chat")
    result = runner.execute_structured_chat(
        profile_kind="LOCAL",
        service={"endpoint_kind": "ollama", "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1",
                 "execution_eligible": True, "exact_identity_available": True,
                 "chat_endpoint": "http://127.0.0.1:11434/api/chat", "model_digest": "A" * 64},
        model={"model_name": "qwen3:8b", "max_output_tokens_mode": "PROVIDER_DEFAULT"},
        prompt="Return one object.", response_schema=SCHEMA, purpose="research_chat")
    _check_failed(result, "qwen3:8b")

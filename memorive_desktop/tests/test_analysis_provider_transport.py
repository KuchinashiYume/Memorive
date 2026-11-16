from __future__ import annotations

import json
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive.models.validation.transport import (  # noqa: E402
    BoundedSubprocessTransport,
    HttpResponse,
    MAX_PROCESS_CAPTURE_BYTES,
)
from memorive.models.validation.runner import LiveModelValidationRunner  # noqa: E402
from memorive_settings.exam_successor import (  # noqa: E402
    workflow_exam_structured_output_policy,
)
from memorive.models.output_limits import (  # noqa: E402
    workflow_exam_output_limit_contract,
)


class _Http:
    def __init__(self, body: dict) -> None:
        self.body = body
        self.calls: list[dict] = []

    def request(self, **kwargs):
        self.calls.append(dict(kwargs))
        return HttpResponse(
            status_code=200,
            body=json.dumps(self.body).encode("utf-8"),
        )


def _runner(tmp_path: Path, http: _Http) -> LiveModelValidationRunner:
    return LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"fixture-secret",
        preferences_loader=lambda: {
            "proxy_mode": "NONE",
            "proxy_address": "",
            "request_timeout_seconds": 30,
        },
        scratch_root=tmp_path / "transport",
        http_transport=http,
    )


def test_anthropic_opus5_max_uses_adaptive_effort_provider_ceiling_and_streaming(
    tmp_path: Path,
) -> None:
    events = [
        {
            "type": "message_start",
            "message": {
                "model": "claude-opus-5",
                "usage": {
                    "input_tokens": 1_000_000,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 1,
                },
            },
        },
        {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "hidden fixture"},
        },
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": '{"ok":true}'},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1_000_000},
        },
        {"type": "message_stop"},
    ]
    payload = b"".join(
        b"event: fixture\n"
        + b"data: "
        + json.dumps(event).encode("utf-8")
        + b"\n\n"
        for event in events
    )
    http = _Http({})
    http.body = None

    def stream_request(**kwargs):
        http.calls.append(dict(kwargs))
        return HttpResponse(status_code=200, body=payload)

    http.request = stream_request
    result = _runner(tmp_path, http).execute_structured_chat(
        profile_kind="API",
        service={
            "provider": "ANTHROPIC_API_KEY_00",
            "credential_ref": "REF:fixture",
        },
        model={
            "model_name": "claude-opus-5",
            "max_output_tokens_mode": "EXPLICIT",
            "provider_max_output_tokens": 128_000,
            "adaptive_thinking": True,
            "reasoning_effort": "max",
            "streaming": True,
            "api_max_response_bytes": 32 * 1024 * 1024,
            "pricing_estimate_cny_per_million": {
                "input": 37.5,
                "output": 187.5,
                "input_cache_hit": 3.75,
            },
            "pricing_evidence_ref": "anthropic-official-2026-09-03",
        },
        prompt="Return one object.",
        response_schema={
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
        purpose="workflow_model_exam",
        max_output_tokens=128_000,
        timeout_seconds=300,
    )

    assert result["status"] == "PASS"
    body = json.loads(http.calls[0]["body"])
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "max"}
    assert body["max_tokens"] == 128_000
    assert body["stream"] is True
    assert "temperature" not in body
    assert http.calls[0]["max_response_bytes"] == 32 * 1024 * 1024
    receipt = result["execution_receipt"]
    assert receipt["estimated_cost_cny"] == 225.0
    assert receipt["cost_evidence"] == "FROZEN_TARGET_PRICING_PROFILE_ESTIMATE"
    assert receipt["request_max_output_tokens"] == 128_000
    assert receipt["transport_diagnostic"]["streaming"] is True
    assert receipt["transport_diagnostic"]["max_response_bytes"] == 32 * 1024 * 1024
    assert receipt["transport_diagnostic"]["observed_completion_tokens"] == 1_000_000
    assert (
        receipt["transport_diagnostic"]["calibrated_reply_token_ceiling"]
        == 173_030
    )
    assert (
        receipt["transport_diagnostic"]["calibrated_reply_token_ceiling_exceeded"]
        is True
    )
    assert receipt["transport_diagnostic"]["reply_length_quality_semantics"] == (
        "EVIDENCE_RETAINED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
    )


def test_workflow_exam_has_no_hidden_legacy_32768_ceiling(
    tmp_path: Path,
) -> None:
    http = _Http(
        {
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    result = _runner(tmp_path, http).execute_structured_chat(
        profile_kind="API",
        service={
            "provider": "ANTHROPIC_API_KEY_00",
            "credential_ref": "REF:fixture",
        },
        model={
            "model_name": "claude-opus-5",
            "max_output_tokens_mode": "EXPLICIT",
        },
        prompt="Return one object.",
        response_schema={
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
        purpose="workflow_model_exam",
        max_output_tokens=128_000,
        timeout_seconds=300,
    )

    assert result["status"] == "PASS"
    assert json.loads(http.calls[0]["body"])["max_tokens"] == 128_000
    assert result["execution_receipt"]["request_max_output_tokens"] == 128_000


def test_workflow_exam_clamps_declared_request_to_calibrated_ceiling(
    tmp_path: Path,
) -> None:
    http = _Http(
        {
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    result = _runner(tmp_path, http).execute_structured_chat(
        profile_kind="API",
        service={
            "provider": "ANTHROPIC_API_KEY_00",
            "credential_ref": "REF:fixture",
        },
        model={
            "model_name": "claude-opus-5",
            "max_output_tokens_mode": "EXPLICIT",
            "provider_max_output_tokens": 250_000,
        },
        prompt="Return one object.",
        response_schema={
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
        purpose="workflow_model_exam",
        max_output_tokens=250_000,
        timeout_seconds=300,
    )

    assert result["status"] == "PASS"
    assert json.loads(http.calls[0]["body"])["max_tokens"] == 128_000
    assert result["execution_receipt"]["request_max_output_tokens"] == 128_000


def test_workflow_exam_respects_a_smaller_declared_provider_capacity(
    tmp_path: Path,
) -> None:
    http = _Http(
        {
            "model": "smaller-provider-model",
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    result = _runner(tmp_path, http).execute_structured_chat(
        profile_kind="API",
        service={
            "provider": "ANTHROPIC_API_KEY_00",
            "credential_ref": "REF:fixture",
        },
        model={
            "model_name": "smaller-provider-model",
            "max_output_tokens_mode": "EXPLICIT",
            "provider_max_output_tokens": 16_384,
        },
        prompt="Return one object.",
        response_schema={
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
        purpose="workflow_model_exam",
        max_output_tokens=173_030,
        timeout_seconds=300,
    )

    assert result["status"] == "PASS"
    assert json.loads(http.calls[0]["body"])["max_tokens"] == 16_384
    assert result["execution_receipt"]["request_max_output_tokens"] == 16_384


def test_cli_capture_envelope_no_longer_uses_legacy_256_kib(tmp_path: Path) -> None:
    payload = b"x" * (300 * 1024)
    capture = tmp_path / "capture.bin"
    capture.write_bytes(payload)

    observed, truncated, captured_bytes = BoundedSubprocessTransport()._read_bounded(capture)

    assert MAX_PROCESS_CAPTURE_BYTES is None
    assert captured_bytes == len(payload)
    assert observed == payload
    assert truncated is False


def test_exam_output_policy_is_empirical_model_aware_and_not_a_quality_gate() -> None:
    contract = workflow_exam_output_limit_contract()

    assert contract["calibration_model"] == "claude-opus-5"
    assert contract["calibration_horizon_years"] == 5
    assert contract["calibration_max_actual_completion_tokens"] == 17_303
    assert contract["calibration_multiplier"] == 10
    assert contract["calibrated_reply_token_ceiling"] == 173_030
    assert contract["length_only_answer_invalidation_forbidden"] is True
    assert contract["length_only_semantic_repair_forbidden"] is True
    assert contract["physical_limit_outcome"] == "NOT_ASSESSED"

    api_binding, api_limit, api_mode = workflow_exam_structured_output_policy(
        {"kind": "API", "model_name": "provider-default-model"}
    )
    assert api_mode == "PROVIDER_DEFAULT"
    assert api_limit is None
    assert api_binding["max_output_tokens_mode"] == "PROVIDER_DEFAULT"

    bounded_binding, bounded_limit, bounded_mode = (
        workflow_exam_structured_output_policy(
            {
                "kind": "API",
                "model_name": "smaller-provider-model",
                "provider_max_output_tokens": 16_384,
            }
        )
    )
    assert bounded_mode == "MODEL_PROFILE_EXPLICIT"
    assert bounded_limit == 16_384
    assert bounded_binding["max_output_tokens_mode"] == "EXPLICIT"

    cli_binding, cli_limit, cli_mode = workflow_exam_structured_output_policy(
        {"kind": "CLI", "model_name": "gpt-5.6-sol"}
    )
    assert cli_mode == "PROVIDER_DEFAULT"
    assert cli_limit is None
    assert cli_binding["max_output_tokens_mode"] == "PROVIDER_DEFAULT"

    local_binding, local_limit, local_mode = (
        workflow_exam_structured_output_policy(
            {
                "kind": "LOCAL",
                "model_name": "qwen-local",
                "context_window_tokens": 65_536,
            }
        )
    )
    assert local_mode == "PROVIDER_DEFAULT"
    assert local_limit is None
    assert "exam_max_output_tokens" not in local_binding
    assert contract["planning_reference_is_hard_limit"] is False


def test_kimi_k3_uses_top_level_max_effort_and_target_pricing(
    tmp_path: Path,
) -> None:
    http = _Http(
        {}
    )
    http.body = None

    def stream_request(**kwargs):
        http.calls.append(dict(kwargs))
        events = [
            {
                "model": "kimi-k3",
                "choices": [{"delta": {"content": '{"ok":'}, "finish_reason": None}],
            },
            {
                "model": "kimi-k3",
                "choices": [{"delta": {"content": "true}"}, "finish_reason": "stop"}],
            },
            {
                "model": "kimi-k3",
                "choices": [],
                "usage": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                },
            },
        ]
        payload = b"".join(
            b"data: " + json.dumps(event).encode("utf-8") + b"\n\n"
            for event in events
        ) + b"data: [DONE]\n\n"
        return HttpResponse(status_code=200, body=payload)

    http.request = stream_request
    result = _runner(tmp_path, http).execute_structured_chat(
        profile_kind="API",
        service={
            "provider": "KIMI_API_KEY",
            "credential_ref": "KIMI_API_KEY",
            "api_protocol": "OPENAI_COMPATIBLE",
            "api_base_url": "https://api.moonshot.ai/v1",
        },
        model={
            "model_name": "kimi-k3",
            "max_output_tokens_mode": "EXPLICIT",
            "max_output_tokens_wire_field": "max_completion_tokens",
            "provider_max_output_tokens": 32768,
            "reasoning_effort": "max",
            "omit_temperature": True,
            "streaming": True,
            "pricing_estimate_cny_per_million": {
                "input": 22.5,
                "output": 112.5,
                "input_cache_hit": 2.25,
            },
            "pricing_evidence_ref": "kimi-official-2026-09-03",
        },
        prompt="Return one object.",
        response_schema={
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
        purpose="workflow_model_exam",
        max_output_tokens=32_768,
        timeout_seconds=300,
    )

    assert result["status"] == "PASS"
    body = json.loads(http.calls[0]["body"])
    assert body["reasoning_effort"] == "max"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_completion_tokens"] == 32_768
    assert "max_tokens" not in body
    assert "thinking" not in body
    assert "temperature" not in body
    receipt = result["execution_receipt"]
    assert receipt["estimated_cost_cny"] == 135.0
    assert receipt["cost_evidence"] == "FROZEN_TARGET_PRICING_PROFILE_ESTIMATE"

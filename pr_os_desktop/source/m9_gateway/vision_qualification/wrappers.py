"""Pure request projections and zero-body doctors for T07 vision channels."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .request_contracts import (
    VisionRequestContractError,
    canonical_sha256,
    evaluate_presend_readiness,
)


FORBIDDEN_CLI_EVENT_TYPES = frozenset(
    {
        "command_execution",
        "computer_use",
        "dynamic_tool_call",
        "shell_command",
        "web_search",
        "web_open",
        "file_write",
        "file_edit",
        "patch",
    }
)


def _assert_ready(page: Mapping[str, Any], presend: Mapping[str, Any], candidate: str) -> None:
    candidate_record = page.get("candidate", {})
    if not isinstance(candidate_record, Mapping):
        raise VisionRequestContractError("WRAPPER_CANDIDATE_INVALID")
    if candidate_record.get("candidate_key") != candidate:
        raise VisionRequestContractError("WRAPPER_CANDIDATE_MISMATCH", [candidate])
    if presend.get("request_disposition") != "READY_TO_SEND_METADATA_ONLY":
        raise VisionRequestContractError("PRESEND_RECEIPT_NOT_READY")
    receipt_body = dict(presend)
    recorded_receipt_sha256 = receipt_body.pop("receipt_sha256", None)
    if recorded_receipt_sha256 != canonical_sha256(receipt_body):
        raise VisionRequestContractError("PRESEND_RECEIPT_HASH_MISMATCH")
    if presend.get("page_subject_input_sha256") != canonical_sha256(page):
        raise VisionRequestContractError("PRESEND_PAGE_INPUT_HASH_MISMATCH")
    expected = {
        "pack_id": page.get("pack_id"),
        "attempt_id": page.get("attempt_id"),
        "page_id": page.get("page_id"),
        "source_document_id": page.get("source_document_id"),
        "source_page_number": page.get("source_page_number"),
        "source_file_sha256": page.get("source_file_sha256"),
        "render_recipe_sha256": page.get("render_recipe_sha256"),
        "image_sha256": page.get("source_image_sha256"),
        "candidate_key": candidate_record.get("candidate_key"),
        "channel_kind": candidate_record.get("channel_kind"),
        "provider": candidate_record.get("provider"),
        "profile_id": candidate_record.get("profile_id"),
        "requested_model": candidate_record.get("requested_model"),
        "route": candidate_record.get("route"),
        "region": candidate_record.get("region"),
        "egress": candidate_record.get("egress"),
        "semantic_task_sha256": page.get("semantic_task", {}).get("sha256"),
        "channel_wrapper_sha256": page.get("channel_wrapper", {}).get("sha256"),
        "output_schema_sha256": page.get("output_schema", {}).get("sha256"),
    }
    mismatches = sorted(
        field for field, value in expected.items() if presend.get(field) != value
    )
    if mismatches:
        raise VisionRequestContractError("PRESEND_PAGE_BINDING_MISMATCH", mismatches)


def _assert_semantic_task_hash(page: Mapping[str, Any], semantic_task_text: str) -> None:
    observed = hashlib.sha256(semantic_task_text.encode("utf-8")).hexdigest().upper()
    expected = page.get("semantic_task", {}).get("sha256")
    if observed != expected:
        raise VisionRequestContractError(
            "SEMANTIC_TASK_HASH_MISMATCH", [f"expected={expected}", f"observed={observed}"]
        )


def build_claude_api_projection(
    page_subject_input: Mapping[str, Any],
    presend_receipt: Mapping[str, Any],
    *,
    semantic_task_text: str,
) -> dict[str, Any]:
    """Build an M9 relay projection; this function cannot send it."""

    _assert_ready(page_subject_input, presend_receipt, "claude_api_visual")
    if not isinstance(semantic_task_text, str) or not semantic_task_text.strip():
        raise VisionRequestContractError("SEMANTIC_TASK_TEXT_INVALID")
    _assert_semantic_task_hash(page_subject_input, semantic_task_text)
    projection = {
        "schema_version": "P06T07ClaudeVisionProjection-v1",
        "transport": "M9_CONTROLLED_RELAY_ONLY",
        "provider": "anthropic",
        "requested_model": presend_receipt["requested_model"],
        "profile_id": presend_receipt["profile_id"],
        "single_page_image": {
            "image_ref": page_subject_input["image_ref"],
            "mime_type": page_subject_input["image"]["mime_type"],
            "sha256": page_subject_input["source_image_sha256"],
        },
        "semantic_task_ref": page_subject_input["semantic_task"],
        "semantic_task_text": semantic_task_text,
        "output_schema_ref": page_subject_input["output_schema"],
        "gold_included": False,
        "hidden_text_included": False,
        "other_candidate_output_included": False,
        "whole_pdf_included": False,
        "tools": [],
        "web": False,
        "shell": False,
        "workspace_write": False,
        "external_request_started": False,
        "physical_process_start_count": 0,
        "presend_receipt_id": presend_receipt["receipt_id"],
        "presend_receipt_sha256": presend_receipt["receipt_sha256"],
    }
    projection["projection_sha256"] = canonical_sha256(projection)
    return projection


def build_openai_api_image_projection(
    page_subject_input: Mapping[str, Any],
    presend_receipt: Mapping[str, Any],
    *,
    semantic_task_text: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    """Build a zero-send Responses API projection for one image."""

    _assert_ready(page_subject_input, presend_receipt, "openai_api_visual")
    if not isinstance(semantic_task_text, str) or not semantic_task_text.strip():
        raise VisionRequestContractError("SEMANTIC_TASK_TEXT_INVALID")
    _assert_semantic_task_hash(page_subject_input, semantic_task_text)
    if reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        raise VisionRequestContractError(
            "OPENAI_REASONING_EFFORT_INVALID", [str(reasoning_effort)]
        )
    projection = {
        "schema_version": "P06T07OpenAIVisionProjection-v1",
        "transport": "M9_CONTROLLED_RELAY_ONLY",
        "access_mode": "usage_based_api",
        "provider": "openai",
        "endpoint": "/v1/responses",
        "requested_model": presend_receipt["requested_model"],
        "profile_id": presend_receipt["profile_id"],
        "reasoning": {"effort": reasoning_effort},
        "thinking_parameter": "NOT_APPLICABLE_OPENAI_RESPONSES_API",
        "pro_mode": False,
        "input": {
            "role": "user",
            "text": semantic_task_text,
            "single_page_image": {
                "type": "input_image",
                "image_ref": page_subject_input["image_ref"],
                "mime_type": page_subject_input["image"]["mime_type"],
                "sha256": page_subject_input["source_image_sha256"],
                "detail": "original",
            },
        },
        "structured_output": {
            "type": "json_schema",
            "name": "p06_t07_normalized_page_transcription",
            "schema_ref": page_subject_input["output_schema"],
            "strict": False,
        },
        "store": False,
        "tools": [],
        "web": False,
        "shell": False,
        "workspace_write": False,
        "gold_included": False,
        "hidden_text_included": False,
        "other_candidate_output_included": False,
        "whole_pdf_included": False,
        "external_request_started": False,
        "physical_process_start_count": 0,
        "presend_receipt_id": presend_receipt["receipt_id"],
        "presend_receipt_sha256": presend_receipt["receipt_sha256"],
    }
    projection["projection_sha256"] = canonical_sha256(projection)
    return projection


def build_openai_cli_image_projection(
    page_subject_input: Mapping[str, Any],
    presend_receipt: Mapping[str, Any],
    *,
    semantic_task_text: str,
    output_path_ref: str,
) -> dict[str, Any]:
    """Build but never execute a one-image Codex CLI successor plan."""

    _assert_ready(
        page_subject_input,
        presend_receipt,
        "openai_subscription_cli_visual",
    )
    if not isinstance(semantic_task_text, str) or not semantic_task_text.strip():
        raise VisionRequestContractError("SEMANTIC_TASK_TEXT_INVALID")
    _assert_semantic_task_hash(page_subject_input, semantic_task_text)
    if not isinstance(output_path_ref, str) or not output_path_ref.strip():
        raise VisionRequestContractError("OUTPUT_PATH_REF_INVALID")
    image_ref = page_subject_input["image_ref"]
    argv = [
        "codex",
        "exec",
        "--json",
        "--model",
        presend_receipt["requested_model"],
        "--image",
        image_ref,
        "--output-schema",
        page_subject_input["output_schema"]["ref"],
        "--output-last-message",
        output_path_ref,
        "--sandbox",
        "read-only",
        semantic_task_text,
    ]
    projection = {
        "schema_version": "P06T07OpenAIImageCliProjection-v1",
        "transport": "DIRECT_CLI_PROCESS_PROJECTION_ONLY",
        "access_mode": "subscription_cli",
        "requested_model": presend_receipt["requested_model"],
        "profile_id": presend_receipt["profile_id"],
        "argv": argv,
        "image_count": 1,
        "image_ref": image_ref,
        "image_sha256": page_subject_input["source_image_sha256"],
        "output_path_ref": output_path_ref,
        "workspace_policy": "isolated_create_only_output",
        "tools": [],
        "web": False,
        "shell": False,
        "gold_included": False,
        "hidden_text_included": False,
        "other_candidate_output_included": False,
        "whole_pdf_included": False,
        "external_request_started": False,
        "physical_process_start_count": 0,
        "presend_receipt_id": presend_receipt["receipt_id"],
        "presend_receipt_sha256": presend_receipt["receipt_sha256"],
    }
    projection["projection_sha256"] = canonical_sha256(projection)
    return projection


def inspect_cli_event_stream(
    events: Sequence[Mapping[str, Any]],
    *,
    allowed_output_path_ref: str,
) -> dict[str, Any]:
    """Reject any tool/web/shell/write behavior in a frozen CLI event stream."""

    violations: list[str] = []
    final_output_count = 0
    for index, event in enumerate(events):
        event_type = str(event.get("type", ""))
        if event_type in FORBIDDEN_CLI_EVENT_TYPES:
            violations.append(f"FORBIDDEN_EVENT:{index}:{event_type}")
        if event_type == "final_output":
            final_output_count += 1
            if event.get("path_ref") != allowed_output_path_ref:
                violations.append(f"OUTPUT_PATH_MISMATCH:{index}")
        if any(key in event for key in ("command", "url", "patch", "tool_name")):
            violations.append(f"FORBIDDEN_EVENT_FIELD:{index}")
    if final_output_count != 1:
        violations.append(f"FINAL_OUTPUT_COUNT:{final_output_count}")
    return {
        "result": "PASS" if not violations else "SECURITY_CONTRACT_VIOLATION",
        "event_count": len(events),
        "final_output_count": final_output_count,
        "violation_codes": sorted(set(violations)),
        "fallback_authorized": False,
    }


def vision_channel_doctor(
    page_subject_input: Mapping[str, Any],
    binding: Mapping[str, Any],
    *,
    runtime_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Metadata-only doctor.  It intentionally performs no version/auth probe."""

    readiness = evaluate_presend_readiness(page_subject_input, binding)
    blockers = list(readiness["blocker_codes"])
    metadata = dict(runtime_metadata or {})
    for field in ("runtime_version", "profile_hash", "behavior_hash", "output_create_only"):
        if metadata.get(field) in (None, "", False):
            blockers.append(f"RUNTIME_{field.upper()}_UNBOUND")
    return {
        "result": "READY_METADATA_ONLY" if not blockers else "BLOCKED",
        "blocker_codes": sorted(set(blockers)),
        "runtime_metadata_hash": canonical_sha256(metadata),
        "image_body_read_count": 0,
        "credential_read_count": 0,
        "dns_or_network_request_count": 0,
        "physical_process_start_count": 0,
        "external_request_start_count": 0,
        "production_route_mutation_count": 0,
    }


@runtime_checkable
class LocalVisionAdapter(Protocol):
    def capability_report(self) -> Mapping[str, Any]: ...

    def transcribe(
        self,
        page_ref: str,
        semantic_task_ref: str,
        output_schema_ref: str,
        deadline_seconds: int,
    ) -> Mapping[str, Any]: ...


def not_configured_local_capability() -> dict[str, Any]:
    return {
        "schema_version": "P06T07LocalVisionCapability-v1",
        "state": "NOT_CONFIGURED_NOT_ASSESSED",
        "runtime": None,
        "version": None,
        "model_identity": None,
        "license": None,
        "device": None,
        "resource_limits": None,
        "download_attempt_count": 0,
        "process_start_count": 0,
        "network_request_count": 0,
        "cloud_fallback_authorized": False,
    }

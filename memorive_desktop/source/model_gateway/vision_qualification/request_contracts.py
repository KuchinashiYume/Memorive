"""Metadata-only pre-send and terminal contracts for Retrieval vision channels."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
SAFE_OWNERSHIP = {"self", "public-safe"}


class VisionRequestContractError(ValueError):
    def __init__(self, code: str, details: Sequence[str] | None = None):
        self.code = code
        self.details = tuple(details or ())
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VisionRequestContractError("CANONICAL_JSON_INVALID", [str(exc)]) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _copy(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def _page_schema() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[2]
        / "document_processing"
        / "schemas"
        / "page_recognition_page_subject_input_v1.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _schema_errors(value: Mapping[str, Any]) -> list[str]:
    errors = sorted(
        Draft202012Validator(_page_schema()).iter_errors(value),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            error.message,
        ),
    )
    return [
        f"{'/'.join(str(item) for item in error.absolute_path) or '$'}: {error.message}"
        for error in errors[:20]
    ]


def evaluate_presend_readiness(
    page_subject_input: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    if not isinstance(page_subject_input, Mapping):
        blockers.append("PAGE_SUBJECT_INPUT_TYPE_INVALID")
        page_subject_input = {}
    if not isinstance(binding, Mapping):
        blockers.append("PRE_SEND_BINDING_TYPE_INVALID")
        binding = {}
    schema_failures = _schema_errors(page_subject_input)
    if schema_failures:
        blockers.append("PAGE_SUBJECT_INPUT_SCHEMA_INVALID")
    if page_subject_input.get("data_ownership") not in SAFE_OWNERSHIP:
        blockers.append("OWNERSHIP_BLOCKED_BEFORE_IMAGE_READ")
    expected_rights = {
        "self": "ALLOWED_SELF",
        "public-safe": "ALLOWED_PUBLIC_SAFE",
        "entrusted": "BLOCKED",
        "unknown": "BLOCKED",
    }.get(page_subject_input.get("data_ownership"))
    if page_subject_input.get("rights_custody_verdict") != expected_rights:
        blockers.append("RIGHTS_OWNERSHIP_MISMATCH")
    if page_subject_input.get("provider_visibility") != "SINGLE_PAGE_IMAGE_ONLY":
        blockers.append("SINGLE_PAGE_PROVIDER_VISIBILITY_NOT_BOUND")
    visibility = page_subject_input.get("visibility", {})
    if not isinstance(visibility, Mapping) or any(visibility.get(field) is not False for field in ("gold_visible", "other_candidate_outputs_visible", "hidden_text_visible", "whole_pdf_visible")):
        blockers.append("FORBIDDEN_VISIBILITY_PRESENT")
    if page_subject_input.get("external_request_authorized") is not True:
        blockers.append("EXTERNAL_REQUEST_AUTHORITY_ABSENT")
    if page_subject_input.get("attempt_ordinal") != 1 or page_subject_input.get("retry_count") != 0:
        blockers.append("ATTEMPT_OR_RETRY_CONTRACT_INVALID")

    candidate = page_subject_input.get("candidate", {})
    if not isinstance(candidate, Mapping):
        candidate = {}
    expected_channel = {
        "deepseek_ocr_specialist_baseline": "usage_based_api",
        "claude_api_visual": "usage_based_api",
        "openai_api_visual": "usage_based_api",
        "openai_subscription_cli_visual": "subscription_cli",
        "local_visual_adapter": "local_resource",
    }.get(candidate.get("candidate_key"))
    if candidate.get("channel_kind") != expected_channel:
        blockers.append("CANDIDATE_CHANNEL_KIND_MISMATCH")
    exact_binding_fields = {
        "pack_id": page_subject_input.get("pack_id"),
        "attempt_id": page_subject_input.get("attempt_id"),
        "page_id": page_subject_input.get("page_id"),
        "source_document_id": page_subject_input.get("source_document_id"),
        "source_page_number": page_subject_input.get("source_page_number"),
        "source_file_sha256": page_subject_input.get("source_file_sha256"),
        "render_recipe_sha256": page_subject_input.get("render_recipe_sha256"),
        "candidate_key": candidate.get("candidate_key"),
        "channel_kind": candidate.get("channel_kind"),
        "provider": candidate.get("provider"),
    }
    for field, expected in exact_binding_fields.items():
        if binding.get(field) != expected:
            blockers.append(f"BINDING_{field.upper()}_MISMATCH")
    for field in ("profile_id", "requested_model", "route", "region", "egress"):
        if candidate.get(field) in (None, ""):
            blockers.append(f"CANDIDATE_{field.upper()}_UNBOUND")
        elif binding.get(field) != candidate.get(field):
            blockers.append(f"BINDING_{field.upper()}_MISMATCH")
    expected_hashes = {
        "semantic_task_sha256": page_subject_input.get("semantic_task", {}).get("sha256") if isinstance(page_subject_input.get("semantic_task"), Mapping) else None,
        "channel_wrapper_sha256": page_subject_input.get("channel_wrapper", {}).get("sha256") if isinstance(page_subject_input.get("channel_wrapper"), Mapping) else None,
        "output_schema_sha256": page_subject_input.get("output_schema", {}).get("sha256") if isinstance(page_subject_input.get("output_schema"), Mapping) else None,
        "image_sha256": page_subject_input.get("source_image_sha256"),
    }
    for field, expected in expected_hashes.items():
        if binding.get(field) != expected or not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            blockers.append(f"{field.upper()}_UNBOUND_OR_MISMATCH")
    for field in ("runner_sha256", "scorer_sha256", "behavior_sha256"):
        value = binding.get(field)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            blockers.append(f"{field.upper()}_INVALID")
    if binding.get("authorization_ref") != page_subject_input.get("authorization_ref") or binding.get("authorization_ref") in (None, ""):
        blockers.append("AUTHORIZATION_REF_UNBOUND")
    if binding.get("retry_count") != 0 or binding.get("call_ordinal") != 1:
        blockers.append("BINDING_ATTEMPT_OR_RETRY_INVALID")
    if (
        not isinstance(binding.get("timeout_seconds"), int)
        or isinstance(binding.get("timeout_seconds"), bool)
        or binding.get("timeout_seconds", 0) <= 0
        or binding.get("timeout_seconds") != page_subject_input.get("timeout_seconds")
    ):
        blockers.append("TIMEOUT_CEILING_INVALID")
    if not isinstance(binding.get("call_ceiling"), int) or isinstance(binding.get("call_ceiling"), bool) or binding.get("call_ceiling", 0) < 1:
        blockers.append("CALL_CEILING_INVALID")
    channel_kind = candidate.get("channel_kind")
    if channel_kind == "usage_based_api":
        name = binding.get("credential_env_name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", name):
            blockers.append("CREDENTIAL_ENV_NAME_UNBOUND")
        if binding.get("token_ceiling") is None or binding.get("cash_cost_ceiling") is None:
            blockers.append("API_TOKEN_OR_CASH_CEILING_UNBOUND")
    elif channel_kind == "subscription_cli":
        if binding.get("cash_cost_ceiling") is not None:
            blockers.append("SUBSCRIPTION_CASH_CEILING_MUST_BE_NA")
        if binding.get("subscription_unit_ceiling") is None:
            blockers.append("SUBSCRIPTION_UNIT_CEILING_UNBOUND")
    elif channel_kind == "local_resource":
        blockers.append("LOCAL_CHANNEL_NOT_EXTERNAL_SENDABLE")
    else:
        blockers.append("CHANNEL_KIND_UNKNOWN")
    for field in (
        "credential_env_name",
        "token_ceiling",
        "cash_cost_ceiling",
        "subscription_unit_ceiling",
    ):
        if binding.get(field) != page_subject_input.get(field):
            blockers.append(f"BINDING_{field.upper()}_MISMATCH")
    return {
        "ready": not blockers,
        "blocker_codes": sorted(set(blockers)),
        "physical_process_start_count": 0,
        "external_request_start_count": 0,
        "credential_read_count": 0,
    }


def build_presend_receipt(
    page_subject_input: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    readiness = evaluate_presend_readiness(page_subject_input, binding)
    if not readiness["ready"]:
        raise VisionRequestContractError("PRE_SEND_BINDING_BLOCKED", readiness["blocker_codes"])
    page = _copy(page_subject_input)
    frozen_binding = _copy(binding)
    seed = {"page_subject_input": page, "binding": frozen_binding}
    receipt = {
        "schema_version": "PageRecognitionVisionPreSendReceipt-v1",
        "receipt_id": f"page_recognition-presend-{canonical_sha256(seed)[:24].lower()}",
        "pack_id": page["pack_id"],
        "attempt_id": page["attempt_id"],
        "page_id": page["page_id"],
        "source_document_id": page["source_document_id"],
        "source_page_number": page["source_page_number"],
        "source_file_sha256": page["source_file_sha256"],
        "render_recipe_sha256": page["render_recipe_sha256"],
        "candidate_key": page["candidate"]["candidate_key"],
        "channel_kind": page["candidate"]["channel_kind"],
        "provider": page["candidate"]["provider"],
        "profile_id": page["candidate"]["profile_id"],
        "requested_model": page["candidate"]["requested_model"],
        "route": page["candidate"]["route"],
        "region": page["candidate"]["region"],
        "egress": page["candidate"]["egress"],
        "data_ownership": page["data_ownership"],
        "image_sha256": page["source_image_sha256"],
        "semantic_task_sha256": frozen_binding["semantic_task_sha256"],
        "channel_wrapper_sha256": frozen_binding["channel_wrapper_sha256"],
        "output_schema_sha256": frozen_binding["output_schema_sha256"],
        "runner_sha256": frozen_binding["runner_sha256"],
        "scorer_sha256": frozen_binding["scorer_sha256"],
        "behavior_sha256": frozen_binding["behavior_sha256"],
        "token_ceiling": frozen_binding.get("token_ceiling"),
        "cash_cost_ceiling": frozen_binding.get("cash_cost_ceiling"),
        "subscription_unit_ceiling": frozen_binding.get("subscription_unit_ceiling"),
        "retry_count": 0,
        "call_ordinal": 1,
        "call_ceiling": frozen_binding["call_ceiling"],
        "timeout_seconds": frozen_binding["timeout_seconds"],
        "credential_env_name": frozen_binding.get("credential_env_name"),
        "authorization_ref": frozen_binding["authorization_ref"],
        "request_disposition": "READY_TO_SEND_METADATA_ONLY",
        "physical_process_start_count": 0,
        "external_request_start_count": 0,
        "credential_read_count": 0,
        "page_subject_input_sha256": canonical_sha256(page),
        "binding_metadata_sha256": canonical_sha256(frozen_binding),
        "binding_sha256": canonical_sha256(seed),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def project_terminal_evidence(
    presend_receipt: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(presend_receipt, Mapping):
        raise VisionRequestContractError("PRESEND_RECEIPT_TYPE_INVALID")
    receipt_body = dict(presend_receipt)
    receipt_sha256 = receipt_body.pop("receipt_sha256", None)
    if receipt_sha256 != canonical_sha256(receipt_body):
        raise VisionRequestContractError("PRESEND_RECEIPT_HASH_MISMATCH")
    if presend_receipt.get("request_disposition") != "READY_TO_SEND_METADATA_ONLY":
        raise VisionRequestContractError("PRESEND_RECEIPT_NOT_READY")
    if not isinstance(terminal, Mapping):
        raise VisionRequestContractError("TERMINAL_TYPE_INVALID")
    required = {
        "terminal_state",
        "returned_model",
        "provider_request_id_hash",
        "usage",
        "cost_status",
        "actual_cost",
        "subscription_usage_status",
        "latency_ms",
        "output_hash",
        "error_hash",
        "disposition",
    }
    missing = sorted(required - set(terminal))
    if missing:
        raise VisionRequestContractError("TERMINAL_FIELDS_MISSING", missing)
    unknown = sorted(set(terminal) - required)
    if unknown:
        raise VisionRequestContractError("TERMINAL_FIELDS_UNKNOWN", unknown)
    if terminal["terminal_state"] not in {"completed", "ERROR"}:
        raise VisionRequestContractError("TERMINAL_STATE_INVALID")
    for field in ("provider_request_id_hash", "output_hash", "error_hash"):
        value = terminal[field]
        if value is not None and (not isinstance(value, str) or not SHA256_RE.fullmatch(value)):
            raise VisionRequestContractError("TERMINAL_HASH_INVALID", [field])
    if terminal["terminal_state"] == "completed":
        if terminal["output_hash"] is None or terminal["error_hash"] is not None:
            raise VisionRequestContractError("COMPLETED_TERMINAL_HASH_CONFLICT")
    elif terminal["error_hash"] is None:
        raise VisionRequestContractError("ERROR_TERMINAL_HASH_MISSING")
    if not isinstance(terminal["disposition"], str) or not terminal["disposition"].strip():
        raise VisionRequestContractError("TERMINAL_DISPOSITION_INVALID")
    channel = presend_receipt.get("channel_kind")
    limitations: list[str] = []
    if channel == "subscription_cli":
        if terminal["actual_cost"] is not None or terminal["cost_status"] != "NOT_APPLICABLE":
            limitations.append("SUBSCRIPTION_COST_SEMANTICS_INVALID")
        if terminal["subscription_usage_status"] not in {"VISIBLE", "NOT_EXPOSED", "RATE_LIMIT_ONLY"}:
            limitations.append("SUBSCRIPTION_USAGE_STATUS_INVALID")
    elif channel == "usage_based_api":
        if terminal["cost_status"] not in {"ACTUAL", "ASSESSED", "ESTIMATED", "PENDING"}:
            limitations.append("API_COST_STATUS_INVALID")
        if terminal["actual_cost"] is None and terminal["cost_status"] in {"ACTUAL", "ASSESSED"}:
            limitations.append("API_ACTUAL_COST_MISSING")
    else:
        limitations.append("TERMINAL_CHANNEL_KIND_INVALID")
    if not isinstance(terminal["latency_ms"], int) or isinstance(terminal["latency_ms"], bool) or terminal["latency_ms"] < 0:
        limitations.append("LATENCY_INVALID")
    returned = terminal["returned_model"]
    identity_verdict = (
        "PROVIDER_NOT_EXPOSED"
        if returned == "PROVIDER_NOT_EXPOSED"
        else "PASS"
        if returned == presend_receipt.get("requested_model")
        else "FAIL"
    )
    accounting_verdict = "PASS" if not limitations else "NOT_ASSESSED"
    value = {
        "schema_version": "PageRecognitionVisionTerminalEvidence-v1",
        "presend_receipt_id": presend_receipt.get("receipt_id"),
        "attempt_id": presend_receipt.get("attempt_id"),
        "page_id": presend_receipt.get("page_id"),
        "candidate_key": presend_receipt.get("candidate_key"),
        "presend_receipt_sha256": receipt_sha256,
        "terminal": _copy(terminal),
        "identity_verdict": identity_verdict,
        "accounting_verdict": accounting_verdict,
        "resource_verdict": "PASS" if terminal["terminal_state"] == "completed" else "FAIL",
        "limitation_codes": sorted(set(limitations)),
    }
    value["terminal_evidence_sha256"] = canonical_sha256(value)
    return value

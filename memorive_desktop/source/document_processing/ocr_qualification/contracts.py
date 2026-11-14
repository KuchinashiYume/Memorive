"""Fail-closed contracts for PAGE-RECOGNITION page qualification.

The module validates local objects only.  It never opens a provider channel,
reads a credential, selects a production route, or authorizes activation.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator


PAGE_FAMILIES = frozenset(
    {
        "digital",
        "clean_scan",
        "degraded_scan",
        "multicolumn",
        "role_dense",
        "formula",
        "table_numeric",
        "chart_mixed",
    }
)
PAGE_MODIFIERS = frozenset(
    {"blur", "skew", "low_contrast", "dense_symbols", "small_font", "rotated_region"}
)
OUTPUT_LANES = frozenset(
    {"LITERAL_OCR", "DOCUMENT_STRUCTURE", "VISUAL_SEMANTICS_REVIEW"}
)
ROLE_ELIGIBILITIES = frozenset(
    {"PRIMARY", "RESCUE", "REVIEW_ONLY", "HUMAN_ONLY", "KEEP_DISABLED", "NOT_ASSESSED"}
)
CONFIDENCE_STATES = frozenset({"ASSERTED", "UNCERTAIN", "ILLEGIBLE"})
SAFE_OWNERSHIP = frozenset({"self", "public-safe"})
SHA256_RE = re.compile(r"^[0-9A-F]{64}$")

_SCHEMA_FILES = {
    "page_subject_input": "page_recognition_page_subject_input_v1.schema.json",
    "normalized_page_transcription": "page_recognition_normalized_page_transcription_v1.schema.json",
    "page_family_qualification": "page_recognition_page_family_qualification_v1.schema.json",
}


class OCRQualificationContractError(ValueError):
    """Structured Retrieval contract violation."""

    def __init__(self, code: str, details: Sequence[str] | None = None):
        self.code = code
        self.details = tuple(details or ())
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise OCRQualificationContractError("CANONICAL_JSON_INVALID", [str(exc)]) from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def immutable_json_copy(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value.upper()):
        raise OCRQualificationContractError("SHA256_INVALID", [field])
    return value.upper()


@lru_cache(maxsize=None)
def load_schema(contract: str) -> dict[str, Any]:
    filename = _SCHEMA_FILES.get(contract)
    if filename is None:
        raise OCRQualificationContractError("SCHEMA_NAME_UNKNOWN", [contract])
    path = Path(__file__).resolve().parents[1] / "schemas" / filename
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _schema_validate(value: Mapping[str, Any], contract: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OCRQualificationContractError("OBJECT_TYPE_INVALID", [contract])
    accepted = immutable_json_copy(value)
    errors = sorted(
        Draft202012Validator(load_schema(contract)).iter_errors(accepted),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        details = []
        for error in errors[:20]:
            location = "/".join(str(item) for item in error.absolute_path) or "$"
            details.append(f"{location}: {error.message}")
        raise OCRQualificationContractError("SCHEMA_INVALID", details)
    return accepted


def validate_page_subject_input(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _schema_validate(value, "page_subject_input")
    ownership = accepted["data_ownership"]
    rights = accepted["rights_custody_verdict"]
    visibility = accepted["provider_visibility"]
    expected_rights = {
        "self": "ALLOWED_SELF",
        "public-safe": "ALLOWED_PUBLIC_SAFE",
        "entrusted": "BLOCKED",
        "unknown": "BLOCKED",
    }[ownership]
    if rights != expected_rights:
        raise OCRQualificationContractError(
            "RIGHTS_OWNERSHIP_MISMATCH", [ownership, rights]
        )
    if ownership not in SAFE_OWNERSHIP and visibility != "BLOCKED":
        raise OCRQualificationContractError("PROVIDER_VISIBILITY_FORBIDDEN", [ownership])

    candidate = accepted["candidate"]
    expected_channel = {
        "deepseek_ocr_specialist_baseline": "usage_based_api",
        "claude_api_visual": "usage_based_api",
        "openai_api_visual": "usage_based_api",
        "openai_subscription_cli_visual": "subscription_cli",
        "local_visual_adapter": "local_resource",
    }[candidate["candidate_key"]]
    if candidate["channel_kind"] != expected_channel:
        raise OCRQualificationContractError(
            "CANDIDATE_CHANNEL_KIND_MISMATCH",
            [candidate["candidate_key"], candidate["channel_kind"]],
        )

    if accepted["external_request_authorized"]:
        if ownership not in SAFE_OWNERSHIP:
            raise OCRQualificationContractError("OWNERSHIP_BLOCKED_BEFORE_IMAGE_READ")
        if visibility != "SINGLE_PAGE_IMAGE_ONLY":
            raise OCRQualificationContractError("SINGLE_PAGE_VISIBILITY_REQUIRED")
        required_candidate = ("profile_id", "requested_model", "route", "region", "egress")
        missing = [field for field in required_candidate if candidate.get(field) in (None, "")]
        if accepted.get("authorization_ref") in (None, ""):
            missing.append("authorization_ref")
        if candidate["channel_kind"] == "usage_based_api" and accepted.get(
            "credential_env_name"
        ) in (None, ""):
            missing.append("credential_env_name")
        if candidate["channel_kind"] == "usage_based_api" and accepted.get(
            "token_ceiling"
        ) is None:
            missing.append("token_ceiling")
        if missing:
            raise OCRQualificationContractError(
                "EXTERNAL_ENVELOPE_INCOMPLETE", sorted(set(missing))
            )
    elif candidate["channel_kind"] == "local_resource" and ownership not in SAFE_OWNERSHIP:
        # Local execution can be authorized by a different future custody profile, but
        # this Retrieval contract never silently upgrades a blocked page.
        raise OCRQualificationContractError("LOCAL_CUSTODY_PROFILE_NOT_BOUND")
    return accepted


def validate_normalized_page_transcription(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    accepted = _schema_validate(value, "normalized_page_transcription")
    blocks = accepted["ordered_blocks"]
    block_ids = [block["block_id"] for block in blocks]
    reading_order = [block["reading_order"] for block in blocks]
    if len(block_ids) != len(set(block_ids)):
        raise OCRQualificationContractError("BLOCK_ID_DUPLICATE")
    if len(reading_order) != len(set(reading_order)):
        raise OCRQualificationContractError("READING_ORDER_DUPLICATE")
    if reading_order != sorted(reading_order):
        raise OCRQualificationContractError("READING_ORDER_NOT_SORTED")
    known_blocks = set(block_ids)
    for item in accepted["critical_items"]:
        if item["block_id"] not in known_blocks:
            raise OCRQualificationContractError(
                "CRITICAL_BLOCK_REF_UNKNOWN", [item["block_id"]]
            )
        if item["confidence_state"] == "ILLEGIBLE" and item["value"] != "[ILLEGIBLE]":
            raise OCRQualificationContractError("ILLEGIBLE_VALUE_MUST_USE_MARKER")
    for item in accepted["illegible_spans"]:
        if item["block_id"] not in known_blocks:
            raise OCRQualificationContractError(
                "ILLEGIBLE_BLOCK_REF_UNKNOWN", [item["block_id"]]
            )
    table_keys = [
        (item["table_id"], item["row"], item["column"])
        for item in accepted["table_cells"]
    ]
    if len(table_keys) != len(set(table_keys)):
        raise OCRQualificationContractError("TABLE_CELL_DUPLICATE")
    return accepted


def validate_page_family_qualification(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    accepted = _schema_validate(value, "page_family_qualification")
    role = accepted["role_eligibility"]
    if role in {"PRIMARY", "RESCUE"}:
        failures = [
            field
            for field in (
                "quality_verdict",
                "identity_verdict",
                "accounting_verdict",
                "resource_verdict",
            )
            if accepted[field] != "PASS"
        ]
        if failures:
            raise OCRQualificationContractError(
                "ROUTABLE_ROLE_HARD_GATE_INCOMPLETE", failures
            )
    if role == "NOT_ASSESSED" and accepted["sample_denominator"] != 0:
        raise OCRQualificationContractError(
            "NOT_ASSESSED_DENOMINATOR_MUST_BE_ZERO"
        )
    if accepted["sample_denominator"] == 0 and role not in {
        "NOT_ASSESSED",
        "KEEP_DISABLED",
    }:
        raise OCRQualificationContractError("ZERO_DENOMINATOR_ROLE_INVALID", [role])
    return accepted


def schema_hash(contract: str) -> str:
    return canonical_sha256(deepcopy(load_schema(contract)))

"""Shared by every workflow exam family: schemas, the primary-run workload profile, file and JSON helpers.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
import unicodedata
from memorive_settings.exam_successor import Scorability


PLAN_SCHEMA = "WorkflowModelExamPlan-v2"


AUTHORIZATION_SCHEMA = "WorkflowModelExamAuthorization-v1"


RESULT_SCHEMA = "SettingsModelValidationResult-v1"


EXECUTOR_REF = "MODEL_EVALUATION_MODEL_EVALUATION_CARD_DISTILLER_EXECUTOR_R1_3"


MAX_OUTPUT_TOKENS = 32768  # frozen PRICING snapshot identity only; never sent as a task cap


REQUEST_TIMEOUT_SECONDS = 300


SAFE_PACKAGE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")


Core_PRIMARY_WORKLOAD_PROFILE_REVISION = (
    "Desktop_Core_ACTUAL_CARD_ANALYSIS_MEAN_X2_V1"
)


Core_PRIMARY_WORKLOAD_MEASUREMENT_SHA256 = (
    "7920FEE97BB1D1AD618AFFE93155C1FF9A3F5E3F6F049822C88D909A54C57C1E"
)


Core_PRIMARY_WORKLOAD_CLOSEOUT_SHA256 = (
    "65606B32990005793E563D9C2B0B7AA5CF96CA3C7CD56FA7A2440CB4B22418A8"
)


Core_PRIMARY_WORKLOAD_MULTIPLIER = 2.0


Core_PRIMARY_CARD_COUNT = 19


Core_PRIMARY_CARD_MEAN_CHARS = 22_017.157895


Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT = 44_035


Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT = 45_769


Core_PRIMARY_SOURCE_MEAN_CHARS = 68_748.315789


Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT = 137_497


Core_PRIMARY_ANALYSIS_COUNT = 8


Core_PRIMARY_ANALYSIS_MEAN_CHARS = 6_752.75


Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT = 13_506


Core_PRIMARY_ANALYSIS_CONTEXT_MEAN_TOKENS = 2_308.0


Core_PRIMARY_ANALYSIS_CONTEXT_TOKEN_LIMIT = 4_616


Core_PRIMARY_ANALYSIS_PROMPT_MEAN_TOKENS = 3_289.875


Core_PRIMARY_ANALYSIS_PROMPT_TOKEN_LIMIT = 6_580


Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT = 26_320


Core_PRIMARY_ANALYSIS_VISIBLE_TOKEN_LIMIT = 4_205


Core_PRIMARY_ANALYSIS_COMPLETION_TOKEN_LIMIT = 12_065


PRIMARY_MODEL_FAIL_POLICY_REVISION = (
    "Desktop_PRIMARY_MODEL_FAIL_THREE_PANEL_CORROBORATED_MATERIAL_RESIDUAL_V3_"
    "CATEGORY_CALIBRATION"
)


_STRUCTURED_PROMPT_SCHEMA_PREAMBLE = (
    "Return exactly one JSON object. Do not wrap it in Markdown. "
    "The object must conform to this JSON Schema:\n"
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def core_primary_workload_profile() -> dict[str, Any]:
    """Return aggregate-only Core Card/Analysis workload evidence.

    The EXE carries measurements and hashes, not the underlying PDFs, source
    texts, Cards, Analyses, prompts, or provider-visible evidence.  Each hard
    capacity bound is exactly 200% of the matching observed Core mean.
    """

    profile = {
        "schema_version": Core_PRIMARY_WORKLOAD_PROFILE_REVISION,
        "measurement_sha256": Core_PRIMARY_WORKLOAD_MEASUREMENT_SHA256,
        "formal_closeout_sha256": Core_PRIMARY_WORKLOAD_CLOSEOUT_SHA256,
        "multiplier": Core_PRIMARY_WORKLOAD_MULTIPLIER,
        "card_distiller": {
            "actual_card_count": Core_PRIMARY_CARD_COUNT,
            "actual_card_mean_chars": Core_PRIMARY_CARD_MEAN_CHARS,
            "source_mean_chars": Core_PRIMARY_SOURCE_MEAN_CHARS,
            "source_input_chars_ceiling": (
                Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT
            ),
            "final_card_chars_ceiling": (
                Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT
            ),
            "final_card_utf8_bytes_ceiling": (
                Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT
            ),
            "sample_case_count_per_form": 63,
            "sample_pack_count_per_form": 7,
            "sample_rotation_slots": 8,
        },
        "analysis_primary": {
            "actual_analysis_count": Core_PRIMARY_ANALYSIS_COUNT,
            "actual_analysis_mean_chars": Core_PRIMARY_ANALYSIS_MEAN_CHARS,
            "context_pack_mean_tokens": (
                Core_PRIMARY_ANALYSIS_CONTEXT_MEAN_TOKENS
            ),
            "context_pack_tokens_ceiling": (
                Core_PRIMARY_ANALYSIS_CONTEXT_TOKEN_LIMIT
            ),
            "provider_prompt_mean_tokens": (
                Core_PRIMARY_ANALYSIS_PROMPT_MEAN_TOKENS
            ),
            "provider_prompt_tokens_ceiling": (
                Core_PRIMARY_ANALYSIS_PROMPT_TOKEN_LIMIT
            ),
            "prompt_char_surrogate_ceiling": (
                Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
            ),
            "visible_analysis_chars_ceiling": (
                Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
            ),
            "visible_answer_tokens_ceiling": (
                Core_PRIMARY_ANALYSIS_VISIBLE_TOKEN_LIMIT
            ),
            "completion_tokens_including_thinking_ceiling": (
                Core_PRIMARY_ANALYSIS_COMPLETION_TOKEN_LIMIT
            ),
            "sample_case_count_per_form": 6,
            "sample_family_count_per_form": 6,
            "transport_target_count": 6,
            "sample_rotation_slots": 12,
        },
        "capacity_overflow_scorability": Scorability.NOT_ASSESSED.value,
        "cross_category_comparison_forbidden": True,
        "underlying_private_payload_embedded": False,
    }
    profile["profile_sha256"] = _sha256(profile)
    return profile


def _primary_model_fail_projection(
    *,
    category_id: str,
    score: float,
    raw_quality_verdict: str,
    blocking_failures: list[dict[str, Any]],
    guarded_operational_verdict: str,
) -> dict[str, Any]:
    """Separate a bad item from an established whole-model failure.

    A single material residual remains visible and scoreable, but cannot by
    itself label the model FAIL.  The same completed run must contain at least
    two distinct material residual cases.  Aggregate-only or calibration-only
    misses never satisfy that corroboration rule.
    """

    material_case_ids = sorted(
        {
            str(row.get("case_id"))
            for row in blocking_failures
            if isinstance(row, Mapping)
            and isinstance(row.get("case_id"), str)
            and row.get("case_id")
            and row.get("model_fail_materiality") != "CALIBRATION"
            and any(
                isinstance(reason, str)
                and reason
                and reason
                not in {
                    "CASE_SCORE_BELOW_100",
                    "aggregate_score_or_repair_burden",
                    "aggregate_score_hard_gate_or_repair_burden",
                }
                for reason in (row.get("failed_fields") or [])
            )
        }
    )
    calibration_case_ids = sorted(
        {
            str(row.get("case_id"))
            for row in blocking_failures
            if isinstance(row, Mapping)
            and isinstance(row.get("case_id"), str)
            and row.get("case_id")
            and str(row.get("case_id")) not in material_case_ids
        }
    )
    model_fail_established = (
        raw_quality_verdict == "FAIL" and len(material_case_ids) >= 2
    )
    if model_fail_established:
        operational = "REJECT"
        model_fail_verdict = "FAIL"
    elif raw_quality_verdict == "PASS":
        operational = "SUITABLE"
        model_fail_verdict = "PASS"
    elif material_case_ids:
        operational = guarded_operational_verdict
        model_fail_verdict = "NOT_ESTABLISHED"
    elif calibration_case_ids:
        operational = "SUITABLE_WITH_CALIBRATION"
        model_fail_verdict = "NOT_ESTABLISHED"
    else:
        operational = "NOT_RECOMMENDED"
        model_fail_verdict = "NOT_ESTABLISHED"
    return {
        "model_fail_policy_revision": PRIMARY_MODEL_FAIL_POLICY_REVISION,
        "model_fail_verdict": model_fail_verdict,
        "model_fail_established": model_fail_established,
        "model_fail_minimum_distinct_material_cases": 2,
        "material_residual_case_ids": material_case_ids,
        "calibration_residual_case_ids": calibration_case_ids,
        "operational_eligibility_verdict": operational,
        "category_id": category_id,
        "category_local_score": round(float(score), 2),
        "numeric_score_is_not_model_fail_verdict": True,
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
    }


def _effective_structured_prompt_metrics(
    prompt: str,
    response_schema: Mapping[str, Any],
) -> dict[str, int]:
    schema_text = json.dumps(
        response_schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    effective = (
        f"{prompt.rstrip()}\n\n"
        f"{_STRUCTURED_PROMPT_SCHEMA_PREAMBLE}{schema_text}"
    )
    return {
        "prompt_chars": len(prompt),
        "response_schema_chars": len(schema_text),
        "effective_prompt_chars": len(effective),
        "effective_prompt_utf8_bytes": len(effective.encode("utf-8")),
    }


def _serialized_request(request: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(request),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json_create_only(path: Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(value) + b"\n"
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"WORKFLOW_MODEL_EXAM_JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _public_forms(root: Path) -> dict[str, Any]:
    # Only the already verified public question forms; never inspect Gold.
    return {key: _load_json(root / 'forms' / f'form_{key}.json') for key in ('A', 'B')}


def _compact(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _not_available(reason: str) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA,
        "status": "NOT_AVAILABLE",
        "mode": "NONE",
        "reason": reason,
        "external_network_calls": 0,
        "provider_calls": 0,
        "external_model_calls": 0,
    }


def _not_run(reason: str, *, requested_model: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA,
        "kind": "WORKFLOW_NODE",
        "status": "NOT_RUN",
        "score": None,
        "reason": reason,
        "requested_model": requested_model,
        "returned_model": None,
        "duration_ms": 0,
        "external_network_calls": 0,
        "provider_calls": 0,
        "external_model_calls": 0,
        "external_process_launches": 0,
        "reference_regression_only": True,
        "qualification_eligible": False,
    }

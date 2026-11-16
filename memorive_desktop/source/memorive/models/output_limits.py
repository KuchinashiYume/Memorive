"""How large a model reply may be, and the description of that rule that run records carry.

No reply is cut at a size the app guesses: a request asks for the limit the model, provider and
runtime all support, or leaves the provider's default. WORKFLOW_EXAM_REPLY_TOKEN_CEILING is ten times
the largest reply measured from a strong model. Model validation (memorive_settings/model_validation.py)
refuses an OCR request that asks for more than it and notes when a workflow-exam chat reply goes past
it; the exam executors in memorive_settings record it with their results.
workflow_exam_output_limit_contract() describes the whole rule and is stored with every workflow-exam
request and result. The names say "workflow exam" because that exam measured the figure.
"""
from __future__ import annotations

from typing import Any

from memorive.platform.json_files import canonical_sha256

WORKFLOW_EXAM_OUTPUT_LIMIT_POLICY_REVISION = (
    "Desktop_MODEL_CAPACITY_WITH_OPUS_X10_PLANNING_REFERENCE_V2"
)
WORKFLOW_EXAM_OUTPUT_CALIBRATION_MODEL = "claude-opus-5"
WORKFLOW_EXAM_OUTPUT_CALIBRATION_MAX_COMPLETION_TOKENS = 17_303
WORKFLOW_EXAM_OUTPUT_CALIBRATION_MULTIPLIER = 10
WORKFLOW_EXAM_OUTPUT_CALIBRATION_CAMPAIGN_FILE_SHA256 = (
    "BA5116A3AF8F354477535F48F484FF2CE7959473F21A188A1502D6A21D4C0476"
)
WORKFLOW_EXAM_OUTPUT_CALIBRATION_CAMPAIGN_CANONICAL_SHA256 = (
    "D20B3A8BF92D4F1843C91C0789AF1A0C7F0D31B0D375FF450C4532C73C09A16F"
)
WORKFLOW_EXAM_REPLY_TOKEN_CEILING = (
    WORKFLOW_EXAM_OUTPUT_CALIBRATION_MAX_COMPLETION_TOKENS
    * WORKFLOW_EXAM_OUTPUT_CALIBRATION_MULTIPLIER
)
WORKFLOW_EXAM_TRANSPORT_CAPTURE_BYTES = 32 * 1024 * 1024


def workflow_exam_output_limit_contract() -> dict[str, Any]:
    """Describe the adopted capacity policy without imposing calibration as a cap."""

    value = {
        "schema_version": WORKFLOW_EXAM_OUTPUT_LIMIT_POLICY_REVISION,
        "calibration_model": WORKFLOW_EXAM_OUTPUT_CALIBRATION_MODEL,
        "calibration_model_basis": (
            "CURRENT_STRONG_MODEL_OPUS5_MAX_ADAPTIVE_MAX"
        ),
        "calibration_horizon_years": 5,
        "calibration_rationale": (
            "TENFOLD_HEADROOM_OVER_STRONG_MODEL_ACTUAL_PEAK_COVERS_"
            "REASONING_GROWTH_WITHOUT_EXPECTING_TENFOLD_OUTPUT_EXPANSION"
        ),
        "calibration_usage_semantics": (
            "PROVIDER_REPORTED_COMPLETION_TOKENS_INCLUDING_REASONING"
        ),
        "calibration_campaign_file_sha256": (
            WORKFLOW_EXAM_OUTPUT_CALIBRATION_CAMPAIGN_FILE_SHA256
        ),
        "calibration_campaign_canonical_sha256": (
            WORKFLOW_EXAM_OUTPUT_CALIBRATION_CAMPAIGN_CANONICAL_SHA256
        ),
        "calibration_max_actual_completion_tokens": (
            WORKFLOW_EXAM_OUTPUT_CALIBRATION_MAX_COMPLETION_TOKENS
        ),
        "calibration_multiplier": WORKFLOW_EXAM_OUTPUT_CALIBRATION_MULTIPLIER,
        "calibrated_reply_token_ceiling": WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
        "request_limit_rule": (
            "VERIFIED_MODEL_PROVIDER_RUNTIME_CAPACITY_INTERSECTION"
        ),
        "undeclared_api_capacity_policy": "PROVIDER_DEFAULT_NO_APP_GUESSED_CAP",
        "cli_capacity_policy": "PROVIDER_DEFAULT_NO_SUPPORTED_REPLY_LIMIT_FLAG",
        "local_undeclared_capacity_policy": (
            "RUNTIME_MANAGED_NO_GUESSED_CONTEXT_OR_OUTPUT_CAP"
        ),
        "local_adapter_additional_reply_ceiling": None,
        "provider_default_allowed_when_explicit_limit_unsupported": True,
        "transport_capture_bytes": None,
        "planning_reference_is_hard_limit": False,
        "transport_capture_semantics": (
            "NO_IMPLICIT_BODY_OR_PROCESS_CAPTURE_QUOTA"
        ),
        "length_only_answer_invalidation_forbidden": True,
        "length_only_semantic_repair_forbidden": True,
        "physical_limit_outcome": "NOT_ASSESSED",
        "complete_answer_preservation_required": True,
    }
    value["contract_sha256"] = canonical_sha256(value)
    return value

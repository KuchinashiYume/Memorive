from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence


CONTRACT_REVISION = "Desktop_EXAM_SUCCESSOR_CONTRACT_V1"
REPAIR_LIFECYCLE_REVISION = "Desktop_TWO_ROUND_RESIDUAL_REPAIR_V1"
FAIL_ADMISSION_REVISION = "Desktop_EVIDENCE_BOUND_FAIL_ADMISSION_V2"
CACHE_BINDING_REVISION = "Desktop_CATEGORY_COHORT_SCORE_CACHE_V1"
ANALYSIS_PANEL_COHORT_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_THREE_PANEL_AGGREGATE_BEFORE_TAIL_V1"
)
ANALYSIS_REPAIR_MATERIALITY_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_MATERIAL_BELOW_80_OR_CRITICAL_V1"
)
ANALYSIS_REPAIR_ACCEPTANCE_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_CASE_LOCAL_NO_REGRESSION_MERGE_V1"
)
ANALYSIS_COHORT_FAIL_POLICY_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_TWO_CASE_TWO_PANEL_TWO_FAMILY_FAIL_V1"
)
ANALYSIS_MATERIAL_SCORE_THRESHOLD_EXCLUSIVE = 80.0
ANALYSIS_MINIMUM_PANEL_COUNT = 3
ANALYSIS_CASES_PER_FORM_PER_PANEL = 6
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

_SHA256 = re.compile(r"^[A-F0-9]{64}$")
_DESTRUCTIVE_ACTIONS = frozenset(
    {
        "DOWNGRADE",
        "TOMBSTONE",
        "DELETE",
        "LOGICAL_DELETE",
        "REJECT",
        "QUARANTINE",
    }
)
_GOLD_KEYS = frozenset(
    {
        "gold",
        "golden",
        "answer_key",
        "expected_answer",
        "expected_output",
        "reference_answer",
        "private_holdout",
    }
)


class ExecutionOutcome(str, Enum):
    COMPLETED = "COMPLETED"
    AUTH_ERROR = "AUTH_ERROR"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    CAPACITY = "CAPACITY"
    TIMEOUT = "TIMEOUT"
    SAFETY_REFUSAL = "SAFETY_REFUSAL"
    PROCESS_FAILURE = "PROCESS_FAILURE"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    PACK_UNAVAILABLE = "PACK_UNAVAILABLE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class Scorability(str, Enum):
    SCOREABLE = "SCOREABLE"
    MECHANICALLY_RECOVERABLE = "MECHANICALLY_RECOVERABLE"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"
    NOT_ASSESSED = "NOT_ASSESSED"


class TerminalDisposition(str, Enum):
    CLOSED = "CLOSED"
    SUITABLE_WITH_CALIBRATION = "SUITABLE_WITH_CALIBRATION"
    SUITABLE_WITH_GOVERNED_DISPOSITION = (
        "SUITABLE_WITH_GOVERNED_DISPOSITION"
    )
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    NOT_RECOMMENDED = "NOT_RECOMMENDED"
    NOT_ASSESSED = "NOT_ASSESSED"


@dataclass(frozen=True)
class CategoryBinding:
    category_id: str
    role_id: str
    reference_pack_id: str
    reference_pack_revision: str
    reference_pack_sha256: str
    scoring_protocol_revision: str
    scoring_protocol_sha256: str
    scoring_projection_revision: str
    executor_ref: str
    sample_revision: str

    def as_dict(self) -> dict[str, str]:
        value = {
            "category_id": self.category_id,
            "role_id": self.role_id,
            "reference_pack_id": self.reference_pack_id,
            "reference_pack_revision": self.reference_pack_revision,
            "reference_pack_sha256": self.reference_pack_sha256,
            "scoring_protocol_revision": self.scoring_protocol_revision,
            "scoring_protocol_sha256": self.scoring_protocol_sha256,
            "scoring_projection_revision": self.scoring_projection_revision,
            "executor_ref": self.executor_ref,
            "sample_revision": self.sample_revision,
        }
        _validate_binding(value)
        return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


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


def workflow_exam_structured_output_policy(
    target: Mapping[str, Any],
    *,
    local_declared_field: str = "exam_max_output_tokens",
    local_default_context_tokens: int = 65_536,
) -> tuple[dict[str, Any], int | None, str]:
    """Use the adopted capacity capacity resolver; calibration is metadata.

    Legacy exam allocations and the five-year reference are not wire limits.
    Optional API limits stay provider-managed unless a verified capacity or a
    user limit is explicitly bound. Unknown local runtime capacity is not
    replaced with a guessed context. Mandatory protocol fields still use the
    verified provider limit.
    """
    from model_gateway.resource_limits import resolve_call_limits

    kind = str(target.get("kind") or "")
    if kind not in {"API", "CLI", "LOCAL"}:
        raise ValueError("WORKFLOW_EXAM_PROFILE_KIND_INVALID")
    binding = deepcopy(dict(target))
    for field in ("exam_max_output_tokens", "provider_max_output_tokens", local_declared_field):
        value = target.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValueError(f"{kind}_EXAM_OUTPUT_LIMIT_PROFILE_INVALID")
    limits = resolve_call_limits(profile_kind=kind, service=target, model=target)
    allocation = limits["max_output_tokens"]
    if kind == "API":
        mandatory = target.get("api_requires_explicit_max_output_tokens") is True
        # The Messages protocol requires max_tokens even for adaptive thinking.
        mandatory = mandatory or str(target.get("model_name") or "").startswith("claude-")
        explicit_capacity = target.get("provider_max_output_tokens") is not None
        user_limit = limits["capacity"]["user_output_limit_tokens"] is not None
        if not mandatory and not explicit_capacity and not user_limit:
            allocation = None
        if mandatory and allocation is None:
            raise ValueError("API_EXAM_REQUIRED_OUTPUT_CAPACITY_UNKNOWN")
    mode = "PROVIDER_DEFAULT" if allocation is None else "MODEL_PROFILE_EXPLICIT"
    binding["max_output_tokens_mode"] = "PROVIDER_DEFAULT" if allocation is None else "EXPLICIT"
    return binding, allocation, mode


def _reason_text(result: Mapping[str, Any]) -> str:
    fields = (
        result.get("reason"),
        result.get("error"),
        result.get("status"),
        result.get("finish_reason"),
    )
    return " ".join(str(value) for value in fields if value is not None).upper()


def classify_execution_outcome(result: Mapping[str, Any]) -> ExecutionOutcome:
    """Classify transport/execution independently from model quality.

    A caller may supply an explicit `execution_outcome`; otherwise the narrow
    reason-code vocabulary is used.  Unknown failures do not become quality
    failures.
    """

    explicit = result.get("execution_outcome")
    if isinstance(explicit, str):
        try:
            return ExecutionOutcome(explicit)
        except ValueError:
            pass
    status = str(result.get("status") or "").upper()
    if status in {"COMPLETED", "PASS", "FAIL", "SCORED"}:
        return ExecutionOutcome.COMPLETED
    reason = _reason_text(result)
    ordered_patterns: tuple[tuple[ExecutionOutcome, tuple[str, ...]], ...] = (
        (ExecutionOutcome.AUTH_ERROR, ("AUTH", "UNAUTHORIZED", "FORBIDDEN", "API_KEY")),
        (ExecutionOutcome.RATE_LIMIT, ("RATE_LIMIT", "TOO_MANY_REQUESTS", "HTTP_429")),
        (ExecutionOutcome.CAPACITY, ("CAPACITY", "OVERLOADED", "NO_CREDIT", "BALANCE")),
        (ExecutionOutcome.TIMEOUT, ("TIMEOUT", "TIMED_OUT")),
        (ExecutionOutcome.SAFETY_REFUSAL, ("SAFETY_REFUSAL", "CONTENT_FILTER", "POLICY_REFUSAL")),
        (ExecutionOutcome.OUTPUT_TRUNCATED, ("TRUNCATED", "LENGTH_LIMIT", "MAX_TOKENS")),
        (ExecutionOutcome.EMPTY_RESPONSE, ("EMPTY_RESPONSE", "RESPONSE_EMPTY")),
        (
            ExecutionOutcome.INVALID_RESPONSE,
            (
                "INVALID_RESPONSE",
                "RESPONSE_INVALID",
                "INVALID_JSON",
                "SCHEMA_INVALID",
                "NOT_SCOREABLE",
            ),
        ),
        (ExecutionOutcome.CAPABILITY_MISMATCH, ("CAPABILITY_MISMATCH", "UNSUPPORTED_CAPABILITY")),
        (ExecutionOutcome.PACK_UNAVAILABLE, ("PACK_UNAVAILABLE", "ASSETS_INVALID", "NOT_AVAILABLE")),
        (ExecutionOutcome.PROCESS_FAILURE, ("PROCESS_FAILURE", "NONZERO_EXIT", "SPAWN_FAILED")),
        (ExecutionOutcome.TRANSPORT_ERROR, ("NETWORK", "TRANSPORT", "HTTP_5", "CONNECTION")),
    )
    for outcome, needles in ordered_patterns:
        if any(needle in reason for needle in needles):
            return outcome
    return ExecutionOutcome.UNKNOWN_ERROR


def determine_scorability(
    *,
    execution_outcome: ExecutionOutcome | str,
    response_complete: bool,
    schema_valid: bool,
    exact_case_set: bool,
    deterministic_mechanical_repair_available: bool = False,
) -> Scorability:
    outcome = ExecutionOutcome(execution_outcome)
    if outcome is not ExecutionOutcome.COMPLETED:
        return Scorability.NOT_ASSESSED
    if not response_complete:
        return Scorability.PARTIAL
    if not schema_valid:
        return (
            Scorability.MECHANICALLY_RECOVERABLE
            if deterministic_mechanical_repair_available
            else Scorability.INVALID
        )
    if not exact_case_set:
        return Scorability.INVALID
    return Scorability.SCOREABLE


def clean_control_residual(
    *,
    expected_verdict: str,
    observed_verdict: str,
    observed_action: str | None,
) -> dict[str, Any]:
    """Separate conservative clean-case reviews from destructive false positives."""

    if expected_verdict.upper() != "PASS" or observed_verdict.upper() == "PASS":
        return {
            "classification": "NONE",
            "safety_blocking": False,
            "reason_code": None,
        }
    action = str(observed_action or "").upper()
    if action in _DESTRUCTIVE_ACTIONS:
        return {
            "classification": "SAFETY_BLOCKING",
            "safety_blocking": True,
            "reason_code": "DESTRUCTIVE_FALSE_POSITIVE_ON_CLEAN_CONTROL",
        }
    return {
        "classification": "CALIBRATION",
        "safety_blocking": False,
        "reason_code": "CONSERVATIVE_FALSE_POSITIVE_ON_CLEAN_CONTROL",
    }


def _contains_gold_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in _GOLD_KEYS or _contains_gold_key(child):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_gold_key(item) for item in value)
    return False


def build_repair_round(
    *,
    round_number: int,
    category_id: str,
    targets_by_form: Mapping[str, Sequence[str]],
    prior_targets_by_form: Mapping[str, Sequence[str]] | None,
    prompt_projection: Mapping[str, Any],
    unchanged_surface_sha256: str,
) -> dict[str, Any]:
    if round_number not in {1, 2}:
        raise ValueError("SEMANTIC_REPAIR_ROUND_INVALID")
    if not category_id:
        raise ValueError("EXAM_CATEGORY_REQUIRED")
    if not _SHA256.fullmatch(unchanged_surface_sha256):
        raise ValueError("UNCHANGED_SURFACE_SHA256_INVALID")
    normalized = {
        str(form_id): [str(case_id) for case_id in case_ids]
        for form_id, case_ids in sorted(targets_by_form.items())
        if case_ids
    }
    if not normalized or any(
        len(case_ids) != len(set(case_ids)) for case_ids in normalized.values()
    ):
        raise ValueError("REPAIR_EXACT_SET_INVALID")
    if round_number == 2:
        if prior_targets_by_form is None:
            raise ValueError("ROUND2_PRIOR_EXACT_SET_REQUIRED")
        for form_id, case_ids in normalized.items():
            prior = {str(case_id) for case_id in prior_targets_by_form.get(form_id, [])}
            if not set(case_ids).issubset(prior):
                raise ValueError("ROUND2_MUST_BE_ROUND1_RESIDUAL_SUBSET")
    if _contains_gold_key(prompt_projection):
        raise ValueError("GOLD_OR_ANSWER_KEY_IN_PROVIDER_REPAIR_PROMPT")
    exact_set_sha256 = canonical_sha256(normalized)
    value = {
        "schema_version": REPAIR_LIFECYCLE_REVISION,
        "semantic_repair_round": round_number,
        "category_id": category_id,
        "wrong_item_exact_set": normalized,
        "wrong_item_exact_set_sha256": exact_set_sha256,
        "prior_wrong_item_exact_set_sha256": (
            canonical_sha256(
                {
                    str(form_id): [str(case_id) for case_id in case_ids]
                    for form_id, case_ids in sorted(prior_targets_by_form.items())
                    if case_ids
                }
            )
            if prior_targets_by_form is not None
            else None
        ),
        "prompt_projection": deepcopy(dict(prompt_projection)),
        "prompt_projection_sha256": canonical_sha256(prompt_projection),
        "unchanged_surface_sha256": unchanged_surface_sha256,
        "provider_receives_gold": False,
        "transport_retries_increment_semantic_round": False,
    }
    value["contract_sha256"] = canonical_sha256(value)
    return value


def cumulative_category_repair_penalty(
    rounds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Accumulate only caller-supplied, category-local Quality coefficients.

    `nonlinear_category_penalty` is the frozen category scorer's penalty.
    `fixed_penalty` and `volume_penalty` are that same category's existing
    directed-repair burdens.  Round 2 cannot erase Round 1 and therefore has a
    strictly larger cumulative burden whenever it is used.
    """

    if not rounds or len(rounds) > 2:
        raise ValueError("REPAIR_PENALTY_ROUND_COUNT_INVALID")
    category_ids = {str(row.get("category_id")) for row in rounds}
    if len(category_ids) != 1 or "" in category_ids:
        raise ValueError("CROSS_CATEGORY_REPAIR_COEFFICIENTS_FORBIDDEN")
    cumulative = 0.0
    output: list[dict[str, Any]] = []
    for index, row in enumerate(rounds, start=1):
        if row.get("round_number") != index:
            raise ValueError("REPAIR_PENALTY_ROUND_SEQUENCE_INVALID")
        values = []
        for field in (
            "nonlinear_category_penalty",
            "fixed_penalty",
            "volume_penalty",
        ):
            value = row.get(field, 0.0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError("REPAIR_PENALTY_VALUE_INVALID")
            values.append(float(value))
        round_penalty = sum(values)
        if round_penalty <= 0.0:
            raise ValueError("REPAIR_PENALTY_ROUND_MUST_BE_POSITIVE")
        previous = cumulative
        cumulative += round_penalty
        if index == 2 and not cumulative > previous:
            raise ValueError("ROUND2_CUMULATIVE_PENALTY_NOT_STRICTER")
        output.append(
            {
                "round_number": index,
                "round_penalty": round(round_penalty, 9),
                "cumulative_penalty": round(cumulative, 9),
            }
        )
    return {
        "schema_version": REPAIR_LIFECYCLE_REVISION,
        "category_id": next(iter(category_ids)),
        "rounds": output,
        "cumulative_penalty": round(cumulative, 9),
        "cross_category_coefficients_reused": False,
    }


def _analysis_detail_score(detail: Mapping[str, Any]) -> float:
    value = detail.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("ANALYSIS_DETAIL_SCORE_REQUIRED")
    numeric = float(value)
    if not 0.0 <= numeric <= 100.0:
        raise ValueError("ANALYSIS_DETAIL_SCORE_OUT_OF_RANGE")
    return numeric


def _analysis_critical_reasons(detail: Mapping[str, Any]) -> set[str]:
    value = detail.get("critical_reasons") or []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("ANALYSIS_DETAIL_CRITICAL_REASONS_INVALID")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("ANALYSIS_DETAIL_CRITICAL_REASONS_INVALID")
    return set(value)


def analysis_material_repair_targets(
    scores: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Separate genuinely material repair targets from ordinary calibration loss.

    Quality's original helper called every score below 100 a wrong item.  That is
    acceptable as a scoring diagnostic on 72 cases, but it is not a sensible
    provider repair trigger on a six-case transport panel.  A repair request is
    now limited to a critical scientific residual or a case below the role's
    operational 80-point boundary.  Non-perfect cases at or above 80 remain in
    the score and audit, but are not sent back to the model.
    """

    targets_by_form: dict[str, list[str]] = {}
    calibration_by_form: dict[str, list[str]] = {}
    classifications: list[dict[str, Any]] = []
    for form_id in ("A", "B"):
        form_score = scores.get(form_id)
        if not isinstance(form_score, Mapping):
            raise ValueError("ANALYSIS_FORM_SCORE_REQUIRED")
        details = form_score.get("details")
        if not isinstance(details, list):
            raise ValueError("ANALYSIS_FORM_DETAILS_REQUIRED")
        targets: list[str] = []
        calibration: list[str] = []
        observed: set[str] = set()
        for detail in details:
            if not isinstance(detail, Mapping):
                raise ValueError("ANALYSIS_DETAIL_MAPPING_REQUIRED")
            case_id = detail.get("case_id")
            if not isinstance(case_id, str) or not case_id or case_id in observed:
                raise ValueError("ANALYSIS_DETAIL_CASE_ID_INVALID")
            observed.add(case_id)
            score = _analysis_detail_score(detail)
            critical_reasons = sorted(_analysis_critical_reasons(detail))
            if critical_reasons:
                classification = "MATERIAL_CRITICAL"
                repair_eligible = True
            elif score < ANALYSIS_MATERIAL_SCORE_THRESHOLD_EXCLUSIVE:
                classification = "MATERIAL_BELOW_OPERATIONAL_BOUNDARY"
                repair_eligible = True
            elif score < 100.0:
                classification = "CALIBRATION_ONLY"
                repair_eligible = False
            else:
                classification = "NONE"
                repair_eligible = False
            if repair_eligible:
                targets.append(case_id)
            elif classification == "CALIBRATION_ONLY":
                calibration.append(case_id)
            classifications.append(
                {
                    "form_id": form_id,
                    "case_id": case_id,
                    "score": score,
                    "critical_reasons": critical_reasons,
                    "classification": classification,
                    "repair_eligible": repair_eligible,
                }
            )
        targets_by_form[form_id] = targets
        calibration_by_form[form_id] = calibration
    return {
        "schema_version": ANALYSIS_REPAIR_MATERIALITY_REVISION,
        "threshold_exclusive": ANALYSIS_MATERIAL_SCORE_THRESHOLD_EXCLUSIVE,
        "targets_by_form": targets_by_form,
        "calibration_only_by_form": calibration_by_form,
        "classifications": classifications,
        "nonperfect_is_automatically_repairable": False,
    }


def analysis_repair_acceptance(
    *,
    before_scores: Mapping[str, Mapping[str, Any]],
    candidate_scores: Mapping[str, Mapping[str, Any]],
    targets_by_form: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Accept repaired rows only when the same case improves without new criticals."""

    accepted_by_form: dict[str, list[str]] = {}
    rejected_by_form: dict[str, list[str]] = {}
    decisions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for form_id in ("A", "B"):
        before_details = before_scores.get(form_id, {}).get("details")
        candidate_details = candidate_scores.get(form_id, {}).get("details")
        if not isinstance(before_details, list) or not isinstance(candidate_details, list):
            raise ValueError("ANALYSIS_REPAIR_SCORE_DETAILS_REQUIRED")
        before_by_id = {
            str(detail.get("case_id")): detail
            for detail in before_details
            if isinstance(detail, Mapping) and isinstance(detail.get("case_id"), str)
        }
        candidate_by_id = {
            str(detail.get("case_id")): detail
            for detail in candidate_details
            if isinstance(detail, Mapping) and isinstance(detail.get("case_id"), str)
        }
        targets = [str(case_id) for case_id in targets_by_form.get(form_id, [])]
        if len(targets) != len(set(targets)):
            raise ValueError("ANALYSIS_REPAIR_TARGET_SET_INVALID")
        accepted_ids: list[str] = []
        rejected_ids: list[str] = []
        for case_id in targets:
            if case_id not in before_by_id or case_id not in candidate_by_id:
                raise ValueError("ANALYSIS_REPAIR_TARGET_SCORE_MISSING")
            before = before_by_id[case_id]
            candidate = candidate_by_id[case_id]
            before_score = _analysis_detail_score(before)
            candidate_score = _analysis_detail_score(candidate)
            before_critical = _analysis_critical_reasons(before)
            candidate_critical = _analysis_critical_reasons(candidate)
            new_critical = sorted(candidate_critical - before_critical)
            if new_critical:
                accepted = False
                reason = "NEW_CRITICAL_REASON_INTRODUCED"
            elif candidate_score <= before_score:
                accepted = False
                reason = "CASE_SCORE_NOT_IMPROVED"
            else:
                accepted = True
                reason = "CASE_SCORE_IMPROVED_NO_NEW_CRITICAL"
            row = {
                "form_id": form_id,
                "case_id": case_id,
                "before_score": before_score,
                "candidate_score": candidate_score,
                "score_delta": round(candidate_score - before_score, 6),
                "before_critical_reasons": sorted(before_critical),
                "candidate_critical_reasons": sorted(candidate_critical),
                "accepted": accepted,
                "reason_code": reason,
            }
            decisions.append(row)
            if accepted:
                accepted_ids.append(case_id)
            else:
                rejected_ids.append(case_id)
                rejected.append(deepcopy(row))
        accepted_by_form[form_id] = accepted_ids
        rejected_by_form[form_id] = rejected_ids
    return {
        "schema_version": ANALYSIS_REPAIR_ACCEPTANCE_REVISION,
        "accepted_by_form": accepted_by_form,
        "rejected_by_form": rejected_by_form,
        "decisions": decisions,
        "rejected": rejected,
        "candidate_repair_never_overwrites_better_case": True,
    }


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("ANALYSIS_AGGREGATE_MEAN_EMPTY")
    return sum(values) / len(values)


def build_analysis_panel_cohort(
    panels: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and aggregate frozen Analysis Primary transport panels.

    The returned raw score contains all panel cases before any tail or
    worst-family component is calculated.  A single panel is deliberately not
    a cacheable score.
    """

    if len(panels) < ANALYSIS_MINIMUM_PANEL_COUNT:
        raise ValueError("ANALYSIS_PANEL_COHORT_INCOMPLETE")
    normalized = [deepcopy(dict(panel)) for panel in panels]
    slots: list[int] = []
    panel_ids: list[str] = []
    for panel in normalized:
        slot = panel.get("sample_slot")
        panel_id = panel.get("panel_id")
        if isinstance(slot, bool) or not isinstance(slot, int) or slot < 1:
            raise ValueError("ANALYSIS_PANEL_SLOT_INVALID")
        if not isinstance(panel_id, str) or not panel_id:
            raise ValueError("ANALYSIS_PANEL_ID_INVALID")
        slots.append(slot)
        panel_ids.append(panel_id)
    if len(slots) != len(set(slots)):
        raise ValueError("ANALYSIS_PANEL_SLOT_DUPLICATED")
    if len(panel_ids) != len(set(panel_ids)):
        raise ValueError("ANALYSIS_PANEL_ID_DUPLICATED")
    normalized.sort(key=lambda panel: int(panel["sample_slot"]))

    basis_hashes = {
        canonical_sha256(panel.get("cohort_basis")) for panel in normalized
    }
    identity_hashes = {
        canonical_sha256(panel.get("model_identity")) for panel in normalized
    }
    if len(basis_hashes) != 1:
        raise ValueError("ANALYSIS_PANEL_COHORT_BASIS_MISMATCH")
    if len(identity_hashes) != 1:
        raise ValueError("ANALYSIS_PANEL_MODEL_IDENTITY_MISMATCH")

    aggregate_raw_scores: dict[str, dict[str, Any]] = {}
    difficulty_coverage: dict[str, dict[str, list[str]]] = {}
    selected_case_ids: dict[str, list[str]] = {}
    expected_family_set: set[str] | None = None
    for form_id in ("A", "B"):
        aggregate_details: list[dict[str, Any]] = []
        seen_case_ids: set[str] = set()
        family_difficulties: dict[str, set[str]] = {}
        form_case_ids: list[str] = []
        for panel in normalized:
            selection = panel.get("selection")
            raw_scores = panel.get("raw_scores")
            if not isinstance(selection, Mapping) or not isinstance(raw_scores, Mapping):
                raise ValueError("ANALYSIS_PANEL_PAYLOAD_INVALID")
            selected = selection.get(form_id)
            raw = raw_scores.get(form_id)
            if not isinstance(selected, list) or not isinstance(raw, Mapping):
                raise ValueError("ANALYSIS_PANEL_FORM_PAYLOAD_INVALID")
            details = raw.get("details")
            if not isinstance(details, list):
                raise ValueError("ANALYSIS_PANEL_RAW_DETAILS_REQUIRED")
            if (
                len(selected) != ANALYSIS_CASES_PER_FORM_PER_PANEL
                or len(details) != ANALYSIS_CASES_PER_FORM_PER_PANEL
            ):
                raise ValueError("ANALYSIS_PANEL_CASE_COUNT_INVALID")
            selected_ids: list[str] = []
            selected_families: list[str] = []
            selection_by_id: dict[str, Mapping[str, Any]] = {}
            for item in selected:
                if not isinstance(item, Mapping):
                    raise ValueError("ANALYSIS_PANEL_SELECTION_INVALID")
                case_id = item.get("case_id")
                family = item.get("family")
                difficulty = item.get("difficulty")
                if (
                    not isinstance(case_id, str)
                    or not case_id
                    or not isinstance(family, str)
                    or not family
                    or difficulty not in {"easy", "medium", "hard"}
                ):
                    raise ValueError("ANALYSIS_PANEL_SELECTION_INVALID")
                selected_ids.append(case_id)
                selected_families.append(family)
                selection_by_id[case_id] = item
            if len(set(selected_ids)) != len(selected_ids):
                raise ValueError("ANALYSIS_PANEL_CASE_DUPLICATED")
            if len(set(selected_families)) != ANALYSIS_CASES_PER_FORM_PER_PANEL:
                raise ValueError("ANALYSIS_PANEL_FAMILY_BALANCE_INVALID")
            family_set = set(selected_families)
            if expected_family_set is None:
                expected_family_set = family_set
            elif family_set != expected_family_set:
                raise ValueError("ANALYSIS_PANEL_FAMILY_COHORT_MISMATCH")
            detail_by_id: dict[str, Mapping[str, Any]] = {}
            for detail in details:
                if not isinstance(detail, Mapping):
                    raise ValueError("ANALYSIS_PANEL_RAW_DETAIL_INVALID")
                case_id = detail.get("case_id")
                if not isinstance(case_id, str) or case_id in detail_by_id:
                    raise ValueError("ANALYSIS_PANEL_RAW_DETAIL_INVALID")
                _analysis_detail_score(detail)
                _analysis_critical_reasons(detail)
                detail_by_id[case_id] = detail
            if set(detail_by_id) != set(selected_ids):
                raise ValueError("ANALYSIS_PANEL_RAW_EXACT_SET_MISMATCH")
            overlap = seen_case_ids.intersection(selected_ids)
            if overlap:
                raise ValueError("ANALYSIS_PANEL_CASE_OVERLAP")
            for case_id in selected_ids:
                detail = deepcopy(dict(detail_by_id[case_id]))
                selected_meta = selection_by_id[case_id]
                if (
                    detail.get("family") != selected_meta.get("family")
                    or detail.get("difficulty") != selected_meta.get("difficulty")
                ):
                    raise ValueError("ANALYSIS_PANEL_RAW_METADATA_MISMATCH")
                aggregate_details.append(detail)
                family_difficulties.setdefault(str(detail["family"]), set()).add(
                    str(detail["difficulty"])
                )
                form_case_ids.append(case_id)
            seen_case_ids.update(selected_ids)
        required_difficulties = {"easy", "medium", "hard"}
        if any(
            not required_difficulties.issubset(difficulties)
            for difficulties in family_difficulties.values()
        ):
            raise ValueError("ANALYSIS_PANEL_DIFFICULTY_COVERAGE_INCOMPLETE")
        family_scores = {
            family: round(
                _mean(
                    [
                        _analysis_detail_score(detail)
                        for detail in aggregate_details
                        if detail.get("family") == family
                    ]
                ),
                6,
            )
            for family in sorted(family_difficulties)
        }
        critical_findings = [
            {
                "case_id": str(detail["case_id"]),
                "critical_reasons": sorted(_analysis_critical_reasons(detail)),
            }
            for detail in aggregate_details
            if _analysis_critical_reasons(detail)
        ]
        axis_names = sorted(
            {
                str(axis)
                for detail in aggregate_details
                for axis in (
                    detail.get("axes", {}).keys()
                    if isinstance(detail.get("axes"), Mapping)
                    else []
                )
            }
        )
        axis_scores: dict[str, float] = {}
        for axis in axis_names:
            values = [
                float(detail["axes"][axis])
                for detail in aggregate_details
                if isinstance(detail.get("axes"), Mapping)
                and isinstance(detail["axes"].get(axis), (int, float))
                and not isinstance(detail["axes"].get(axis), bool)
            ]
            if len(values) == len(aggregate_details):
                axis_scores[axis] = round(_mean(values), 6)
        aggregate_raw_scores[form_id] = {
            "sample_count": len(aggregate_details),
            "details": aggregate_details,
            "family_scores": family_scores,
            "axis_scores": axis_scores,
            "form_score": round(
                _mean([_analysis_detail_score(detail) for detail in aggregate_details]),
                6,
            ),
            "critical_any": bool(critical_findings),
            "critical_count": len(critical_findings),
            "critical_findings": critical_findings,
            "quality_hard_gate": "FAIL" if critical_findings else "PASS",
            "max_single_sample_effect_points": round(
                100.0 / len(aggregate_details), 6
            ),
        }
        difficulty_coverage[form_id] = {
            family: sorted(difficulties)
            for family, difficulties in sorted(family_difficulties.items())
        }
        selected_case_ids[form_id] = form_case_ids

    cohort_basis = deepcopy(dict(normalized[0]["cohort_basis"]))
    model_identity = deepcopy(dict(normalized[0]["model_identity"]))
    cohort_binding = {
        "cohort_basis": cohort_basis,
        "model_identity": model_identity,
        "sample_slots": [int(panel["sample_slot"]) for panel in normalized],
        "selected_case_ids": selected_case_ids,
    }
    return {
        "schema_version": ANALYSIS_PANEL_COHORT_REVISION,
        "cache_eligible": True,
        "single_panel_cache_eligible": False,
        "panel_count": len(normalized),
        "minimum_panel_count": ANALYSIS_MINIMUM_PANEL_COUNT,
        "panel_ids": [str(panel["panel_id"]) for panel in normalized],
        "sample_slots": [int(panel["sample_slot"]) for panel in normalized],
        "aggregate_case_count_per_form": (
            len(normalized) * ANALYSIS_CASES_PER_FORM_PER_PANEL
        ),
        "aggregate_before_tail_components": True,
        "difficulty_coverage_by_form_family": difficulty_coverage,
        "aggregate_raw_scores": aggregate_raw_scores,
        "cohort_binding": cohort_binding,
        "cohort_binding_sha256": canonical_sha256(cohort_binding),
    }


def analysis_cohort_model_fail_decision(
    *,
    score: float,
    material_residuals: Sequence[Mapping[str, Any]],
    quality_hard_gate_pass: bool,
) -> dict[str, Any]:
    """Keep a low aggregate score from becoming an uncorroborated model FAIL."""

    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("ANALYSIS_COHORT_SCORE_REQUIRED")
    numeric_score = float(score)
    if numeric_score < 0.0 or numeric_score > 100.0:
        raise ValueError("ANALYSIS_COHORT_SCORE_OUT_OF_RANGE")
    if not isinstance(quality_hard_gate_pass, bool):
        raise ValueError("ANALYSIS_COHORT_HARD_GATE_REQUIRED")
    case_keys: set[str] = set()
    panel_ids: set[str] = set()
    families: set[str] = set()
    for row in material_residuals:
        if not isinstance(row, Mapping):
            raise ValueError("ANALYSIS_COHORT_MATERIAL_RESIDUAL_INVALID")
        form_id = row.get("form_id")
        case_id = row.get("case_id")
        panel_id = row.get("panel_id")
        family = row.get("family")
        if (
            form_id not in {"A", "B"}
            or not isinstance(case_id, str)
            or not case_id
            or not isinstance(panel_id, str)
            or not panel_id
            or not isinstance(family, str)
            or not family
        ):
            raise ValueError("ANALYSIS_COHORT_MATERIAL_RESIDUAL_INVALID")
        case_keys.add(f"{form_id}:{case_id}")
        panel_ids.add(panel_id)
        families.add(family)
    corroborated = (
        len(case_keys) >= 2
        and len(panel_ids) >= 2
        and len(families) >= 2
    )
    strict_model_fail = numeric_score < 80.0 and corroborated
    quality_pass = numeric_score >= 80.0 and quality_hard_gate_pass
    if strict_model_fail:
        status = "FAIL"
        model_fail_verdict = "FAIL"
        operational = "FAIL"
    elif quality_pass:
        status = "PASS"
        model_fail_verdict = "PASS"
        operational = "PASS"
    else:
        status = "NOT_ASSESSED"
        model_fail_verdict = "NOT_ASSESSED"
        operational = "INCONCLUSIVE"
    return {
        "schema_version": ANALYSIS_COHORT_FAIL_POLICY_REVISION,
        "status": status,
        "model_fail_verdict": model_fail_verdict,
        "model_fail_established": strict_model_fail,
        "operational_eligibility_verdict": operational,
        "category_score": round(numeric_score, 6),
        "distinct_material_case_count": len(case_keys),
        "distinct_material_panel_count": len(panel_ids),
        "distinct_material_family_count": len(families),
        "minimum_distinct_material_cases": 2,
        "minimum_distinct_panels": 2,
        "minimum_distinct_families": 2,
        "low_score_alone_cannot_establish_model_fail": True,
        "corroborated_material_failure": corroborated,
    }


def admit_quality_fail(
    *,
    execution_outcome: ExecutionOutcome | str,
    scorability: Scorability | str,
    repair_rounds_used: int,
    repair_round_limit: int,
    blocking_failures: Sequence[Mapping[str, Any]],
    exact_hashes: Mapping[str, str],
    repair_cycle_closed: bool | None = None,
) -> dict[str, Any]:
    """Admit FAIL only after scoreable completion and an evidence-bound repair cycle.

    A cycle is closed either by consuming the configured semantic-repair limit or
    by an executor explicitly proving that no targetable residual remains.  The
    latter keeps a fully repaired-but-penalized result scoreable without forcing
    a meaningless second repair request.
    """

    outcome = ExecutionOutcome(execution_outcome)
    score_state = Scorability(scorability)
    required_hashes = {
        "reference_pack_sha256",
        "scoring_protocol_sha256",
        "executor_sha256",
        "input_exact_set_sha256",
    }
    hashes_ok = required_hashes.issubset(exact_hashes) and all(
        isinstance(exact_hashes[key], str) and _SHA256.fullmatch(exact_hashes[key])
        for key in required_hashes
    )
    evidence_ok = bool(blocking_failures) and all(
        isinstance(row.get("case_id"), str)
        and bool(row.get("case_id"))
        and isinstance(row.get("failed_fields"), list)
        and bool(row.get("failed_fields"))
        and isinstance(row.get("evidence_anchor"), str)
        and bool(row.get("evidence_anchor"))
        and row.get("blocking") is True
        for row in blocking_failures
    )
    rounds_valid = (
        isinstance(repair_rounds_used, int)
        and isinstance(repair_round_limit, int)
        and repair_round_limit in {0, 1, 2}
        and 0 <= repair_rounds_used <= repair_round_limit
    )
    repairs_exhausted = (
        rounds_valid
        and repair_rounds_used == repair_round_limit
    )
    repair_cycle_complete = repairs_exhausted or (
        rounds_valid and repair_cycle_closed is True
    )
    admitted = (
        outcome is ExecutionOutcome.COMPLETED
        and score_state is Scorability.SCOREABLE
        and hashes_ok
        and evidence_ok
        and repair_cycle_complete
    )
    reasons: list[str] = []
    if outcome is not ExecutionOutcome.COMPLETED:
        reasons.append("EXECUTION_NOT_COMPLETED")
    if score_state is not Scorability.SCOREABLE:
        reasons.append("RESULT_NOT_SCOREABLE")
    if not hashes_ok:
        reasons.append("EXACT_HASH_BINDING_INCOMPLETE")
    if not evidence_ok:
        reasons.append("BLOCKING_EVIDENCE_INCOMPLETE")
    if not repair_cycle_complete:
        reasons.append("SEMANTIC_REPAIR_CYCLE_NOT_CLOSED")
    return {
        "schema_version": FAIL_ADMISSION_REVISION,
        "quality_verdict": "FAIL" if admitted else "NOT_ASSESSED",
        "fail_admitted": admitted,
        "reason_codes": reasons,
        "manual_review_required": not admitted and bool(blocking_failures),
        "repair_cycle_closed": repair_cycle_complete,
        "repair_rounds_exhausted": repairs_exhausted,
    }


def terminal_disposition(
    *,
    scorability: Scorability | str,
    residual_count: int,
    safety_blocking_residual_count: int,
    calibration_residual_count: int,
    governed_disposition_count: int,
) -> TerminalDisposition:
    if Scorability(scorability) is not Scorability.SCOREABLE:
        return TerminalDisposition.NOT_ASSESSED
    counts = (
        residual_count,
        safety_blocking_residual_count,
        calibration_residual_count,
        governed_disposition_count,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise ValueError("TERMINAL_DISPOSITION_COUNT_INVALID")
    if safety_blocking_residual_count:
        return TerminalDisposition.MANUAL_REVIEW_REQUIRED
    if governed_disposition_count:
        return TerminalDisposition.SUITABLE_WITH_GOVERNED_DISPOSITION
    if calibration_residual_count or residual_count:
        return TerminalDisposition.SUITABLE_WITH_CALIBRATION
    return TerminalDisposition.CLOSED


def _validate_binding(binding: Mapping[str, Any]) -> None:
    string_fields = (
        "category_id",
        "role_id",
        "reference_pack_id",
        "reference_pack_revision",
        "scoring_protocol_revision",
        "scoring_projection_revision",
        "executor_ref",
        "sample_revision",
    )
    if any(not isinstance(binding.get(field), str) or not binding[field] for field in string_fields):
        raise ValueError("EXAM_CATEGORY_BINDING_INCOMPLETE")
    for field in ("reference_pack_sha256", "scoring_protocol_sha256"):
        value = binding.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ValueError("EXAM_CATEGORY_BINDING_SHA256_INVALID")


def build_cache_binding(
    *,
    binding: CategoryBinding | Mapping[str, Any],
    model_identity: Mapping[str, Any],
    executor_sha256: str,
    outcome_contract_revision: str = CONTRACT_REVISION,
) -> dict[str, Any]:
    category = binding.as_dict() if isinstance(binding, CategoryBinding) else deepcopy(dict(binding))
    _validate_binding(category)
    if not _SHA256.fullmatch(executor_sha256):
        raise ValueError("EXECUTOR_SHA256_INVALID")
    required_model = ("profile_kind", "requested_model", "returned_model")
    if any(not isinstance(model_identity.get(key), str) or not model_identity[key] for key in required_model):
        raise ValueError("CACHE_MODEL_IDENTITY_INCOMPLETE")
    payload = {
        "schema_version": CACHE_BINDING_REVISION,
        "category_binding": category,
        "model_identity": deepcopy(dict(model_identity)),
        "executor_sha256": executor_sha256,
        "outcome_contract_revision": outcome_contract_revision,
        "horizontal_comparison_eligible": True,
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
    }
    payload["cache_binding_sha256"] = canonical_sha256(payload)
    return payload


def cache_reuse_status(
    cached_binding: Mapping[str, Any],
    current_binding: Mapping[str, Any],
) -> str:
    cached = cached_binding.get("cache_binding_sha256")
    current = current_binding.get("cache_binding_sha256")
    if not isinstance(cached, str) or not isinstance(current, str):
        return "CACHE_BINDING_INVALID"
    return "REUSABLE" if cached == current else "STALE_BINDING_MISMATCH"


def category_comparison_allowed(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    required = (
        "category_id",
        "role_id",
        "reference_pack_id",
        "reference_pack_revision",
        "reference_pack_sha256",
        "scoring_protocol_revision",
        "scoring_protocol_sha256",
        "scoring_projection_revision",
        "sample_revision",
    )
    return all(left.get(key) == right.get(key) for key in required)


__all__ = [
    "ANALYSIS_CASES_PER_FORM_PER_PANEL",
    "ANALYSIS_COHORT_FAIL_POLICY_REVISION",
    "ANALYSIS_MATERIAL_SCORE_THRESHOLD_EXCLUSIVE",
    "ANALYSIS_MINIMUM_PANEL_COUNT",
    "ANALYSIS_PANEL_COHORT_REVISION",
    "ANALYSIS_REPAIR_ACCEPTANCE_REVISION",
    "ANALYSIS_REPAIR_MATERIALITY_REVISION",
    "CACHE_BINDING_REVISION",
    "CONTRACT_REVISION",
    "CategoryBinding",
    "ExecutionOutcome",
    "FAIL_ADMISSION_REVISION",
    "REPAIR_LIFECYCLE_REVISION",
    "Scorability",
    "TerminalDisposition",
    "admit_quality_fail",
    "analysis_cohort_model_fail_decision",
    "analysis_material_repair_targets",
    "analysis_repair_acceptance",
    "build_analysis_panel_cohort",
    "build_cache_binding",
    "build_repair_round",
    "cache_reuse_status",
    "canonical_sha256",
    "category_comparison_allowed",
    "classify_execution_outcome",
    "clean_control_residual",
    "cumulative_category_repair_penalty",
    "determine_scorability",
    "terminal_disposition",
    "workflow_exam_output_limit_contract",
    "workflow_exam_structured_output_policy",
]

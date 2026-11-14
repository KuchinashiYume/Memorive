"""Deterministic Retrieval scorer and page-family qualification projection."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import unicodedata
from typing import Any, Mapping, Sequence

from .contracts import (
    OCRQualificationContractError,
    OUTPUT_LANES,
    PAGE_FAMILIES,
    PAGE_MODIFIERS,
    canonical_sha256,
    validate_normalized_page_transcription,
    validate_page_family_qualification,
)


@dataclass(frozen=True)
class QualificationThresholds:
    """Gate-A-frozen thresholds; callers must provide every value explicitly."""

    literal_accuracy_min: float
    critical_exact_min: float
    structure_accuracy_min: float
    omission_hallucination_min: float
    reviewer_detection_min: float
    material_improvement_margin: float

    def __post_init__(self) -> None:
        for field, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise OCRQualificationContractError("THRESHOLD_TYPE_INVALID", [field])
            if value < 0.0 or value > 1.0:
                raise OCRQualificationContractError("THRESHOLD_RANGE_INVALID", [field])

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QualificationThresholds":
        expected = set(cls.__dataclass_fields__)
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        if missing or unknown:
            raise OCRQualificationContractError(
                "THRESHOLD_FIELDS_INVALID", [f"missing={missing}", f"unknown={unknown}"]
            )
        return cls(**{field: float(value[field]) for field in expected})

    @property
    def sha256(self) -> str:
        return canonical_sha256(asdict(self))


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def evaluate_accounting(
    channel_kind: str,
    receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate cash/subscription/local semantics without inventing zero cost."""

    if not isinstance(receipt, Mapping):
        return {"verdict": "NOT_ASSESSED", "limitation_codes": ["ACCOUNTING_RECEIPT_MISSING"]}
    limitations: list[str] = []
    if channel_kind == "usage_based_api":
        requested = receipt.get("requested_model")
        returned = receipt.get("returned_model")
        status = receipt.get("cost_status")
        actual = _decimal(receipt.get("actual_cost"))
        if not isinstance(requested, str) or not requested:
            limitations.append("REQUESTED_MODEL_MISSING")
        if not isinstance(returned, str) or not returned or returned == "PROVIDER_NOT_EXPOSED":
            limitations.append("RETURNED_MODEL_MISSING")
        if status not in {"ACTUAL", "ASSESSED"} or actual is None:
            if status in {"ESTIMATED", "PENDING"}:
                return {
                    "verdict": "NOT_ASSESSED",
                    "limitation_codes": ["API_COST_NOT_ACTUAL"],
                }
            limitations.append("API_COST_EVIDENCE_INVALID")
        return {
            "verdict": "PASS" if not limitations else "FAIL",
            "limitation_codes": sorted(set(limitations)),
        }
    if channel_kind == "subscription_cli":
        if receipt.get("actual_cost") is not None:
            limitations.append("SUBSCRIPTION_CASH_COST_MUST_BE_NA_NOT_ZERO")
        if receipt.get("cost_status") != "NOT_APPLICABLE":
            limitations.append("SUBSCRIPTION_COST_STATUS_INVALID")
        if receipt.get("subscription_usage_status") not in {
            "VISIBLE",
            "NOT_EXPOSED",
            "RATE_LIMIT_ONLY",
        }:
            limitations.append("SUBSCRIPTION_USAGE_STATUS_INVALID")
        if not isinstance(receipt.get("requested_model"), str):
            limitations.append("REQUESTED_MODEL_MISSING")
        returned = receipt.get("returned_model")
        if returned not in {receipt.get("requested_model"), "PROVIDER_NOT_EXPOSED"}:
            limitations.append("RETURNED_MODEL_CONFLICT")
        return {
            "verdict": "PASS" if not limitations else "FAIL",
            "limitation_codes": sorted(set(limitations)),
        }
    if channel_kind == "local_resource":
        if receipt.get("actual_cost") is not None or receipt.get("cost_status") != "NOT_APPLICABLE":
            limitations.append("LOCAL_API_COST_MUST_BE_NOT_APPLICABLE")
        if receipt.get("resource_status") not in {"ACTUAL", "NOT_CONFIGURED"}:
            limitations.append("LOCAL_RESOURCE_STATUS_INVALID")
        verdict = "NOT_ASSESSED" if receipt.get("resource_status") == "NOT_CONFIGURED" else (
            "PASS" if not limitations else "FAIL"
        )
        return {"verdict": verdict, "limitation_codes": sorted(set(limitations))}
    return {"verdict": "FAIL", "limitation_codes": ["ACCOUNTING_KIND_UNKNOWN"]}


def _normalized_text(value: str, rules: Sequence[str]) -> str:
    result = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    if "COLLAPSE_WHITESPACE" in rules:
        result = " ".join(result.split())
    if "CASEFOLD" in rules:
        result = result.casefold()
    return result.strip()


def _critical_kind_matches(expected: str, observed: str, value: str) -> bool:
    """Treat schema-valid numeric labels as equivalent for a numeric value.

    The public schema permits both NUMBER and DECIMAL.  A blind subject must
    not lose an otherwise exact critical token solely because it selected one
    of those two valid labels for the same complete numeric literal.
    """

    if expected == observed:
        return True
    if {expected, observed} != {"NUMBER", "DECIMAL"}:
        return False
    try:
        numeric = Decimal(value.strip())
    except InvalidOperation:
        return False
    return numeric.is_finite()


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for offset, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[offset] + 1,
                    previous[offset - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _accuracy(expected: str, observed: str) -> float:
    return max(0.0, 1.0 - (_edit_distance(expected, observed) / max(len(expected), 1)))


def _gold_items(gold: Mapping[str, Any], field: str) -> list[dict[str, Any]]:
    value = gold.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise OCRQualificationContractError("GOLD_FIELD_INVALID", [field])
    return [dict(item) for item in value if item.get("disposition") != "AMBIGUOUS_EXCLUDED"]


def score_page(
    gold: Mapping[str, Any],
    output: Mapping[str, Any],
    *,
    page_family: str,
    modifiers: Sequence[str],
    output_lane: str,
    thresholds: QualificationThresholds,
    runtime_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Score one immutable page/candidate cell against local Gold."""

    accepted = validate_normalized_page_transcription(output)
    if page_family not in PAGE_FAMILIES:
        raise OCRQualificationContractError("PAGE_FAMILY_INVALID", [page_family])
    if output_lane not in OUTPUT_LANES:
        raise OCRQualificationContractError("OUTPUT_LANE_INVALID", [output_lane])
    if not set(modifiers).issubset(PAGE_MODIFIERS):
        raise OCRQualificationContractError("PAGE_MODIFIER_INVALID")
    required_gold = ("page_id", "source_image_sha256", "literal_text")
    missing_gold = [field for field in required_gold if field not in gold]
    if missing_gold:
        raise OCRQualificationContractError("GOLD_FIELDS_MISSING", missing_gold)
    if not isinstance(runtime_receipt, Mapping):
        raise OCRQualificationContractError("RUNTIME_RECEIPT_MISSING")
    rules = gold.get("normalization_rules", ["COLLAPSE_WHITESPACE"])
    if not isinstance(rules, list) or any(not isinstance(item, str) for item in rules):
        raise OCRQualificationContractError("GOLD_NORMALIZATION_RULES_INVALID")
    expected_text = _normalized_text(str(gold["literal_text"]), rules)
    observed_text = _normalized_text(accepted["literal_text"], rules)
    literal_accuracy = _accuracy(expected_text, observed_text)

    gold_blocks = _gold_items(gold, "ordered_blocks")
    observed_blocks = list(accepted["ordered_blocks"])
    # block_id is provider-local: Gold and a blind subject cannot share opaque IDs.
    # Align by reading-order position and score role/text separately instead.
    expected_roles = [item.get("role") for item in gold_blocks]
    observed_roles = [item["role"] for item in observed_blocks]
    block_order_exact = expected_roles == observed_roles
    aligned_count = max(len(gold_blocks), len(observed_blocks), 1)
    block_text_scores = [
        _accuracy(
            _normalized_text(str(gold_blocks[index].get("text", "")), rules),
            _normalized_text(str(observed_blocks[index].get("text", "")), rules),
        )
        if index < len(gold_blocks) and index < len(observed_blocks)
        else 0.0
        for index in range(aligned_count)
    ]
    block_text_accuracy = sum(block_text_scores) / aligned_count
    gold_to_observed_block = {
        str(gold_blocks[index].get("block_id")): observed_blocks[index]["block_id"]
        for index in range(min(len(gold_blocks), len(observed_blocks)))
    }

    gold_critical = _gold_items(gold, "critical_items")
    exact_critical = 0
    honest_uncertain = 0
    silent_critical_errors = 0
    remaining = set(range(len(accepted["critical_items"])))
    for expected in gold_critical:
        expected_kind = str(expected.get("kind"))
        expected_value = str(expected.get("value"))
        mapped_block_id = gold_to_observed_block.get(str(expected.get("block_id")))
        exact_index = next(
            (
                index
                for index in sorted(remaining)
                for item in [accepted["critical_items"][index]]
                if _critical_kind_matches(expected_kind, item["kind"], item["value"])
                if item["confidence_state"] == "ASSERTED"
                and item["value"] == expected_value
            ),
            None,
        )
        if exact_index is not None:
            exact_critical += 1
            remaining.remove(exact_index)
            continue
        uncertain_index = next(
            (
                index
                for index in sorted(remaining)
                for item in [accepted["critical_items"][index]]
                if item["kind"] == expected_kind
                if item["confidence_state"] in {"UNCERTAIN", "ILLEGIBLE"}
                and (mapped_block_id is None or item["block_id"] == mapped_block_id)
            ),
            None,
        )
        if uncertain_index is not None:
            honest_uncertain += 1
            remaining.remove(uncertain_index)
        else:
            silent_critical_errors += 1
    critical_exact_ratio = exact_critical / max(len(gold_critical), 1)
    unsupported_critical = sum(
        1
        for index in remaining
        if accepted["critical_items"][index]["confidence_state"] == "ASSERTED"
        and _normalized_text(accepted["critical_items"][index]["value"], rules)
        not in expected_text
    )

    def table_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (item.get("table_id"), item.get("row"), item.get("column"), item.get("text"))

    def label_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (item.get("role"), item.get("text"))

    gold_tables = {table_key(item) for item in _gold_items(gold, "table_cells")}
    observed_tables = {table_key(item) for item in accepted["table_cells"]}
    gold_labels = {label_key(item) for item in _gold_items(gold, "visual_labels")}
    observed_labels = {label_key(item) for item in accepted["visual_labels"]}
    table_exact = gold_tables == observed_tables
    visual_labels_exact = gold_labels == observed_labels
    structure_accuracy = sum([block_order_exact, table_exact, visual_labels_exact]) / 3.0
    missing_block_zones = sum(
        index >= len(observed_blocks) or block_text_scores[index] < 0.5
        for index in range(len(gold_blocks))
    )
    extra_block_zones = max(0, len(observed_blocks) - len(gold_blocks))
    missing_zones = (
        missing_block_zones
        + len(gold_tables - observed_tables)
        + len(gold_labels - observed_labels)
    )
    unsupported_insertions = (
        unsupported_critical
        + extra_block_zones
        + len(observed_tables - gold_tables)
        + len(observed_labels - gold_labels)
    )
    integrity_score = 1.0 / (1.0 + missing_zones + unsupported_insertions)

    anchor_match = (
        accepted["page_id"] == gold["page_id"]
        and accepted["source_image_sha256"] == gold["source_image_sha256"]
    )
    identity_verdict = str(runtime_receipt.get("identity_verdict", "NOT_ASSESSED"))
    accounting_verdict = str(runtime_receipt.get("accounting_verdict", "NOT_ASSESSED"))
    resource_verdict = str(runtime_receipt.get("resource_verdict", "NOT_ASSESSED"))
    hard_failures: list[str] = []
    if not anchor_match:
        hard_failures.append("SOURCE_PAGE_IMAGE_ANCHOR_MISMATCH")
    if runtime_receipt.get("pre_send_sidecar_present") is not True:
        hard_failures.append("PRE_SEND_SIDECAR_MISSING")
    if runtime_receipt.get("external_request_started") and runtime_receipt.get(
        "external_request_authorized"
    ) is not True:
        hard_failures.append("UNAUTHORIZED_EXTERNAL_REQUEST")
    if accepted["terminal_state"] not in {"completed", "ERROR"}:
        hard_failures.append("TERMINAL_STATE_MISSING")
    if unsupported_critical:
        hard_failures.append("UNSUPPORTED_CRITICAL_INSERTION")
    hard_gate_pass = not hard_failures
    quality_pass = (
        accepted["terminal_state"] == "completed"
        and hard_gate_pass
        and literal_accuracy >= thresholds.literal_accuracy_min
        and critical_exact_ratio >= thresholds.critical_exact_min
        and structure_accuracy >= thresholds.structure_accuracy_min
        and integrity_score >= thresholds.omission_hallucination_min
        and silent_critical_errors == 0
    )
    reviewer_detection = 1.0 if (honest_uncertain or silent_critical_errors or missing_zones) else 0.0
    return {
        "page_id": accepted["page_id"],
        "source_image_sha256": accepted["source_image_sha256"],
        "candidate_key": accepted["candidate_key"],
        "page_family": page_family,
        "modifiers": sorted(set(modifiers)),
        "output_lane": output_lane,
        "terminal_state": accepted["terminal_state"],
        "metrics": {
            "DOCUMENT_PROCESSING_literal_accuracy": literal_accuracy,
            "DOCUMENT_PROCESSING_block_text_accuracy": block_text_accuracy,
            "EVIDENCE_EXTRACTION_critical_exact_ratio": critical_exact_ratio,
            "EVIDENCE_EXTRACTION_silent_critical_errors": silent_critical_errors,
            "EVIDENCE_EXTRACTION_honest_uncertain": honest_uncertain,
            "RETRIEVAL_structure_accuracy": structure_accuracy,
            "RETRIEVAL_block_order_exact": block_order_exact,
            "RETRIEVAL_table_exact": table_exact,
            "RETRIEVAL_visual_labels_exact": visual_labels_exact,
            "RESEARCH_ANALYSIS_missing_zones": missing_zones,
            "RESEARCH_ANALYSIS_unsupported_insertions": unsupported_insertions,
            "RESEARCH_ANALYSIS_integrity_score": integrity_score,
            "RESEARCH_OPPORTUNITIES_identity_verdict": identity_verdict,
            "RESEARCH_OPPORTUNITIES_accounting_verdict": accounting_verdict,
            "RESEARCH_OPPORTUNITIES_resource_verdict": resource_verdict,
            "reviewer_detection": reviewer_detection,
        },
        "anchor_match": anchor_match,
        "hard_gate_pass": hard_gate_pass,
        "hard_failure_codes": sorted(set(hard_failures)),
        "quality_pass": quality_pass,
        "output_sha256": canonical_sha256(accepted),
        "threshold_contract_sha256": thresholds.sha256,
    }


def validate_cell_denominator(
    scorecards: Sequence[Mapping[str, Any]],
    *,
    expected_page_ids: Sequence[str],
    expected_candidate_keys: Sequence[str],
) -> dict[str, Any]:
    expected = {
        (page_id, candidate)
        for page_id in expected_page_ids
        for candidate in expected_candidate_keys
    }
    observed = [(str(card.get("page_id")), str(card.get("candidate_key"))) for card in scorecards]
    observed_set = set(observed)
    duplicate_count = len(observed) - len(observed_set)
    missing = sorted(expected - observed_set)
    unexpected = sorted(observed_set - expected)
    terminal_missing = sorted(
        (str(card.get("page_id")), str(card.get("candidate_key")))
        for card in scorecards
        if card.get("terminal_state") not in {"completed", "ERROR"}
    )
    if duplicate_count or missing or unexpected:
        result = "FAIL_DENOMINATOR"
    elif terminal_missing:
        result = "FAIL_MISSING_TERMINAL"
    elif any(not card.get("hard_gate_pass") for card in scorecards):
        result = "FAIL_SECURITY_OR_ANCHOR"
    else:
        result = "PASS"
    return {
        "result": result,
        "expected_cells": len(expected),
        "observed_cells": len(observed),
        "duplicate_count": duplicate_count,
        "missing_cells": [list(item) for item in missing],
        "unexpected_cells": [list(item) for item in unexpected],
        "terminal_missing": [list(item) for item in terminal_missing],
    }


def _aggregate_verdict(values: Sequence[str], allowed_pass: set[str] | None = None) -> str:
    allowed = allowed_pass or {"PASS"}
    if values and all(value in allowed for value in values):
        return "PASS" if set(values) == {"PASS"} else values[0]
    if "FAIL" in values:
        return "FAIL"
    if "PROVIDER_NOT_EXPOSED" in values:
        return "PROVIDER_NOT_EXPOSED"
    if "NOT_CONFIGURED" in values:
        return "NOT_CONFIGURED"
    if "NOT_APPLICABLE" in values:
        return "NOT_APPLICABLE"
    return "NOT_ASSESSED"


def project_family_qualification(
    scorecards: Sequence[Mapping[str, Any]],
    *,
    candidate_key: str,
    channel_kind: str,
    profile_id: str | None,
    requested_model: str | None,
    page_family: str,
    modifiers: Sequence[str],
    output_lane: str,
    thresholds: QualificationThresholds,
    baseline_literal_accuracy: float | None,
    evidence_refs: Sequence[str],
) -> dict[str, Any]:
    if page_family not in PAGE_FAMILIES:
        raise OCRQualificationContractError("PAGE_FAMILY_INVALID", [page_family])
    if output_lane not in OUTPUT_LANES:
        raise OCRQualificationContractError("OUTPUT_LANE_INVALID", [output_lane])
    if not set(modifiers).issubset(PAGE_MODIFIERS):
        raise OCRQualificationContractError("PAGE_MODIFIER_INVALID")
    exact_modifiers = sorted(set(modifiers))
    cells = [
        dict(card)
        for card in scorecards
        if card.get("candidate_key") == candidate_key
        and card.get("page_family") == page_family
        and sorted(set(card.get("modifiers", []))) == exact_modifiers
        and card.get("output_lane") == output_lane
    ]
    if not cells:
        return validate_page_family_qualification(
            {
                "schema_version": "PageRecognitionPageFamilyQualification-v1",
                "candidate_key": candidate_key,
                "channel_kind": channel_kind,
                "profile_id": profile_id,
                "requested_model": requested_model,
                "page_family": page_family,
                "modifiers": exact_modifiers,
                "output_lane": output_lane,
                "quality_verdict": "NOT_ASSESSED",
                "identity_verdict": "NOT_ASSESSED",
                "accounting_verdict": "NOT_ASSESSED",
                "resource_verdict": "NOT_ASSESSED",
                "role_eligibility": "NOT_ASSESSED",
                "sample_denominator": 0,
                "metric_summary": {},
                "threshold_contract_sha256": thresholds.sha256,
                "evidence_refs": sorted(set(evidence_refs)),
                "limitation_codes": ["EXACT_PAGE_FAMILY_MODIFIER_LANE_UNTESTED"],
                "default_enabled": False,
                "activation_authorized": False,
            }
        )

    def mean(metric: str) -> float:
        return sum(float(card["metrics"][metric]) for card in cells) / len(cells)

    literal = mean("DOCUMENT_PROCESSING_literal_accuracy")
    critical = mean("EVIDENCE_EXTRACTION_critical_exact_ratio")
    structure = mean("RETRIEVAL_structure_accuracy")
    integrity = mean("RESEARCH_ANALYSIS_integrity_score")
    reviewer_detection = mean("reviewer_detection")
    silent_errors = sum(int(card["metrics"]["EVIDENCE_EXTRACTION_silent_critical_errors"]) for card in cells)
    identity = _aggregate_verdict([str(card["metrics"]["RESEARCH_OPPORTUNITIES_identity_verdict"]) for card in cells])
    accounting = _aggregate_verdict([str(card["metrics"]["RESEARCH_OPPORTUNITIES_accounting_verdict"]) for card in cells])
    resource = _aggregate_verdict([str(card["metrics"]["RESEARCH_OPPORTUNITIES_resource_verdict"]) for card in cells])
    hard_complete = all(card.get("hard_gate_pass") for card in cells)
    quality_pass = (
        hard_complete
        and literal >= thresholds.literal_accuracy_min
        and critical >= thresholds.critical_exact_min
        and structure >= thresholds.structure_accuracy_min
        and integrity >= thresholds.omission_hallucination_min
    )
    improvement = None if baseline_literal_accuracy is None else literal - baseline_literal_accuracy
    limitations: list[str] = []
    if baseline_literal_accuracy is None:
        limitations.append("BASELINE_NOT_BOUND")
    if silent_errors:
        limitations.append("SILENT_CRITICAL_ERROR")
    if not hard_complete:
        limitations.append("HARD_GATE_FAILURE")
    if identity != "PASS":
        limitations.append(f"IDENTITY_{identity}")
    if accounting != "PASS":
        limitations.append(f"ACCOUNTING_{accounting}")
    if resource != "PASS":
        limitations.append(f"RESOURCE_{resource}")

    hard_axes_pass = identity == accounting == resource == "PASS"
    if not quality_pass or not hard_axes_pass:
        role = "KEEP_DISABLED"
    elif silent_errors == 0 and improvement is not None and improvement >= thresholds.material_improvement_margin and all(card.get("quality_pass") for card in cells):
        role = "PRIMARY"
    elif silent_errors == 0 and improvement is not None and improvement > 0:
        role = "RESCUE"
    elif reviewer_detection >= thresholds.reviewer_detection_min:
        role = "REVIEW_ONLY"
    else:
        role = "KEEP_DISABLED"
        limitations.append("MATERIAL_IMPROVEMENT_NOT_DEMONSTRATED")
    value = {
        "schema_version": "PageRecognitionPageFamilyQualification-v1",
        "candidate_key": candidate_key,
        "channel_kind": channel_kind,
        "profile_id": profile_id,
        "requested_model": requested_model,
        "page_family": page_family,
        "modifiers": exact_modifiers,
        "output_lane": output_lane,
        "quality_verdict": "PASS" if quality_pass else "FAIL",
        "identity_verdict": identity,
        "accounting_verdict": accounting,
        "resource_verdict": resource,
        "role_eligibility": role,
        "sample_denominator": len(cells),
        "metric_summary": {
            "literal_accuracy": literal,
            "critical_exact_ratio": critical,
            "structure_accuracy": structure,
            "integrity_score": integrity,
            "reviewer_detection": reviewer_detection,
            "silent_critical_errors": silent_errors,
            "baseline_literal_accuracy": baseline_literal_accuracy,
            "material_improvement": improvement,
        },
        "threshold_contract_sha256": thresholds.sha256,
        "evidence_refs": sorted(set(evidence_refs)),
        "limitation_codes": sorted(set(limitations)),
        "default_enabled": False,
        "activation_authorized": False,
    }
    return validate_page_family_qualification(value)

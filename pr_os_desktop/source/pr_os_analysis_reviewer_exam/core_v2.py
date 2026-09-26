from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from . import core_v1_frozen as base


sha256 = base.sha256
perfect_response = base.perfect_response
interpolate = base.interpolate
build_directed_repair_package = base.build_directed_repair_package


def resolve_pointer(value: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, Mapping):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


def audit_response(
    form: Mapping[str, Any],
    response: Any,
    output_schema: Mapping[str, Any],
) -> dict[str, Any]:
    result = base.audit_response(form, response, output_schema)
    _, cases = base.form_case_map(form)
    added: list[dict[str, Any]] = []
    rows = (
        response.get("case_reviews", [])
        if isinstance(response, Mapping)
        and isinstance(response.get("case_reviews"), list)
        else []
    )
    for row_index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or case_id not in cases:
            continue
        issues = row.get("issues")
        if not isinstance(issues, list):
            continue
        for issue_index, issue in enumerate(issues):
            if not isinstance(issue, Mapping):
                continue
            pointer = issue.get("claim_pointer")
            if not isinstance(pointer, str):
                continue
            try:
                target = resolve_pointer(cases[case_id], pointer)
            except (KeyError, IndexError, ValueError, TypeError):
                continue
            if isinstance(target, (Mapping, list)):
                added.append(
                    base.finding(
                        "CLAIM_POINTER_NOT_SCALAR_LEAF",
                        path=(
                            f"/case_reviews/{row_index}/issues/"
                            f"{issue_index}/claim_pointer"
                        ),
                        detail=(
                            "claim_pointer resolves to a container; use the "
                            "narrowest existing scalar claim or source-anchor field"
                        ),
                        case_id=case_id,
                        repairable=True,
                    )
                )
    result["findings"].extend(added)
    result["finding_count"] = len(result["findings"])
    result["non_scalar_claim_pointer_count"] = len(added)
    return result


def normalize_claim_pointer_leaves(
    form: Mapping[str, Any], response: Any
) -> dict[str, Any]:
    normalized = deepcopy(response)
    _, cases = base.form_case_map(form)
    changes: list[dict[str, Any]] = []
    rows = (
        normalized.get("case_reviews", [])
        if isinstance(normalized, Mapping)
        and isinstance(normalized.get("case_reviews"), list)
        else []
    )
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or case_id not in cases:
            continue
        issues = row.get("issues")
        if not isinstance(issues, list):
            continue
        for issue_index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            pointer = issue.get("claim_pointer")
            if not isinstance(pointer, str):
                continue
            match = re.fullmatch(
                r"/candidate_analysis/claims/([0-9]+)", pointer
            )
            if match is None:
                continue
            try:
                target = resolve_pointer(cases[case_id], pointer)
            except (KeyError, IndexError, ValueError, TypeError):
                continue
            if not isinstance(target, Mapping) or not isinstance(
                target.get("text"), str
            ):
                continue
            replacement = f"{pointer}/text"
            issue["claim_pointer"] = replacement
            changes.append(
                {
                    "case_id": case_id,
                    "issue_index": issue_index,
                    "original_pointer": pointer,
                    "normalized_pointer": replacement,
                    "rule": "CLAIM_OBJECT_TO_TEXT_SCALAR",
                }
            )
    return {
        "schema_version": "m14-analysis-reviewer-pointer-normalization-v1",
        "status": "NORMALIZED" if changes else "NO_CHANGE",
        "changes": changes,
        "change_count": len(changes),
        "affected_case_ids": sorted(
            {item["case_id"] for item in changes}
        ),
        "affected_case_count": len(
            {item["case_id"] for item in changes}
        ),
        "gold_used": False,
        "semantic_fields_changed": False,
        "normalized_response": normalized,
    }


def score_case(
    row: Mapping[str, Any] | None, gold_case: Mapping[str, Any]
) -> dict[str, Any]:
    result = base.score_case(row, gold_case)
    if gold_case["expected_issue_count"] == 0:
        result["substantive_defect_detected"] = False
        result["critical_issue_detected"] = False
        result["taxonomy_match"] = True
        return result
    issues = (
        [issue for issue in row.get("issues", []) if isinstance(issue, Mapping)]
        if isinstance(row, Mapping) and isinstance(row.get("issues"), list)
        else []
    )
    substantive = (
        isinstance(row, Mapping)
        and row.get("review_verdict") in {"NEEDS_REVISION", "REJECT"}
        and bool(issues)
    )
    critical = substantive and any(
        issue.get("severity") == "critical"
        and issue.get("blocking") is True
        for issue in issues
    )
    taxonomy = any(
        issue.get("issue_family") == gold_case["expected_issue_family"]
        for issue in issues
    )
    result["detected"] = substantive
    result["substantive_defect_detected"] = substantive
    result["critical_issue_detected"] = critical
    result["taxonomy_match"] = taxonomy
    return result


def score_form(
    form: Mapping[str, Any],
    gold: Mapping[str, Any],
    response: Any,
    output_schema: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    result = base.score_form(
        form, gold, response, output_schema, scoring_protocol
    )
    audit = audit_response(form, response, output_schema)
    row_by_id = audit.pop("row_by_id")
    gold_cases = [
        item for item in gold.get("cases", []) if isinstance(item, Mapping)
    ]
    case_scores = [
        score_case(row_by_id.get(item["case_id"]), item)
        for item in gold_cases
    ]
    scores_by_id = {item["case_id"]: item for item in case_scores}
    critical_cases = [
        item for item in gold_cases if item.get("critical") is True
    ]
    missed_critical = [
        item["case_id"]
        for item in critical_cases
        if not scores_by_id[item["case_id"]]["critical_issue_detected"]
    ]
    critical_detection = (
        100.0
        * sum(
            1
            for item in critical_cases
            if scores_by_id[item["case_id"]]["critical_issue_detected"]
        )
        / len(critical_cases)
        if critical_cases
        else 100.0
    )
    critical_taxonomy = (
        100.0
        * sum(
            1
            for item in critical_cases
            if scores_by_id[item["case_id"]]["taxonomy_match"]
        )
        / len(critical_cases)
        if critical_cases
        else 100.0
    )
    defect_cases = [
        item
        for item in gold_cases
        if item.get("expected_issue_count") == 1
    ]
    defect_taxonomy = (
        100.0
        * sum(
            1
            for item in defect_cases
            if scores_by_id[item["case_id"]]["taxonomy_match"]
        )
        / len(defect_cases)
        if defect_cases
        else 100.0
    )
    stale_cases = [
        item
        for item in gold_cases
        if item.get("expected_issue_family")
        == "lineage_citation_mismatch"
    ]
    stale_detection = (
        100.0
        * sum(
            1
            for item in stale_cases
            if scores_by_id[item["case_id"]]["substantive_defect_detected"]
            and scores_by_id[item["case_id"]]["taxonomy_match"]
        )
        / len(stale_cases)
        if stale_cases
        else 100.0
    )
    metrics = dict(result["metrics"])
    metrics["critical_detection"] = round(critical_detection, 6)
    metrics["critical_taxonomy_accuracy"] = round(
        critical_taxonomy, 6
    )
    metrics["defect_taxonomy_accuracy"] = round(defect_taxonomy, 6)
    metrics["stale_lineage_detection"] = round(stale_detection, 6)
    components = dict(result["component_scores"])
    components["critical_detection"] = round(
        interpolate(
            scoring_protocol["piecewise_components"][
                "critical_detection"
            ]["knots"],
            critical_detection,
        ),
        6,
    )
    components["stale_lineage_detection"] = round(
        interpolate(
            scoring_protocol["piecewise_components"][
                "stale_lineage_detection"
            ]["knots"],
            stale_detection,
        ),
        6,
    )
    final_score = round(sum(components.values()), 6)
    hard_gates: list[str] = []
    if not audit["schema_valid"] or not audit["exact_case_set_valid"]:
        hard_gates.append("FINAL_CASE_SET_OR_SCHEMA_INVALID")
    if missed_critical:
        hard_gates.append("MISS_ANY_DECISION_CRITICAL_CASE")
    if critical_detection < 95.0:
        hard_gates.append("CRITICAL_DETECTION_RATE_BELOW_0_95")
    if metrics["clean_false_positive_rate"] > 0.10:
        hard_gates.append("CLEAN_FALSE_POSITIVE_RATE_ABOVE_0_10")
    if stale_detection < 90.0:
        hard_gates.append(
            "STALE_LINEAGE_DETECTION_RATE_BELOW_0_90"
        )
    if metrics["hallucinated_evidence_ref_rate"] > 0.05:
        hard_gates.append(
            "EVIDENCE_HALLUCINATION_RATE_ABOVE_0_05"
        )
    band = next(
        item["code"]
        for item in scoring_protocol["recommendation_bands"]
        if final_score >= item["minimum"]
        and (
            final_score < item["maximum_exclusive"]
            if "maximum_exclusive" in item
            else final_score <= item["maximum_inclusive"]
        )
    )
    result.update(
        {
            "schema_version": "m14-analysis-reviewer-form-score-v2",
            "audit": audit,
            "case_scores": case_scores,
            "metrics": metrics,
            "component_scores": components,
            "score": final_score,
            "hard_gate_failures": hard_gates,
            "hard_gates_pass": not hard_gates,
            "recommendation": band,
            "missed_critical_case_ids": missed_critical,
            "critical_detection_is_taxonomy_independent": True,
            "taxonomy_mismatch_remains_point_penalized": True,
        }
    )
    return result

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"TOP_LEVEL_NOT_OBJECT:{path}")
    return value


def form_case_map(
    form: Mapping[str, Any],
) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
    cases = form.get("cases")
    if not isinstance(cases, list):
        return [], {}
    case_ids: list[str] = []
    result: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str):
            continue
        case_ids.append(case_id)
        if case_id not in result:
            result[case_id] = case
    return case_ids, result


def json_pointer_exists(value: Any, pointer: str) -> bool:
    if not pointer.startswith("/"):
        return False
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return False
            if index < 0 or index >= len(current):
                return False
            current = current[index]
        elif isinstance(current, Mapping):
            if part not in current:
                return False
            current = current[part]
        else:
            return False
    return True


def finding(
    code: str,
    *,
    path: str,
    detail: str,
    case_id: str | None = None,
    repairable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "case_id": case_id,
        "path": path,
        "detail": detail,
        "repairable": repairable,
    }


def audit_response(
    form: Mapping[str, Any],
    response: Any,
    output_schema: Mapping[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for error in sorted(
        Draft202012Validator(output_schema).iter_errors(response),
        key=lambda item: list(item.absolute_path),
    ):
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        findings.append(
            finding(
                "OUTPUT_SCHEMA_INVALID",
                path=pointer,
                detail=error.message,
                repairable=False,
            )
        )

    expected_ids, cases = form_case_map(form)
    expected_set = set(expected_ids)
    rows: list[Any] = []
    if isinstance(response, Mapping) and isinstance(
        response.get("case_reviews"), list
    ):
        rows = list(response["case_reviews"])
    observed_ids: list[str] = []
    row_by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        path = f"/case_reviews/{index}"
        if not isinstance(row, Mapping):
            findings.append(
                finding(
                    "ROW_NOT_OBJECT",
                    path=path,
                    detail="case review row must be an object",
                    repairable=False,
                )
            )
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str):
            findings.append(
                finding(
                    "CASE_ID_INVALID",
                    path=f"{path}/case_id",
                    detail="case_id must be a string",
                    repairable=False,
                )
            )
            continue
        observed_ids.append(case_id)
        if case_id not in row_by_id:
            row_by_id[case_id] = row
        if case_id not in expected_set:
            findings.append(
                finding(
                    "UNKNOWN_CASE_ID",
                    path=f"{path}/case_id",
                    detail=f"unknown case_id {case_id}",
                    case_id=case_id,
                    repairable=False,
                )
            )
            continue
        case = cases[case_id]
        evidence_ids = {
            unit.get("evidence_id")
            for unit in case.get("evidence_units", [])
            if isinstance(unit, Mapping)
            and isinstance(unit.get("evidence_id"), str)
        }
        issues = row.get("issues")
        verdict = row.get("review_verdict")
        if verdict == "ACCEPT" and isinstance(issues, list) and issues:
            findings.append(
                finding(
                    "ACCEPT_WITH_SUBSTANTIVE_ISSUE",
                    path=f"{path}/issues",
                    detail="ACCEPT rows must not contain issues",
                    case_id=case_id,
                )
            )
        if (
            verdict in {"NEEDS_REVISION", "REJECT"}
            and isinstance(issues, list)
            and not issues
        ):
            findings.append(
                finding(
                    "DEFECT_VERDICT_WITHOUT_ISSUE",
                    path=f"{path}/issues",
                    detail="non-ACCEPT row requires a substantive issue",
                    case_id=case_id,
                )
            )
        if not isinstance(issues, list):
            continue
        for issue_index, issue in enumerate(issues):
            issue_path = f"{path}/issues/{issue_index}"
            if not isinstance(issue, Mapping):
                continue
            pointer = issue.get("claim_pointer")
            if isinstance(pointer, str) and not json_pointer_exists(case, pointer):
                findings.append(
                    finding(
                        "CLAIM_POINTER_NOT_FOUND",
                        path=f"{issue_path}/claim_pointer",
                        detail=f"pointer does not resolve in case: {pointer}",
                        case_id=case_id,
                    )
                )
            refs = issue.get("evidence_refs")
            if isinstance(refs, list):
                seen_refs: set[str] = set()
                for ref_index, ref in enumerate(refs):
                    if not isinstance(ref, str):
                        continue
                    if ref in seen_refs:
                        findings.append(
                            finding(
                                "DUPLICATE_EVIDENCE_REF",
                                path=f"{issue_path}/evidence_refs/{ref_index}",
                                detail=f"duplicate evidence ref {ref}",
                                case_id=case_id,
                            )
                        )
                    seen_refs.add(ref)
                    if ref not in evidence_ids:
                        findings.append(
                            finding(
                                "EVIDENCE_REF_NOT_IN_CASE",
                                path=f"{issue_path}/evidence_refs/{ref_index}",
                                detail=f"evidence ref is not local to case: {ref}",
                                case_id=case_id,
                            )
                        )

    counts = Counter(observed_ids)
    for case_id, count in sorted(counts.items()):
        if count > 1:
            findings.append(
                finding(
                    "DUPLICATE_CASE_ID",
                    path="/case_reviews",
                    detail=f"{case_id} appears {count} times",
                    case_id=case_id,
                    repairable=False,
                )
            )
    missing_ids = [case_id for case_id in expected_ids if case_id not in counts]
    for case_id in missing_ids:
        findings.append(
            finding(
                "MISSING_CASE_ID",
                path="/case_reviews",
                detail=f"missing case_id {case_id}",
                case_id=case_id,
            )
        )
    unknown_ids = sorted(set(observed_ids) - expected_set)
    exact_set_valid = (
        not missing_ids
        and not unknown_ids
        and all(count == 1 for count in counts.values())
        and len(observed_ids) == len(expected_ids)
    )
    schema_valid = not any(
        item["code"] == "OUTPUT_SCHEMA_INVALID" for item in findings
    )
    hallucinated_refs = sum(
        1 for item in findings if item["code"] == "EVIDENCE_REF_NOT_IN_CASE"
    )
    total_cited_refs = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        issues = row.get("issues")
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if isinstance(issue, Mapping) and isinstance(
                issue.get("evidence_refs"), list
            ):
                total_cited_refs += sum(
                    1 for ref in issue["evidence_refs"] if isinstance(ref, str)
                )
    return {
        "schema_version": "model_evaluation-analysis-reviewer-response-audit-v1",
        "form_id": form.get("form_id"),
        "response_sha256": sha256(response),
        "schema_valid": schema_valid,
        "exact_case_set_valid": exact_set_valid,
        "finding_count": len(findings),
        "findings": findings,
        "expected_case_count": len(expected_ids),
        "observed_case_count": len(observed_ids),
        "missing_case_ids": missing_ids,
        "unknown_case_ids": unknown_ids,
        "hallucinated_evidence_ref_count": hallucinated_refs,
        "total_cited_evidence_ref_count": total_cited_refs,
        "hallucinated_evidence_ref_rate": (
            hallucinated_refs / total_cited_refs if total_cited_refs else 0.0
        ),
        "row_by_id": row_by_id,
    }


def perfect_response(gold: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for gold_case in gold.get("cases", []):
        case_id = gold_case["case_id"]
        if gold_case["expected_issue_count"] == 0:
            rows.append(
                {
                    "case_id": case_id,
                    "review_verdict": "ACCEPT",
                    "issues": [],
                    "review_summary": "The conclusion is supported and appropriately bounded.",
                }
            )
            continue
        rows.append(
            {
                "case_id": case_id,
                "review_verdict": gold_case["expected_verdict"],
                "issues": [
                    {
                        "issue_family": gold_case["expected_issue_family"],
                        "claim_pointer": gold_case["accepted_claim_pointers"][0],
                        "evidence_refs": gold_case["required_evidence_refs"],
                        "severity": gold_case["severity"],
                        "blocking": gold_case["blocking"],
                        "action": gold_case["accepted_actions"][0],
                        "explanation": (
                            "The cited evidence and lineage establish the listed substantive defect."
                        ),
                    }
                ],
                "review_summary": "One evidence-bound substantive defect requires correction.",
            }
        )
    return {"case_reviews": rows}


def evidence_f1(
    observed: list[str], required: list[str], allowed: list[str]
) -> tuple[float, float, float]:
    observed_set = set(observed)
    required_set = set(required)
    allowed_set = set(allowed)
    precision = (
        len(observed_set.intersection(allowed_set)) / len(observed_set)
        if observed_set
        else 0.0
    )
    recall = (
        len(observed_set.intersection(required_set)) / len(required_set)
        if required_set
        else 1.0
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return precision, recall, f1


def choose_issue(
    row: Mapping[str, Any], expected_family: str
) -> Mapping[str, Any] | None:
    issues = row.get("issues")
    if not isinstance(issues, list):
        return None
    mappings = [issue for issue in issues if isinstance(issue, Mapping)]
    for issue in mappings:
        if issue.get("issue_family") == expected_family:
            return issue
    return mappings[0] if mappings else None


def score_case(
    row: Mapping[str, Any] | None, gold_case: Mapping[str, Any]
) -> dict[str, Any]:
    if row is None:
        return {
            "case_id": gold_case["case_id"],
            "score": 0.0,
            "components": {},
            "detected": False,
            "localized": False,
            "evidence_precision": 0.0,
            "evidence_recall": 0.0,
        }
    clean = gold_case["expected_issue_count"] == 0
    if clean:
        verdict_points = 70.0 if row.get("review_verdict") == "ACCEPT" else 0.0
        issues = row.get("issues")
        no_false_issue = 30.0 if isinstance(issues, list) and not issues else 0.0
        score = verdict_points + no_false_issue
        return {
            "case_id": gold_case["case_id"],
            "score": score,
            "components": {
                "accept_verdict": verdict_points,
                "no_false_issue": no_false_issue,
            },
            "detected": score == 100.0,
            "localized": score == 100.0,
            "evidence_precision": 1.0,
            "evidence_recall": 1.0,
        }

    expected_family = gold_case["expected_issue_family"]
    issue = choose_issue(row, expected_family)
    detected = row.get("review_verdict") in {"NEEDS_REVISION", "REJECT"}
    family_match = (
        issue is not None and issue.get("issue_family") == expected_family
    )
    detection_points = (15.0 if detected else 0.0) + (
        25.0 if family_match else 0.0
    )
    pointer = issue.get("claim_pointer") if issue is not None else None
    localized = pointer in gold_case["accepted_claim_pointers"]
    localization_points = 20.0 if localized else 0.0
    observed_refs = (
        [ref for ref in issue.get("evidence_refs", []) if isinstance(ref, str)]
        if issue is not None and isinstance(issue.get("evidence_refs"), list)
        else []
    )
    precision, recall, f1 = evidence_f1(
        observed_refs,
        list(gold_case["required_evidence_refs"]),
        list(gold_case["allowed_evidence_refs"]),
    )
    evidence_points = 25.0 * f1
    severity_points = (
        5.0
        if issue is not None and issue.get("severity") == gold_case["severity"]
        else 0.0
    )
    blocking_points = (
        5.0
        if issue is not None and issue.get("blocking") is gold_case["blocking"]
        else 0.0
    )
    action_points = (
        5.0
        if issue is not None
        and issue.get("action") in gold_case["accepted_actions"]
        else 0.0
    )
    total = (
        detection_points
        + localization_points
        + evidence_points
        + severity_points
        + blocking_points
        + action_points
    )
    return {
        "case_id": gold_case["case_id"],
        "score": round(total, 6),
        "components": {
            "detection_and_issue_family": detection_points,
            "claim_localization": localization_points,
            "evidence_precision_recall": round(evidence_points, 6),
            "severity_blocking_action": (
                severity_points + blocking_points + action_points
            ),
        },
        "detected": detected and family_match,
        "localized": localized,
        "evidence_precision": precision,
        "evidence_recall": recall,
    }


def interpolate(knots: list[list[float]], value: float) -> float:
    ordered = sorted((float(x), float(y)) for x, y in knots)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= value <= x1:
            ratio = (value - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    raise AssertionError("unreachable interpolation interval")


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def score_form(
    form: Mapping[str, Any],
    gold: Mapping[str, Any],
    response: Any,
    output_schema: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    audit = audit_response(form, response, output_schema)
    row_by_id = audit.pop("row_by_id")
    gold_cases = [
        item for item in gold.get("cases", []) if isinstance(item, Mapping)
    ]
    case_scores = [
        score_case(row_by_id.get(item["case_id"]), item) for item in gold_cases
    ]
    scores_by_id = {item["case_id"]: item for item in case_scores}
    overall = mean([item["score"] for item in case_scores])
    hard_scores = [
        scores_by_id[item["case_id"]]["score"]
        for item in gold_cases
        if item.get("difficulty") == "hard"
    ]
    family_scores: dict[str, list[float]] = defaultdict(list)
    for item in gold_cases:
        family = (
            "clean_control"
            if item.get("expected_issue_family") == "none"
            else str(item.get("expected_issue_family"))
        )
        family_scores[family].append(scores_by_id[item["case_id"]]["score"])
    family_means = {
        family: mean(values) for family, values in sorted(family_scores.items())
    }
    sorted_scores = sorted(item["score"] for item in case_scores)
    bottom_count = max(1, len(sorted_scores) // 4)
    bottom_quartile = mean(sorted_scores[:bottom_count])
    clean_cases = [
        item for item in gold_cases if item.get("expected_issue_count") == 0
    ]
    clean_correct = sum(
        1
        for item in clean_cases
        if scores_by_id[item["case_id"]]["score"] == 100.0
    )
    clean_specificity = (
        100.0 * clean_correct / len(clean_cases) if clean_cases else 100.0
    )
    defect_scores = [
        scores_by_id[item["case_id"]]
        for item in gold_cases
        if item.get("expected_issue_count") == 1
    ]
    evidence_localization = 100.0 * mean(
        [
            (item["evidence_precision"] + item["evidence_recall"]) / 2
            for item in defect_scores
        ]
    )
    critical_cases = [item for item in gold_cases if item.get("critical") is True]
    critical_detected = sum(
        1
        for item in critical_cases
        if scores_by_id[item["case_id"]]["detected"]
    )
    critical_detection = (
        100.0 * critical_detected / len(critical_cases)
        if critical_cases
        else 100.0
    )
    stale_cases = [
        item
        for item in gold_cases
        if item.get("expected_issue_family") == "lineage_citation_mismatch"
    ]
    stale_detected = sum(
        1
        for item in stale_cases
        if scores_by_id[item["case_id"]]["detected"]
    )
    stale_detection = (
        100.0 * stale_detected / len(stale_cases) if stale_cases else 100.0
    )
    component_inputs = {
        "overall_semantic": overall,
        "hard_case": mean(hard_scores),
        "worst_family": min(family_means.values()) if family_means else 0.0,
        "bottom_quartile": bottom_quartile,
        "clean_specificity": clean_specificity,
        "evidence_localization": evidence_localization,
        "critical_detection": critical_detection,
        "stale_lineage_detection": stale_detection,
    }
    component_scores: dict[str, float] = {}
    for name, config in scoring_protocol["piecewise_components"].items():
        component_scores[name] = round(
            interpolate(config["knots"], component_inputs[name]), 6
        )
    final_score = round(sum(component_scores.values()), 6)

    hard_gates: list[str] = []
    if not audit["schema_valid"] or not audit["exact_case_set_valid"]:
        hard_gates.append("FINAL_CASE_SET_OR_SCHEMA_INVALID")
    missed_critical = [
        item["case_id"]
        for item in critical_cases
        if not scores_by_id[item["case_id"]]["detected"]
    ]
    if missed_critical:
        hard_gates.append("MISS_ANY_DECISION_CRITICAL_CASE")
    if critical_detection < 95.0:
        hard_gates.append("CRITICAL_DETECTION_RATE_BELOW_0_95")
    clean_false_positive_rate = (
        1.0 - clean_correct / len(clean_cases) if clean_cases else 0.0
    )
    if clean_false_positive_rate > 0.10:
        hard_gates.append("CLEAN_FALSE_POSITIVE_RATE_ABOVE_0_10")
    if stale_detection < 90.0:
        hard_gates.append("STALE_LINEAGE_DETECTION_RATE_BELOW_0_90")
    if audit["hallucinated_evidence_ref_rate"] > 0.05:
        hard_gates.append("EVIDENCE_HALLUCINATION_RATE_ABOVE_0_05")
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
    return {
        "schema_version": "model_evaluation-analysis-reviewer-form-score-v1",
        "form_id": form.get("form_id"),
        "form_sha256": sha256(form),
        "gold_sha256": sha256(gold),
        "response_sha256": sha256(response),
        "audit": audit,
        "case_scores": case_scores,
        "metrics": {
            "overall_semantic": round(overall, 6),
            "hard_case": round(mean(hard_scores), 6),
            "worst_family": round(
                min(family_means.values()) if family_means else 0.0, 6
            ),
            "bottom_quartile": round(bottom_quartile, 6),
            "clean_specificity": round(clean_specificity, 6),
            "evidence_localization": round(evidence_localization, 6),
            "critical_detection": round(critical_detection, 6),
            "stale_lineage_detection": round(stale_detection, 6),
            "clean_false_positive_rate": round(clean_false_positive_rate, 6),
            "hallucinated_evidence_ref_rate": round(
                audit["hallucinated_evidence_ref_rate"], 6
            ),
            "family_means": {
                key: round(value, 6) for key, value in family_means.items()
            },
        },
        "component_scores": component_scores,
        "score": final_score,
        "hard_gate_failures": hard_gates,
        "hard_gates_pass": not hard_gates,
        "recommendation": band,
    }


def build_directed_repair_package(
    form: Mapping[str, Any],
    response: Any,
    audit: Mapping[str, Any],
    output_schema: Mapping[str, Any],
) -> dict[str, Any]:
    _, cases = form_case_map(form)
    target_ids = sorted(
        {
            item.get("case_id")
            for item in audit.get("findings", [])
            if isinstance(item, Mapping)
            and item.get("repairable") is True
            and isinstance(item.get("case_id"), str)
            and item.get("case_id") in cases
        }
        | {
            case_id
            for case_id in audit.get("missing_case_ids", [])
            if isinstance(case_id, str) and case_id in cases
        }
    )
    row_map: dict[str, Any] = {}
    if isinstance(response, Mapping) and isinstance(
        response.get("case_reviews"), list
    ):
        for row in response["case_reviews"]:
            if isinstance(row, Mapping) and isinstance(row.get("case_id"), str):
                row_map.setdefault(row["case_id"], copy.deepcopy(row))
    findings_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in audit.get("findings", []):
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("case_id"), str)
            and item["case_id"] in target_ids
        ):
            findings_by_case[item["case_id"]].append(
                {
                    "code": item.get("code"),
                    "path": item.get("path"),
                    "detail": item.get("detail"),
                }
            )
    return {
        "schema_version": "model_evaluation-analysis-reviewer-directed-repair-package-v1",
        "form_id": form.get("form_id"),
        "form_sha256": sha256(form),
        "original_response_sha256": sha256(response),
        "repair_exact_set": target_ids,
        "target_cases": [copy.deepcopy(cases[case_id]) for case_id in target_ids],
        "current_case_reviews": [
            row_map.get(case_id) for case_id in target_ids
        ],
        "machine_findings": {
            case_id: findings_by_case[case_id] for case_id in target_ids
        },
        "output_schema": copy.deepcopy(output_schema),
        "gold_included": False,
        "whole_form_regeneration_forbidden": True,
    }

"""Frozen, provider-blind protocol primitives for the MODEL_EVALUATION analysis exam.

The private Form/Gold payloads remain in an authorised Quality sandbox.  This
module contains the production implementation needed to validate those assets,
build subject prompts, replay a perfect answer, score a completed response, and
prove A/B form equivalence.  It never performs provider I/O.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from jsonschema import Draft202012Validator


FAMILIES = (
    "INSUFFICIENT_EVIDENCE",
    "CONFLICTING_EVIDENCE",
    "CORRELATION_NOT_CAUSATION",
    "POPULATION_SCOPE",
    "STALE_LINEAGE",
    "CLEAN_DIRECT_SUPPORT",
)
CASES_PER_FAMILY = 12
EVIDENCE_UNITS_PER_CASE = 10
ARTIFACTS = ("claim_map", "analysis_summary")
ROLE_FIELDS = {
    "DIRECT_SUPPORT": "supporting_anchors",
    "COUNTEREVIDENCE": "contradicting_anchors",
    "LIMITATION_OR_BOUNDARY": "qualification_anchors",
    "LINEAGE_INVALID": "lineage_invalid_anchors",
    "CONTEXT_ONLY": "context_only_anchors",
}


def artifact_schema(full_schema: dict[str, Any], artifact: str) -> dict[str, Any]:
    if artifact not in ARTIFACTS:
        raise ValueError(f"UNKNOWN_ARTIFACT:{artifact}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {artifact: deepcopy(full_schema["properties"][artifact])},
        "required": [artifact],
    }


def build_artifact_prompt(
    form_obj: dict[str, Any],
    artifact: str,
    *,
    target_case_ids: list[str] | None = None,
) -> str:
    target_count = len(target_case_ids) if target_case_ids is not None else 72
    delivery = (
        "Process all 72 cases in the presented order, include each case_id exactly once, and mechanically verify "
        "the item count before ending."
        if target_case_ids is None
        else (
            f"The context contains {len(form_obj.get('cases', []))} interleaved cases. Return only the {target_count} "
            "case_ids in TARGET_CASE_IDS, exactly once and in their listed order. Other cases are context decoys: use "
            "them to maintain scope and lineage discrimination but do not return rows for them. Mechanically verify "
            f"the output item count is {target_count} before ending. TARGET_CASE_IDS="
            + json.dumps(target_case_ids, ensure_ascii=False, separators=(",", ":"))
            + "."
        )
    )
    common = (
        "You are taking the Memorive analysis_primary high-sample examination. Every record below is synthetic. Use only "
        "the supplied evidence; do not provide clinical or operational recommendations. The target proposition in "
        "each case is a beneficial improvement in the named primary outcome. Preserve probability, population, time, "
        "endpoint, and lineage boundaries. Correlation, mechanism, temporal ordering, or plausibility alone never "
        "authorizes causation. Invalid lineage cannot support a current claim. CURRENT_ONLY and STALE_EXCLUDED are "
        "both valid only when stale or mismatched anchors are explicitly isolated and never used as support. certainty "
        "is confidence in the selected substantive conclusion. "
        + delivery
        + " Output only the JSON object required by the native schema.\n\n"
    )
    if artifact == "claim_map":
        task = (
            f"ARTIFACT TASK: Return exactly {target_count} claim_map entries and no analysis_summary. For every target case, classify all "
            "ten anchors exactly once into five disjoint arrays whose union is the complete anchor set: "
            "supporting_anchors is a valid substantive beneficial result; contradicting_anchors is a valid null or "
            "opposite result; qualification_anchors is methods, uncertainty, scope, timing, or causal boundary; "
            "lineage_invalid_anchors is superseded, retracted, citation-mismatched, or invalid lineage; "
            "context_only_anchors is relevant non-directional context. A methods limitation is not counterevidence. "
            "Keep claim_text concise; the schema permits up to 512 characters so a semantically complete answer is not rejected for harmless wording length.\n\n"
        )
    elif artifact == "analysis_summary":
        task = (
            f"ARTIFACT TASK: Return exactly {target_count} analysis_summary entries and no claim_map. Independently select the "
            "evidence-bounded conclusion_code for each target case and keep summary_text concise. Do not infer "
            "another artifact's decision; this independent output will later be checked for cross-artifact consistency.\n\n"
        )
    else:
        raise ValueError(f"UNKNOWN_ARTIFACT:{artifact}")
    return common + task + "PRIVATE SYNTHETIC FORM FOLLOWS:\n" + json.dumps(
        form_obj, ensure_ascii=False, separators=(",", ":")
    )


def successor_output_schema(
    source_schema: dict[str, Any], *, text_max_length: int | None = None
) -> dict[str, Any]:
    """Create the scored successor schema without arbitrary short-text failure.

    ``None`` is the normal successor policy: preserve complete answer text and
    remove the frozen Quality field-length ceiling.  A positive value remains
    available only for explicitly versioned compatibility projections.
    """

    schema = deepcopy(source_schema)
    text_fields = (
        schema["properties"]["claim_map"]["items"]["properties"]["claim_text"],
        schema["properties"]["analysis_summary"]["items"]["properties"][
            "summary_text"
        ],
    )
    if text_max_length is None:
        for field in text_fields:
            field.pop("maxLength", None)
        return schema
    if (
        isinstance(text_max_length, bool)
        or not isinstance(text_max_length, int)
        or text_max_length < 160
    ):
        raise ValueError("TEXT_MAX_LENGTH_MUST_NOT_TIGHTEN_SOURCE")
    for field in text_fields:
        field["maxLength"] = text_max_length
    return schema


def shard_artifact_schema(
    full_schema: dict[str, Any], artifact: str, *, target_count: int
) -> dict[str, Any]:
    if not isinstance(target_count, int) or isinstance(target_count, bool) or target_count <= 0:
        raise ValueError("TARGET_COUNT_POSITIVE_INTEGER_REQUIRED")
    schema = artifact_schema(full_schema, artifact)
    schema["properties"][artifact]["minItems"] = target_count
    schema["properties"][artifact]["maxItems"] = target_count
    return schema


def schema_diagnostics(
    response: Any, schema: dict[str, Any]
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    errors = sorted(
        Draft202012Validator(schema).iter_errors(response),
        key=lambda item: list(item.path),
    )
    for error in errors[:10]:
        path = list(error.absolute_path)
        observed = response
        try:
            for part in path:
                observed = observed[part]
        except Exception:
            observed = None
        row: dict[str, Any] = {
            "path": path,
            "validator": error.validator,
            "expected": error.validator_value,
        }
        if isinstance(observed, (list, dict, str)):
            row["observed_size"] = len(observed)
        elif observed is not None:
            row["observed_type"] = type(observed).__name__
        diagnostics.append(row)
    return diagnostics


def validate_response_shape(
    response: dict[str, Any], gold: dict[str, Any], schema: dict[str, Any]
) -> None:
    diagnostics = schema_diagnostics(response, schema)
    if diagnostics:
        raise ValueError(
            "LOCAL_SCHEMA_FAIL:"
            + json.dumps(diagnostics[0], ensure_ascii=False, sort_keys=True)
        )
    expected_ids = {case["case_id"] for case in gold["cases"]}
    claim_ids = [item["case_id"] for item in response["claim_map"]]
    summary_ids = [item["case_id"] for item in response["analysis_summary"]]
    if len(claim_ids) != len(set(claim_ids)) or set(claim_ids) != expected_ids:
        raise ValueError("CLAIM_CASE_ID_PARTITION_FAIL")
    if len(summary_ids) != len(set(summary_ids)) or set(summary_ids) != expected_ids:
        raise ValueError("SUMMARY_CASE_ID_PARTITION_FAIL")
    gold_by_id = {case["case_id"]: case for case in gold["cases"]}
    for item in response["claim_map"]:
        if not item["claim_text"].strip():
            raise ValueError(f"EMPTY_CLAIM_TEXT:{item['case_id']}")
        arrays = [item[field] for field in ROLE_FIELDS.values()]
        flattened = [anchor for values in arrays for anchor in values]
        expected = set(gold_by_id[item["case_id"]]["valid_anchors"])
        if len(flattened) != len(set(flattened)):
            raise ValueError(f"ANCHOR_ROLE_OVERLAP:{item['case_id']}")
        if set(flattened) != expected:
            raise ValueError(f"ANCHOR_ROLE_UNION_MISMATCH:{item['case_id']}")
    if any(not item["summary_text"].strip() for item in response["analysis_summary"]):
        raise ValueError("EMPTY_SUMMARY_TEXT")


def perfect_response(gold: dict[str, Any]) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for case in gold["cases"]:
        by_field = {field: [] for field in ROLE_FIELDS.values()}
        for anchor in case["valid_anchors"]:
            role = case["allowed_anchor_roles"][anchor][0]
            by_field[ROLE_FIELDS[role]].append(anchor)
        claim = {
            "case_id": case["case_id"],
            "support_status": case["allowed_support_statuses"][0],
            "conclusion_code": case["allowed_conclusion_codes"][0],
            **by_field,
            "population_scope_code": case["allowed_population_scope_codes"][0],
            "time_scope_code": case["allowed_time_scope_codes"][0],
            "certainty": case["allowed_certainties"][0],
            "causation_asserted": case["causation_asserted"],
            "qualification_codes": case["qualification_codes"],
            "unresolved_conflict": case["unresolved_conflict"],
            "lineage_decision": case["allowed_lineage_decisions"][0],
            "claim_text": f"Evidence-bounded claim for {case['case_id']}.",
        }
        claims.append(claim)
        summaries.append(
            {
                "case_id": case["case_id"],
                "conclusion_code": claim["conclusion_code"],
                "summary_text": f"Evidence-bounded summary for {case['case_id']}.",
            }
        )
    return {"claim_map": claims, "analysis_summary": summaries}


def _recall(required: set[str], observed: set[str]) -> float:
    if not required:
        return 1.0
    return len(required & observed) / len(required)


def _allowed_precision(
    allowed: set[str], observed: set[str], *, required_nonempty: bool
) -> float:
    if not observed:
        return 0.0 if required_nonempty else 1.0
    return len(allowed & observed) / len(observed)


def _observed_roles(item: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for role, field in ROLE_FIELDS.items():
        for anchor in item[field]:
            result[anchor] = role
    return result


def score_response(
    response: dict[str, Any], gold: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    validate_response_shape(response, gold, schema)
    observed = {item["case_id"]: item for item in response["claim_map"]}
    summaries = {item["case_id"]: item for item in response["analysis_summary"]}
    details: list[dict[str, Any]] = []
    family_scores: dict[str, list[float]] = {family: [] for family in FAMILIES}
    axis_totals = {
        "literal_fidelity": 0.0,
        "evidence_scope_coverage": 0.0,
        "cross_artifact_consistency": 0.0,
        "scientific_plausibility": 0.0,
        "lineage_freshness": 0.0,
    }
    expected_by_id = {case["case_id"]: case for case in gold["cases"]}
    for expected in gold["cases"]:
        item = observed[expected["case_id"]]
        summary = summaries[expected["case_id"]]
        roles = _observed_roles(item)
        role_correct = sum(
            roles[anchor] in expected["allowed_anchor_roles"][anchor]
            for anchor in expected["valid_anchors"]
        )
        literal = (
            (8.0 if item["support_status"] in expected["allowed_support_statuses"] else 0.0)
            + (8.0 if item["conclusion_code"] in expected["allowed_conclusion_codes"] else 0.0)
            + (4.0 if item["certainty"] in expected["allowed_certainties"] else 0.0)
        )
        evidence = 25.0 * role_correct / len(expected["valid_anchors"])
        cross = (
            (5.0 if item["population_scope_code"] in expected["allowed_population_scope_codes"] else 0.0)
            + (5.0 if item["time_scope_code"] in expected["allowed_time_scope_codes"] else 0.0)
            + (5.0 if summary["conclusion_code"] == item["conclusion_code"] else 0.0)
            + (
                5.0
                if item["support_status"] in expected["allowed_support_statuses"]
                and item["conclusion_code"] in expected["allowed_conclusion_codes"]
                else 0.0
            )
        )
        required_quals = set(expected["qualification_codes"])
        allowed_quals = set(expected["allowed_qualification_codes"])
        observed_quals = set(item["qualification_codes"])
        qualifier_score = 6.0 * _recall(required_quals, observed_quals)
        qualifier_score += 4.0 * _allowed_precision(
            allowed_quals,
            observed_quals,
            required_nonempty=bool(required_quals),
        )
        scientific = (
            (10.0 if item["causation_asserted"] is expected["causation_asserted"] else 0.0)
            + qualifier_score
            + (5.0 if item["unresolved_conflict"] is expected["unresolved_conflict"] else 0.0)
        )
        lineage = (
            10.0
            if item["lineage_decision"] in expected["allowed_lineage_decisions"]
            else 0.0
        )
        score = round(literal + evidence + cross + scientific + lineage, 4)

        reasons: list[str] = []
        family = expected["family"]
        if family == "CORRELATION_NOT_CAUSATION" and item["causation_asserted"]:
            reasons.append("CAUSAL_OVERCLAIM_FROM_OBSERVATION")
        if family == "INSUFFICIENT_EVIDENCE" and (
            item["support_status"] == "DIRECT_SUPPORT" or item["causation_asserted"]
        ):
            reasons.append("UNSUPPORTED_KEY_CONCLUSION")
        if family == "CONFLICTING_EVIDENCE" and item["conclusion_code"] != "DIRECTION_UNRESOLVED":
            reasons.append("MATERIAL_COUNTEREVIDENCE_HIDDEN")
        if family == "POPULATION_SCOPE" and item["population_scope_code"] in {
            "TARGET_POPULATION",
            "GENERAL_POPULATION",
        }:
            reasons.append("POPULATION_EXTRAPOLATION")
        if family == "STALE_LINEAGE":
            has_explicit_invalid_partition = any(
                role == "LINEAGE_INVALID" for role in roles.values()
            )
            lineage_behaviour_valid = (
                item["lineage_decision"] in expected["allowed_lineage_decisions"]
                and has_explicit_invalid_partition
            )
            if not lineage_behaviour_valid:
                reasons.append("STALE_LINEAGE_NOT_EXCLUDED")
            invalid_in_support = set(item["supporting_anchors"]) & {
                anchor
                for anchor, allowed in expected["allowed_anchor_roles"].items()
                if "LINEAGE_INVALID" in allowed
            }
            if invalid_in_support:
                reasons.append("STALE_LINEAGE_USED_AS_SUPPORT")

        family_scores[family].append(score)
        axis_values = {
            "literal_fidelity": literal,
            "evidence_scope_coverage": evidence,
            "cross_artifact_consistency": cross,
            "scientific_plausibility": scientific,
            "lineage_freshness": lineage,
        }
        for axis, value in axis_values.items():
            axis_totals[axis] += value
        details.append(
            {
                "case_id": expected["case_id"],
                "family": family,
                "difficulty": expected["difficulty"],
                "score": score,
                "axes": {axis: round(value, 4) for axis, value in axis_values.items()},
                "anchor_role_accuracy": round(
                    role_correct / len(expected["valid_anchors"]), 4
                ),
                "expected_allowed_anchor_roles": expected["allowed_anchor_roles"],
                "observed_anchor_roles": roles,
                "expected_allowed": {
                    "support_statuses": expected["allowed_support_statuses"],
                    "conclusion_codes": expected["allowed_conclusion_codes"],
                    "population_scope_codes": expected["allowed_population_scope_codes"],
                    "time_scope_codes": expected["allowed_time_scope_codes"],
                    "certainties": expected["allowed_certainties"],
                    "lineage_decisions": expected["allowed_lineage_decisions"],
                    "qualification_codes": expected["allowed_qualification_codes"],
                },
                "observed": {
                    "support_status": item["support_status"],
                    "conclusion_code": item["conclusion_code"],
                    "population_scope_code": item["population_scope_code"],
                    "time_scope_code": item["time_scope_code"],
                    "certainty": item["certainty"],
                    "causation_asserted": item["causation_asserted"],
                    "qualification_codes": item["qualification_codes"],
                    "unresolved_conflict": item["unresolved_conflict"],
                    "lineage_decision": item["lineage_decision"],
                    "summary_conclusion_code": summary["conclusion_code"],
                },
                "critical_reasons": reasons,
            }
        )

    critical = [
        {"case_id": item["case_id"], "family": item["family"], "reason": reason}
        for item in details
        for reason in item["critical_reasons"]
    ]
    count = len(gold["cases"])
    return {
        "form_score": round(sum(item["score"] for item in details) / count, 2),
        "sample_count": count,
        "max_single_sample_effect_points": round(100 / count, 4),
        "family_scores": {
            family: round(sum(values) / len(values), 2)
            for family, values in family_scores.items()
        },
        "axis_scores": {
            axis: round(total / count, 2) for axis, total in axis_totals.items()
        },
        "critical_any": bool(critical),
        "critical_count": len(critical),
        "critical_findings": critical,
        "quality_hard_gate": "FAIL" if critical else "PASS",
        "details": details,
    }


def form_equivalence(
    form_a: dict[str, Any],
    gold_a: dict[str, Any],
    form_b: dict[str, Any],
    gold_b: dict[str, Any],
) -> dict[str, Any]:
    def counts(gold: dict[str, Any], key: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for case in gold["cases"]:
            result[case[key]] = result.get(case[key], 0) + 1
        return result

    a_units = {
        json.dumps(unit, ensure_ascii=False, sort_keys=True)
        for case in form_a["cases"]
        for unit in case["evidence_units"]
    }
    b_units = {
        json.dumps(unit, ensure_ascii=False, sort_keys=True)
        for case in form_b["cases"]
        for unit in case["evidence_units"]
    }
    a_sources = {
        unit["source_id"]
        for case in form_a["cases"]
        for unit in case["evidence_units"]
    }
    b_sources = {
        unit["source_id"]
        for case in form_b["cases"]
        for unit in case["evidence_units"]
    }
    a_ids = {case["case_id"] for case in gold_a["cases"]}
    b_ids = {case["case_id"] for case in gold_b["cases"]}
    checks = {
        "case_count_equal": len(form_a["cases"])
        == len(form_b["cases"])
        == len(FAMILIES) * CASES_PER_FAMILY,
        "family_counts_equal": counts(gold_a, "family")
        == counts(gold_b, "family")
        == {family: CASES_PER_FAMILY for family in FAMILIES},
        "difficulty_counts_equal": counts(gold_a, "difficulty")
        == counts(gold_b, "difficulty")
        == {"easy": 18, "medium": 24, "hard": 30},
        "evidence_units_each_exact": all(
            len(case["evidence_units"]) == EVIDENCE_UNITS_PER_CASE
            for case in form_a["cases"] + form_b["cases"]
        ),
        "source_identity_overlap_zero": not (a_sources & b_sources),
        "evidence_unit_hash_overlap_zero": not (a_units & b_units),
        "case_identity_overlap_zero": not (a_ids & b_ids),
    }
    return {
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "form_A_family_counts": counts(gold_a, "family"),
        "form_B_family_counts": counts(gold_b, "family"),
        "form_A_difficulty_counts": counts(gold_a, "difficulty"),
        "form_B_difficulty_counts": counts(gold_b, "difficulty"),
        "evidence_units_per_form": len(form_a["cases"])
        * EVIDENCE_UNITS_PER_CASE,
    }

"""Form scoring of the analysis reviewer exam on a small synthetic form (analysis_reviewer/executor.py).

_score_form_successor scores with core_v2 and then reads two of its hard gates by their consequence:
a clean control that is only held for revision is a calibration loss, not a safety block (a rejection
that retracts or downgrades it still is), and a critical defect that the reviewer blocks at the right
claim with the right evidence and action counts as contained even when its severity is one band low.
The points are kept either way. _machine_feedback names the wrong output fields of a case without the
expected values. The form, answer key, schema and protocol below are made up for the tests; nothing
calls a model and no exam pack is read.
"""
from __future__ import annotations

from copy import deepcopy
import json

from memorive.exams.analysis_reviewer.core_v2 import perfect_response
from memorive.exams.analysis_reviewer.executor import _machine_feedback, _score_form_successor

POINTER = "/candidate_analysis/claims/0/text"


def case(case_id):
    return {"case_id": case_id, "candidate_analysis": {"claims": [{"text": f"claim of {case_id}"}]},
            "evidence_units": [{"evidence_id": f"{case_id}-E1", "text": "source"},
                               {"evidence_id": f"{case_id}-E2", "text": "source"}]}


def defect(case_id, family, *, critical, difficulty, severity, verdict, actions):
    return {"case_id": case_id, "expected_issue_count": 1, "expected_verdict": verdict, "expected_issue_family": family,
            "accepted_claim_pointers": [POINTER], "required_evidence_refs": [f"{case_id}-E1"],
            "allowed_evidence_refs": [f"{case_id}-E1", f"{case_id}-E2"], "severity": severity, "blocking": True,
            "accepted_actions": actions, "critical": critical, "difficulty": difficulty}


def clean(case_id):
    return {"case_id": case_id, "expected_issue_count": 0, "expected_verdict": "ACCEPT", "expected_issue_family": "none",
            "accepted_claim_pointers": [], "required_evidence_refs": [], "allowed_evidence_refs": [f"{case_id}-E1"],
            "severity": "none", "blocking": False, "accepted_actions": [], "critical": False, "difficulty": "easy"}


GOLD = {"cases": [
    defect("D1", "numeric_misread", critical=True, difficulty="hard", severity="critical", verdict="REJECT",
           actions=["retract", "correct"]),
    defect("D2", "lineage_citation_mismatch", critical=False, difficulty="medium", severity="major",
           verdict="NEEDS_REVISION", actions=["correct"]),
    defect("D3", "scope_overreach", critical=True, difficulty="medium", severity="critical", verdict="REJECT",
           actions=["correct"]),
    *[clean(f"K{i}") for i in range(1, 6)],
]}
FORM = {"form_id": "SYNTHETIC", "cases": [case(item["case_id"]) for item in GOLD["cases"]]}
ISSUE = {"type": "object", "additionalProperties": False,
         "required": ["issue_family", "claim_pointer", "evidence_refs", "severity", "blocking", "action", "explanation"],
         "properties": {"issue_family": {"type": "string"}, "claim_pointer": {"type": "string"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "severity": {"enum": ["critical", "major", "minor"]}, "blocking": {"type": "boolean"},
                        "action": {"type": "string"}, "explanation": {"type": "string"}}}
SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
          "required": ["case_reviews"],
          "properties": {"case_reviews": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["case_id", "review_verdict", "issues", "review_summary"],
              "properties": {"case_id": {"type": "string"}, "review_verdict": {"enum": ["ACCEPT", "NEEDS_REVISION", "REJECT"]},
                             "issues": {"type": "array", "items": ISSUE}, "review_summary": {"type": "string"}}}}}}
POINTS = {"overall_semantic": 30, "hard_case": 15, "worst_family": 10, "bottom_quartile": 10, "clean_specificity": 10,
          "evidence_localization": 5, "critical_detection": 12, "stale_lineage_detection": 8}
PROTOCOL = {"piecewise_components": {name: {"knots": [[0, 0], [100, points]]} for name, points in POINTS.items()},
            "recommendation_bands": [{"code": "RECOMMENDED", "minimum": 90, "maximum_inclusive": 100},
                                     {"code": "CONDITIONAL", "minimum": 70, "maximum_exclusive": 90},
                                     {"code": "NOT_RECOMMENDED", "minimum": 0, "maximum_exclusive": 70}]}


def answers(**changes):
    """The right answers with some rows replaced: changes maps a case id to its new row fields."""
    response = perfect_response(GOLD)
    for row in response["case_reviews"]:
        row.update(deepcopy(changes.get(row["case_id"], {})))
    return response


def flagged(case_id, verdict, action):
    return {"review_verdict": verdict, "issues": [{
        "issue_family": "scope_overreach", "claim_pointer": POINTER, "evidence_refs": [f"{case_id}-E1"],
        "severity": "minor", "blocking": verdict == "REJECT", "action": action, "explanation": "doubtful"}]}


def score(response):
    return _score_form_successor(form=FORM, gold=GOLD, response=response, output_schema=SCHEMA, scoring_protocol=PROTOCOL)


def test_right_answers_score_100_and_pass_every_gate():
    result = score(answers())
    assert result["score"] == 100.0 and result["recommendation"] == "RECOMMENDED"
    assert result["quality_hard_gate_failures"] == [] and result["hard_gate_failures"] == []


def test_a_clean_control_held_for_revision_costs_points_but_is_no_safety_block():
    result = score(answers(K1=flagged("K1", "NEEDS_REVISION", "flag")))
    assert result["quality_hard_gate_failures"] == ["CLEAN_FALSE_POSITIVE_RATE_ABOVE_0_10"]
    assert result["hard_gate_failures"] == [] and result["hard_gates_pass"] is True
    classes = result["clean_false_positive_successor_classification"]
    assert classes["conservative_calibration_case_ids"] == ["K1"] and classes["destructive_safety_blocking_case_ids"] == []
    assert result["metrics"]["clean_specificity"] == 80.0 and result["score"] < 100.0


def test_a_clean_control_rejected_and_retracted_stays_a_safety_block():
    result = score(answers(K1=flagged("K1", "REJECT", "retract")))
    assert result["hard_gate_failures"] == ["CLEAN_FALSE_POSITIVE_RATE_ABOVE_0_10"]
    assert result["clean_false_positive_successor_classification"]["destructive_safety_blocking_case_ids"] == ["K1"]


def one_band_low():
    row = answers()["case_reviews"][0]
    row["issues"][0]["severity"] = "major"
    return answers(D1=row)


def test_a_blocked_critical_defect_one_severity_band_low_is_contained():
    result = score(one_band_low())
    assert result["quality_hard_gate_failures"] == ["MISS_ANY_DECISION_CRITICAL_CASE", "CRITICAL_DETECTION_RATE_BELOW_0_95"]
    assert result["hard_gate_failures"] == []
    containment = result["critical_containment_successor_classification"]
    assert containment["severity_calibration_case_ids"] == ["D1"]
    assert containment["operationally_uncontained_case_ids"] == []
    assert containment["operational_containment_rate"] == 100.0
    assert {row["case_id"]: row["score"] for row in result["case_scores"]}["D1"] == 95.0  # severity points lost


def test_an_accepted_critical_defect_is_a_miss():
    result = score(answers(D1={"review_verdict": "ACCEPT", "issues": []}))
    assert result["hard_gate_failures"] == ["MISS_ANY_DECISION_CRITICAL_CASE", "CRITICAL_DETECTION_RATE_BELOW_0_95"]
    containment = result["critical_containment_successor_classification"]
    assert containment["operationally_uncontained_case_ids"] == ["D1"]
    assert containment["operational_containment_rate"] == 50.0


def test_feedback_names_the_wrong_fields_without_the_expected_values():
    response = one_band_low()
    feedback = _machine_feedback(score(response), ["D1"], gold=GOLD, response=response)
    assert feedback == {"D1": {
        "failed_component_groups": ["severity_blocking_action"],
        "mismatched_output_fields": ["/issues/0/severity"],
        "mismatched_output_fields_disclose_expected_values": False,
        "must_reconsider_every_listed_field": True,
        "current_case_score": 95.0,
        "reference_values_disclosed": False,
    }}
    assert "critical" not in json.dumps(feedback)

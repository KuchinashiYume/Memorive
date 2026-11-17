"""Scoring of the card reviewer exam on the code's own synthetic forms (evaluation/card_reviewer_exam.py).

card_reviewer_exam_assets.build_form makes the two forms (72 cases each: five defect families and clean
controls) and their answer keys. The answers below are written from the key, then spoiled on purpose.
The tests follow one exam: the form score and its hard gates, the one repair round (it asks only for the
wrong cases and carries no answer key), the merge of the repaired rows and the score after the repair.
Nothing calls a model.
"""
from __future__ import annotations

import copy
import json

import pytest

from memorive.exams.evaluation import card_reviewer_exam as exam
from memorive.exams.evaluation import card_reviewer_exam_assets as assets

SCHEMA = assets.output_schema()
PROTOCOL = assets.scoring_protocol()


def answer(key):
    clean = key["expected_verdict"] == "PASS"
    return {
        "case_id": key["case_id"],
        "verdict": key["expected_verdict"],
        "issue_family": key["issue_family"],
        "severity": key["severity"],
        "target_pointer": key["accepted_target_pointers"][0],
        "status": key["expected_status"],
        "evidence_refs": [] if clean else list(key["required_evidence_refs"]),
        "action": key["accepted_actions"][0],
        "reason": "checked against the evidence",
    }


@pytest.fixture(scope="module")
def exam_forms():
    forms, golds = {}, {}
    for name in ("A", "B"):
        forms[name], golds[name] = assets.build_form(name)
    right = {name: {"reviews": [answer(key) for key in golds[name]["cases"]]} for name in forms}
    # Form A: the first case (critical, numeric direction) is passed, the sixth (a clean control) is failed.
    wrong = copy.deepcopy(right)
    wrong["A"]["reviews"][0]["verdict"] = "PASS"
    wrong["A"]["reviews"][5]["verdict"] = "FAIL"
    return forms, golds, right, wrong


def score(forms, golds, responses, name):
    return exam.score_form(form=forms[name], final_response=responses[name], gold=golds[name],
                           schema=SCHEMA, scoring_protocol=PROTOCOL)


def first_round(exam_forms):
    forms, golds, _right, wrong = exam_forms
    scores = {name: score(forms, golds, wrong, name) for name in forms}
    package = exam.build_exam_directed_repair_package(forms=forms, original_responses=wrong,
                                                      original_scores=scores, schema=SCHEMA)
    return scores, package


def after_repair(exam_forms, rows):
    forms, golds, _right, wrong = exam_forms
    scores, package = first_round(exam_forms)
    merged = exam.merge_exam_directed_repair(forms=forms, original_responses=wrong,
                                             repair_response={"form_A": {"reviews": rows}},
                                             schema=SCHEMA, repair_contract=package["contract"])
    return exam.score_exam_after_directed_repair(forms=forms, golds=golds, original_scores=scores,
                                                 merged_responses=merged["responses"], schema=SCHEMA,
                                                 scoring_protocol=PROTOCOL, repair_contract=package["contract"])


def test_right_answers_score_100_and_pass_the_hard_gates(exam_forms):
    forms, golds, right, _wrong = exam_forms
    result = score(forms, golds, right, "A")
    assert len(result["details"]) == 72
    assert result["score"] == 100.0
    assert result["hard_gate_verdict"] == "PASS" and result["hard_failures"] == []


def test_a_missed_critical_case_and_a_flagged_clean_control_fail_the_hard_gates(exam_forms):
    forms, golds, _right, wrong = exam_forms
    result = score(forms, golds, wrong, "A")
    assert result["score"] == 88.59375
    assert result["critical_missed"] == ["CRX-A-001"]
    assert result["clean_false_positive_cases"] == ["CRX-A-006"]
    assert result["hard_failures"] == ["MISS_ANY_CRITICAL_CASE", "SUBSTANTIVE_FALSE_POSITIVE_ON_ANY_CLEAN_CONTROL"]
    assert result["hard_gate_verdict"] == "FAIL"


def test_the_repair_round_asks_only_for_the_wrong_cases_and_carries_no_answer_key(exam_forms):
    _scores, package = first_round(exam_forms)
    contract = package["contract"]
    assert contract["wrong_item_exact_set"] == {"A": ["CRX-A-001", "CRX-A-006"], "B": []}
    assert contract["severity_categories"] == ["critical", "major"]
    assert [target["case_id"] for target in package["repair_targets"]["A"]] == ["CRX-A-001", "CRX-A-006"]
    schema_ids = package["repair_schema"]["properties"]["form_A"]["properties"]["reviews"]["items"]["properties"]["case_id"]["enum"]
    assert schema_ids == ["CRX-A-001", "CRX-A-006"]
    prompt = json.dumps(package["prompt"], ensure_ascii=False)
    assert "CRX-A-001" in prompt and "CRX-A-006" in prompt and "CRX-A-002" not in prompt
    for key_field in ("expected_verdict", "expected_status", "accepted_target_pointers", "required_evidence_refs"):
        assert key_field not in prompt


def test_a_complete_repair_scores_under_the_ceiling_less_the_penalty(exam_forms):
    _forms, golds, _right, _wrong = exam_forms
    result = after_repair(exam_forms, [answer(golds["A"]["cases"][0]), answer(golds["A"]["cases"][5])])
    assert result["status"] == "DIRECTED_REPAIR_SUCCEEDED" and result["qualification_verdict"] == "PASS"
    penalty = result["forms"]["A"]["repair_penalty"]
    # 2 of 72 cases (0.277778), the reviews artifact (0.5), critical and major (1.5), 5 issue codes (0.5).
    assert (penalty["wrong_item_volume"], penalty["artifact_category_penalty"],
            penalty["severity_category_penalty"], penalty["issue_categories_penalty"]) == (0.277778, 0.5, 1.5, 0.5)
    assert result["forms"]["A"]["successful_repair_ceiling_applied"] is True
    assert result["final_form_scores"] == {"A": 87.212222, "B": 100.0}  # A: 89.99 - 2.777778
    assert result["final_score"] == 93.606111


def test_a_wrong_case_left_after_the_repair_disqualifies_the_exam(exam_forms):
    _forms, golds, _right, wrong = exam_forms
    result = after_repair(exam_forms, [answer(golds["A"]["cases"][0]), wrong["A"]["reviews"][5]])
    assert result["status"] == "DIRECTED_REPAIR_FAILED" and result["qualification_verdict"] == "DISQUALIFIED"
    assert result["remaining_wrong_items"] == {"A": ["CRX-A-006"], "B": []}
    assert result["final_score"] == 0.0

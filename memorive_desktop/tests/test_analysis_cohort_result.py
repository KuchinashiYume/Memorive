"""The three-panel result of the analysis exam (QualityAnalysisExamExecutor._analysis_cohort_result).

Each panel record holds six cases per form, one per family, as an earlier panel run would have written
them: the scores after the repairs and, apart, the first-pass scores. The exam module is a stand-in
whose form score is the mean of the case scores. The tests check when the cohort is combined, which
panel and family a residual case is charged to, and the three outcomes: PASS, a corroborated FAIL, and
an uncorroborated low score that stays NOT_ASSESSED. Nothing calls a model.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from memorive.exams.analysis import QualityAnalysisExamExecutor, _AnalysisExamRun

FAMILIES = ("F1", "F2", "F3", "F4", "F5", "F6")
DIFFICULTY = {1: "hard", 2: "medium", 3: "easy"}
PACK_SHA = "A" * 64
EXAM = SimpleNamespace(score_analysis_form_v2=lambda *, raw_scoring: {"score": raw_scoring["form_score"]},
                       __file__=__file__)


def detail(case_id, family, slot, score):
    return {"case_id": case_id, "family": family, "difficulty": DIFFICULTY[slot], "score": score,
            "critical_reasons": []}


def panel(executor, slot, final, first=None):
    """final / first: {case_id: score} overrides; every other case scores 90 (first pass 85)."""
    first = first or {}
    selection, raw, initial = {}, {}, {}
    for form_id in ("A", "B"):
        ids = [f"{form_id}-S{slot}-{family}" for family in FAMILIES]
        selection[form_id] = [{"case_id": cid, "family": family, "difficulty": DIFFICULTY[slot]}
                              for cid, family in zip(ids, FAMILIES)]
        raw[form_id] = {"details": [detail(cid, family, slot, final.get(cid, 90.0)) for cid, family in zip(ids, FAMILIES)]}
        initial[form_id] = {"details": [detail(cid, family, slot, first.get(cid, 85.0)) for cid, family in zip(ids, FAMILIES)]}
    return {"panel_id": f"panel-{slot}", "sample_slot": slot, "cohort_basis": executor._panel_cohort_basis(PACK_SHA),
            "model_identity": executor._panel_model_identity({"kind": "API", "model_name": "model-x"}),
            "selection": selection, "raw_scores": raw, "initial_raw_scores": initial,
            "panel_score_diagnostic": 90, "first_pass_panel_score_diagnostic": 85.0, "semantic_repair_rounds": 1}


def cohort(tmp_path, records):
    executor = QualityAnalysisExamExecutor(scratch_root=tmp_path, quality_package="exam_pack",
                                           reference_pack_root=tmp_path / "a" / "b" / "c" / "pack")
    run = _AnalysisExamRun(target={"kind": "API", "model_name": "model-x"}, context={}, structured_chat=None,
                           plan={"sample_slot": 3, "reference_pack_sha256": PACK_SHA, "comparison_binding_sha256": "C" * 64},
                           requested_model="model-x", started=0.0, run_id="panel-3", run_root=tmp_path, state={})
    run.exam = EXAM
    run.verdict, run.result_reason = "NOT_ASSESSED", "ANALYSIS_PRIMARY_PANEL_COMPLETED_COHORT_PENDING"
    run.completed_panel_records = [record(executor) for record in records]
    executor._analysis_cohort_result(run)
    return run


def test_two_panels_are_not_combined_yet(tmp_path):
    run = cohort(tmp_path, [lambda e: panel(e, 1, {}), lambda e: panel(e, 2, {})])
    assert run.verdict == "NOT_ASSESSED" and run.result_reason == "ANALYSIS_PRIMARY_PANEL_COMPLETED_COHORT_PENDING"
    assert not (tmp_path / "panel_scores" / "cohort_result.json").exists()


def test_three_panels_without_material_residuals_pass_and_keep_the_first_pass_score_apart(tmp_path):
    run = cohort(tmp_path, [lambda e, s=slot: panel(e, s, {}) for slot in (1, 2, 3)])
    assert run.verdict == "PASS" and run.result_reason == "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_COMPLETED"
    assert run.form_scores == {"A": 90.0, "B": 90.0} and run.score == 90
    assert run.first_pass_form_scores == {"A": 85.0, "B": 85.0} and run.first_pass_role_ability_score == 85.0
    assert run.failed_item_count == 0 and run.quality_fail_admission is None
    assert run.stable_score_cache_eligible is True and run.cohort_sample_slots == [1, 2, 3]
    written = json.loads((tmp_path / "panel_scores" / "cohort_result.json").read_text(encoding="utf-8"))
    assert written["status"] == "PASS" and written["panel_ids"] == ["panel-1", "panel-2", "panel-3"]
    assert written["provider_received_answer_key"] is False


LOW = 0.0  # a form holds 18 cases (6 per panel) at 90; each case at 0 takes 5 points off its form


def test_material_cases_in_two_panels_and_two_families_fail_the_model(tmp_path):
    run = cohort(tmp_path, [lambda e: panel(e, 1, {"A-S1-F1": LOW, "B-S1-F1": LOW}),
                            lambda e: panel(e, 2, {"A-S2-F2": LOW, "B-S2-F2": LOW}),
                            lambda e: panel(e, 3, {})])
    assert run.form_scores == {"A": 80.0, "B": 80.0}
    # Every residual is charged to the panel whose selection holds it, with the score reason.
    rows = {(row["form_id"], row["case_id"]): (row["panel_id"], row["family"], row["failed_fields"])
            for row in json.loads((tmp_path / "panel_scores" / "cohort_result.json").read_text(encoding="utf-8"))["material_residuals"]}
    assert rows[("A", "A-S1-F1")] == ("panel-1", "F1", ["CASE_SCORE_BELOW_80_MATERIAL"])
    assert rows[("B", "B-S2-F2")] == ("panel-2", "F2", ["CASE_SCORE_BELOW_80_MATERIAL"])
    assert run.failed_item_count == 4
    # Two residuals per form across two panels and two families, but the score is 80: not a model FAIL.
    assert run.verdict == "PASS" and run.model_fail_projection["model_fail_established"] is False


def test_a_low_score_with_corroborated_residuals_fails_and_admits_the_quality_fail(tmp_path):
    low = {f"{form_id}-S{slot}-{family}": LOW for form_id in ("A", "B") for slot, family in ((1, "F1"), (2, "F2"), (2, "F3"))}
    run = cohort(tmp_path, [lambda e: panel(e, 1, low), lambda e: panel(e, 2, low), lambda e: panel(e, 3, {})])
    assert run.form_scores == {"A": 75.0, "B": 75.0} and run.score == 75
    assert run.verdict == "FAIL" and run.result_reason == "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_COMPLETED"
    projection = run.model_fail_projection
    assert projection["model_fail_established"] is True
    assert projection["model_fail_distinct_material_case_count"] == 6
    assert (projection["model_fail_distinct_panel_count"], projection["model_fail_distinct_family_count"]) == (2, 3)
    assert run.quality_fail_admission is not None


def test_a_low_score_with_residuals_in_one_family_stays_not_assessed(tmp_path):
    low = {f"{form_id}-S{slot}-F1": LOW for form_id in ("A", "B") for slot in (1, 2, 3)}
    run = cohort(tmp_path, [lambda e, s=slot: panel(e, s, low) for slot in (1, 2, 3)])
    assert run.score == 75
    assert run.verdict == "NOT_ASSESSED" and run.result_reason == "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_INCONCLUSIVE"
    assert run.model_fail_projection["operational_eligibility_verdict"] == "INCONCLUSIVE"
    assert run.model_fail_projection["model_fail_distinct_family_count"] == 1
    assert run.quality_fail_admission is None

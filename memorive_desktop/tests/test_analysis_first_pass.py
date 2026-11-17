"""The first pass of the analysis exam over forms A and B (QualityAnalysisExamExecutor._run_analysis_forms).

The protocol and exam modules are small stand-ins (the audit accepts rows that are the form's cases in
order with anchors of their own case) and the model under test is a list of fixed replies (the
executor's _stable_provider_call is replaced). The tests check how a form is split into shards, that
rows preloaded from an earlier panel are reused without a request, when a structural retry is asked
for, and what the run keeps. Nothing leaves the computer.
"""
from __future__ import annotations

import pytest

from memorive.exams.analysis import QualityAnalysisExamExecutor, _AnalysisExamRun
from memorive.exams.common import _sha256

ARTIFACTS = ("claim_map", "analysis_summary")


class Protocol:
    def shard_artifact_schema(self, full_schema, artifact, *, target_count):
        return {"artifact": artifact, "rows": target_count}

    def artifact_schema(self, full_schema, artifact):
        return {"artifact": artifact}

    def build_artifact_prompt(self, form, artifact, *, target_case_ids):
        return f"{artifact} for {','.join(target_case_ids)}"

    def score_response(self, response, gold, schema):
        return {"gold": gold, "rows": {artifact: len(rows) for artifact, rows in response.items()}}


class Exam:
    def audit_analysis_artifact(self, *, form, response, schema, artifact):
        cases = {case["case_id"]: case for case in form["cases"]}
        rows = response.get(artifact)
        defects = []
        if not isinstance(rows, list) or [row.get("case_id") for row in rows] != list(cases):
            defects.append({"code": "EXACT_SET_MISMATCH"})
        elif artifact == "claim_map":
            for row in rows:
                anchors = {unit["anchor"] for unit in cases[row["case_id"]]["evidence_units"]}
                defects += [{"code": "ANCHOR_UNKNOWN", "case_id": row["case_id"], "anchor": anchor}
                            for anchor in row["supporting_anchors"] if anchor not in anchors]
        return {"status": "FAIL" if defects else "PASS", "defects": defects, "defect_count": len(defects),
                "repair_exact_set": sorted({d["case_id"] for d in defects if "case_id" in d})}


def form(form_id, count):
    return {"form": form_id, "cases": [
        {"case_id": f"{form_id}{i}", "evidence_units": [{"anchor": f"{form_id}{i}-U01"}]} for i in range(1, count + 1)]}


def rows(artifact, ids, anchor="{}-U01"):
    if artifact == "claim_map":
        return [{"case_id": cid, "supporting_anchors": [anchor.format(cid)]} for cid in ids]
    return [{"case_id": cid, "summary": cid} for cid in ids]


def answer(**kwargs):
    request = kwargs["request"]
    return {request["artifact"]: rows(request["artifact"], request["target_case_ids"])}


def make_run(tmp_path, counts, context=None):
    run = _AnalysisExamRun(target={"kind": "API", "model_name": "model-x"}, context=context or {},
                           structured_chat=None, plan={"sample_slot": 1}, requested_model="model-x", started=0.0,
                           run_id="run-1", run_root=tmp_path,
                           state={"analysis_anchor_normalizations": [], "analysis_preloaded_initial_rows": []})
    run.protocol, run.exam, run.full_schema = Protocol(), Exam(), {"title": "full"}
    run.forms = {form_id: form(form_id, count) for form_id, count in counts.items()}
    run.golds = {"A": "gold A", "B": "gold B"}
    return run


def first_pass(tmp_path, run, provider_call):
    executor = QualityAnalysisExamExecutor(scratch_root=tmp_path, quality_package="exam_pack",
                                           reference_pack_root=tmp_path / "a" / "b" / "c" / "pack")
    calls = []

    def call(**kwargs):
        calls.append(kwargs)
        return provider_call(**kwargs)

    executor._stable_provider_call = call
    executor._run_analysis_forms(run)
    return calls


def test_a_form_of_more_cases_than_a_shard_takes_one_request_per_shard(tmp_path):
    run = make_run(tmp_path, {"A": 8, "B": 1})
    calls = first_pass(tmp_path, run, answer)
    assert [call["call_kind"] for call in calls] == [
        "FORM_A_CLAIM_MAP_SHARD_01", "FORM_A_CLAIM_MAP_SHARD_02", "FORM_A_ANALYSIS_SUMMARY_SHARD_01",
        "FORM_A_ANALYSIS_SUMMARY_SHARD_02", "FORM_B_CLAIM_MAP_SHARD_01", "FORM_B_ANALYSIS_SUMMARY_SHARD_01"]
    assert calls[0]["request"]["target_case_ids"] == [f"A{i}" for i in range(1, 7)]
    assert calls[1]["request"]["target_case_ids"] == ["A7", "A8"]
    assert calls[1]["response_schema"] == {"artifact": "claim_map", "rows": 2}
    assert [row["case_id"] for row in run.responses["A"]["claim_map"]] == [f"A{i}" for i in range(1, 9)]
    assert run.raw_scores["A"] == {"gold": "gold A", "rows": {"claim_map": 8, "analysis_summary": 8}}


def test_rows_preloaded_from_an_earlier_panel_are_reused(tmp_path):
    preloaded = rows("claim_map", ["A1"], anchor="U01")
    context = {"workflow_exam_preloaded_initial_artifacts": {"A:claim_map": {
        "sample_slot": 1, "requested_model": "model-x", "returned_model": "model-x", "rows": preloaded,
        "rows_sha256": _sha256(preloaded),
        "source_calls": [{"call_id": "call-01", "receipt_sha256": "A" * 64, "provider_output_sha256": "B" * 64}]}}}
    run = make_run(tmp_path, {"A": 2, "B": 1}, context)
    calls = first_pass(tmp_path, run, answer)
    assert calls[0]["call_kind"] == "FORM_A_CLAIM_MAP_SHARD_01"
    assert calls[0]["request"]["target_case_ids"] == ["A2"]
    assert run.responses["A"]["claim_map"][0] == {"case_id": "A1", "supporting_anchors": ["A1-U01"]}
    reused, = run.state["analysis_preloaded_initial_rows"]
    assert reused["reused_case_ids"] == ["A1"] and reused["missing_case_ids"] == ["A2"]
    assert reused["provider_calls_for_reused_rows"] == 0
    assert run.state["analysis_anchor_normalizations"][0]["transport_stage"] == "PRELOADED_INITIAL"


def test_a_shard_failing_its_audit_is_retried_once(tmp_path):
    run = make_run(tmp_path, {"A": 2, "B": 1})
    replies = iter([{"claim_map": rows("claim_map", ["A1", "A2"], anchor="{}-Z9")}])

    def provider_call(**kwargs):
        return next(replies, None) or answer(**kwargs)

    calls = first_pass(tmp_path, run, provider_call)
    assert [call["call_kind"] for call in calls][:2] == ["FORM_A_CLAIM_MAP_SHARD_01", "FORM_A_CLAIM_MAP_SHARD_01_REPAIR"]
    retry = calls[1]["request"]
    assert retry["phase"] == "ANALYSIS_STRUCTURAL_REPAIR" and retry["target_case_ids"] == ["A1", "A2"]
    assert retry["residual_projection"]["repair_exact_set"] == ["A1", "A2"]
    assert retry["current_response"] == {"claim_map": rows("claim_map", ["A1", "A2"], anchor="{}-Z9")}
    assert run.responses["A"]["claim_map"] == rows("claim_map", ["A1", "A2"])


def test_a_shard_still_wrong_after_the_retry_fails(tmp_path):
    run = make_run(tmp_path, {"A": 1, "B": 1})
    with pytest.raises(ValueError, match="^WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_CONTRACT_UNCLOSED$"):
        first_pass(tmp_path, run, lambda **kwargs: {"claim_map": []})
    assert run.responses == {}

"""One semantic repair round of the analysis exam (QualityAnalysisExamExecutor._run_analysis_semantic_repair).

The model under test is a list of fixed replies (the executor's _stable_provider_call is replaced) and
the exam module is a small stand-in whose audit accepts a form's rows when they are its cases in order
and every claim anchor belongs to its case. The tests check how the wrong cases of each form are split
into requests, what a request carries, how case-local anchors are expanded, when a structural retry is
asked for and which rows it replaces, the merged reply and the shard record. Nothing leaves the computer.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from memorive.exams.analysis import QualityAnalysisExamExecutor

ARTIFACTS = ("claim_map", "analysis_summary")


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
                defects += [{"code": "ANCHOR_UNKNOWN", "case_id": row["case_id"], "field": "supporting_anchors",
                             "anchor": anchor} for anchor in row["supporting_anchors"] if anchor not in anchors]
        exact = sorted({defect["case_id"] for defect in defects if "case_id" in defect})
        return {"status": "FAIL" if defects else "PASS", "defects": defects, "defect_count": len(defects),
                "repair_exact_set": exact, "audit_sha256": "A" * 64}


def case(case_id, size=50):
    return {"case_id": case_id, "text": "x" * size,
            "evidence_units": [{"anchor": f"{case_id}-U01"}, {"anchor": f"{case_id}-U02"}]}


def package(wrong, sizes=None):
    sizes = sizes or {}
    item = {"type": "object", "properties": {"case_id": {"type": "string"}}}
    form_schema = {"type": "object", "properties": {a: {"type": "array", "items": deepcopy(item)} for a in ARTIFACTS}}
    return {
        "contract": {"wrong_item_exact_set": wrong, "contract_sha256": "C" * 64},
        "repair_schema": {"type": "object", "required": ["form_A", "form_B"],
                          "properties": {"form_A": deepcopy(form_schema), "form_B": deepcopy(form_schema)}},
        "repair_targets": {form_id: [{"case_id": cid, "source_case": case(cid, sizes.get(cid, 50)),
                                      "issue_codes": ["CLAIM_WRONG"]} for cid in ids]
                           for form_id, ids in wrong.items()},
    }


def rows(form_id, ids, *, anchor="{}-U01"):
    return {f"form_{form_id}": {
        "claim_map": [{"case_id": cid, "supporting_anchors": [anchor.format(cid)]} for cid in ids],
        "analysis_summary": [{"case_id": cid, "summary": f"summary {cid}"} for cid in ids],
    }}


def run(tmp_path, replies, repair_package, *, round_number=1):
    executor = QualityAnalysisExamExecutor(scratch_root=tmp_path, quality_package="exam_pack",
                                           reference_pack_root=tmp_path / "a" / "b" / "c" / "pack")
    executor._quality = lambda: {"exam": Exam()}
    calls = []

    def provider_call(**kwargs):
        calls.append(kwargs)
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return deepcopy(reply)

    executor._stable_provider_call = provider_call
    state = {"analysis_anchor_normalizations": [], "analysis_semantic_repair_structural_retries": 0}
    merged = executor._run_analysis_semantic_repair(
        run_id="run-1", run_root=tmp_path / "run", state=state, target={"kind": "API"}, package=repair_package,
        lifecycle_contract={"contract_sha256": "L" * 64}, round_number=round_number, structured_chat=None)
    return merged, calls, state


def shards(tmp_path, round_number=1):
    path = tmp_path / "run" / "responses" / f"analysis_semantic_repair_round_{round_number}_shards.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_each_form_with_wrong_cases_is_asked_once(tmp_path):
    merged, calls, state = run(tmp_path, [rows("A", ["A1", "A2"]), rows("B", ["B1"])],
                               package({"A": ["A1", "A2"], "B": ["B1"]}))
    assert [call["call_kind"] for call in calls] == [
        "ANALYSIS_SEMANTIC_REPAIR_ROUND_1_FORM_A_CHUNK_1_OF_1", "ANALYSIS_SEMANTIC_REPAIR_ROUND_1_FORM_B_CHUNK_1_OF_1"]
    request = calls[0]["request"]
    assert request["wrong_item_exact_set"] == {"A": ["A1", "A2"]}
    assert [row["case_id"] for row in request["repair_targets"]["A"]] == ["A1", "A2"]
    assert request["transport_shard"]["chunk_count"] == 1 and request["semantic_repair_round"] == 1
    assert request["parent_contract_sha256"] == "C" * 64 and request["successor_lifecycle_sha256"] == "L" * 64
    schema = calls[0]["response_schema"]
    assert schema["required"] == ["form_A"] and list(schema["properties"]) == ["form_A"]
    assert schema["properties"]["form_A"]["properties"]["claim_map"]["items"]["properties"]["case_id"]["enum"] == ["A1", "A2"]
    assert merged == {"form_A": rows("A", ["A1", "A2"])["form_A"], "form_B": rows("B", ["B1"])["form_B"]}
    record = shards(tmp_path)
    assert [(s["form_id"], s["case_ids"], s["structural_retry_used"]) for s in record["shards"]] == [
        ("A", ["A1", "A2"], 0), ("B", ["B1"], 0)]
    assert record["provider_received_answer_key"] is False and state["analysis_semantic_repair_structural_retries"] == 0


def test_a_form_without_wrong_cases_is_not_asked(tmp_path):
    merged, calls, _ = run(tmp_path, [rows("B", ["B1"])], package({"A": [], "B": ["B1"]}), round_number=2)
    assert [call["call_kind"] for call in calls] == ["ANALYSIS_SEMANTIC_REPAIR_ROUND_2_FORM_B_CHUNK_1_OF_1"]
    assert list(merged) == ["form_B"]


def test_long_cases_are_split_into_requests_that_fit(tmp_path):
    ids = ["A1", "A2", "A3"]
    merged, calls, _ = run(tmp_path, [rows("A", ["A1"]), rows("A", ["A2"]), rows("A", ["A3"])],
                           package({"A": ids, "B": []}, sizes={cid: 14000 for cid in ids}))
    assert [call["call_kind"] for call in calls] == [
        f"ANALYSIS_SEMANTIC_REPAIR_ROUND_1_FORM_A_CHUNK_{i}_OF_3" for i in (1, 2, 3)]
    assert [call["request"]["transport_shard"]["chunk_index"] for call in calls] == [1, 2, 3]
    assert [row["case_id"] for row in merged["form_A"]["claim_map"]] == ids


def test_case_local_anchors_are_expanded_and_recorded(tmp_path):
    merged, _, state = run(tmp_path, [rows("A", ["A1"], anchor="U02")], package({"A": ["A1"], "B": []}))
    assert merged["form_A"]["claim_map"][0]["supporting_anchors"] == ["A1-U02"]
    assert state["analysis_anchor_normalizations"] == [{
        "form_id": "A", "artifact": "claim_map", "transport_stage": "SEMANTIC_REPAIR_ROUND_1", "shard_index": 1,
        "case_id": "A1", "field": "supporting_anchors", "item_index": 0, "observed_anchor": "U02",
        "normalized_anchor": "A1-U02", "rule": "EXPAND_UNAMBIGUOUS_CASE_LOCAL_ANCHOR_SUFFIX", "gold_consulted": False,
        "provider_call_required": False, "semantic_repair_round_increment": 0}]


def test_a_structural_retry_replaces_only_the_residual_rows(tmp_path):
    first = rows("A", ["A1", "A2"])
    first["form_A"]["claim_map"][1]["supporting_anchors"] = ["A2-Z9"]
    retry = rows("A", ["A2"])
    retry["form_A"]["analysis_summary"][0]["summary"] = "summary A2 again"
    merged, calls, state = run(tmp_path, [first, retry], package({"A": ["A1", "A2"], "B": []}))
    assert calls[1]["call_kind"] == "ANALYSIS_SEMANTIC_REPAIR_ROUND_1_FORM_A_CHUNK_1_STRUCTURAL_REPAIR"
    request = calls[1]["request"]
    assert request["residual_exact_set"] == {"A": ["A2"]}
    assert request["current_response"]["form_A"]["claim_map"] == [first["form_A"]["claim_map"][1]]
    assert request["residual_projections"]["claim_map"]["repair_exact_set"] == ["A2"]
    assert "analysis_summary" not in request["residual_projections"]
    assert [s["case_id"] for s in request["source_cases"]["A"]] == ["A2"]
    assert merged["form_A"]["claim_map"][1]["supporting_anchors"] == ["A2-U01"]
    assert merged["form_A"]["analysis_summary"][1]["summary"] == "summary A2 again"
    assert merged["form_A"]["analysis_summary"][0]["summary"] == "summary A1"
    assert state["analysis_semantic_repair_structural_retries"] == 1
    assert shards(tmp_path)["shards"][0]["structural_retry_used"] == 1


def test_a_shard_still_wrong_after_the_retry_fails(tmp_path):
    wrong = rows("A", ["A1"], anchor="{}-Z9")
    with pytest.raises(ValueError, match="^ANALYSIS_REPAIR_SHARD_CONTRACT_UNCLOSED$"):
        run(tmp_path, [wrong, wrong], package({"A": ["A1"], "B": []}))


def test_a_retry_with_other_rows_fails(tmp_path):
    wrong = rows("A", ["A1", "A2"], anchor="{}-Z9")
    with pytest.raises(ValueError, match="^ANALYSIS_REPAIR_SHARD_STRUCTURAL_EXACT_SET_MISMATCH$"):
        run(tmp_path, [wrong, rows("A", ["A2", "A1"])], package({"A": ["A1", "A2"], "B": []}))


@pytest.mark.parametrize("damage, reason", [
    (lambda p: p.update(contract=None), "ANALYSIS_REPAIR_PACKAGE_INVALID"),
    (lambda p: p["repair_targets"]["A"].reverse(), "ANALYSIS_REPAIR_TARGET_ORDER_INVALID"),
    (lambda p: p["contract"]["wrong_item_exact_set"].update(B="B1"), "ANALYSIS_REPAIR_PACKAGE_INVALID"),
])
def test_a_malformed_package_is_refused(tmp_path, damage, reason):
    repair_package = package({"A": ["A1", "A2"], "B": []})
    damage(repair_package)
    with pytest.raises(ValueError, match=f"^{reason}$"):
        run(tmp_path, [rows("A", ["A1", "A2"])], repair_package)


def test_a_reply_without_the_form_fails(tmp_path):
    with pytest.raises(ValueError, match="^ANALYSIS_REPAIR_SHARD_RESPONSE_INVALID$"):
        run(tmp_path, [{"form_A": []}], package({"A": ["A1"], "B": []}))

"""One shard of the card distiller exam: the first answer, the structural repair rounds, and the final
partition patch (QualityCardDistillerExamExecutor._run_shard).

The model under test is a list of fixed replies (the executor's _provider_call is replaced), and the
exam protocol is a small stand-in that accepts a shard when its rows are the target rows in order and,
for a card projection, every source case of a pack sits in exactly one partition field. The tests
check which calls the shard makes, what each repair request carries, the reply it settles on, and the
audits it writes. Nothing leaves the computer.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from memorive.exams.card_distiller.executor import QualityCardDistillerExamExecutor

FIELDS = ["research_question", "research_object", "method", "key_results", "author_conclusion",
          "boundary_conditions"]
FORM = {
    "form_id": "A",
    "cases": [{"case_id": "C1", "text": "one"}, {"case_id": "C2", "text": "two"}, {"case_id": "C9", "text": "nine"}],
    "packs": [{"pack_id": "P1", "case_ids": ["C1", "C2", "C3"]}, {"pack_id": "P2", "case_ids": ["C4"]},
              {"pack_id": "P9", "case_ids": ["C9"]}],
}
SCHEMA = {"type": "object", "title": "shard schema"}


class Protocol:
    """A stand-in for the exam pack's protocol module."""

    def build_artifact_prompt(self, form, artifact, *, target_ids, schema):
        return f"task {artifact} {','.join(target_ids)}"

    def audit_artifact(self, *, form, response, schema, artifact, target_ids):
        rows = response.get(artifact)
        key = "case_id" if artifact == "fact_map" else "pack_id"
        defects = []
        if not isinstance(rows, list) or [row.get(key) for row in rows] != target_ids:
            defects.append({"code": "TARGET_SET_MISMATCH"})
        elif artifact == "card_projection":
            source = {pack["pack_id"]: pack["case_ids"] for pack in form["packs"]}
            for row in rows:
                assigned = [case for field in FIELDS for case in row[field]["case_ids"]]
                if sorted(assigned) != sorted(source[row["pack_id"]]):
                    defects.append({"code": "PARTITION_NOT_EXACT", "pack_id": row["pack_id"]})
        return {"status": "FAIL" if defects else "PASS", "defects": defects}


def projection_row(pack_id, partition):
    row = {"pack_id": pack_id}
    for field in FIELDS:
        row[field] = {"text": field, "case_ids": list(partition.get(field, []))}
    return row


def run(tmp_path, monkeypatch, replies, *, artifact, target_ids):
    executor = QualityCardDistillerExamExecutor(
        scratch_root=tmp_path, quality_package="exam_pack", reference_pack_root=tmp_path / "pack")
    calls = []

    def provider_call(**kwargs):
        calls.append(kwargs)
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return deepcopy(reply)

    monkeypatch.setattr(executor, "_provider_call", provider_call)
    response = executor._run_shard(
        modules={"protocol": Protocol()}, run_id="run-1", stage_root=tmp_path / "stage", state={},
        target={"kind": "LOCAL"}, form=deepcopy(FORM), artifact=artifact, target_ids=target_ids,
        schema=SCHEMA, structured_chat=lambda **_: pytest.fail("the provider call is replaced"))
    return response, calls


def audits(tmp_path):
    return sorted(path.name for path in (tmp_path / "stage" / "audits").iterdir())


def test_a_shard_that_passes_first_is_kept(tmp_path, monkeypatch):
    good = {"fact_map": [{"case_id": "C1"}, {"case_id": "C2"}]}
    response, calls = run(tmp_path, monkeypatch, [good], artifact="fact_map", target_ids=["C1", "C2"])
    assert response == good
    assert [call["call_kind"] for call in calls] == ["INITIAL_FACT_MAP"]
    assert calls[0]["prompt"] == "task fact_map C1,C2" and calls[0]["response_schema"] == SCHEMA
    assert audits(tmp_path) == ["fact_map-C1-C2-initial.json"]


def test_a_failed_fact_map_is_repaired_with_its_defects_and_source(tmp_path, monkeypatch):
    bad = {"fact_map": [{"case_id": "C1"}]}
    good = {"fact_map": [{"case_id": "C1"}, {"case_id": "C2"}]}
    response, calls = run(tmp_path, monkeypatch, [bad, good], artifact="fact_map", target_ids=["C1", "C2"])
    assert response == good
    assert [call["call_kind"] for call in calls] == ["INITIAL_FACT_MAP", "STRUCTURAL_REPAIR_FACT_MAP_ROUND_1"]
    request = json.loads(calls[1]["prompt"])
    assert calls[1]["response_schema"] == SCHEMA
    assert request["defects"] == [{"code": "TARGET_SET_MISMATCH"}]
    assert request["current_response"] == bad and request["output_schema"] == SCHEMA
    assert request["target_source"] == [{"case_id": "C1", "text": "one"}, {"case_id": "C2", "text": "two"}]
    assert request["artifact_contract"]["row_identity"] == "case_id"
    assert request["partition_diagnostics"] == [] and request["repair_round"] == 1
    assert request["maximum_repair_rounds"] == 2 and len(request["source_task_prompt_sha256"]) == 64
    assert audits(tmp_path) == ["fact_map-C1-C2-initial.json", "fact_map-C1-C2-repaired.json"]


def test_a_shard_still_wrong_after_every_round_fails(tmp_path, monkeypatch):
    bad = {"fact_map": [{"case_id": "C2"}]}
    with pytest.raises(ValueError, match="^WORKFLOW_MODEL_EXAM_SHARD_CONTRACT_UNCLOSED$"):
        run(tmp_path, monkeypatch, [bad, bad, bad], artifact="fact_map", target_ids=["C1"])
    assert audits(tmp_path) == ["fact_map-C1-C1-initial.json", "fact_map-C1-C1-repair-02.json",
                                "fact_map-C1-C1-repaired.json"]


def test_a_failed_call_during_repair_ends_the_shard(tmp_path, monkeypatch):
    bad = {"fact_map": []}
    with pytest.raises(ValueError, match="^PROVIDER_TIMEOUT$"):
        run(tmp_path, monkeypatch, [bad, ValueError("PROVIDER_TIMEOUT")], artifact="fact_map", target_ids=["C1"])
    assert audits(tmp_path) == ["fact_map-C1-C1-initial.json"]


def test_the_last_round_of_a_projection_patches_only_the_partition(tmp_path, monkeypatch):
    # C1 twice, C3 missing in P1; P2 is right.
    wrong = {"card_projection": [
        projection_row("P1", {"method": ["C1", "C2"], "key_results": ["C1"]}),
        projection_row("P2", {"method": ["C4"]}),
    ]}
    still_wrong = deepcopy(wrong)
    still_wrong["card_projection"][0]["author_conclusion"]["case_ids"] = ["C7"]
    patch = {"decisions": [{"pack_id": "P1", "case_id": "C1", "field": "key_results"},
                           {"pack_id": "P1", "case_id": "C3", "field": "boundary_conditions"}]}
    response, calls = run(tmp_path, monkeypatch, [wrong, still_wrong, patch], artifact="card_projection",
                          target_ids=["P1", "P2"])
    assert [call["call_kind"] for call in calls] == [
        "INITIAL_CARD_PROJECTION", "STRUCTURAL_REPAIR_CARD_PROJECTION_ROUND_1",
        "DIRECTED_PARTITION_PATCH_CARD_PROJECTION_ROUND_2"]
    first_repair = json.loads(calls[1]["prompt"])
    assert first_repair["artifact_contract"]["partition_fields"] == FIELDS
    assert first_repair["partition_diagnostics"][0] == {
        "pack_id": "P1", "exact_source_case_ids": ["C1", "C2", "C3"], "expected_assignment_count": 3,
        "observed_assignment_count": 3, "observed_unique_count": 2, "missing_case_ids": ["C3"],
        "duplicate_case_ids": ["C1"], "extra_case_ids": [],
        "current_partition": {field: (["C1", "C2"] if field == "method" else ["C1"] if field == "key_results"
                                      else []) for field in FIELDS},
    }
    patch_request = json.loads(calls[2]["prompt"])
    assert patch_request["decision_targets"] == [
        {"pack_id": "P1", "case_id": "C1", "operation": "KEEP_ONE_REMOVE_DUPLICATES",
         "allowed_fields": ["method", "key_results"]},
        {"pack_id": "P1", "case_id": "C3", "operation": "ASSIGN_MISSING", "allowed_fields": FIELDS},
    ]
    assert patch_request["current_response"] == still_wrong
    decision = calls[2]["response_schema"]["properties"]["decisions"]
    assert decision["minItems"] == decision["maxItems"] == 2
    assert decision["items"]["properties"]["case_id"]["enum"] == ["C1", "C3"]
    assert decision["items"]["properties"]["pack_id"]["enum"] == ["P1", "P2"]
    p1 = response["card_projection"][0]
    assert {field: p1[field]["case_ids"] for field in FIELDS} == {
        "research_question": [], "research_object": [], "method": ["C2"], "key_results": ["C1"],
        "author_conclusion": [], "boundary_conditions": ["C3"]}
    assert response["card_projection"][1] == wrong["card_projection"][1]
    record = json.loads((tmp_path / "stage" / "partition_patches" / "card_projection-P1-P2-round-02.json")
                        .read_text(encoding="utf-8"))
    assert record["patch_response"] == patch and record["audit"]["status"] == "PASS"
    assert audits(tmp_path) == ["card_projection-P1-P2-initial.json", "card_projection-P1-P2-repair-02.json",
                                "card_projection-P1-P2-repaired.json"]


def test_a_patch_in_another_order_is_refused(tmp_path, monkeypatch):
    wrong = {"card_projection": [projection_row("P1", {"method": ["C1", "C2"], "key_results": ["C1"]})]}
    patch = {"decisions": [{"pack_id": "P1", "case_id": "C3", "field": "method"},
                           {"pack_id": "P1", "case_id": "C1", "field": "method"}]}
    with pytest.raises(ValueError, match="^WORKFLOW_MODEL_EXAM_PARTITION_DECISION_EXACT_SET_MISMATCH$"):
        run(tmp_path, monkeypatch, [wrong, wrong, patch], artifact="card_projection", target_ids=["P1"])


def test_a_patch_may_keep_a_duplicate_only_where_it_already_is(tmp_path, monkeypatch):
    wrong = {"card_projection": [projection_row("P1", {"method": ["C1", "C2"], "key_results": ["C1"]})]}
    patch = {"decisions": [{"pack_id": "P1", "case_id": "C1", "field": "research_question"},
                           {"pack_id": "P1", "case_id": "C3", "field": "method"}]}
    with pytest.raises(ValueError, match="^WORKFLOW_MODEL_EXAM_PARTITION_DECISION_FIELD_INVALID$"):
        run(tmp_path, monkeypatch, [wrong, wrong, patch], artifact="card_projection", target_ids=["P1"])


def test_a_last_round_with_nothing_to_patch_is_a_full_repair(tmp_path, monkeypatch):
    # After round 1 the only defect is a case from another pack: no case is missing or doubled.
    wrong = {"card_projection": []}
    extra = {"card_projection": [projection_row("P9", {"method": ["C9"], "key_results": ["C8"]})]}
    good = {"card_projection": [projection_row("P9", {"method": ["C9"]})]}
    response, calls = run(tmp_path, monkeypatch, [wrong, extra, good], artifact="card_projection",
                          target_ids=["P9"])
    assert response == good
    assert [call["call_kind"] for call in calls] == [
        "INITIAL_CARD_PROJECTION", "STRUCTURAL_REPAIR_CARD_PROJECTION_ROUND_1",
        "STRUCTURAL_REPAIR_CARD_PROJECTION_ROUND_2"]
    last = json.loads(calls[2]["prompt"])
    assert last["partition_diagnostics"][0]["extra_case_ids"] == ["C8"]
    assert last["defects"] == [{"code": "PARTITION_NOT_EXACT", "pack_id": "P9"}]
    assert not (tmp_path / "stage" / "partition_patches").exists()

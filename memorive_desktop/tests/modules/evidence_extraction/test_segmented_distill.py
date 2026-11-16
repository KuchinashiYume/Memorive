from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from memorive.documents.card_distillation.segmented_distill import ArtifactKind, CapacityProfile, CapacityRoute, SegmentCompletionLedger, SegmentShardValidator, SegmentedDistillContractError, build_source_manifest, build_topology, route_document
from memorive.documents.card_distillation.segmented_distill import assemble_card_candidate, build_assembly_manifest, build_field_candidates, dispatch_artifact_validation, dry_assemble, merge_facts, plan_bounded_segment_split
SCHEMA_ROOT = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "card_distillation") / "contracts" / "long_document"


def chunks() -> list[dict[str, object]]:
    return [
        {"chunk_id": "c001", "source_id": "src-1", "page_id": 1, "text": "Sample size was 120 participants.", "estimated_tokens": 9},
        {"chunk_id": "c002", "source_id": "src-1", "page_id": 2, "text": "Treatment dose was 50 mg daily.", "estimated_tokens": 9},
        {"chunk_id": "c003", "source_id": "src-1", "page_id": 3, "text": "Outcome improved by 12 points.", "estimated_tokens": 9},
        {"chunk_id": "c004", "source_id": "src-1", "page_id": 4, "text": "Follow-up duration was 24 weeks.", "estimated_tokens": 9},
    ]


def manifest() -> dict:
    return build_source_manifest(
        paper_id="paper-synthetic-001",
        chunks=chunks(),
        estimator_revision="estimator-v1",
    )


def topology() -> dict:
    return build_topology(
        manifest(), core_token_limit=10, overlap_unit_count=1, max_leaf_segments=8
    )


def source_ref(chunk: dict) -> dict:
    return {
        "chunk_id": chunk["chunk_id"],
        "source_id": chunk["source_id"],
        "page_id": chunk["page_id"],
        "quote": chunk["text"],
        "start_offset": 0,
        "end_offset": len(chunk["text"]),
    }


SLOTS = {
    "c001": ("sample_size", "120", "participants"),
    "c002": ("dose", "50", "mg"),
    "c003": ("outcome_improvement", "12", "points"),
    "c004": ("follow_up", "24", "weeks"),
}


def raw_shard(segment: dict, *, fact_for_core: bool = True) -> dict:
    manifest_chunks = {item["chunk_id"]: item for item in manifest()["chunks"]}
    core_id = segment["core_chunk_ids"][0]
    chunk = manifest_chunks[core_id]
    slot, value, unit = SLOTS[core_id]
    ref = source_ref(chunk)
    facts = []
    high_risk = []
    if fact_for_core:
        facts.append(
            {
                "local_fact_id": f"local-{core_id}",
                "claim_type": slot,
                "normalized_value_candidate": value,
                "unit": unit,
                "qualifiers": {"timepoint": "reported"},
                "occurrences": [deepcopy(ref)],
                "source_refs": [deepcopy(ref)],
                "slot_refs": [slot],
                "confidence_class": "high",
            }
        )
        high_risk.append({"kind": "number", "value": value, "source_ref": ref})
    return {
        "schema_version": "long_document-segment-fact-shard-v1",
        "artifact_kind": "segment_fact_shard",
        "paper_id": "paper-synthetic-001",
        "segment_id": segment["segment_id"],
        "attempt_id": f"attempt-{segment['sequence_index']}",
        "source_manifest_hash": manifest()["source_manifest_hash"],
        "topology_hash": topology()["topology_hash"],
        "expected_core_chunk_ids": segment["core_chunk_ids"],
        "observed_source_ids": [chunk["source_id"]] if fact_for_core else [],
        "terminal_marker": "LONG_DOCUMENT_SEGMENT_COMPLETE",
        "facts": facts,
        "slot_observations": [],
        "high_risk_tokens": high_risk,
        "local_conflicts": [],
        "capacity_state": "complete",
        "producer_receipt_ref": f"receipt://{segment['segment_id']}",
    }


def full_pipeline() -> tuple[dict, dict, list[dict], dict, list[dict], dict]:
    source = manifest()
    topo = topology()
    validator = SegmentShardValidator(source, topo)
    valid = [
        validator.validate(raw_shard(segment), provider_receipt={"finish_reason": "stop"})
        for segment in topo["segments"]
    ]
    ledger = SegmentCompletionLedger(topo, max_attempts_per_segment=2, max_total_calls=16)
    for shard in valid:
        ledger.record_attempt(
            segment_id=shard["segment_id"],
            attempt_id=shard["attempt_id"],
            state="terminal",
            receipt_ref=shard["producer_receipt_ref"],
        )
        assert ledger.admit(shard) == "ADMITTED"
    snapshot = ledger.snapshot()
    merged = merge_facts(
        ledger=snapshot,
        terminal_shards=ledger.terminal_shards(),
        required_slots=list(SLOTS.values())[0][0:0] or [item[0] for item in SLOTS.values()],
        critical_slots=[item[0] for item in SLOTS.values()],
    )
    fields = build_field_candidates(merged)
    assembly = build_assembly_manifest(
        completion_ledger=snapshot,
        merge_ledger=merged,
        field_candidates=fields,
        required_core_fields=[item[0] for item in SLOTS.values()],
        card_schema_sha256="A" * 64,
        prompt_revision="prompt-v1",
        profile_revision="profile-v1",
    )
    return source, topo, valid, snapshot, fields, assembly


def test_three_draft_2020_12_schemas_are_valid() -> None:
    schemas = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))]
    assert len(schemas) == 3
    for schema in schemas:
        Draft202012Validator.check_schema(schema)


def test_router_is_profile_aware_three_way_and_local() -> None:
    source = manifest()
    short = CapacityProfile("p", 100, 10, 5, 5, 1.0, 10, "estimator-v1")
    long = CapacityProfile("p", 45, 10, 5, 5, 1.0, 10, "estimator-v1")
    assert route_document(source, short).route is CapacityRoute.LEGACY_SINGLE_PASS
    assert route_document(source, long).route is CapacityRoute.SEGMENTED_LONG_DOCUMENT
    bad = deepcopy(source)
    bad["estimator_revision"] = "unknown"
    assert route_document(bad, long).route is CapacityRoute.BLOCKED_ROUTE_UNCERTAIN


def test_force_segment_is_comparison_only() -> None:
    profile = CapacityProfile("p", 100, 10, 5, 5, 1.0, 10, "estimator-v1")
    with pytest.raises(SegmentedDistillContractError, match="FORCE_SEGMENT_PRODUCTION_FORBIDDEN"):
        route_document(manifest(), profile, force_segment=True)
    decision = route_document(manifest(), profile, force_segment=True, comparison_mode=True)
    assert decision.route is CapacityRoute.SEGMENTED_LONG_DOCUMENT
    assert decision.force_segment_comparison is True


def test_topology_replay_is_deterministic_and_has_one_core_owner() -> None:
    first = topology()
    second = topology()
    assert first == second
    owners = [chunk_id for segment in first["segments"] for chunk_id in segment["core_chunk_ids"]]
    assert owners == ["c001", "c002", "c003", "c004"]
    assert all(
        set(segment["core_chunk_ids"]).isdisjoint(
            set(segment["overlap_before_chunk_ids"]) | set(segment["overlap_after_chunk_ids"])
        )
        for segment in first["segments"]
    )


def test_single_oversized_unsplittable_sentence_fails_closed() -> None:
    source = build_source_manifest(
        paper_id="p",
        chunks=[{"chunk_id": "huge", "text": "X" * 300, "estimated_tokens": 100}],
        estimator_revision="e",
    )
    with pytest.raises(SegmentedDistillContractError, match="CAPACITY_UNRESOLVED"):
        build_topology(source, core_token_limit=20)


def test_oversized_chunk_with_sentence_boundaries_routes_and_splits_deterministically() -> None:
    source = build_source_manifest(
        paper_id="p",
        chunks=[{
            "chunk_id": "large", "text": "First sentence has facts. Second sentence has more facts.",
            "estimated_tokens": 30,
        }],
        estimator_revision="e",
    )
    profile = CapacityProfile("p", 100, 10, 5, 5, 1.0, 16, "e")
    assert route_document(source, profile).route is CapacityRoute.SEGMENTED_LONG_DOCUMENT
    assert len(build_topology(source, core_token_limit=16, max_split_depth=1)["segments"]) == 2

    recovery_source = build_source_manifest(
        paper_id="recovery",
        chunks=[
            {"chunk_id": f"r{index}", "text": f"Sentence {index}.", "estimated_tokens": 3}
            for index in range(1, 4)
        ],
        estimator_revision="e",
    )
    topo = build_topology(recovery_source, core_token_limit=10, max_split_depth=1)
    assert len(topo["segments"]) == 1
    plan = plan_bounded_segment_split(topo, recovery_source, segment_id=topo["segments"][0]["segment_id"])
    assert len(plan["children"]) == 2
    assert plan["children"][0]["parent_segment_id"] == topo["segments"][0]["segment_id"]
    assert plan == plan_bounded_segment_split(topo, recovery_source, segment_id=topo["segments"][0]["segment_id"])


def test_valid_shard_passes_its_own_guard_not_whole_card_guard() -> None:
    source, topo = manifest(), topology()
    shard = raw_shard(topo["segments"][0])
    validated = dispatch_artifact_validation(
        expected_artifact_kind=ArtifactKind.SEGMENT_FACT_SHARD,
        payload=shard,
        shard_validator=lambda item: SegmentShardValidator(source, topo).validate(
            item, provider_receipt={"finish_reason": "stop"}
        ),
    )
    assert validated["validation_result"] == "PASS"
    assert "fields" not in validated


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "error"])
def test_balanced_or_structured_but_incomplete_provider_result_is_rejected(finish_reason: str) -> None:
    topo = topology()
    receipt = {"finish_reason": finish_reason}
    if finish_reason == "content_filter":
        receipt = {"finish_reason": "stop", "content_filter": True}
    if finish_reason == "error":
        receipt = {"finish_reason": "stop", "error": "synthetic"}
    with pytest.raises(SegmentedDistillContractError):
        SegmentShardValidator(manifest(), topo).validate(raw_shard(topo["segments"][0]), provider_receipt=receipt)


def test_card_shaped_payload_cannot_satisfy_shard_call_contract() -> None:
    with pytest.raises(SegmentedDistillContractError, match="ARTIFACT_KIND_MISMATCH"):
        dispatch_artifact_validation(
            expected_artifact_kind="segment_fact_shard",
            payload={"artifact_kind": "card_candidate", "fields": {}},
            shard_validator=lambda item: item,
        )


def test_quote_and_foreign_topology_fail_closed() -> None:
    topo = topology()
    bad_quote = raw_shard(topo["segments"][0])
    bad_quote["facts"][0]["occurrences"][0]["quote"] = "120 invented"
    bad_quote["facts"][0]["source_refs"] = deepcopy(bad_quote["facts"][0]["occurrences"])
    with pytest.raises(SegmentedDistillContractError, match="FACT_QUOTE_NOT_EXACT"):
        SegmentShardValidator(manifest(), topo).validate(bad_quote, provider_receipt={"finish_reason": "stop"})
    foreign = raw_shard(topo["segments"][0])
    foreign["topology_hash"] = "0" * 64
    with pytest.raises(SegmentedDistillContractError, match="FOREIGN_TOPOLOGY"):
        SegmentShardValidator(manifest(), topo).validate(foreign, provider_receipt={"finish_reason": "stop"})


def test_completion_ledger_idempotence_conflict_and_resume_scope() -> None:
    source, topo = manifest(), topology()
    validator = SegmentShardValidator(source, topo)
    first = validator.validate(raw_shard(topo["segments"][0]), provider_receipt={"finish_reason": "stop"})
    ledger = SegmentCompletionLedger(topo)
    ledger.record_attempt(segment_id=first["segment_id"], attempt_id=first["attempt_id"], state="terminal", receipt_ref="r")
    assert ledger.admit(first) == "ADMITTED"
    assert ledger.admit(deepcopy(first)) == "IDEMPOTENT_REPLAY"
    conflicting = deepcopy(first)
    conflicting["content_hash"] = "0" * 64
    with pytest.raises(SegmentedDistillContractError, match="CONFLICTING_DUPLICATE_SHARD"):
        ledger.admit(conflicting)
    plan = ledger.resume_plan()
    assert plan["reuse_terminal_segment_ids"] == [first["segment_id"]]
    assert first["segment_id"] not in plan["schedule_segment_ids"]
    assert plan["full_document_rerun"] is False


def test_overlap_only_fact_does_not_close_required_slot() -> None:
    source, topo = manifest(), topology()
    second = topo["segments"][1]
    raw = raw_shard(second, fact_for_core=False)
    overlap_id = second["overlap_before_chunk_ids"][0]
    chunk = {item["chunk_id"]: item for item in source["chunks"]}[overlap_id]
    slot, value, unit = SLOTS[overlap_id]
    ref = source_ref(chunk)
    raw["facts"] = [{
        "local_fact_id": "overlap-only", "claim_type": slot,
        "normalized_value_candidate": value, "unit": unit,
        "qualifiers": {"timepoint": "reported"}, "occurrences": [deepcopy(ref)],
        "source_refs": [deepcopy(ref)], "slot_refs": [slot], "confidence_class": "high"
    }]
    raw["observed_source_ids"] = [chunk["source_id"]]
    valid = SegmentShardValidator(source, topo).validate(raw, provider_receipt={"finish_reason": "stop"})
    ledger = SegmentCompletionLedger(topo)
    for segment in topo["segments"]:
        shard = valid if segment["segment_id"] == second["segment_id"] else SegmentShardValidator(source, topo).validate(raw_shard(segment, fact_for_core=False), provider_receipt={"finish_reason": "stop"})
        ledger.admit(shard)
    merged = merge_facts(
        ledger=ledger.snapshot(), terminal_shards=ledger.terminal_shards(),
        required_slots=[slot], critical_slots=[slot]
    )
    assert merged["slot_assessments"][slot]["state"] == "extraction_gap"


def test_conflicting_values_are_retained_without_voting() -> None:
    source, topo = manifest(), topology()
    validator = SegmentShardValidator(source, topo)
    valid = [validator.validate(raw_shard(segment), provider_receipt={"finish_reason": "stop"}) for segment in topo["segments"]]
    altered_raw = raw_shard(topo["segments"][2])
    altered_raw["facts"][0]["normalized_value_candidate"] = "Outcome improved by 13 points."
    altered_raw["facts"][0]["occurrences"][0]["quote"] = "Outcome improved by 12 points."
    altered_raw["facts"][0]["source_refs"] = deepcopy(altered_raw["facts"][0]["occurrences"])
    # A second value must still be source-anchored, so use a synthetic manifest successor.
    changed_chunks = chunks()
    changed_chunks[2] = {**changed_chunks[2], "text": "Outcome improved by 12 points; alternate analysis reported 13 points."}
    changed_source = build_source_manifest(paper_id="paper-synthetic-001", chunks=changed_chunks, estimator_revision="estimator-v1")
    changed_topo = build_topology(changed_source, core_token_limit=10, overlap_unit_count=1, max_leaf_segments=8)
    changed_validator = SegmentShardValidator(changed_source, changed_topo)
    ledger = SegmentCompletionLedger(changed_topo)
    shards = []
    for segment in changed_topo["segments"]:
        item = raw_shard(topology()["segments"][segment["sequence_index"] - 1])
        item["segment_id"] = segment["segment_id"]
        item["source_manifest_hash"] = changed_source["source_manifest_hash"]
        item["topology_hash"] = changed_topo["topology_hash"]
        if segment["sequence_index"] == 3:
            chunk = changed_source["chunks"][2]
            ref = source_ref(chunk)
            item["facts"] = []
            for value in ("12", "13"):
                item["facts"].append({
                    "local_fact_id": f"outcome-{value}", "claim_type": "outcome_improvement",
                    "normalized_value_candidate": value, "unit": "points",
                    "qualifiers": {"timepoint": "reported"}, "occurrences": [deepcopy(ref)],
                    "source_refs": [deepcopy(ref)], "slot_refs": ["outcome_improvement"], "confidence_class": "high"
                })
            item["high_risk_tokens"] = []
        shard = changed_validator.validate(item, provider_receipt={"finish_reason": "stop"})
        ledger.admit(shard)
        shards.append(shard)
    merged = merge_facts(
        ledger=ledger.snapshot(), terminal_shards=shards,
        required_slots=[item[0] for item in SLOTS.values()], critical_slots=[item[0] for item in SLOTS.values()]
    )
    assert merged["axes"]["conflict"] == "unresolved"
    assert merged["conflict_ledger"][0]["resolution"] == "UNRESOLVED_NO_VOTE_NO_AVERAGE_NO_LATEST_WINS"


def test_closed_pipeline_assembles_only_after_full_barrier_and_card_gate() -> None:
    _, _, _, snapshot, fields, assembly = full_pipeline()
    assert snapshot["recovery_state"] == "completed"
    assert assembly["barrier_result"] == "PASS"
    assert dry_assemble(
        completion_ledger=snapshot,
        merge_ledger={
            "content_hash": assembly["fact_merge_ledger_hash"],
            "segment_completion_ledger_hash": snapshot["content_hash"],
            "axes": assembly["axes"],
            "slot_assessments": {field: {"state": "present"} for field in assembly["required_core_fields"]},
        },
        field_candidates=fields,
        required_core_fields=assembly["required_core_fields"],
        card_schema_sha256="A" * 64,
        prompt_revision="prompt-v1",
        profile_revision="profile-v1",
    )["card_bytes_created"] is False

    def full_gate(card: dict) -> dict:
        assert card["review_status"] == "pending"
        assert set(card["fields"]) == set(assembly["required_core_fields"])
        assert all(value["source_refs"] for value in card["fields"].values())
        return card

    card = assemble_card_candidate(
        assembly_manifest=assembly,
        field_candidates=fields,
        full_card_validator=full_gate,
    )
    assert card["artifact_kind"] == "card_candidate"
    with pytest.raises(SegmentedDistillContractError, match="FULL_CARD_VALIDATOR_REQUIRED"):
        dispatch_artifact_validation(
            expected_artifact_kind="card_candidate", payload=card
        )


def test_missing_segment_and_target_collision_block_card_visibility() -> None:
    _, _, _, snapshot, fields, assembly = full_pipeline()
    incomplete = deepcopy(snapshot)
    incomplete["terminal_segment_ids"] = incomplete["terminal_segment_ids"][:-1]
    incomplete["missing_segment_ids"] = [snapshot["terminal_segment_ids"][-1]]
    incomplete["recovery_state"] = "recoverable_pending"
    blocked = build_assembly_manifest(
        completion_ledger=incomplete,
        merge_ledger={
            "content_hash": assembly["fact_merge_ledger_hash"],
            "segment_completion_ledger_hash": incomplete["content_hash"],
            "axes": assembly["axes"],
            "slot_assessments": {field: {"state": "present"} for field in assembly["required_core_fields"]},
        },
        field_candidates=fields,
        required_core_fields=assembly["required_core_fields"],
        card_schema_sha256="A" * 64,
        prompt_revision="prompt-v1",
        profile_revision="profile-v1",
        target_exists=True,
    )
    assert blocked["barrier_result"] == "BLOCK"
    assert blocked["card_candidate_visible"] is False
    assert "TARGET_CARD_ALREADY_EXISTS" in blocked["blocking_reasons"]


def test_schema_instances_validate_for_closed_pipeline() -> None:
    _, _, valid, snapshot, _, assembly = full_pipeline()
    schema_by_name = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in SCHEMA_ROOT.glob("*.schema.json")
    }
    Draft202012Validator(schema_by_name["segment_fact_shard_v1.schema.json"]).validate(raw_shard(topology()["segments"][0]))
    Draft202012Validator(schema_by_name["segment_completion_ledger_v1.schema.json"]).validate(snapshot)
    Draft202012Validator(schema_by_name["card_assembly_manifest_v1.schema.json"]).validate(assembly)
    assert len(valid) == 4

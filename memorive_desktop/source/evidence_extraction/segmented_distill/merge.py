"""Deterministic, paper-scoped fact merge and three-axis assessment."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import SegmentedDistillContractError, canonical_hash


SLOT_STATES = {
    "present",
    "absent_in_source",
    "not_applicable",
    "extraction_gap",
    "not_assessed",
}


def _conflict_key(fact: Mapping[str, Any]) -> str:
    return canonical_hash(
        {
            "claim_type": fact["claim_type"],
            "slot_refs": fact["slot_refs"],
            "qualifiers": fact.get("qualifiers") or {},
            "polarity": fact.get("polarity"),
            "uncertainty": fact.get("uncertainty"),
        }
    )


def merge_facts(
    *,
    ledger: Mapping[str, Any],
    terminal_shards: Sequence[Mapping[str, Any]],
    required_slots: Sequence[str],
    critical_slots: Sequence[str],
    absence_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if len(required_slots) != len(set(required_slots)) or not required_slots:
        raise SegmentedDistillContractError(
            "REQUIRED_SLOT_SET_INVALID", "required slots must be non-empty and unique"
        )
    if not set(critical_slots).issubset(required_slots):
        raise SegmentedDistillContractError(
            "CRITICAL_SLOT_SET_INVALID", "critical slots must be required slots"
        )
    expected = ledger["expected_segment_ids"]
    by_segment: dict[str, Mapping[str, Any]] = {}
    for shard in terminal_shards:
        segment_id = shard["segment_id"]
        if segment_id not in expected:
            raise SegmentedDistillContractError("ORPHAN_SEGMENT_ID", segment_id)
        if segment_id in by_segment:
            raise SegmentedDistillContractError("DUPLICATE_SHARD_INPUT", segment_id)
        if ledger["terminal_shard_hashes"].get(segment_id) != shard.get("content_hash"):
            raise SegmentedDistillContractError("STALE_SHARD_HASH", segment_id)
        by_segment[segment_id] = shard
    facts: dict[str, dict[str, Any]] = {}
    for segment_id in expected:
        shard = by_segment.get(segment_id)
        if not shard:
            continue
        for fact in shard["facts"]:
            fact_id = fact["fact_record_id"]
            if fact_id not in facts:
                facts[fact_id] = {
                    "fact_record_id": fact_id,
                    "claim_type": fact["claim_type"],
                    "normalized_value_candidate": fact["normalized_value_candidate"],
                    "unit": fact.get("unit"),
                    "qualifiers": deepcopy(fact.get("qualifiers") or {}),
                    "polarity": fact.get("polarity"),
                    "uncertainty": fact.get("uncertainty"),
                    "slot_refs": list(fact["slot_refs"]),
                    "occurrences": [],
                    "core_owned": False,
                    "source_segment_ids": [],
                }
            target = facts[fact_id]
            target["core_owned"] = target["core_owned"] or fact["core_owned"]
            if segment_id not in target["source_segment_ids"]:
                target["source_segment_ids"].append(segment_id)
            seen = {
                canonical_hash({key: value for key, value in item.items() if key != "core_owned"})
                for item in target["occurrences"]
            }
            for occurrence in fact["occurrences"]:
                identity = canonical_hash(
                    {key: value for key, value in occurrence.items() if key != "core_owned"}
                )
                if identity not in seen:
                    target["occurrences"].append(deepcopy(occurrence))
                    seen.add(identity)
    conflict_groups: dict[str, list[dict[str, Any]]] = {}
    for fact in facts.values():
        if fact["core_owned"]:
            conflict_groups.setdefault(_conflict_key(fact), []).append(fact)
    conflict_ledger: list[dict[str, Any]] = []
    conflicted_ids: set[str] = set()
    for key, candidates in conflict_groups.items():
        values = {
            (item["normalized_value_candidate"], item.get("unit")) for item in candidates
        }
        if len(values) > 1:
            conflicted_ids.update(item["fact_record_id"] for item in candidates)
            conflict_ledger.append(
                {
                    "conflict_key": key,
                    "fact_record_ids": sorted(item["fact_record_id"] for item in candidates),
                    "values": [
                        {"value": value, "unit": unit}
                        for value, unit in sorted(values, key=lambda item: (item[0], item[1] or ""))
                    ],
                    "resolution": "UNRESOLVED_NO_VOTE_NO_AVERAGE_NO_LATEST_WINS",
                }
            )
    absence_evidence = absence_evidence or {}
    ledger_closed = ledger["recovery_state"] == "completed" and not ledger["missing_segment_ids"]
    slot_assessments: dict[str, dict[str, Any]] = {}
    for slot in required_slots:
        candidates = [
            fact
            for fact in facts.values()
            if fact["core_owned"] and slot in fact["slot_refs"]
        ]
        conflicted = [item for item in candidates if item["fact_record_id"] in conflicted_ids]
        if conflicted:
            state, reason = "not_assessed", "UNRESOLVED_CONFLICT"
        elif candidates:
            state, reason = "present", "CORE_OWNED_FACT_PRESENT"
        elif ledger_closed and slot in absence_evidence:
            evidence = absence_evidence[slot]
            if evidence.get("relevant_segment_ids") != expected or evidence.get("result") != "ABSENT_IN_SOURCE":
                raise SegmentedDistillContractError("ABSENCE_EVIDENCE_INVALID", slot)
            state, reason = "absent_in_source", "CLOSED_RANGE_ABSENCE_EVIDENCE"
        elif ledger_closed:
            state, reason = "extraction_gap", "CLOSED_RANGE_WITHOUT_FACT_OR_ABSENCE_EVIDENCE"
        else:
            state, reason = "not_assessed", "SEGMENT_SET_NOT_CLOSED"
        slot_assessments[slot] = {
            "state": state,
            "reason_code": reason,
            "fact_record_ids": sorted(item["fact_record_id"] for item in candidates),
            "critical": slot in critical_slots,
        }
    capacity_state = (
        "capacity_blocked"
        if ledger["recovery_state"] == "capacity_blocked"
        else "within_budget"
        if ledger_closed
        else "unresolved"
    )
    coverage_state = (
        "complete"
        if ledger_closed and all(item["state"] in {"present", "absent_in_source", "not_applicable"} for item in slot_assessments.values())
        else "partial"
        if facts
        else "unresolved"
    )
    body = {
        "schema_version": "long_document-fact-merge-ledger-v1",
        "paper_id": ledger["paper_id"],
        "source_manifest_hash": ledger["source_manifest_hash"],
        "topology_hash": ledger["topology_hash"],
        "segment_completion_ledger_hash": ledger["content_hash"],
        "fact_ledger": sorted(facts.values(), key=lambda item: item["fact_record_id"]),
        "conflict_ledger": sorted(conflict_ledger, key=lambda item: item["conflict_key"]),
        "slot_assessments": slot_assessments,
        "axes": {
            "coverage": coverage_state,
            "conflict": "unresolved" if conflict_ledger else "none",
            "capacity": capacity_state,
        },
    }
    return {**body, "content_hash": canonical_hash(body)}


__all__ = ["SLOT_STATES", "merge_facts"]

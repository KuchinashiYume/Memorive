"""Segment shard validation and create-only completion ledger."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import (
    ArtifactKind,
    SegmentedDistillContractError,
    assert_artifact_kind,
    canonical_hash,
    exact_keys,
    text,
)
from .topology import segment_exact_set


SHARD_REQUIRED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "paper_id",
    "segment_id",
    "attempt_id",
    "source_manifest_hash",
    "topology_hash",
    "expected_core_chunk_ids",
    "observed_source_ids",
    "terminal_marker",
    "facts",
    "slot_observations",
    "high_risk_tokens",
    "local_conflicts",
    "capacity_state",
    "producer_receipt_ref",
}


def _segment_map(topology: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {item["segment_id"]: item for item in topology["segments"]}


def _chunk_map(source_manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {item["chunk_id"]: item for item in source_manifest["chunks"]}


def _span_allows(
    spans: Sequence[Mapping[str, Any]], chunk_id: str, start: int, end: int
) -> bool:
    return any(
        item["chunk_id"] == chunk_id
        and item["start_offset"] <= start
        and end <= item["end_offset"]
        for item in spans
    )


def _normalize_occurrence(
    occurrence: Mapping[str, Any],
    *,
    allowed_spans: Sequence[Mapping[str, Any]],
    core_spans: Sequence[Mapping[str, Any]],
    chunks: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    exact_keys(
        occurrence,
        required={"chunk_id", "source_id", "page_id", "quote", "start_offset", "end_offset"},
        label="FactOccurrence",
    )
    chunk_id = text(occurrence["chunk_id"], "occurrence.chunk_id")
    if chunk_id not in chunks:
        raise SegmentedDistillContractError(
            "SOURCE_REF_UNKNOWN_CHUNK", chunk_id
        )
    chunk = chunks[chunk_id]
    if occurrence["source_id"] != chunk["source_id"] or occurrence["page_id"] != chunk["page_id"]:
        raise SegmentedDistillContractError(
            "SOURCE_IDENTITY_MISMATCH", chunk_id
        )
    start, end = occurrence["start_offset"], occurrence["end_offset"]
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(chunk["text"]):
        raise SegmentedDistillContractError(
            "SOURCE_OFFSET_INVALID", chunk_id
        )
    quote = text(occurrence["quote"], "occurrence.quote")
    if chunk["text"][start:end] != quote:
        raise SegmentedDistillContractError(
            "FACT_QUOTE_NOT_EXACT", chunk_id
        )
    if not _span_allows(allowed_spans, chunk_id, start, end):
        raise SegmentedDistillContractError(
            "FACT_REFERENCE_OUTSIDE_SEGMENT", chunk_id
        )
    return {
        **dict(occurrence),
        "core_owned": _span_allows(core_spans, chunk_id, start, end),
    }


def _fact_identity(paper_id: str, fact: Mapping[str, Any]) -> str:
    qualifiers = fact.get("qualifiers") or {}
    identity = {
        "paper_id": paper_id,
        "claim_type": fact["claim_type"],
        "normalized_value_candidate": fact["normalized_value_candidate"],
        "unit": fact.get("unit"),
        "qualifiers": qualifiers,
        "polarity": fact.get("polarity"),
        "uncertainty": fact.get("uncertainty"),
        "formula_or_citation_identity": fact.get("formula_or_citation_identity"),
    }
    return "fr_" + canonical_hash(identity)[:32]


class SegmentShardValidator:
    """Validate one response against its frozen call contract."""

    def __init__(self, source_manifest: Mapping[str, Any], topology: Mapping[str, Any]) -> None:
        self.source_manifest = source_manifest
        self.topology = topology
        self.segments = _segment_map(topology)
        self.chunks = _chunk_map(source_manifest)

    def validate(
        self,
        shard: Mapping[str, Any],
        *,
        provider_receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert_artifact_kind(ArtifactKind.SEGMENT_FACT_SHARD, shard)
        exact_keys(shard, required=SHARD_REQUIRED_FIELDS, label="SegmentFactShard")
        finish_reason = provider_receipt.get("finish_reason")
        if provider_receipt.get("error") or provider_receipt.get("aborted"):
            raise SegmentedDistillContractError(
                "PROVIDER_ATTEMPT_NOT_COMPLETE", "provider error or abort"
            )
        if finish_reason != "stop":
            raise SegmentedDistillContractError(
                "PROVIDER_FINISH_REASON_NOT_COMPLETE", repr(finish_reason)
            )
        if provider_receipt.get("content_filter"):
            raise SegmentedDistillContractError(
                "PROVIDER_CONTENT_FILTERED", "filtered response cannot merge"
            )
        segment_id = shard["segment_id"]
        if segment_id not in self.segments:
            raise SegmentedDistillContractError(
                "ORPHAN_SEGMENT_ID", str(segment_id)
            )
        segment = self.segments[segment_id]
        if shard["paper_id"] != self.source_manifest["paper_id"]:
            raise SegmentedDistillContractError("FOREIGN_PAPER", str(shard["paper_id"]))
        if shard["source_manifest_hash"] != self.source_manifest["source_manifest_hash"]:
            raise SegmentedDistillContractError(
                "FOREIGN_SOURCE_MANIFEST", str(shard["source_manifest_hash"])
            )
        if shard["topology_hash"] != self.topology["topology_hash"]:
            raise SegmentedDistillContractError(
                "FOREIGN_TOPOLOGY", str(shard["topology_hash"])
            )
        if shard["expected_core_chunk_ids"] != segment["core_chunk_ids"]:
            raise SegmentedDistillContractError(
                "CORE_CHUNK_EXACT_SET_MISMATCH", segment_id
            )
        if shard["terminal_marker"] != "LONG_DOCUMENT_SEGMENT_COMPLETE":
            raise SegmentedDistillContractError(
                "TERMINAL_MARKER_MISSING", segment_id
            )
        if shard["capacity_state"] != "complete":
            raise SegmentedDistillContractError(
                "SHARD_CAPACITY_NOT_COMPLETE", str(shard["capacity_state"])
            )
        text(shard["attempt_id"], "attempt_id")
        text(shard["producer_receipt_ref"], "producer_receipt_ref")
        allowed_spans = (
            segment["core_source_spans"]
            + segment["overlap_before_source_spans"]
            + segment["overlap_after_source_spans"]
        )
        observed_ids = shard["observed_source_ids"]
        if not isinstance(observed_ids, list) or len(observed_ids) != len(set(observed_ids)):
            raise SegmentedDistillContractError(
                "OBSERVED_SOURCE_SET_INVALID", segment_id
            )
        allowed_source_ids = {
            self.chunks[span["chunk_id"]]["source_id"] for span in allowed_spans
        }
        if not set(observed_ids).issubset(allowed_source_ids):
            raise SegmentedDistillContractError(
                "OBSERVED_SOURCE_OUTSIDE_SEGMENT", segment_id
            )
        facts = shard["facts"]
        if not isinstance(facts, list):
            raise SegmentedDistillContractError("FACT_LIST_INVALID", segment_id)
        normalized_facts: list[dict[str, Any]] = []
        local_ids: set[str] = set()
        for fact_index, fact in enumerate(facts):
            exact_keys(
                fact,
                required={
                    "local_fact_id",
                    "claim_type",
                    "normalized_value_candidate",
                    "qualifiers",
                    "occurrences",
                    "source_refs",
                    "slot_refs",
                    "confidence_class",
                },
                optional={"unit", "polarity", "uncertainty", "formula_or_citation_identity", "composite", "join_evidence"},
                label=f"facts[{fact_index}]",
            )
            local_id = text(fact["local_fact_id"], "local_fact_id")
            if local_id in local_ids:
                raise SegmentedDistillContractError(
                    "LOCAL_FACT_ID_DUPLICATE", local_id
                )
            local_ids.add(local_id)
            text(fact["claim_type"], "claim_type")
            value = text(fact["normalized_value_candidate"], "normalized_value_candidate")
            if not isinstance(fact["qualifiers"], Mapping):
                raise SegmentedDistillContractError("FACT_QUALIFIERS_INVALID", local_id)
            if fact["confidence_class"] not in {"high", "medium", "low", "needs_review"}:
                raise SegmentedDistillContractError("CONFIDENCE_CLASS_INVALID", local_id)
            if not isinstance(fact["slot_refs"], list) or not fact["slot_refs"]:
                raise SegmentedDistillContractError("FACT_SLOT_REFS_MISSING", local_id)
            occurrences = [
                _normalize_occurrence(
                    item,
                    allowed_spans=allowed_spans,
                    core_spans=segment["core_source_spans"],
                    chunks=self.chunks,
                )
                for item in fact["occurrences"]
            ]
            if not occurrences or not any(value in item["quote"] for item in occurrences):
                raise SegmentedDistillContractError(
                    "FACT_VALUE_NOT_ANCHORED", local_id
                )
            if fact["source_refs"] != fact["occurrences"]:
                raise SegmentedDistillContractError(
                    "FACT_SOURCE_REFS_DIVERGE_FROM_OCCURRENCES", local_id
                )
            if fact.get("composite") and len({item["chunk_id"] for item in occurrences}) > 1 and not fact.get("join_evidence"):
                raise SegmentedDistillContractError(
                    "SYNTHETIC_COMPOSITE_FACT_FORBIDDEN", local_id
                )
            normalized_facts.append(
                {
                    **deepcopy(dict(fact)),
                    "occurrences": occurrences,
                    "source_refs": occurrences,
                    "fact_record_id": _fact_identity(shard["paper_id"], fact),
                    "core_owned": any(item["core_owned"] for item in occurrences),
                }
            )
        high_risk = shard["high_risk_tokens"]
        if not isinstance(high_risk, list):
            raise SegmentedDistillContractError("HIGH_RISK_TOKEN_LIST_INVALID", segment_id)
        for token in high_risk:
            if token.get("kind") not in {
                "number", "unit", "doi", "formula", "quotation", "p_value",
                "confidence_interval", "sample_size", "dose", "year",
            }:
                raise SegmentedDistillContractError(
                    "HIGH_RISK_TOKEN_KIND_INVALID", repr(token.get("kind"))
                )
            value = text(token.get("value"), "high_risk_token.value")
            source_ref = _normalize_occurrence(
                token.get("source_ref", {}),
                allowed_spans=allowed_spans,
                core_spans=segment["core_source_spans"],
                chunks=self.chunks,
            )
            if value not in source_ref["quote"]:
                raise SegmentedDistillContractError(
                    "HIGH_RISK_TOKEN_NOT_EXACT", value
                )
        normalized_body = {
            **deepcopy(dict(shard)),
            "facts": normalized_facts,
            "validation_result": "PASS",
        }
        return {**normalized_body, "content_hash": canonical_hash(normalized_body)}


class SegmentCompletionLedger:
    """Immutable terminal-shard set plus append-only failed-attempt receipts."""

    def __init__(
        self,
        topology: Mapping[str, Any],
        *,
        max_attempts_per_segment: int = 2,
        max_total_calls: int = 128,
    ) -> None:
        if max_attempts_per_segment < 1 or max_total_calls < 1:
            raise SegmentedDistillContractError(
                "RECOVERY_BUDGET_INVALID", "attempt and call ceilings must be positive"
            )
        self.paper_id = topology["paper_id"]
        self.topology_hash = topology["topology_hash"]
        self.source_manifest_hash = topology["source_manifest_hash"]
        self.expected = segment_exact_set(topology)
        self.max_attempts_per_segment = max_attempts_per_segment
        self.max_total_calls = max_total_calls
        self._terminal: dict[str, dict[str, Any]] = {}
        self._attempts: list[dict[str, Any]] = []

    def record_attempt(
        self,
        *,
        segment_id: str,
        attempt_id: str,
        state: str,
        receipt_ref: str,
        reason_code: str | None = None,
    ) -> None:
        if segment_id not in self.expected:
            raise SegmentedDistillContractError("ORPHAN_SEGMENT_ID", segment_id)
        if any(item["attempt_id"] == attempt_id for item in self._attempts):
            raise SegmentedDistillContractError("ATTEMPT_ID_DUPLICATE", attempt_id)
        segment_attempts = sum(item["segment_id"] == segment_id for item in self._attempts)
        if segment_attempts >= self.max_attempts_per_segment or len(self._attempts) >= self.max_total_calls:
            raise SegmentedDistillContractError("RECOVERY_BUDGET_EXHAUSTED", segment_id)
        if state not in {"terminal", "invalid_for_merge", "retryable", "capacity_blocked", "foreign_input"}:
            raise SegmentedDistillContractError("ATTEMPT_STATE_INVALID", state)
        self._attempts.append(
            {
                "segment_id": segment_id,
                "attempt_id": text(attempt_id, "attempt_id"),
                "state": state,
                "receipt_ref": text(receipt_ref, "receipt_ref"),
                "reason_code": reason_code,
            }
        )

    def admit(self, shard: Mapping[str, Any]) -> str:
        segment_id = shard["segment_id"]
        if segment_id not in self.expected:
            raise SegmentedDistillContractError("ORPHAN_SEGMENT_ID", segment_id)
        if shard.get("validation_result") != "PASS" or not shard.get("content_hash"):
            raise SegmentedDistillContractError("UNVALIDATED_SHARD_FORBIDDEN", segment_id)
        existing = self._terminal.get(segment_id)
        if existing:
            if existing["content_hash"] != shard["content_hash"]:
                raise SegmentedDistillContractError(
                    "CONFLICTING_DUPLICATE_SHARD", segment_id
                )
            return "IDEMPOTENT_REPLAY"
        self._terminal[segment_id] = deepcopy(dict(shard))
        return "ADMITTED"

    def snapshot(self) -> dict[str, Any]:
        terminal_ids = tuple(item for item in self.expected if item in self._terminal)
        missing = tuple(item for item in self.expected if item not in self._terminal)
        capacity_blocked = tuple(
            sorted({item["segment_id"] for item in self._attempts if item["state"] == "capacity_blocked"})
        )
        if not missing:
            state = "completed"
        elif capacity_blocked:
            state = "capacity_blocked"
        elif len(self._attempts) >= self.max_total_calls:
            state = "capacity_blocked"
        else:
            state = "recoverable_pending"
        body = {
            "schema_version": "long_document-segment-completion-ledger-v1",
            "paper_id": self.paper_id,
            "source_manifest_hash": self.source_manifest_hash,
            "topology_hash": self.topology_hash,
            "expected_segment_ids": list(self.expected),
            "terminal_segment_ids": list(terminal_ids),
            "missing_segment_ids": list(missing),
            "terminal_shard_hashes": {
                segment_id: self._terminal[segment_id]["content_hash"]
                for segment_id in terminal_ids
            },
            "attempts": deepcopy(self._attempts),
            "max_attempts_per_segment": self.max_attempts_per_segment,
            "max_total_calls": self.max_total_calls,
            "recovery_state": state,
        }
        return {**body, "content_hash": canonical_hash(body)}

    def terminal_shards(self) -> list[dict[str, Any]]:
        return [deepcopy(self._terminal[item]) for item in self.expected if item in self._terminal]

    def resume_plan(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        runnable: list[str] = []
        blocked: list[str] = []
        for segment_id in snapshot["missing_segment_ids"]:
            attempts = sum(item["segment_id"] == segment_id for item in self._attempts)
            (runnable if attempts < self.max_attempts_per_segment else blocked).append(segment_id)
        return {
            "reuse_terminal_segment_ids": snapshot["terminal_segment_ids"],
            "schedule_segment_ids": runnable,
            "blocked_segment_ids": blocked,
            "full_document_rerun": False,
        }


__all__ = ["SegmentCompletionLedger", "SegmentShardValidator"]

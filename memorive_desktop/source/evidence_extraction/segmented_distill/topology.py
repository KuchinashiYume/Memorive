"""Deterministic source manifests and segment topology."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from .contracts import SegmentedDistillContractError, canonical_hash, text


def build_source_manifest(
    *,
    paper_id: str,
    chunks: Sequence[Mapping[str, Any]],
    estimator_revision: str,
) -> dict[str, Any]:
    text(paper_id, "paper_id")
    text(estimator_revision, "estimator_revision")
    if not chunks:
        raise SegmentedDistillContractError(
            "SOURCE_MANIFEST_EMPTY", "at least one original chunk is required"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, source in enumerate(chunks, start=1):
        chunk_id = text(source.get("chunk_id"), f"chunks[{index}].chunk_id")
        if chunk_id in seen:
            raise SegmentedDistillContractError(
                "SOURCE_CHUNK_ID_NOT_UNIQUE", chunk_id
            )
        seen.add(chunk_id)
        chunk_text = source.get("text")
        text(chunk_text, f"chunks[{index}].text")  # validate without trimming source coordinates
        estimated = source.get("estimated_tokens")
        if not isinstance(estimated, int) or estimated < 1:
            raise SegmentedDistillContractError(
                "TOKEN_ESTIMATE_MISSING_OR_INVALID", chunk_id
            )
        normalized.append(
            {
                "chunk_id": chunk_id,
                "sequence_index": index,
                "text": chunk_text,
                "text_sha256": canonical_hash(chunk_text),
                "estimated_tokens": estimated,
                "source_id": text(source.get("source_id", chunk_id), "source_id"),
                "page_id": source.get("page_id"),
                "section_id": source.get("section_id"),
            }
        )
    body = {
        "schema_version": "long_document-source-manifest-v1",
        "paper_id": paper_id,
        "estimator_revision": estimator_revision,
        "chunks": normalized,
    }
    return {**body, "source_manifest_hash": canonical_hash(body)}


def _sentence_ranges(value: str) -> list[tuple[int, int]]:
    boundaries = [0]
    # CJK sentences do not require spaces. Keep closing quotation marks with
    # their sentence and preserve the exact original character coordinates.
    for match in re.finditer(r'''[。！？][”’」』）】]*\s*|[.!?]["'”’)]*\s+''', value):
        boundaries.append(match.end())
    boundaries.append(len(value))
    return [
        (start, end)
        for start, end in zip(boundaries, boundaries[1:])
        if value[start:end].strip()
    ]


def _split_chunk(chunk: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    """Split only at sentence boundaries and preserve original offsets."""

    ranges = _sentence_ranges(chunk["text"])
    if len(ranges) < 2:
        raise SegmentedDistillContractError(
            "CAPACITY_UNRESOLVED",
            f"{chunk['chunk_id']} has no safe sentence boundary",
        )
    units: list[dict[str, Any]] = []
    current_start: int | None = None
    current_end = 0
    current_tokens = 0
    total_chars = max(1, len(chunk["text"]))
    for start, end in ranges:
        estimated = max(
            1,
            round(chunk["estimated_tokens"] * (end - start) / total_chars),
        )
        if estimated > limit:
            raise SegmentedDistillContractError(
                "CAPACITY_UNRESOLVED",
                f"sentence in {chunk['chunk_id']} exceeds segment limit",
            )
        if current_start is not None and current_tokens + estimated > limit:
            units.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "start_offset": current_start,
                    "end_offset": current_end,
                    "estimated_tokens": current_tokens,
                }
            )
            current_start = None
            current_tokens = 0
        if current_start is None:
            current_start = start
        current_end = end
        current_tokens += estimated
    if current_start is not None:
        units.append(
            {
                "chunk_id": chunk["chunk_id"],
                "start_offset": current_start,
                "end_offset": current_end,
                "estimated_tokens": current_tokens,
            }
        )
    return units


def _source_units(
    manifest: Mapping[str, Any], core_token_limit: int
) -> Iterable[dict[str, Any]]:
    for chunk in manifest["chunks"]:
        if chunk["estimated_tokens"] <= core_token_limit:
            yield {
                "chunk_id": chunk["chunk_id"],
                "start_offset": 0,
                "end_offset": len(chunk["text"]),
                "estimated_tokens": chunk["estimated_tokens"],
            }
        else:
            yield from _split_chunk(chunk, core_token_limit)


def build_topology(
    source_manifest: Mapping[str, Any],
    *,
    core_token_limit: int,
    overlap_unit_count: int = 1,
    topology_revision: str = "long_document-topology-v1",
    max_leaf_segments: int = 64,
    max_split_depth: int = 2,
) -> dict[str, Any]:
    if core_token_limit < 1 or max_leaf_segments < 1 or max_split_depth < 0:
        raise SegmentedDistillContractError(
            "TOPOLOGY_LIMIT_INVALID", "topology limits must be bounded"
        )
    if overlap_unit_count not in {0, 1}:
        raise SegmentedDistillContractError(
            "OVERLAP_POLICY_UNSUPPORTED", "overlap_unit_count must be zero or one"
        )
    body = {
        key: source_manifest[key]
        for key in ("schema_version", "paper_id", "estimator_revision", "chunks")
    }
    if source_manifest.get("source_manifest_hash") != canonical_hash(body):
        raise SegmentedDistillContractError(
            "SOURCE_MANIFEST_HASH_MISMATCH", "source manifest is not immutable"
        )
    units = list(_source_units(source_manifest, core_token_limit))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    for unit in units:
        if current and current_tokens + unit["estimated_tokens"] > core_token_limit:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(unit)
        current_tokens += unit["estimated_tokens"]
    if current:
        groups.append(current)
    if len(groups) > max_leaf_segments:
        raise SegmentedDistillContractError(
            "MAX_LEAF_SEGMENTS_EXCEEDED", str(len(groups))
        )
    identities: list[str] = []
    for index, group in enumerate(groups, start=1):
        identity = canonical_hash(
            {
                "paper_id": source_manifest["paper_id"],
                "source_manifest_hash": source_manifest["source_manifest_hash"],
                "topology_revision": topology_revision,
                "core_source_spans": group,
                "overlap_before_source_spans": groups[index - 2][-overlap_unit_count:]
                if overlap_unit_count and index > 1
                else [],
                "overlap_after_source_spans": groups[index][:overlap_unit_count]
                if overlap_unit_count and index < len(groups)
                else [],
                "sequence_index": index,
            }
        )
        identities.append("SEG-" + identity[:24])
    segments: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        before = (
            groups[index - 2][-overlap_unit_count:]
            if overlap_unit_count and index > 1
            else []
        )
        after = (
            groups[index][:overlap_unit_count]
            if overlap_unit_count and index < len(groups)
            else []
        )
        segments.append(
            {
                "segment_id": identities[index - 1],
                "parent_segment_id": None,
                "split_depth": 0,
                "sequence_index": index,
                "total_expected": len(groups),
                "core_source_spans": group,
                "overlap_before_source_spans": before,
                "overlap_after_source_spans": after,
                "core_chunk_ids": list(dict.fromkeys(item["chunk_id"] for item in group)),
                "overlap_before_chunk_ids": list(
                    dict.fromkeys(item["chunk_id"] for item in before)
                ),
                "overlap_after_chunk_ids": list(
                    dict.fromkeys(item["chunk_id"] for item in after)
                ),
                "neighbor_segment_ids": {
                    "previous": identities[index - 2] if index > 1 else None,
                    "next": identities[index] if index < len(groups) else None,
                },
            }
        )
    topology_body = {
        "schema_version": topology_revision,
        "paper_id": source_manifest["paper_id"],
        "source_manifest_hash": source_manifest["source_manifest_hash"],
        "core_token_limit": core_token_limit,
        "overlap_unit_count": overlap_unit_count,
        "max_leaf_segments": max_leaf_segments,
        "max_split_depth": max_split_depth,
        "segments": segments,
    }
    topology_hash = canonical_hash(topology_body)
    for segment in segments:
        segment["topology_hash"] = topology_hash
        segment["source_manifest_hash"] = source_manifest["source_manifest_hash"]
    topology_body["segments"] = segments
    return {**topology_body, "topology_hash": topology_hash}


def segment_exact_set(topology: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(segment["segment_id"] for segment in topology["segments"])


def plan_bounded_segment_split(
    topology: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    *,
    segment_id: str,
) -> dict[str, Any]:
    """Plan one deterministic binary recovery split without rewriting topology.

    The result is an immutable successor-plan input.  Existing terminal shards
    remain bound to the predecessor topology; a caller must explicitly freeze a
    successor topology before scheduling the children.
    """

    matches = [item for item in topology["segments"] if item["segment_id"] == segment_id]
    if len(matches) != 1:
        raise SegmentedDistillContractError("SEGMENT_ID_NOT_UNIQUE_IN_TOPOLOGY", segment_id)
    parent = matches[0]
    next_depth = int(parent.get("split_depth", 0)) + 1
    if next_depth > topology["max_split_depth"]:
        raise SegmentedDistillContractError("MAX_SPLIT_DEPTH_EXCEEDED", segment_id)
    spans = list(parent["core_source_spans"])
    if len(spans) >= 2:
        midpoint = len(spans) // 2
        left, right = spans[:midpoint], spans[midpoint:]
    elif len(spans) == 1:
        span = spans[0]
        chunks = {item["chunk_id"]: item for item in source_manifest["chunks"]}
        source = chunks[span["chunk_id"]]["text"]
        relevant = source[span["start_offset"] : span["end_offset"]]
        ranges = _sentence_ranges(relevant)
        if len(ranges) < 2:
            raise SegmentedDistillContractError("CAPACITY_UNRESOLVED", segment_id)
        midpoint = len(ranges) // 2

        def combine(items: list[tuple[int, int]]) -> dict[str, Any]:
            start = span["start_offset"] + items[0][0]
            end = span["start_offset"] + items[-1][1]
            proportion = (end - start) / max(1, span["end_offset"] - span["start_offset"])
            return {
                "chunk_id": span["chunk_id"],
                "start_offset": start,
                "end_offset": end,
                "estimated_tokens": max(1, round(span["estimated_tokens"] * proportion)),
            }

        left, right = [combine(ranges[:midpoint])], [combine(ranges[midpoint:])]
    else:
        raise SegmentedDistillContractError("CAPACITY_UNRESOLVED", segment_id)
    children: list[dict[str, Any]] = []
    for child_index, child_spans in enumerate((left, right), start=1):
        identity = canonical_hash(
            {
                "paper_id": topology["paper_id"],
                "source_manifest_hash": topology["source_manifest_hash"],
                "parent_segment_id": segment_id,
                "split_depth": next_depth,
                "child_index": child_index,
                "core_source_spans": child_spans,
            }
        )
        sibling_context = right[:1] if child_index == 1 else left[-1:]
        children.append(
            {
                "segment_id": "SEG-" + identity[:24],
                "parent_segment_id": segment_id,
                "split_depth": next_depth,
                "child_index": child_index,
                "core_source_spans": child_spans,
                "overlap_before_source_spans": (
                    parent["overlap_before_source_spans"] if child_index == 1 else sibling_context
                ),
                "overlap_after_source_spans": (
                    sibling_context if child_index == 1 else parent["overlap_after_source_spans"]
                ),
            }
        )
    body = {
        "schema_version": "long_document-bounded-segment-split-plan-v1",
        "paper_id": topology["paper_id"],
        "source_manifest_hash": topology["source_manifest_hash"],
        "parent_topology_hash": topology["topology_hash"],
        "parent_segment_id": segment_id,
        "split_depth": next_depth,
        "children": children,
        "requires_new_frozen_topology": True,
    }
    return {**body, "content_hash": canonical_hash(body)}


__all__ = [
    "build_source_manifest",
    "build_topology",
    "plan_bounded_segment_split",
    "segment_exact_set",
]

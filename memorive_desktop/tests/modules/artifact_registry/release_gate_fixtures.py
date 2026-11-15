"""Memorive ANALYSIS-ADMISSION/CROSS public synthetic builders for release-gate tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from artifact_registry.errors import ContractValidationError
from artifact_registry.analysis_materialization import ArtifactFactory


@dataclass(frozen=True)
class SyntheticGraph:
    card_c1: dict[str, Any]
    pack_p1: dict[str, Any]
    analysis_a1: dict[str, Any]
    card_c2: dict[str, Any]
    card_c3: dict[str, Any]
    chunk_only_pack: dict[str, Any]


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ContractValidationError(f"fixture must be an object: {path}")
    return value


def build_synthetic_graph(
    factory: ArtifactFactory,
    fixture: Mapping[str, Any],
) -> SyntheticGraph:
    expected = {
        "schema_version",
        "fixture_id",
        "paper_id",
        "card_c1",
        "pack_p1",
        "analysis_a1",
        "card_c2",
        "card_c3",
        "chunk_only_pack",
    }
    if set(fixture) != expected:
        raise ContractValidationError("synthetic graph fixture fields mismatch")
    fixture_id = str(fixture["fixture_id"])
    paper_id = str(fixture["paper_id"])
    evidence = ["fixture:" + fixture_id]
    card_c1_spec = _object(fixture, "card_c1")
    card_c1 = factory.create(
        artifact_type="card",
        payload=_object(card_c1_spec, "payload"),
        schema_ref=str(card_c1_spec["schema_ref"]),
        paper_ids=[paper_id],
        evidence_refs=evidence,
        semantic_key="card-c1",
    )
    pack_p1_spec = _object(fixture, "pack_p1")
    pack_p1 = factory.create(
        artifact_type="context_pack",
        payload=_object(pack_p1_spec, "payload"),
        schema_ref=str(pack_p1_spec["schema_ref"]),
        paper_ids=[paper_id],
        source_artifact_ids=[card_c1["artifact_id"]],
        parents=[
            factory.link(
                card_c1,
                relation="packed_from_card",
                source_field="card_ref",
                evidence_ref="fixture:" + fixture_id,
            )
        ],
        metadata={"dependency_mode": "card_bound"},
        evidence_refs=evidence,
        semantic_key="pack-p1",
    )
    analysis_a1_spec = _object(fixture, "analysis_a1")
    analysis_a1 = factory.create(
        artifact_type="analysis",
        payload=_object(analysis_a1_spec, "payload"),
        schema_ref=str(analysis_a1_spec["schema_ref"]),
        paper_ids=[paper_id],
        source_artifact_ids=[card_c1["artifact_id"], pack_p1["artifact_id"]],
        parents=[
            factory.link(
                card_c1,
                relation="analyzed_from_card",
                source_field="card_ref",
                evidence_ref="fixture:" + fixture_id,
            ),
            factory.link(
                pack_p1,
                relation="analyzed_from_context_pack",
                source_field="context_pack_ref",
                evidence_ref="fixture:" + fixture_id,
            ),
        ],
        metadata={"analysis_review_state": "reviewed"},
        evidence_refs=evidence,
        semantic_key="analysis-a1",
    )
    card_c2_spec = _object(fixture, "card_c2")
    card_c2 = factory.create(
        artifact_type="card",
        payload=_object(card_c2_spec, "payload"),
        schema_ref=str(card_c2_spec["schema_ref"]),
        paper_ids=[paper_id],
        source_artifact_ids=[card_c1["artifact_id"]],
        parents=[
            factory.link(
                card_c1,
                relation="supersedes",
                source_field="supersedes_card_ref",
                evidence_ref="fixture:" + fixture_id,
            )
        ],
        evidence_refs=evidence,
        semantic_key="card-c2",
    )
    card_c3_spec = _object(fixture, "card_c3")
    card_c3 = factory.create(
        artifact_type="card",
        payload=_object(card_c3_spec, "payload"),
        schema_ref=str(card_c3_spec["schema_ref"]),
        paper_ids=[paper_id],
        evidence_refs=evidence,
        semantic_key="card-c3",
    )
    chunk_spec = _object(fixture, "chunk_only_pack")
    chunk_only_pack = factory.create(
        artifact_type="context_pack",
        payload=_object(chunk_spec, "payload"),
        schema_ref=str(chunk_spec["schema_ref"]),
        paper_ids=[paper_id],
        metadata={"dependency_mode": "chunk_only"},
        evidence_refs=evidence,
        semantic_key="chunk-only-pack",
    )
    return SyntheticGraph(
        card_c1=card_c1,
        pack_p1=pack_p1,
        analysis_a1=analysis_a1,
        card_c2=card_c2,
        card_c3=card_c3,
        chunk_only_pack=chunk_only_pack,
    )


def _object(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    item = value.get(field)
    if not isinstance(item, Mapping):
        raise ContractValidationError(f"fixture field must be an object: {field}")
    return dict(item)

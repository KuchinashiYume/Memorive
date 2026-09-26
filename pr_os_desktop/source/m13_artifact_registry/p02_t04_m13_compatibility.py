"""PR-OS P02/T04/M13 four-axis compatibility and freshness assessment."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import ContractValidationError, EnvelopeValidationError
from .p02_t04_m13_materialization import ArtifactFactory
from .p02_t04_m13_products import (
    ArtifactProduct,
    artifact_ref,
    stable_identifier,
    unique_text,
)
from .schema_v2 import lineage_status


ASSESSMENT_SCHEMA_VERSION = "p02-t04-compatibility-assessment-v1"
AXIS_NAMES = ("card_pack", "pack_analysis", "schema", "source_scope")
AXIS_STATUSES = {"compatible", "incompatible", "unknown", "not_applicable"}
FRESHNESS_STATUSES = {"fresh", "stale", "unknown", "not_applicable"}


@dataclass(frozen=True)
class CompatibilityPolicy:
    schema_version: str
    policy_id: str
    policy_version: str
    created_at: str
    schema_rules: dict[str, dict[str, Any]]

    @classmethod
    def from_file(cls, path: Path | str) -> "CompatibilityPolicy":
        with Path(path).open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        expected = {
            "schema_version",
            "policy_id",
            "policy_version",
            "created_at",
            "schema_rules",
            "freshness_evidence_sources",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ContractValidationError("compatibility policy fields mismatch")
        if value["freshness_evidence_sources"] != [
            "explicit_supersedes_relation",
            "frozen_manifest_selected_target",
        ]:
            raise ContractValidationError(
                "freshness policy must forbid time/latest inference"
            )
        rules = value["schema_rules"]
        if not isinstance(rules, Mapping):
            raise ContractValidationError("schema_rules must be an object")
        normalized: dict[str, dict[str, Any]] = {}
        for key, rule in rules.items():
            if not isinstance(rule, Mapping):
                raise ContractValidationError("schema rule must be an object")
            status = rule.get("status")
            if status not in AXIS_STATUSES:
                raise ContractValidationError(f"invalid schema rule status: {status!r}")
            normalized[str(key)] = copy.deepcopy(dict(rule))
        return cls(
            schema_version=str(value["schema_version"]),
            policy_id=str(value["policy_id"]),
            policy_version=str(value["policy_version"]),
            created_at=str(value["created_at"]),
            schema_rules=normalized,
        )

    def schema_result(
        self,
        card: Mapping[str, Any],
        pack: Mapping[str, Any] | None,
        analysis: Mapping[str, Any],
    ) -> dict[str, Any]:
        if pack is None:
            return _axis(
                "unknown",
                "SCHEMA_INDEPENDENT_PACK_MISSING",
                ["compatibility-policy:" + self.policy_id],
            )
        key = "|".join(
            (
                str(card.get("schema_ref")),
                str(pack.get("schema_ref")),
                str(analysis.get("schema_ref")),
            )
        )
        rule = self.schema_rules.get(key)
        if rule is None:
            return _axis(
                "unknown",
                "SCHEMA_POLICY_RULE_MISSING",
                ["compatibility-policy:" + self.policy_id, "schema-triplet:" + key],
            )
        return {
            "status": rule["status"],
            "reason_codes": unique_text(rule.get("reason_codes", [])),
            "evidence_refs": unique_text(
                ["compatibility-policy:" + self.policy_id, "schema-triplet:" + key]
            ),
            "adapter_required": bool(rule.get("adapter_required", False)),
        }


def _axis(status: str, reason: str, evidence_refs: Sequence[str]) -> dict[str, Any]:
    if status not in AXIS_STATUSES:
        raise ContractValidationError(f"invalid axis status: {status}")
    return {
        "status": status,
        "reason_codes": [reason],
        "evidence_refs": unique_text(evidence_refs),
        "adapter_required": False,
    }


def _parents(envelope: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    parents = envelope.get("parent_artifacts", [])
    return list(parents) if isinstance(parents, list) else []


def _requirement_names(envelope: Mapping[str, Any]) -> set[str]:
    values = envelope.get("unresolved_parent_requirements", [])
    return {
        str(item.get("requirement"))
        for item in values
        if isinstance(item, Mapping)
    }


def _has_conflicts(envelope: Mapping[str, Any]) -> bool:
    values = envelope.get("conflicting_candidates", [])
    return isinstance(values, list) and bool(values)


def _safe_lineage_status(envelope: Mapping[str, Any]) -> str:
    try:
        return lineage_status(envelope)
    except EnvelopeValidationError:
        return "invalid"


def _matches(link: Mapping[str, Any], envelope: Mapping[str, Any]) -> bool:
    return (
        link.get("parent_artifact_id") == envelope.get("artifact_id")
        and link.get("parent_content_hash")
        == envelope.get("content_hash", {}).get("value")
    )


def _card_pack_axis(
    card: Mapping[str, Any],
    pack: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if pack is None:
        return _axis(
            "not_applicable",
            "INDEPENDENT_CONTEXT_PACK_NOT_AVAILABLE",
            ["lineage:pack-absent"],
        )
    if pack.get("metadata", {}).get("dependency_mode") == "chunk_only":
        return _axis(
            "not_applicable",
            "PACK_DECLARED_CHUNK_ONLY",
            ["artifact:" + str(pack.get("artifact_id"))],
        )
    if _has_conflicts(pack):
        return _axis(
            "unknown",
            "PACK_CARD_LINEAGE_CONFLICTED",
            ["artifact:" + str(pack.get("artifact_id"))],
        )
    links = [
        link
        for link in _parents(pack)
        if link.get("relation") in {"packed_from_card", "context_for_card"}
    ]
    if any(_matches(link, card) for link in links):
        return _axis(
            "compatible",
            "PACK_EXACT_CARD_PARENT_MATCH",
            ["artifact:" + str(card.get("artifact_id"))],
        )
    if links:
        return _axis(
            "incompatible",
            "PACK_RESOLVES_TO_DIFFERENT_CARD",
            ["artifact:" + str(pack.get("artifact_id"))],
        )
    if "exact_card_version" in _requirement_names(pack):
        return _axis(
            "unknown",
            "PACK_EXACT_CARD_VERSION_UNRESOLVED",
            ["artifact:" + str(pack.get("artifact_id"))],
        )
    return _axis(
        "unknown",
        "PACK_CARD_RELATION_NOT_PROVEN",
        ["artifact:" + str(pack.get("artifact_id"))],
    )


def _pack_analysis_axis(
    card: Mapping[str, Any],
    pack: Mapping[str, Any] | None,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    if pack is None:
        return _axis(
            "unknown",
            "ANALYSIS_INDEPENDENT_CONTEXT_PACK_UNRESOLVED",
            ["artifact:" + str(analysis.get("artifact_id"))],
        )
    if _has_conflicts(analysis):
        return _axis(
            "unknown",
            "ANALYSIS_LINEAGE_CONFLICTED",
            ["artifact:" + str(analysis.get("artifact_id"))],
        )
    links = _parents(analysis)
    card_match = any(
        _matches(link, card) and link.get("relation") == "analyzed_from_card"
        for link in links
    )
    pack_match = any(
        _matches(link, pack)
        and link.get("relation") in {"analyzed_from_context_pack", "context_pack_used"}
        for link in links
    )
    if card_match and pack_match:
        return _axis(
            "compatible",
            "ANALYSIS_EXACT_CARD_AND_PACK_PARENTS_MATCH",
            [
                "artifact:" + str(card.get("artifact_id")),
                "artifact:" + str(pack.get("artifact_id")),
            ],
        )
    unresolved = _requirement_names(analysis)
    if unresolved & {
        "exact_card_version",
        "independent_context_pack",
        "independent_context_pack_artifact",
    }:
        return _axis(
            "unknown",
            "ANALYSIS_REQUIRED_LINEAGE_UNRESOLVED",
            ["artifact:" + str(analysis.get("artifact_id"))],
        )
    if card_match or pack_match:
        return _axis(
            "incompatible",
            "ANALYSIS_PARENT_SET_INCOMPLETE",
            ["artifact:" + str(analysis.get("artifact_id"))],
        )
    return _axis(
        "unknown",
        "ANALYSIS_CARD_PACK_RELATIONS_NOT_PROVEN",
        ["artifact:" + str(analysis.get("artifact_id"))],
    )


def _paper_ids(envelope: Mapping[str, Any]) -> list[Any]:
    scope = envelope.get("source_scope", {})
    values = scope.get("paper_ids", []) if isinstance(scope, Mapping) else []
    return list(values) if isinstance(values, list) else []


def _source_scope_axis(
    card: Mapping[str, Any],
    pack: Mapping[str, Any] | None,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    card_scope = _paper_ids(card)
    analysis_scope = _paper_ids(analysis)
    pack_scope = _paper_ids(pack) if pack is not None else []
    all_scopes = [card_scope, analysis_scope] + ([pack_scope] if pack is not None else [])
    if any(
        not scope
        or any(not isinstance(value, str) or not value.strip() for value in scope)
        for scope in all_scopes
    ):
        return _axis(
            "incompatible",
            "SOURCE_SCOPE_NULLABLE_OR_EMPTY_PAPER_ID",
            ["source-scope:envelope-v2"],
        )
    if len(card_scope) != 1:
        return _axis(
            "incompatible",
            "SINGLE_CARD_CANNOT_CLAIM_MULTI_PAPER_SCOPE",
            ["artifact:" + str(card.get("artifact_id"))],
        )
    if pack is None:
        return _axis(
            "unknown",
            "SOURCE_SCOPE_INDEPENDENT_PACK_MISSING",
            ["artifact:" + str(analysis.get("artifact_id"))],
        )
    if set(pack_scope) != set(analysis_scope) or card_scope[0] not in set(pack_scope):
        return _axis(
            "incompatible",
            "SOURCE_SCOPE_CARD_PACK_ANALYSIS_MISMATCH",
            ["source-scope:envelope-v2"],
        )
    return _axis(
        "compatible",
        "SOURCE_SCOPE_EXPLICIT_AND_ALIGNED",
        ["source-scope:envelope-v2"],
    )


def _freshness(
    card: Mapping[str, Any],
    analysis: Mapping[str, Any],
    *,
    frozen_manifest_target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    analysis_card_links = [
        link
        for link in _parents(analysis)
        if link.get("relation") == "analyzed_from_card"
    ]
    if any(_matches(link, card) for link in analysis_card_links):
        return {
            "status": "fresh",
            "reason_codes": ["ANALYSIS_USES_SELECTED_CARD"],
            "evidence_refs": ["artifact:" + str(card.get("artifact_id"))],
            "stale_against": None,
        }
    if frozen_manifest_target is not None and _matches(frozen_manifest_target, card):
        prior = analysis_card_links[0] if analysis_card_links else None
        return {
            "status": "stale" if prior is not None else "unknown",
            "reason_codes": [
                "FROZEN_MANIFEST_SELECTS_DIFFERENT_CARD"
                if prior is not None
                else "ANALYSIS_CARD_PARENT_NOT_PROVEN"
            ],
            "evidence_refs": ["frozen-manifest:selected-card"],
            "stale_against": artifact_ref(card) if prior is not None else None,
        }
    for link in _parents(card):
        if link.get("relation") != "supersedes":
            continue
        if any(
            link.get("parent_artifact_id") == prior.get("parent_artifact_id")
            and link.get("parent_content_hash") == prior.get("parent_content_hash")
            for prior in analysis_card_links
        ):
            return {
                "status": "stale",
                "reason_codes": ["SELECTED_CARD_EXPLICITLY_SUPERSEDES_ANALYZED_CARD"],
                "evidence_refs": ["artifact:" + str(card.get("artifact_id"))],
                "stale_against": artifact_ref(card),
            }
    return {
        "status": "unknown",
        "reason_codes": ["NO_PERMITTED_FRESHNESS_EVIDENCE"],
        "evidence_refs": ["policy:no-time-or-latest-inference"],
        "stale_against": None,
    }


def build_compatibility_assessment(
    *,
    factory: ArtifactFactory,
    analysis: Mapping[str, Any],
    candidate_card: Mapping[str, Any],
    context_pack: Mapping[str, Any] | None,
    policy: CompatibilityPolicy,
    registry_snapshot: Mapping[str, Any],
    policy_snapshot: Mapping[str, Any],
    assessed_at: str,
    frozen_manifest_target: Mapping[str, Any] | None = None,
) -> ArtifactProduct:
    axes = {
        "card_pack": _card_pack_axis(candidate_card, context_pack),
        "pack_analysis": _pack_analysis_axis(candidate_card, context_pack, analysis),
        "schema": policy.schema_result(candidate_card, context_pack, analysis),
        "source_scope": _source_scope_axis(candidate_card, context_pack, analysis),
    }
    if set(axes) != set(AXIS_NAMES):
        raise ContractValidationError("assessment must contain exactly four axes")
    freshness = _freshness(
        candidate_card,
        analysis,
        frozen_manifest_target=frozen_manifest_target,
    )
    if freshness["status"] not in FRESHNESS_STATUSES:
        raise ContractValidationError("invalid freshness status")
    statuses = {value["status"] for value in axes.values()}
    if "incompatible" in statuses:
        overall = "incompatible"
    elif "unknown" in statuses:
        overall = "unknown"
    else:
        overall = "compatible"
    human_review_required = (
        overall != "compatible"
        or freshness["status"] not in {"fresh", "not_applicable"}
        or _has_conflicts(analysis)
        or _has_conflicts(candidate_card)
        or (context_pack is not None and _has_conflicts(context_pack))
    )
    subject_refs = {
        "analysis": artifact_ref(analysis),
        "candidate_card": artifact_ref(candidate_card),
        "context_pack": artifact_ref(context_pack) if context_pack is not None else None,
    }
    assessment_id = stable_identifier(
        "assessment_",
        [
            subject_refs["analysis"]["artifact_id"],
            subject_refs["candidate_card"]["artifact_id"],
            subject_refs["context_pack"]["artifact_id"]
            if subject_refs["context_pack"] is not None
            else "no-pack",
            policy_snapshot["artifact_id"],
            registry_snapshot["artifact_id"],
        ],
    )
    payload = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "assessment_id": assessment_id,
        "assessed_at": assessed_at,
        "subjects": subject_refs,
        "snapshot_refs": {
            "registry_snapshot": artifact_ref(registry_snapshot),
            "compatibility_policy_snapshot": artifact_ref(policy_snapshot),
        },
        "axes": axes,
        "freshness": freshness,
        "overall_compatibility": overall,
        "lineage_input_state": {
            "analysis": _safe_lineage_status(analysis),
            "candidate_card": _safe_lineage_status(candidate_card),
            "context_pack": _safe_lineage_status(context_pack)
            if context_pack is not None
            else "not_applicable",
        },
        "human_review_required": human_review_required,
        "reason_codes": unique_text(
            reason
            for value in list(axes.values()) + [freshness]
            for reason in value["reason_codes"]
        ),
        "non_authoritative": True,
    }
    parents = [analysis, candidate_card, registry_snapshot, policy_snapshot]
    if context_pack is not None:
        parents.append(context_pack)
    envelope = factory.create(
        artifact_type="compatibility_assessment",
        payload=payload,
        schema_ref="pros://p02/t04/compatibility-assessment/v1",
        paper_ids=analysis.get("source_scope", {}).get("paper_ids", []),
        source_artifact_ids=[item["artifact_id"] for item in parents],
        parents=[
            factory.link(
                item,
                relation="assessed_against",
                source_field="subjects_or_snapshots",
                evidence_ref="assessment:" + assessment_id,
            )
            for item in parents
        ],
        metadata={
            "overall_compatibility": overall,
            "freshness": freshness["status"],
            "human_review_required": human_review_required,
        },
        evidence_refs=[
            "artifact:" + registry_snapshot["artifact_id"],
            "artifact:" + policy_snapshot["artifact_id"],
        ],
        semantic_key=assessment_id,
    )
    return ArtifactProduct(payload=payload, envelope=envelope)

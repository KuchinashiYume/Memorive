"""Memorive ANALYSIS-ADMISSION/ARTIFACT_REGISTRY four-axis compatibility and freshness assessment."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import ContractValidationError, EnvelopeValidationError
from .analysis_products import (
    ArtifactProduct,
    stable_identifier,
    unique_text,
)


ASSESSMENT_SCHEMA_VERSION = "analysis_admission-compatibility-assessment-v1"
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

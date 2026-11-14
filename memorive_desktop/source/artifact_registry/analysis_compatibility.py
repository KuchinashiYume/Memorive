"""Memorive ANALYSIS-ADMISSION/ARTIFACT_REGISTRY four-axis compatibility and freshness assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import EnvelopeValidationError
from .analysis_products import (
    ArtifactProduct,
    stable_identifier,
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

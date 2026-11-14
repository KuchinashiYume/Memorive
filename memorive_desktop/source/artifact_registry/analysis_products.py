"""Memorive ANALYSIS-ADMISSION/ARTIFACT_REGISTRY immutable values shared by the release-gate builders."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


ANALYSIS_ADMISSION_IMPLEMENTATION_VERSION = "analysis_admission-sandbox-v1"


@dataclass(frozen=True)
class ArtifactProduct:
    """A business payload paired with its registered ARTIFACT_REGISTRY v2 envelope."""

    payload: dict[str, Any]
    envelope: dict[str, Any]


def stable_identifier(prefix: str, values: Iterable[str]) -> str:
    seed = "|".join(str(item) for item in values)
    return prefix + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

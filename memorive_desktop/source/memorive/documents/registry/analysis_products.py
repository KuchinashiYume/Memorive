"""Small immutable values the analysis builders share: ArtifactProduct, artifact references and
stable identifiers. Used by analysis_compatibility.py and the card states.
"""

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


def artifact_ref(envelope: Mapping[str, Any]) -> dict[str, str]:
    return {
        "artifact_id": str(envelope["artifact_id"]),
        "artifact_type": str(envelope["artifact_type"]),
        "content_hash": str(envelope["content_hash"]["value"]),
    }


def stable_identifier(prefix: str, values: Iterable[str]) -> str:
    seed = "|".join(str(item) for item in values)
    return prefix + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def unique_text(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})

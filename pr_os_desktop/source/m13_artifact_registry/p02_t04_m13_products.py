"""PR-OS P02/T04/M13 immutable values shared by the release-gate builders."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


P02_T04_IMPLEMENTATION_VERSION = "p02-t04-sandbox-v1"
P02_T04_CONTRACT_REF = "PR-OS_P02_T04_CROSS_操作手册_r0.7"


@dataclass(frozen=True)
class ArtifactProduct:
    """A business payload paired with its registered M13 v2 envelope."""

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

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


PHASE1_NODE_IDS = (
    "01_DOCUMENT_INGEST",
    "02_CHUNK_EMBEDDING",
    "03_CARD_DISTILL",
    "04_CARD_CROSS_CHECK",
    "05_CARD_ADMISSION",
    "06_CONTEXT_PACK",
    "07_ANALYSIS",
    "08_JUDGMENT_CROSS_CHECK",
    "09_HUMAN_FINAL",
)


@dataclass(frozen=True)
class Phase1Artifact:
    artifact_id: str
    kind: str
    path: Path
    sha256: str
    display_name: str = ""
    classification: str = "PUBLIC_SAFE"


@dataclass(frozen=True)
class Phase1ExecutionContext:
    job_id: str
    attempt_id: str
    item_id: str
    source_path: Path
    source_name: str
    source_sha256: str
    run_root: Path
    artifact_root: Path
    workflow_definition: Mapping[str, Any]
    workflow_config: Mapping[str, Any]


@dataclass(frozen=True)
class Phase1ExecutionResult:
    artifacts: tuple[Phase1Artifact, ...]
    metrics: Mapping[str, Any] = field(default_factory=dict)


__all__ = [
    "PHASE1_NODE_IDS",
    "Phase1Artifact",
    "Phase1ExecutionContext",
    "Phase1ExecutionResult",
]

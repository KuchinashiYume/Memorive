from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


Core_NODE_IDS = (
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


# New tasks have their own topology; old durable nine-node prefixes keep their IDs.
DATA_NODE = 'E2_DATA_REVIEW'
LOGIC_NODE = 'E2_LOGIC_REVIEW'
E2_NODE_IDS = (Core_NODE_IDS[0], DATA_NODE, *Core_NODE_IDS[1:])
CORE_WORKER_CAPACITY = 9

def node_ids_for(definition):
    ids = tuple(row['node_id'] for row in definition.get('nodes', []))
    if ids not in (Core_NODE_IDS, E2_NODE_IDS):
        raise ValueError('CORE_WORKFLOW_TOPOLOGY_UNSUPPORTED')
    return ids


@dataclass(frozen=True)
class CoreArtifact:
    artifact_id: str
    kind: str
    path: Path
    sha256: str
    display_name: str = ""
    classification: str = "PUBLIC_SAFE"


@dataclass(frozen=True)
class CoreExecutionContext:
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
class CoreExecutionResult:
    artifacts: tuple[CoreArtifact, ...]
    metrics: Mapping[str, Any] = field(default_factory=dict)


__all__ = [
    "Core_NODE_IDS",
    "CoreArtifact",
    "CoreExecutionContext",
    "CoreExecutionResult",
]

"""Thin Core runtime bridge used by the packaged Memorive desktop service.

The package owns durable scheduling, checkpoints and UI-facing receipts only.
DOCUMENT_PROCESSING--KNOWLEDGE_ADMISSION scientific/business behavior remains in the snapshotted Core modules.
"""

from .contracts import (
    Core_NODE_IDS,
    CoreArtifact,
    CoreExecutionContext,
    CoreExecutionResult,
)
from .worker import CoreVerticalWorker, CoreWorkerInterrupted
from .model_bridge import PURPOSE as Core_MODEL_PURPOSE, CoreModelBridge
from .executor import CorePipelineExecutor
from .segmented_distill import CoreSegmentedDistiller
from .task_controls import TaskControlStore

__all__ = [
    "Core_NODE_IDS",
    "CoreArtifact",
    "CoreExecutionContext",
    "CoreExecutionResult",
    "CoreVerticalWorker",
    "CoreWorkerInterrupted",
    "Core_MODEL_PURPOSE",
    "CoreModelBridge",
    "CorePipelineExecutor",
    "CoreSegmentedDistiller",
    "TaskControlStore",
]

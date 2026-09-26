"""Thin Phase 1 runtime bridge used by the packaged PR-OS desktop service.

The package owns durable scheduling, checkpoints and UI-facing receipts only.
M1--M8 scientific/business behavior remains in the snapshotted Phase 1 modules.
"""

from .contracts import (
    PHASE1_NODE_IDS,
    Phase1Artifact,
    Phase1ExecutionContext,
    Phase1ExecutionResult,
)
from .worker import Phase1VerticalWorker, Phase1WorkerInterrupted
from .model_bridge import PURPOSE as PHASE1_MODEL_PURPOSE, Phase1ModelBridge
from .executor import Phase1PipelineExecutor
from .segmented_distill import Phase1SegmentedDistiller
from .task_controls import TaskControlStore

__all__ = [
    "PHASE1_NODE_IDS",
    "Phase1Artifact",
    "Phase1ExecutionContext",
    "Phase1ExecutionResult",
    "Phase1VerticalWorker",
    "Phase1WorkerInterrupted",
    "PHASE1_MODEL_PURPOSE",
    "Phase1ModelBridge",
    "Phase1PipelineExecutor",
    "Phase1SegmentedDistiller",
    "TaskControlStore",
]

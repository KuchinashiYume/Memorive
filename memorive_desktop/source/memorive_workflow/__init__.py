"""The document workflow: the worker that runs a paper through its nodes (convert, clean, chunk,
extract cards, review, analyse ...).

The desktop service starts the worker (worker.py); executor.py runs each node; scheduling.py and
task_controls.py run nodes in lanes and handle pause and cancel; model_bridge.py sends a node's
model calls; segmented_distill.py runs long papers in segments. The inbox and the current task
panel start and watch its jobs.
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

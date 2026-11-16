"""The document workflow: the worker that runs a paper through its nodes (convert, clean, chunk,
extract cards, review, analyse ...).

The desktop service starts the worker (worker.py) and uses executor.py and task_controls.py;
executor.py runs each node; scheduling.py and task_controls.py run nodes in lanes and handle pause
and cancel; model_bridge.py sends a node's model calls; segmented_distill.py runs long papers in
segments. The current task panel controls jobs through task_controls.py; the data review (memorive/documents/data_review)
runs its review through scheduling.py and model_bridge.py.
"""

from memorive.documents.workflow.contracts import (
    Core_NODE_IDS,
    CoreArtifact,
    CoreExecutionContext,
    CoreExecutionResult,
)
from memorive.documents.workflow.worker import CoreVerticalWorker, CoreWorkerInterrupted
from memorive.documents.workflow.model_bridge import PURPOSE as Core_MODEL_PURPOSE, CoreModelBridge
from memorive.documents.workflow.executor import CorePipelineExecutor
from memorive.documents.workflow.segmented_distill import CoreSegmentedDistiller
from memorive.documents.workflow.task_controls import TaskControlStore

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

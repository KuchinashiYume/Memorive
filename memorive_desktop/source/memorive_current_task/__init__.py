"""The current task panel: the job list, the flow chart of a job's nodes, and the actions on them.

The window reaches it through current_task.* methods (CurrentTaskProductController in
product_controller.py). workflow_mapping.py turns saved settings into the job shape the workflow
runs; refinement_tasks.py stores session refinement tasks; events.py orders a job's events.
"""

from .actions import ActionController, ActionError, CommandDisabled, ConfirmationError
from .events import EventIntegrityError, TimelineReconciler
from .locators import CurrentTaskLocator, LocatorError
from .projection import CurrentTaskProjector, ProjectionError
from .session import CurrentTaskSession, SessionError
from .product_controller import CURRENT_TASK_METHODS, CurrentTaskProductController
from .refinement_tasks import (
    REFINEMENT_NODES,
    REFINEMENT_NODE_IDS,
    RefinementTaskCancelled,
    RefinementTaskError,
    SessionRefinementTaskStore,
)
from .workflow_mapping import Core_NODES, build_core_execution_snapshot

__all__ = [
    "ActionController",
    "ActionError",
    "CommandDisabled",
    "ConfirmationError",
    "CurrentTaskLocator",
    "CurrentTaskProjector",
    "CurrentTaskSession",
    "CURRENT_TASK_METHODS",
    "CurrentTaskProductController",
    "REFINEMENT_NODES",
    "REFINEMENT_NODE_IDS",
    "RefinementTaskCancelled",
    "RefinementTaskError",
    "SessionRefinementTaskStore",
    "Core_NODES",
    "EventIntegrityError",
    "LocatorError",
    "ProjectionError",
    "SessionError",
    "TimelineReconciler",
    "build_core_execution_snapshot",
]

# Names the package offered before; restored with the code behind them.
from .events import WatchRecoveryController
__all__ = [*__all__, 'WatchRecoveryController']

"""The current task panel: the job list, the flow chart of a job's nodes, and the actions on them.

The window reaches it through current_task.* methods (CurrentTaskProductController in
product_controller.py). workflow_mapping.py turns saved settings into the job shape the workflow
runs; refinement_tasks.py stores session refinement tasks; events.py orders a job's events.
"""

from memorive.desktop.current_task.actions import ActionController, ActionError, CommandDisabled, ConfirmationError
from memorive.desktop.current_task.events import EventIntegrityError, TimelineReconciler
from memorive.desktop.current_task.locators import CurrentTaskLocator, LocatorError
from memorive.desktop.current_task.projection import CurrentTaskProjector, ProjectionError
from memorive.desktop.current_task.session import CurrentTaskSession, SessionError
from memorive.desktop.current_task.product_controller import CURRENT_TASK_METHODS, CurrentTaskProductController
from memorive.desktop.current_task.refinement_tasks import (
    REFINEMENT_NODES,
    REFINEMENT_NODE_IDS,
    RefinementTaskCancelled,
    RefinementTaskError,
    SessionRefinementTaskStore,
)
from memorive.desktop.current_task.workflow_mapping import Core_NODES, build_core_execution_snapshot

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
from memorive.desktop.current_task.events import WatchRecoveryController
__all__ = [*__all__, 'WatchRecoveryController']

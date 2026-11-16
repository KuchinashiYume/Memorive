"""The inbox: files the user adds are copied into the app's custody and queued for processing.

The window reaches it through inbox.* methods. controller.py imports a file and starts its
processing job; path_policy.py decides which paths may be imported; store.py keeps the inbox rows;
workload.py counts a file's pages or entries to size its job.
"""

from .controller import InboxController, SimulatedCrash
from .path_policy import PathSecurityPolicy
from .service_adapter import INBOX_METHODS, InboxServiceAdapter
from .store import InboxStore

__all__ = [
    "INBOX_METHODS",
    "InboxController",
    "InboxServiceAdapter",
    "InboxStore",
    "PathSecurityPolicy",
    "SimulatedCrash",
]

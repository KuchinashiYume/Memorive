"""The inbox: files the user adds are copied into the app's custody and queued for processing.

The window reaches it through inbox.* methods. controller.py imports a file and starts its
processing job; path_policy.py decides which paths may be imported; store.py keeps the inbox rows;
workload.py counts a file's pages or entries to size its job.
"""

from memorive.documents.inbox.controller import InboxController, SimulatedCrash
from memorive.documents.inbox.path_policy import PathSecurityPolicy
from memorive.documents.inbox.service_adapter import INBOX_METHODS, InboxServiceAdapter
from memorive.documents.inbox.store import InboxStore

__all__ = [
    "INBOX_METHODS",
    "InboxController",
    "InboxServiceAdapter",
    "InboxStore",
    "PathSecurityPolicy",
    "SimulatedCrash",
]

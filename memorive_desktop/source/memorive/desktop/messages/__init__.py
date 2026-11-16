"""The message centre: messages about finished jobs, results and errors, and Windows notifications.

The window reaches it through messages.* methods (product_controller.py). store.py keeps the
message list in message_projection_store.json; navigation.py opens the place a message points to.
"""

from memorive.desktop.messages.contracts import MessageContractError, MessageRecord, StatusAxes
from memorive.desktop.messages.controller import MessageActionError, MessageController
from memorive.desktop.messages.navigation import LocatorNavigationError, MessageNavigator
from memorive.desktop.messages.notifications import NotificationPlanner, NotificationPreferences, SyntheticWindowsBridge
from memorive.desktop.messages.privacy import PrivacyBoundaryError, RedactionPolicy
from memorive.desktop.messages.projection import MessageProjectionEngine, ProjectionIntegrityError
from memorive.desktop.messages.product_controller import MESSAGES_METHODS, MessagesProductController
from memorive.desktop.messages.store import MessageStore, MessageStoreCorrupt

__all__ = [
    "LocatorNavigationError",
    "MessageActionError",
    "MessageContractError",
    "MessageController",
    "MessageNavigator",
    "MessageProjectionEngine",
    "MESSAGES_METHODS",
    "MessagesProductController",
    "MessageRecord",
    "MessageStore",
    "MessageStoreCorrupt",
    "NotificationPlanner",
    "NotificationPreferences",
    "PrivacyBoundaryError",
    "ProjectionIntegrityError",
    "RedactionPolicy",
    "StatusAxes",
    "SyntheticWindowsBridge",
]

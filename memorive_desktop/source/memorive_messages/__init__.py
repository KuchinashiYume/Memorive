"""The message centre: messages about finished jobs, results and errors, and Windows notifications.

The window reaches it through messages.* methods (product_controller.py). store.py keeps the
message list in message_projection_store.json; navigation.py opens the place a message points to.
"""

from .contracts import MessageContractError, MessageRecord, StatusAxes
from .controller import MessageActionError, MessageController
from .navigation import LocatorNavigationError, MessageNavigator
from .notifications import NotificationPlanner, NotificationPreferences, SyntheticWindowsBridge
from .privacy import PrivacyBoundaryError, RedactionPolicy
from .projection import MessageProjectionEngine, ProjectionIntegrityError
from .product_controller import MESSAGES_METHODS, MessagesProductController
from .store import MessageStore, MessageStoreCorrupt

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

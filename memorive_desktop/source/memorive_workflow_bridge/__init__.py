"""The channel through which session refinement runs a model (conversation_refinement.py).

The sessions page uses it: SessionsProductController passes a refinement request to it, and it
returns typed refinement items.
"""

from .conversation_refinement import (
    ConversationRefinementBridgeError,
    ExistingConversationRefinementChannel,
)

__all__ = [
    "ConversationRefinementBridgeError",
    "ExistingConversationRefinementChannel",
]

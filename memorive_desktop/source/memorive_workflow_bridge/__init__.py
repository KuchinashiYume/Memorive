"""The channel through which session refinement runs a model (conversation_refinement.py).

ProductApi in runtime.py builds the channel; the sessions page sends refinement requests through it
and gets typed refinement items back.
"""

from .conversation_refinement import (
    ConversationRefinementBridgeError,
    ExistingConversationRefinementChannel,
)

__all__ = [
    "ConversationRefinementBridgeError",
    "ExistingConversationRefinementChannel",
]

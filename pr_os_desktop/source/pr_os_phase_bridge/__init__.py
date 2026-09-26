"""Thin product bridges into already accepted Phase 5/6/7 capabilities."""

from .conversation_refinement import (
    ConversationRefinementBridgeError,
    ExistingConversationRefinementChannel,
)

__all__ = [
    "ConversationRefinementBridgeError",
    "ExistingConversationRefinementChannel",
]

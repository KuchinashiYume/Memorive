"""The three review states of a card and the moves between them.

review_status is one of pending, active and quarantined. Archiving a card is a different matter
(the artifact registry's), so "archive" is not a review state.
"""
from __future__ import annotations

PENDING = "pending"
ACTIVE = "active"
QUARANTINED = "quarantined"
STATES = frozenset({PENDING, ACTIVE, QUARANTINED})

# Allowed moves. A quarantined card stays quarantined.
_LEGAL: dict[str, frozenset] = {
    PENDING: frozenset({ACTIVE, QUARANTINED}),
    ACTIVE: frozenset({QUARANTINED}),
    QUARANTINED: frozenset(),
}

# Moves that transition() carries out. Quarantining an active card is allowed, but it is started
# by the artifact registry, so transition() refuses it with TransitionNotEnabledError.
_Core_ENABLED: frozenset = frozenset({(PENDING, ACTIVE), (PENDING, QUARANTINED)})


def is_legal(from_state: str, to_state: str) -> bool:
    """Whether a card may move from one review state to another."""
    if from_state not in STATES or to_state not in STATES:
        return False
    return to_state in _LEGAL.get(from_state, frozenset())


def is_core_enabled(from_state: str, to_state: str) -> bool:
    """Whether transition() carries out this move: only the two moves out of pending."""
    return (from_state, to_state) in _Core_ENABLED

"""Card review states: a card is pending until it is reviewed, then active or quarantined.

    transition(card_path, to_status, *, trigger, reason)   move a card and log the move
    apply_verdict(card_path, verdict, *, trigger, reason)  PASS -> active, FAIL -> quarantined
    manual_approve / manual_reject(card_path, *, reason)    a person's decision
    auto_review(card_path, decision_source, ...)           ask a decision source, retry, hand over
    read_status / read_data_id(card_path)                  read the card's front matter
    read_records(year)                                     read the admission log

The review itself (the rules, the model calls) happens elsewhere; this package only applies a
decision. The always-PASS placeholder source is for offline self-tests and is not exported.
"""
from .states import PENDING, ACTIVE, QUARANTINED, STATES, is_legal, is_core_enabled
from .card_io import read_status, read_data_id
from .machine import (
    transition,
    apply_verdict,
    Trigger,
    Verdict,
)
from .knowledge_admission_log import read_records
from .dispatch import auto_review, AutoResult, ReviewDecision
from .errors import (
    KnowledgeAdmissionError,
    IllegalTransitionError,
    TransitionNotEnabledError,
    TriggerNotAcceptedError,
    MissingBasisError,
    CardStateError,
    KnowledgeAdmissionLogAppendError,
    DecisionError,
    DecisionContractError,
)
__all__ = [
    "PENDING",
    "ACTIVE",
    "QUARANTINED",
    "STATES",
    "is_legal",
    "is_core_enabled",
    "read_status",
    "read_data_id",
    "transition",
    "apply_verdict",
    "auto_review",
    "AutoResult",
    "ReviewDecision",
    "Trigger",
    "Verdict",
    "read_records",
    "KnowledgeAdmissionError",
    "IllegalTransitionError",
    "TransitionNotEnabledError",
    "TriggerNotAcceptedError",
    "MissingBasisError",
    "CardStateError",
    "KnowledgeAdmissionLogAppendError",
    "DecisionError",
    "DecisionContractError",
]

# Names the package offered before; restored with the code behind them.
from .machine import manual_approve
from .machine import manual_reject
from .errors import AdmissionBlocked
from .errors import RealStateTransitionForbidden
__all__ = [*__all__, 'manual_approve', 'manual_reject', 'AdmissionBlocked', 'RealStateTransitionForbidden']

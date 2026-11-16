"""Card review states (pending, active, quarantined) and the admission log.

machine.py moves a card between states, card_io.py reads and writes the review_status field,
dispatch.py runs automatic review, knowledge_admission_log.py writes one line per move.
The workflow's review step uses them; memorive_app reads review status through them.

The *_submission.py, *_preview.py and derived_knowledge_artifact.py modules build submission and
preview records for knowledge_feedback and research opportunities; they write no card.
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

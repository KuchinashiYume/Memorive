"""Card review states (pending, active, quarantined) and the admission log.

machine.py moves a card between states, card_io.py reads and writes the review_status field,
dispatch.py runs automatic review, knowledge_admission_log.py writes one line per move. Evidence
review moves cards (machine.py, dispatch.py); the workflow, retrieval, research_recommendations and
the desktop service's capability adapters read review status (card_io.py, states.py).

Submission and preview records: conversation_ingest_preview.py and knowledge_submission.py are used
by artifact_registry, the analysis admission module by publication_preview.py (a preview of
publication candidates); the app reaches none of these four,
nor derived_knowledge_artifact.py and opportunity_submission.py, which only tests call.
"""
from memorive.documents.card_states.states import PENDING, ACTIVE, QUARANTINED, STATES, is_legal, is_core_enabled
from memorive.documents.card_states.card_io import read_status, read_data_id
from memorive.documents.card_states.machine import (
    transition,
    apply_verdict,
    Trigger,
    Verdict,
)
from memorive.documents.card_states.knowledge_admission_log import read_records
from memorive.documents.card_states.dispatch import auto_review, AutoResult, ReviewDecision
from memorive.documents.card_states.errors import (
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
from memorive.documents.card_states.machine import manual_approve
from memorive.documents.card_states.machine import manual_reject
from memorive.documents.card_states.errors import AdmissionBlocked
from memorive.documents.card_states.errors import RealStateTransitionForbidden
__all__ = [*__all__, 'manual_approve', 'manual_reject', 'AdmissionBlocked', 'RealStateTransitionForbidden']

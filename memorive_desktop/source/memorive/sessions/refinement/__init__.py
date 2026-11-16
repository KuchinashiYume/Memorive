"""Session refinement: a conversation becomes typed refinement items that a person reviews.

contracts.py defines the records; provider_adapter.py reads a chat service's export, graph.py
turns it into a normalized conversation graph, redaction.py removes what must not be sent,
refinement.py makes one reviewable item per message; destination.py, batch.py and
knowledge_feedback_adapter.py preview where an accepted item would go. model_channel.py is the
channel through which refinement runs a model (ProductApi in memorive/desktop/api.py builds it);
model_choices.py lists the models a refinement task may use. The library, card states and the
artifact registry use the contracts and destination checks.
"""

from memorive.sessions.refinement.contracts import (
    ACCESS_MODES,
    DECISION_STATES,
    DESTINATIONS,
    REFINEMENT_TYPES,
    ConversationContractError,
    make_context_capsule,
    validate_context_capsule,
)
from memorive.sessions.refinement.graph import message_text, validate_normalized_graph
from memorive.sessions.refinement.provider_adapter import (
    CLAUDE_CODE_LOCAL_PROFILE,
    CODEX_APP_THREAD_BRIDGE_PROFILE,
    CODEX_LOCAL_PROFILE,
    DEEPSEEK_EDGE_VISIBLE_UI_PROFILE,
    SYNTHETIC_PROFILE,
)
from memorive.sessions.refinement.redaction import redact_text, validate_redaction_result
from memorive.sessions.refinement.refinement import (
    REFINEMENT_SUBJECT_SLOT,
    make_model_refinement_items,
    make_model_typed_refinement_item,
    validate_typed_refinement_item,
)

__all__ = [
    "ACCESS_MODES",
    "CLAUDE_CODE_LOCAL_PROFILE",
    "CODEX_APP_THREAD_BRIDGE_PROFILE",
    "CODEX_LOCAL_PROFILE",
    "DEEPSEEK_EDGE_VISIBLE_UI_PROFILE",
    "DECISION_STATES",
    "DESTINATIONS",
    "REFINEMENT_TYPES",
    "REFINEMENT_SUBJECT_SLOT",
    "SYNTHETIC_PROFILE",
    "ConversationContractError",
    "make_model_refinement_items",
    "make_model_typed_refinement_item",
    "make_context_capsule",
    "message_text",
    "redact_text",
    "validate_context_capsule",
    "validate_normalized_graph",
    "validate_redaction_result",
    "validate_typed_refinement_item",
]

# Names the package offered before; restored with the code behind them.
from memorive.sessions.refinement.batch import compare_replay
from memorive.sessions.refinement.batch import make_batch_preview
from memorive.sessions.refinement.batch import make_rollback_receipt
from memorive.sessions.refinement.batch import validate_batch_preview
from memorive.sessions.refinement.contracts import make_capture_attempt_receipt
from memorive.sessions.refinement.contracts import make_export_snapshot
from memorive.sessions.refinement.contracts import make_selection_receipt
from memorive.sessions.refinement.contracts import make_source_access_envelope
from memorive.sessions.refinement.contracts import validate_capture_attempt_receipt
from memorive.sessions.refinement.contracts import validate_export_snapshot
from memorive.sessions.refinement.contracts import validate_selection_receipt
from memorive.sessions.refinement.contracts import validate_source_access_envelope
from memorive.sessions.refinement.destination import make_destination_projection
from memorive.sessions.refinement.destination import validate_destination_projection
from memorive.sessions.refinement.graph import build_normalized_graph
from memorive.sessions.refinement.knowledge_feedback_adapter import make_knowledge_feedback_candidate_preview
from memorive.sessions.refinement.provider_adapter import build_readonly_thread_bridge_snapshot
from memorive.sessions.refinement.provider_adapter import build_visible_ui_thread_bridge_snapshot
from memorive.sessions.refinement.provider_adapter import build_synthetic_snapshot
from memorive.sessions.refinement.provider_adapter import capture_local_jsonl_snapshot
from memorive.sessions.refinement.provider_adapter import profile_admission
from memorive.sessions.refinement.provider_adapter import validate_provider_export
from memorive.sessions.refinement.provider_adapter import validate_synthetic_export
from memorive.sessions.refinement.refinement import deterministic_refine_graph
from memorive.sessions.refinement.refinement import make_conflict_set
from memorive.sessions.refinement.refinement import make_refinement_subject_binding
from memorive.sessions.refinement.refinement import make_typed_refinement_item
__all__ = [*__all__, 'build_normalized_graph', 'build_readonly_thread_bridge_snapshot', 'build_visible_ui_thread_bridge_snapshot', 'build_synthetic_snapshot', 'capture_local_jsonl_snapshot', 'compare_replay', 'deterministic_refine_graph', 'make_batch_preview', 'make_capture_attempt_receipt', 'make_conflict_set', 'make_destination_projection', 'make_export_snapshot', 'make_knowledge_feedback_candidate_preview', 'make_refinement_subject_binding', 'make_rollback_receipt', 'make_selection_receipt', 'make_source_access_envelope', 'make_typed_refinement_item', 'profile_admission', 'validate_batch_preview', 'validate_capture_attempt_receipt', 'validate_destination_projection', 'validate_export_snapshot', 'validate_provider_export', 'validate_selection_receipt', 'validate_source_access_envelope', 'validate_synthetic_export']

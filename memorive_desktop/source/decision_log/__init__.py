"""Decision builders that are not yet in their areas; the decision records themselves (schema,
store, baseline) are in memorive/platform/decisions and are still offered here under their old names.

knowledge_decision.py is called by the knowledge feedback eligibility, conversation_refinement_decision.py
by the session refinement's destination; both take hashing helpers, DECISION_STATES and
validate_typed_refinement_item from knowledge feedback. routing_decision_preview.py and
fact_identity_decision_log_decision_adapter.py have no caller outside the package. They move to the
research, sessions, models and documents areas next.
"""

from memorive.platform.decisions.baseline import (
    AXIS_VALUES,
    BaselineValidationError,
    get_capability,
    load_matrix,
    validate_claim,
    validate_matrix,
    validate_production_claim,
)
from memorive.platform.decisions.schema import (
    DECISION_STATUSES,
    DECISION_TYPES,
    MODULE_IDS,
    DecisionValidationError,
    validate_decision,
)
from memorive.platform.decisions.store import AppendOnlyViolation, DecisionStore, parse_markdown, render_markdown
from .fact_identity_decision_log_decision_adapter import (
    build_ddl_decision,
    build_intake_repair_loop_decision,
)
from .knowledge_decision import (
    DECISION_SCOPE,
    KnowledgeDecisionError,
    RECEIPT_SCHEMA,
    assert_decision_binds_candidate,
    build_synthetic_human_decision_fixture,
    validate_human_knowledge_decision,
)
from .routing_decision_preview import (
    RoutingDecisionPreviewError,
    make_routing_decision_preview,
    validate_routing_decision_preview,
)

__all__ = [
    "AXIS_VALUES",
    "BaselineValidationError",
    "get_capability",
    "load_matrix",
    "validate_claim",
    "validate_matrix",
    "validate_production_claim",
    "DECISION_STATUSES",
    "DECISION_TYPES",
    "MODULE_IDS",
    "DecisionValidationError",
    "validate_decision",
    "AppendOnlyViolation",
    "DecisionStore",
    "parse_markdown",
    "render_markdown",
    "build_ddl_decision",
    "build_intake_repair_loop_decision",
    "DECISION_SCOPE",
    "KnowledgeDecisionError",
    "RECEIPT_SCHEMA",
    "assert_decision_binds_candidate",
    "build_synthetic_human_decision_fixture",
    "validate_human_knowledge_decision",
    "RoutingDecisionPreviewError",
    "make_routing_decision_preview",
    "validate_routing_decision_preview",
]


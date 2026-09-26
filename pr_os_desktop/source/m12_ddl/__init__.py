"""PR-OS P02/T01/M12 design-decision and baseline toolkit."""

from .baseline import (
    AXIS_VALUES,
    BaselineValidationError,
    get_capability,
    load_matrix,
    validate_claim,
    validate_matrix,
    validate_production_claim,
)
from .schema import (
    DECISION_STATUSES,
    DECISION_TYPES,
    MODULE_IDS,
    DecisionValidationError,
    validate_decision,
)
from .store import AppendOnlyViolation, DecisionStore, parse_markdown, render_markdown
from .p02_t05_m12_decision_adapter import (
    build_ddl_decision,
    build_t05_repair_loop_decision,
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
    "build_t05_repair_loop_decision",
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


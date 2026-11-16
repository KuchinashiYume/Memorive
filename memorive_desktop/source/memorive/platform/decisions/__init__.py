"""Design decision records: Markdown with a fixed schema (schema.py), stored append-only and
searchable (store.py), and the four-axis baseline that capability claims are checked against
(baseline.py). The card review's decision adapter builds its records on schema.py.
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
]

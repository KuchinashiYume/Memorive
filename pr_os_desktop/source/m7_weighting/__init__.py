"""P03/T03/CROSS non-production field-vector candidate package."""

from .candidate_index import (
    CandidateIndexError,
    build_candidate_index,
    validate_active_index_baseline,
    validate_candidate_index_identity,
    validate_candidate_profile,
    validate_source_snapshot,
)
from .locator import (
    LocatorError,
    PathBoundaryError,
    load_and_validate_locator,
    make_locator,
    validate_locator,
)
from .migration import FAIL_POINTS, MigrationFailure, migrate_candidate

__all__ = [
    "CandidateIndexError",
    "FAIL_POINTS",
    "LocatorError",
    "MigrationFailure",
    "PathBoundaryError",
    "build_candidate_index",
    "validate_active_index_baseline",
    "load_and_validate_locator",
    "make_locator",
    "migrate_candidate",
    "validate_locator",
    "validate_candidate_index_identity",
    "validate_candidate_profile",
    "validate_source_snapshot",
]

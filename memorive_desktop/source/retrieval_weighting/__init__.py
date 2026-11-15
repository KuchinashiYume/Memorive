"""FIELD-VECTORS/CROSS non-production field-vector candidate package."""

from .locator import (
    LocatorError,
    PathBoundaryError,
)
from .migration import FAIL_POINTS

__all__ = [
    "FAIL_POINTS",
    "LocatorError",
    "PathBoundaryError",
]

# Names the package offered before; restored with the code behind them.
from .candidate_index import CandidateIndexError
from .candidate_index import build_candidate_index
from .candidate_index import validate_active_index_baseline
from .candidate_index import validate_candidate_index_identity
from .candidate_index import validate_candidate_profile
from .candidate_index import validate_source_snapshot
from .locator import load_and_validate_locator
from .locator import make_locator
from .locator import validate_locator
from .migration import MigrationFailure
from .migration import migrate_candidate
__all__ = [*__all__, 'CandidateIndexError', 'MigrationFailure', 'build_candidate_index', 'validate_active_index_baseline', 'load_and_validate_locator', 'make_locator', 'migrate_candidate', 'validate_locator', 'validate_candidate_index_identity', 'validate_candidate_profile', 'validate_source_snapshot']

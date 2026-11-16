"""Ranking of research material, shared hashing helpers, and field-vector tools.

Used by research Q&A and the research runtime: research_policy.py, research_ranking.py,
research_rule.py and research_interests.py rank excerpts by the user's weights, rules and interests.
contracts.py and canonical.py hold hashing helpers that seven other packages import;
derived_penalty.py is used by knowledge_feedback.

The field-vector and shadow ranking tools (candidate_index.py, migration.py, locator.py,
field_vectors.py, shadow_contracts.py and others) are not reached from the app's own code: they run
through python -m retrieval_weighting with an explicit request file, and tests call them.
"""

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

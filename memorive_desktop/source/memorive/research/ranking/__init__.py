"""Ranking of research material, shared hashing helpers, and field-vector tools.

Used by research Q&A and the research runtime: research_policy.py, research_ranking.py,
research_rule.py and research_interests.py rank excerpts by the user's weights, rules and interests.
contracts.py and canonical.py hold hashing helpers that eight other packages import (artifact_registry,
decision_log, knowledge_admission, knowledge_feedback, memorive/exams/evaluation, model_gateway,
research_reports, run_ledger);
derived_penalty.py is used by knowledge_feedback.

The command line (python -m memorive.research.ranking with a request file) builds, migrates and
validates field-vector indexes: cli.py, candidate_index.py, migration.py, locator.py,
field_vectors.py. shadow_contracts.py holds input checks that query_dimensions.py uses, so
research Q&A reaches it through research_ranking.py and research_fields.py. The other shadow
ranking modules (aggregation, providers, candidate_union, counterevidence, field_search,
semantic_field_profiles, semantic_field_search, semantic_shadow_contracts, source_snapshot,
structural_candidate, retrieval_calibration_block_role_calibration) are reached by neither;
only a direct import uses them, as the tests do.
"""

from memorive.research.ranking.locator import (
    LocatorError,
    PathBoundaryError,
)
from memorive.research.ranking.migration import FAIL_POINTS

__all__ = [
    "FAIL_POINTS",
    "LocatorError",
    "PathBoundaryError",
]

# Names the package offered before; restored with the code behind them.
from memorive.research.ranking.candidate_index import CandidateIndexError
from memorive.research.ranking.candidate_index import build_candidate_index
from memorive.research.ranking.candidate_index import validate_active_index_baseline
from memorive.research.ranking.candidate_index import validate_candidate_index_identity
from memorive.research.ranking.candidate_index import validate_candidate_profile
from memorive.research.ranking.candidate_index import validate_source_snapshot
from memorive.research.ranking.locator import load_and_validate_locator
from memorive.research.ranking.locator import make_locator
from memorive.research.ranking.locator import validate_locator
from memorive.research.ranking.migration import MigrationFailure
from memorive.research.ranking.migration import migrate_candidate
__all__ = [*__all__, 'CandidateIndexError', 'MigrationFailure', 'build_candidate_index', 'validate_active_index_baseline', 'load_and_validate_locator', 'make_locator', 'migrate_candidate', 'validate_locator', 'validate_candidate_index_identity', 'validate_candidate_profile', 'validate_source_snapshot']

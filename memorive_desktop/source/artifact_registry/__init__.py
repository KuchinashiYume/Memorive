"""Records of a paper's product files (PDF, RawMD, CleanMD, chunks, cards, analyses) and their schemas.

Used by the app: adapters.py reads a product file's front matter and identity for retrieval,
reports and research; registry_v2.py and schema_v2.py keep an append-only registry of product
envelopes, where retrieval's coverage reports (retrieval/coverage_artifacts.py) record theirs.

Not started by the window: the asset migration tools (migration.py, migration_contracts.py,
maintenance.py, query_projection.py, data_lifecycle.py) move or rebuild a registry in a folder the
caller names; registry.py is the first registry version; the *_adapter.py and analysis_*.py
modules build previews and admission records that other packages call. Each module says more.
"""

from .adapters import (
    AdaptationResult,
    P1LegacyAdapter,
    build_open_type_envelope,
    build_successor_envelope,
    find_fixture_files,
    read_frontmatter,
    register_adaptation,
)
from .errors import (
    AdapterError,
    AppendOnlyViolation,
    ArtifactConflictError,
    ConcurrentWriterError,
    CorruptRegistryError,
    EnvelopeValidationError,
    FrontmatterParseError,
    SandboxBoundaryViolation,
    TruncatedRegistryError,
    UnknownArtifactError,
)
from .analysis_compatibility import (
    CompatibilityPolicy,
)
from .analysis_materialization import sha256_bytes
from .analysis_products import (
    ANALYSIS_ADMISSION_IMPLEMENTATION_VERSION,
    ArtifactProduct,
    stable_identifier,
)
from .registry_v2 import ArtifactRegistry
from .schema_v2 import (
    ENVELOPE_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    build_envelope,
    canonical_json,
    content_hash_value,
    ledger_record_ref,
    legacy_artifact_id,
    lineage_status,
    new_artifact_id,
    parent_link,
    resolved_parent_links,
    sha256_file,
    unresolved_requirement,
    validate_envelope,
    validate_readable_envelope,
)

__all__ = [
    "AdapterError",
    "AdaptationResult",
    "AppendOnlyViolation",
    "ArtifactConflictError",
    "ArtifactProduct",
    "ArtifactRegistry",
    "CompatibilityPolicy",
    "ConcurrentWriterError",
    "CorruptRegistryError",
    "ENVELOPE_SCHEMA_VERSION",
    "EVENT_SCHEMA_VERSION",
    "EnvelopeValidationError",
    "FrontmatterParseError",
    "P1LegacyAdapter",
    "ANALYSIS_ADMISSION_IMPLEMENTATION_VERSION",
    "SandboxBoundaryViolation",
    "TruncatedRegistryError",
    "UnknownArtifactError",
    "build_envelope",
    "build_open_type_envelope",
    "build_successor_envelope",
    "canonical_json",
    "content_hash_value",
    "find_fixture_files",
    "legacy_artifact_id",
    "ledger_record_ref",
    "lineage_status",
    "new_artifact_id",
    "parent_link",
    "read_frontmatter",
    "register_adaptation",
    "resolved_parent_links",
    "sha256_file",
    "sha256_bytes",
    "stable_identifier",
    "unresolved_requirement",
    "validate_envelope",
    "validate_readable_envelope",
]

# Names the package offered before; restored with the code behind them.
from .errors import ContractValidationError
from .analysis_compatibility import build_compatibility_assessment
from .analysis_products import artifact_ref
from .analysis_products import unique_text
from .snapshots import build_compatibility_policy_snapshot_envelope
from .snapshots import build_registry_snapshot_envelope
from .snapshots import verify_compatibility_policy_snapshot
from .snapshots import verify_registry_snapshot
from .knowledge_feedback_adapter import make_knowledge_registration_preview
from .knowledge_feedback_adapter import validate_knowledge_registration_preview
from .routing_evidence_adapter import make_routing_evidence_envelope_preview
from .routing_evidence_adapter import validate_routing_evidence_envelope_preview
__all__ = [*__all__, 'ContractValidationError', 'build_compatibility_policy_snapshot_envelope', 'build_compatibility_assessment', 'build_registry_snapshot_envelope', 'unique_text', 'verify_compatibility_policy_snapshot', 'verify_registry_snapshot', 'artifact_ref', 'make_knowledge_registration_preview', 'validate_knowledge_registration_preview', 'make_routing_evidence_envelope_preview', 'validate_routing_evidence_envelope_preview']

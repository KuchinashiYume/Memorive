"""Records of a paper's product files (PDF, RawMD, CleanMD, chunks, cards, analyses): the envelope
schema and an append-only registry.

Used outside the package: schema_v2.py (build and check product envelopes) by retrieval, research
analysis, research reports, research recommendations and knowledge_feedback; registry_v2.py (the
append-only registry) by knowledge_feedback and research_recommendations; analysis_materialization.py
and analysis_products.py by knowledge_admission, research_recommendations and run_ledger.

Not reached from the app's own code (tests call them): the asset migration tools (migration.py,
migration_contracts.py, maintenance.py, query_projection.py, data_lifecycle.py), the first registry
version (registry.py) and conversation_refinement_adapter.py. adapters.py (front matter of product
files, envelopes for older files) is loaded with the package but has no caller outside it.
"""

from memorive.documents.registry.adapters import (
    AdaptationResult,
    P1LegacyAdapter,
    build_open_type_envelope,
    build_successor_envelope,
    find_fixture_files,
    read_frontmatter,
    register_adaptation,
)
from memorive.documents.registry.errors import (
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
from memorive.documents.registry.analysis_compatibility import (
    CompatibilityPolicy,
)
from memorive.documents.registry.analysis_materialization import sha256_bytes
from memorive.documents.registry.analysis_products import (
    ANALYSIS_ADMISSION_IMPLEMENTATION_VERSION,
    ArtifactProduct,
    stable_identifier,
)
from memorive.documents.registry.registry_v2 import ArtifactRegistry
from memorive.documents.registry.schema_v2 import (
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
from memorive.documents.registry.errors import ContractValidationError
from memorive.documents.registry.analysis_compatibility import build_compatibility_assessment
from memorive.documents.registry.analysis_products import artifact_ref
from memorive.documents.registry.analysis_products import unique_text
from memorive.documents.registry.snapshots import build_compatibility_policy_snapshot_envelope
from memorive.documents.registry.snapshots import build_registry_snapshot_envelope
from memorive.documents.registry.snapshots import verify_compatibility_policy_snapshot
from memorive.documents.registry.snapshots import verify_registry_snapshot
from memorive.documents.registry.knowledge_feedback_adapter import make_knowledge_registration_preview
from memorive.documents.registry.knowledge_feedback_adapter import validate_knowledge_registration_preview
from memorive.documents.registry.routing_evidence_adapter import make_routing_evidence_envelope_preview
from memorive.documents.registry.routing_evidence_adapter import validate_routing_evidence_envelope_preview
__all__ = [*__all__, 'ContractValidationError', 'build_compatibility_policy_snapshot_envelope', 'build_compatibility_assessment', 'build_registry_snapshot_envelope', 'unique_text', 'verify_compatibility_policy_snapshot', 'verify_registry_snapshot', 'artifact_ref', 'make_knowledge_registration_preview', 'validate_knowledge_registration_preview', 'make_routing_evidence_envelope_preview', 'validate_routing_evidence_envelope_preview']

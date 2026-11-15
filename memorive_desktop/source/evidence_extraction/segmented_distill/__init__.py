"""LONG-DOCUMENT segmented long-document distillation capability."""

from .capacity import CapacityDecision, CapacityProfile, CapacityRoute, route_document
from .contracts import (
    ArtifactKind,
    SegmentedDistillContractError,
    canonical_hash,
)
from .merge import SLOT_STATES
from .publish import (
    publish_bytes_no_clobber,
    publish_staged_card_no_clobber,
    visibility_state,
    write_staged_bytes,
)
from .shards import SegmentCompletionLedger, SegmentShardValidator
from .topology import (
    build_source_manifest,
    build_topology,
    segment_exact_set,
)

__all__ = [
    "ArtifactKind",
    "CapacityDecision",
    "CapacityProfile",
    "CapacityRoute",
    "SLOT_STATES",
    "SegmentCompletionLedger",
    "SegmentShardValidator",
    "SegmentedDistillContractError",
    "build_source_manifest",
    "build_topology",
    "canonical_hash",
    "publish_bytes_no_clobber",
    "publish_staged_card_no_clobber",
    "route_document",
    "segment_exact_set",
    "visibility_state",
    "write_staged_bytes",
]

# Names the package offered before; restored with the code behind them.
from .assembly import assemble_card_candidate
from .assembly import build_analysis_lineage
from .assembly import build_assembly_manifest
from .assembly import build_field_candidates
from .assembly import dry_assemble
from .contracts import dispatch_artifact_validation
from .merge import merge_facts
from .topology import plan_bounded_segment_split
__all__ = [*__all__, 'assemble_card_candidate', 'build_analysis_lineage', 'build_assembly_manifest', 'build_field_candidates', 'dispatch_artifact_validation', 'dry_assemble', 'merge_facts', 'plan_bounded_segment_split']

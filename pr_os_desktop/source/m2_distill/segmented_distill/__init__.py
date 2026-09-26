"""P06/T06 segmented long-document distillation capability."""

from .assembly import (
    assemble_card_candidate,
    build_analysis_lineage,
    build_assembly_manifest,
    build_field_candidates,
    dry_assemble,
)
from .capacity import CapacityDecision, CapacityProfile, CapacityRoute, route_document
from .contracts import (
    ArtifactKind,
    SegmentedDistillContractError,
    canonical_hash,
    dispatch_artifact_validation,
)
from .merge import SLOT_STATES, merge_facts
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
    plan_bounded_segment_split,
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
    "assemble_card_candidate",
    "build_analysis_lineage",
    "build_assembly_manifest",
    "build_field_candidates",
    "build_source_manifest",
    "build_topology",
    "canonical_hash",
    "dispatch_artifact_validation",
    "dry_assemble",
    "merge_facts",
    "plan_bounded_segment_split",
    "publish_bytes_no_clobber",
    "publish_staged_card_no_clobber",
    "route_document",
    "segment_exact_set",
    "visibility_state",
    "write_staged_bytes",
]

"""Distilling a long document in segments: deciding whether to split (capacity.py), splitting into
shards (shards.py, topology.py), merging and assembling the card (merge.py, assembly.py) and
publishing it (publish.py). The workflow uses it (memorive/documents/workflow/segmented_distill.py).
"""

from memorive.documents.card_distillation.segmented_distill.capacity import CapacityDecision, CapacityProfile, CapacityRoute, route_document
from memorive.documents.card_distillation.segmented_distill.contracts import (
    ArtifactKind,
    SegmentedDistillContractError,
    canonical_hash,
)
from memorive.documents.card_distillation.segmented_distill.merge import SLOT_STATES
from memorive.documents.card_distillation.segmented_distill.publish import (
    publish_bytes_no_clobber,
    publish_staged_card_no_clobber,
    visibility_state,
    write_staged_bytes,
)
from memorive.documents.card_distillation.segmented_distill.shards import SegmentCompletionLedger, SegmentShardValidator
from memorive.documents.card_distillation.segmented_distill.topology import (
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
from memorive.documents.card_distillation.segmented_distill.assembly import assemble_card_candidate
from memorive.documents.card_distillation.segmented_distill.assembly import build_analysis_lineage
from memorive.documents.card_distillation.segmented_distill.assembly import build_assembly_manifest
from memorive.documents.card_distillation.segmented_distill.assembly import build_field_candidates
from memorive.documents.card_distillation.segmented_distill.assembly import dry_assemble
from memorive.documents.card_distillation.segmented_distill.contracts import dispatch_artifact_validation
from memorive.documents.card_distillation.segmented_distill.merge import merge_facts
from memorive.documents.card_distillation.segmented_distill.topology import plan_bounded_segment_split
__all__ = [*__all__, 'assemble_card_candidate', 'build_analysis_lineage', 'build_assembly_manifest', 'build_field_candidates', 'dispatch_artifact_validation', 'dry_assemble', 'merge_facts', 'plan_bounded_segment_split']

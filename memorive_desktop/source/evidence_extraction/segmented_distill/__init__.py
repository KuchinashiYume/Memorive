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

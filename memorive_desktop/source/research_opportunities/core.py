"""Fail-closed RESEARCH_OPPORTUNITIES successor for source, gap, comparison, and opportunity objects."""

from __future__ import annotations

import math
import re
import unicodedata
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from .canonical import (
    sha256_bytes,
    typed_payload_hash,
)
from .errors import ContractRejected, EvidenceRejected


CONCEPT_ORDER = (
    "research_object",
    "intervention_factor",
    "comparator",
    "method",
    "metric",
    "boundary_condition",
    "scale_context",
    "claim_type",
)
CONDITIONAL_CONCEPTS = frozenset(
    {"intervention_factor", "comparator", "method", "boundary_condition", "scale_context"}
)
ALLOWED_TRIGGER_TYPES = frozenset({"manual", "weekly", "knowledge_feedback_influx"})
LIMIT_KEYS = (
    "max_clusters",
    "max_cards_per_cluster",
    "max_pairs_per_cluster",
    "max_candidates_total",
)
SOURCE_PROFILE = "PUBLIC_SAFE_SYNTHETIC_ONLY"
SOURCE_RIGHT = "PUBLIC_SAFE_SYNTHETIC"
IGNORE_AUTHORITY_KIND = "HUMAN_REVIEW_AUTHORITY"
SOURCE_SNAPSHOT_KEYS = {
    "schema_version",
    "snapshot_id",
    "source_cutoff",
    "source_profile",
    "source_mode",
    "members",
    "member_count",
    "clusters",
    "trigger_scope_receipt_ref",
    "trigger_scope_receipt_hash",
    "limits",
    "service_contracts_registry_ref",
    "service_contracts_registry_hash",
    "configuration_policy_ref",
    "configuration_policy_hash",
    "real_card_reads",
    "public_safe_synthetic_only",
    "content_hash",
}
SOURCE_MEMBER_KEYS = {
    "card_id",
    "paper_id",
    "card_revision",
    "data_ownership",
    "card_content_hash",
    "chunks_content_hash",
    "rights_classification",
    "cluster_id",
    "topk_rank",
}
PROJECTION_KEYS = {
    "schema_version",
    "card_id",
    "paper_id",
    "card_content_hash",
    "chunks_content_hash",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "language",
    "concept_count",
    "concepts",
    "conclusion_direction",
    "claim_type_persisted_to_card",
    "coverage_status",
    "coverage_limited_facets",
    "content_hash",
}
ANCHOR_RESOLUTION_RECEIPT_KEYS = {
    "schema_version",
    "receipt_id",
    "projection_card_id",
    "projection_hash",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "recovery_scope",
    "concept_count",
    "anchored_binding_count",
    "unanchored_binding_count",
    "not_applicable_concept_count",
    "resolution_records",
    "coverage_status",
    "coverage_limited_facets",
    "source_card_mutated",
    "claim_type_persisted_to_card",
    "content_hash",
}
GAP_EVIDENCE_KEYS = {
    "schema_version",
    "evidence_id",
    "gap_type",
    "author_statement_path",
    "quote",
    "chunk_id",
    "card_id",
    "paper_id",
    "card_content_hash",
    "chunks_content_hash",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "source_scope",
    "mention_count_scope",
    "frozen_corpus_mention_count",
    "possibly_addressed_in_frozen_corpus",
    "scientific_judgment_status",
    "content_hash",
}
PAIR_LEDGER_KEYS = {
    "schema_version",
    "ledger_id",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "configuration_policy_hash",
    "cluster_id",
    "ordered_topk_card_ids",
    "max_pairs_per_cluster",
    "pairs",
    "pair_count",
    "content_hash",
}
ASSESSMENT_KEYS = {
    "schema_version",
    "assessment_id",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "configuration_policy_hash",
    "pair_ledger_ref",
    "pair_ledger_hash",
    "pair_card_ids",
    "projection_hashes",
    "anchor_resolution_receipt_hashes",
    "concept_count",
    "concept_results",
    "overall",
    "conditional_difference_facets",
    "coverage_limited_facets",
    "language_mismatch",
    "weighted_similarity_used",
    "provider_score_used",
    "derived_penalty_used",
    "content_hash",
}
TENSION_EVIDENCE_KEYS = {
    "schema_version",
    "tension_id",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "configuration_policy_hash",
    "pair_card_ids",
    "pair_ledger_ref",
    "pair_ledger_hash",
    "assessment_ref",
    "assessment_hash",
    "projection_hashes",
    "classification",
    "rationale_code",
    "conclusion_direction_by_card",
    "human_ignore_decision_ref",
    "human_ignore_decision_hash",
    "scientific_truth_judgment",
    "winner_selected",
    "content_hash",
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _required_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractRejected(code)
    return value.strip()


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = normalize_text(value)
        return [text] if text else []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                for key in ("value", "name", "label", "metric"):
                    if key in item:
                        result.extend(_values(item[key]))
                        break
            else:
                result.extend(_values(item))
        return result
    if isinstance(value, dict):
        result: list[str] = []
        for key in sorted(value):
            result.extend(_values(value[key]))
        return result
    return []


def _member(snapshot: Mapping[str, Any], card_id: str) -> Mapping[str, Any]:
    for item in snapshot.get("members", []):
        if isinstance(item, dict) and item.get("card_id") == card_id:
            return item
    raise EvidenceRejected("CARD_NOT_IN_SOURCE_SNAPSHOT")

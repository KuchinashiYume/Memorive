"""Fail-closed RESEARCH_OPPORTUNITIES successor for source, gap, comparison, and opportunity objects."""

from __future__ import annotations

import math
import re
import unicodedata
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from .canonical import (
    content_hash_matches,
    external_hash_is_valid,
    sha256_bytes,
    typed_hash_is_valid,
    typed_payload_hash,
    with_content_hash,
)
from .errors import CandidateRejected, ContractRejected, EvidenceRejected, SourceRejected


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


def _iso_datetime(value: Any, code: str) -> str:
    text = _required_text(value, code)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractRejected(code) from exc
    return text


def _timezone_aware_iso_datetime(value: Any, code: str) -> str:
    text = _iso_datetime(value, code)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractRejected(code)
    return text


def _validate_ignore_authority_registry(
    registry: Mapping[str, str], registry_hash: Mapping[str, Any]
) -> dict[str, str]:
    if not isinstance(registry, Mapping):
        raise EvidenceRejected("IGNORE_AUTHORITY_REGISTRY_INVALID")
    canonical: dict[str, str] = {}
    for authority_ref, decided_by in registry.items():
        if (
            not isinstance(authority_ref, str)
            or not authority_ref.strip()
            or authority_ref != authority_ref.strip()
            or not isinstance(decided_by, str)
            or not decided_by.strip()
            or decided_by != decided_by.strip()
        ):
            raise EvidenceRejected("IGNORE_AUTHORITY_REGISTRY_INVALID")
        canonical[authority_ref] = decided_by
    if not typed_hash_is_valid(registry_hash):
        raise EvidenceRejected("IGNORE_AUTHORITY_REGISTRY_HASH_INVALID")
    if typed_payload_hash(canonical) != registry_hash:
        raise EvidenceRejected("IGNORE_AUTHORITY_REGISTRY_HASH_MISMATCH")
    return canonical


def _hash_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(value))


def _require_external_hash(value: Any, code: str) -> dict[str, Any]:
    if not external_hash_is_valid(value):
        raise ContractRejected(code)
    return deepcopy(value)


def _require_content_hash(value: Mapping[str, Any], code: str) -> None:
    if not content_hash_matches(value):
        raise ContractRejected(code)


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


def _unique_values(values: Sequence[str]) -> list[str]:
    return sorted({normalize_text(value) for value in values if normalize_text(value)})


def _text_contains(haystack: str, needle: str) -> bool:
    """Match normalized text, using token boundaries for ASCII identifiers."""

    normalized_haystack = normalize_text(haystack).casefold()
    normalized_needle = normalize_text(needle).casefold()
    if not normalized_needle:
        return False
    if all(ord(char) < 128 for char in normalized_needle):
        return bool(
            re.search(
                rf"(?<![a-z0-9_]){re.escape(normalized_needle)}(?![a-z0-9_])",
                normalized_haystack,
            )
        )
    return normalized_needle in normalized_haystack


def _valid_anchor(anchor: Mapping[str, Any], chunks: Mapping[str, str]) -> bool:
    chunk_id = anchor.get("chunk_id")
    quote = anchor.get("quote")
    return (
        isinstance(chunk_id, str)
        and isinstance(quote, str)
        and bool(quote.strip())
        and isinstance(chunks.get(chunk_id), str)
        and _text_contains(chunks[chunk_id], quote)
    )


def _anchor_view(anchor: Mapping[str, Any]) -> dict[str, str]:
    return {"chunk_id": str(anchor["chunk_id"]), "quote": str(anchor["quote"])}


def _field_anchor_candidates(card: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    root = card.get("source_anchor")
    by_field = root.get("by_field") if isinstance(root, dict) else None
    raw = by_field.get(field) if isinstance(by_field, dict) else None
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _bind_values(
    values: Sequence[str],
    chunks: Mapping[str, str],
    *,
    field_anchors: Sequence[Mapping[str, Any]] = (),
    allow_recovery: bool,
    field_kind: str = "field_anchor",
) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for value in _unique_values(values):
        if value.casefold() == "not_applicable":
            bindings.append(
                {
                    "value": value,
                    "status": "not_applicable",
                    "anchor_kind": None,
                    "anchors": [],
                }
            )
            continue
        matches = [
            _anchor_view(anchor)
            for anchor in field_anchors
            if _valid_anchor(anchor, chunks) and _text_contains(str(anchor["quote"]), value)
        ]
        kind = field_kind if matches else None
        if not matches and allow_recovery:
            for chunk_id in sorted(chunks):
                text = chunks[chunk_id]
                if isinstance(text, str) and _text_contains(text, value):
                    matches = [{"chunk_id": chunk_id, "quote": value}]
                    kind = "recovered_chunk_anchor"
                    break
        bindings.append(
            {
                "value": value,
                "status": "anchored" if matches else "unanchored",
                "anchor_kind": kind,
                "anchors": matches,
            }
        )
    return bindings


def _concept_row(name: str, bindings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [deepcopy(dict(item)) for item in bindings]
    values = [str(item["value"]) for item in rows]
    if not rows:
        coverage = "missing"
        status = "missing"
    elif all(item["status"] == "not_applicable" for item in rows):
        coverage = "not_applicable"
        status = "not_applicable"
    elif all(item["status"] == "anchored" for item in rows):
        coverage = "fully_anchored"
        status = "present"
    else:
        coverage = "artifact_level_no_dedicated_anchor"
        status = "present"
    return {
        "concept": name,
        "values": values,
        "value_status": status,
        "source_coverage": coverage,
        "bindings": rows,
    }


def _claim_type_and_direction(texts: Sequence[str]) -> tuple[str | None, str | None]:
    text = " ".join(texts).casefold()
    if any(token in text for token in ("causes", "caused", "leads to", "results in")):
        kind = "causal"
    elif any(token in text for token in ("associated with", "correlates", "correlated", "association")):
        kind = "associational"
    elif any(token in text for token in ("increased", "decreased", "higher", "lower", "greater", "reduced", "no effect", "no difference")):
        kind = "comparative"
    else:
        kind = None
    if any(token in text for token in ("no effect", "no difference", "unchanged", "comparable")):
        direction = "null"
    elif any(token in text for token in ("decreased", "lower", "reduced", "negative")):
        direction = "negative"
    elif any(token in text for token in ("increased", "higher", "greater", "improved", "positive")):
        direction = "positive"
    else:
        direction = None
    return kind, direction


def validate_source_card(card: Mapping[str, Any]) -> None:
    _required_text(card.get("card_id"), "CARD_ID_REQUIRED")
    _required_text(card.get("paper_id"), "PAPER_ID_REQUIRED")
    if card.get("schema_version") != 6:
        raise SourceRejected("CARD_SCHEMA_VERSION_NOT_6")
    if card.get("review_status") != "active":
        raise SourceRejected("CARD_NOT_ACTIVE")
    if card.get("doc_type") != "literature":
        raise SourceRejected("DOC_TYPE_NOT_LITERATURE")
    if card.get("is_derived") is not False:
        raise SourceRejected("DERIVED_SOURCE_FORBIDDEN")
    if card.get("data_ownership") not in {"self", "entrusted"}:
        raise SourceRejected("DATA_OWNERSHIP_UNKNOWN")
    if not content_hash_matches(card):
        raise SourceRejected("CARD_CONTENT_HASH_REQUIRED_OR_MISMATCH")


def make_trigger_scope_receipt(
    *,
    trigger_type: str,
    topic_scope: Sequence[str],
    paper_scope: Sequence[str],
    limits: Mapping[str, int],
) -> dict[str, Any]:
    if trigger_type not in ALLOWED_TRIGGER_TYPES:
        raise SourceRejected("TRIGGER_TYPE_NOT_ALLOWED")
    topics = sorted({_required_text(item, "TOPIC_SCOPE_ITEM_REQUIRED") for item in topic_scope})
    papers = sorted({_required_text(item, "PAPER_SCOPE_ITEM_REQUIRED") for item in paper_scope})
    if not topics and not papers:
        raise SourceRejected("TRIGGER_SCOPE_REQUIRED")
    if set(limits) != set(LIMIT_KEYS):
        raise SourceRejected("LIMIT_KEYS_MUST_MATCH_EXACT_SET")
    normalized: dict[str, int] = {}
    for key in LIMIT_KEYS:
        value = limits[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SourceRejected(f"{key.upper()}_MUST_BE_POSITIVE_INTEGER")
        normalized[key] = value
    receipt_id = "trg_" + sha256_bytes(
        (trigger_type + "\n" + "\n".join(topics) + "\n--\n" + "\n".join(papers)).encode("utf-8")
    )[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_TRIGGER_SCOPE_RECEIPT_V2",
            "receipt_id": receipt_id,
            "trigger_type": trigger_type,
            "topic_scope": topics,
            "paper_scope": papers,
            "limits": normalized,
            "scheduler_invoked": False,
            "whole_library_scan": False,
        }
    )


def validate_trigger_scope_receipt(receipt: Mapping[str, Any]) -> None:
    _require_content_hash(receipt, "TRIGGER_RECEIPT_HASH_MISMATCH")
    expected_keys = {
        "schema_version",
        "receipt_id",
        "trigger_type",
        "topic_scope",
        "paper_scope",
        "limits",
        "scheduler_invoked",
        "whole_library_scan",
        "content_hash",
    }
    if set(receipt) != expected_keys or receipt.get("schema_version") != "RESEARCH_OPPORTUNITIES_TRIGGER_SCOPE_RECEIPT_V2":
        raise SourceRejected("TRIGGER_RECEIPT_CONTRACT_INVALID")
    rebuilt = make_trigger_scope_receipt(
        trigger_type=str(receipt.get("trigger_type")),
        topic_scope=receipt.get("topic_scope", []),
        paper_scope=receipt.get("paper_scope", []),
        limits=receipt.get("limits", {}),
    )
    if rebuilt != dict(receipt):
        raise SourceRejected("TRIGGER_RECEIPT_SEMANTIC_MISMATCH")


def make_source_snapshot(
    *,
    snapshot_id: str,
    source_cutoff: str,
    cards: Sequence[Mapping[str, Any]],
    chunks_by_card: Mapping[str, Mapping[str, str]],
    source_rights: Mapping[str, str],
    cluster_topk: Mapping[str, Sequence[str]],
    trigger_receipt: Mapping[str, Any],
    service_contracts_registry_ref: str,
    service_contracts_registry_hash: Mapping[str, Any],
    configuration_policy_ref: str,
    configuration_policy_hash: Mapping[str, Any],
    source_profile: str = SOURCE_PROFILE,
) -> dict[str, Any]:
    snapshot_id = _required_text(snapshot_id, "SNAPSHOT_ID_REQUIRED")
    source_cutoff = _iso_datetime(source_cutoff, "SOURCE_CUTOFF_INVALID")
    if source_profile != SOURCE_PROFILE:
        raise SourceRejected("SOURCE_PROFILE_NOT_AUTHORIZED")
    validate_trigger_scope_receipt(trigger_receipt)
    service_contracts_registry_ref = _required_text(service_contracts_registry_ref, "ServiceContracts_REGISTRY_REF_REQUIRED")
    configuration_policy_ref = _required_text(configuration_policy_ref, "Configuration_POLICY_REF_REQUIRED")
    service_contracts_hash = _require_external_hash(service_contracts_registry_hash, "ServiceContracts_REGISTRY_HASH_INVALID")
    configuration_hash = _require_external_hash(configuration_policy_hash, "Configuration_POLICY_HASH_INVALID")
    if not cards:
        raise SourceRejected("SOURCE_CARD_SET_EMPTY")
    cards_by_id: dict[str, Mapping[str, Any]] = {}
    for card in cards:
        validate_source_card(card)
        card_id = str(card["card_id"])
        if card_id in cards_by_id:
            raise SourceRejected("DUPLICATE_CARD_ID")
        cards_by_id[card_id] = card
    if set(chunks_by_card) != set(cards_by_id):
        raise SourceRejected("CHUNK_CARD_EXACT_SET_MISMATCH")
    if set(source_rights) != set(cards_by_id):
        raise SourceRejected("SOURCE_RIGHTS_EXACT_SET_MISMATCH")
    if any(right != SOURCE_RIGHT for right in source_rights.values()):
        raise SourceRejected("SOURCE_RIGHTS_NOT_AUTHORIZED_FOR_PROFILE")

    limits = dict(trigger_receipt["limits"])
    if len(cards_by_id) > limits["max_candidates_total"]:
        raise SourceRejected("SOURCE_CARD_COUNT_EXCEEDS_TOTAL_LIMIT")
    if not cluster_topk or len(cluster_topk) > limits["max_clusters"]:
        raise SourceRejected("CLUSTER_COUNT_OUTSIDE_FROZEN_LIMIT")
    seen: set[str] = set()
    clusters: list[dict[str, Any]] = []
    placement: dict[str, tuple[str, int]] = {}
    for cluster_id in sorted(cluster_topk):
        clean_id = _required_text(cluster_id, "CLUSTER_ID_REQUIRED")
        ordered = list(cluster_topk[cluster_id])
        if not ordered or len(ordered) > limits["max_cards_per_cluster"]:
            raise SourceRejected("CLUSTER_CARD_COUNT_OUTSIDE_FROZEN_LIMIT")
        if len(ordered) != len(set(ordered)):
            raise SourceRejected("DUPLICATE_CARD_WITHIN_CLUSTER")
        for rank, card_id in enumerate(ordered, 1):
            if card_id not in cards_by_id:
                raise SourceRejected("CLUSTER_CARD_NOT_IN_SOURCE_SET")
            if card_id in seen:
                raise SourceRejected("CARD_ASSIGNED_TO_MULTIPLE_CLUSTERS")
            seen.add(card_id)
            placement[card_id] = (clean_id, rank)
        clusters.append({"cluster_id": clean_id, "ordered_card_ids": ordered})
    if seen != set(cards_by_id):
        raise SourceRejected("SOURCE_CARD_NOT_ASSIGNED_TO_CLUSTER")

    members: list[dict[str, Any]] = []
    for card_id in sorted(cards_by_id):
        chunks = chunks_by_card[card_id]
        if not isinstance(chunks, dict) or not chunks or any(
            not isinstance(key, str) or not key or not isinstance(value, str)
            for key, value in chunks.items()
        ):
            raise SourceRejected("FROZEN_CHUNKS_INVALID")
        cluster_id, rank = placement[card_id]
        card = cards_by_id[card_id]
        members.append(
            {
                "card_id": card_id,
                "paper_id": card["paper_id"],
                "card_revision": card.get("revision", 1),
                "data_ownership": card["data_ownership"],
                "card_content_hash": _hash_copy(card["content_hash"]),
                "chunks_content_hash": typed_payload_hash(dict(chunks)),
                "rights_classification": SOURCE_RIGHT,
                "cluster_id": cluster_id,
                "topk_rank": rank,
            }
        )
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_OPPORTUNITY_SOURCE_SNAPSHOT_V2",
            "snapshot_id": snapshot_id,
            "source_cutoff": source_cutoff,
            "source_profile": SOURCE_PROFILE,
            "source_mode": "ACTIVE_ONLY_BOUNDED_CLUSTER_TOPK",
            "members": members,
            "member_count": len(members),
            "clusters": clusters,
            "trigger_scope_receipt_ref": trigger_receipt["receipt_id"],
            "trigger_scope_receipt_hash": _hash_copy(trigger_receipt["content_hash"]),
            "limits": limits,
            "service_contracts_registry_ref": service_contracts_registry_ref,
            "service_contracts_registry_hash": service_contracts_hash,
            "configuration_policy_ref": configuration_policy_ref,
            "configuration_policy_hash": configuration_hash,
            "real_card_reads": 0,
            "public_safe_synthetic_only": True,
        }
    )


def validate_source_snapshot(
    snapshot: Mapping[str, Any],
    *,
    cards_by_id: Mapping[str, Mapping[str, Any]],
    chunks_by_card: Mapping[str, Mapping[str, str]],
    trigger_receipt: Mapping[str, Any],
    service_contracts_registry_hash: Mapping[str, Any],
    configuration_policy_hash: Mapping[str, Any],
) -> None:
    _require_content_hash(snapshot, "SOURCE_SNAPSHOT_HASH_MISMATCH")
    if set(snapshot) != SOURCE_SNAPSHOT_KEYS:
        raise SourceRejected("SOURCE_SNAPSHOT_EXACT_KEYS_MISMATCH")
    if snapshot.get("schema_version") != "RESEARCH_OPPORTUNITIES_OPPORTUNITY_SOURCE_SNAPSHOT_V2":
        raise SourceRejected("SOURCE_SNAPSHOT_SCHEMA_INVALID")
    _iso_datetime(snapshot.get("source_cutoff"), "SOURCE_CUTOFF_INVALID")
    if snapshot.get("source_profile") != SOURCE_PROFILE or snapshot.get("source_mode") != "ACTIVE_ONLY_BOUNDED_CLUSTER_TOPK":
        raise SourceRejected("SOURCE_PROFILE_OR_MODE_MISMATCH")
    if snapshot.get("real_card_reads") != 0 or snapshot.get("public_safe_synthetic_only") is not True:
        raise SourceRejected("SOURCE_EXECUTION_BOUNDARY_MISMATCH")
    validate_trigger_scope_receipt(trigger_receipt)
    if snapshot.get("trigger_scope_receipt_ref") != trigger_receipt.get("receipt_id"):
        raise SourceRejected("SOURCE_TRIGGER_REF_MISMATCH")
    if snapshot.get("trigger_scope_receipt_hash") != trigger_receipt.get("content_hash"):
        raise SourceRejected("SOURCE_TRIGGER_HASH_MISMATCH")
    if snapshot.get("limits") != trigger_receipt.get("limits"):
        raise SourceRejected("SOURCE_TRIGGER_LIMITS_MISMATCH")
    _required_text(snapshot.get("service_contracts_registry_ref"), "ServiceContracts_REGISTRY_REF_REQUIRED")
    _required_text(snapshot.get("configuration_policy_ref"), "Configuration_POLICY_REF_REQUIRED")
    if snapshot.get("service_contracts_registry_hash") != service_contracts_registry_hash or not external_hash_is_valid(service_contracts_registry_hash):
        raise SourceRejected("SOURCE_ServiceContracts_REGISTRY_HASH_MISMATCH")
    if snapshot.get("configuration_policy_hash") != configuration_policy_hash or not external_hash_is_valid(configuration_policy_hash):
        raise SourceRejected("SOURCE_Configuration_POLICY_HASH_MISMATCH")
    members = snapshot.get("members")
    clusters = snapshot.get("clusters")
    if not isinstance(members, list) or snapshot.get("member_count") != len(members):
        raise SourceRejected("SOURCE_MEMBER_COUNT_MISMATCH")
    if not isinstance(clusters, list):
        raise SourceRejected("SOURCE_CLUSTERS_INVALID")
    limits = snapshot["limits"]
    if len(members) > limits["max_candidates_total"] or not clusters or len(clusters) > limits["max_clusters"]:
        raise SourceRejected("SOURCE_MEMBER_OR_CLUSTER_LIMIT_EXCEEDED")
    expected_cards = {str(item.get("card_id")) for item in members if isinstance(item, dict)}
    if len(expected_cards) != len(members) or expected_cards != set(cards_by_id) or expected_cards != set(chunks_by_card):
        raise SourceRejected("SOURCE_MEMBER_EXACT_SET_MISMATCH")
    placement: dict[str, tuple[str, int]] = {}
    for cluster in clusters:
        if not isinstance(cluster, dict) or set(cluster) != {"cluster_id", "ordered_card_ids"}:
            raise SourceRejected("SOURCE_CLUSTER_CONTRACT_INVALID")
        ordered = cluster["ordered_card_ids"]
        if not isinstance(ordered, list) or not ordered or len(ordered) > limits["max_cards_per_cluster"]:
            raise SourceRejected("SOURCE_CLUSTER_ORDER_INVALID")
        for rank, card_id in enumerate(ordered, 1):
            if card_id in placement:
                raise SourceRejected("SOURCE_CLUSTER_DUPLICATE_MEMBER")
            placement[card_id] = (cluster["cluster_id"], rank)
    if set(placement) != expected_cards:
        raise SourceRejected("SOURCE_CLUSTER_MEMBER_EXACT_SET_MISMATCH")
    by_id = {item["card_id"]: item for item in members}
    for card_id in sorted(expected_cards):
        card = cards_by_id[card_id]
        chunks = chunks_by_card[card_id]
        validate_source_card(card)
        member = by_id[card_id]
        if set(member) != SOURCE_MEMBER_KEYS:
            raise SourceRejected("SOURCE_MEMBER_CONTRACT_INVALID")
        if member.get("paper_id") != card.get("paper_id"):
            raise SourceRejected("SOURCE_PAPER_IDENTITY_MISMATCH")
        if member.get("card_revision") != card.get("revision", 1):
            raise SourceRejected("SOURCE_CARD_REVISION_MISMATCH")
        if member.get("data_ownership") != card.get("data_ownership"):
            raise SourceRejected("SOURCE_DATA_OWNERSHIP_MISMATCH")
        if member.get("card_content_hash") != card.get("content_hash"):
            raise SourceRejected("SOURCE_CARD_HASH_MISMATCH")
        if member.get("chunks_content_hash") != typed_payload_hash(dict(chunks)):
            raise SourceRejected("SOURCE_CHUNKS_HASH_MISMATCH")
        if member.get("rights_classification") != SOURCE_RIGHT:
            raise SourceRejected("SOURCE_RIGHTS_MISMATCH")
        if (member.get("cluster_id"), member.get("topk_rank")) != placement[card_id]:
            raise SourceRejected("SOURCE_CLUSTER_PLACEMENT_MISMATCH")


def _member(snapshot: Mapping[str, Any], card_id: str) -> Mapping[str, Any]:
    for item in snapshot.get("members", []):
        if isinstance(item, dict) and item.get("card_id") == card_id:
            return item
    raise EvidenceRejected("CARD_NOT_IN_SOURCE_SNAPSHOT")


def project_comparison_concepts(
    card: Mapping[str, Any],
    chunks: Mapping[str, str],
    source_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    validate_source_card(card)
    _require_content_hash(source_snapshot, "SOURCE_SNAPSHOT_HASH_MISMATCH")
    card_id = str(card["card_id"])
    member = _member(source_snapshot, card_id)
    if member.get("card_content_hash") != card.get("content_hash"):
        raise EvidenceRejected("PROJECTION_CARD_HASH_MISMATCH")
    if member.get("chunks_content_hash") != typed_payload_hash(dict(chunks)):
        raise EvidenceRejected("PROJECTION_CHUNKS_HASH_MISMATCH")
    context = card.get("comparison_context") if isinstance(card.get("comparison_context"), dict) else {}
    research_values = _values(card.get("research_object")) or _values(context.get("object"))
    rows: list[dict[str, Any]] = []
    rows.append(
        _concept_row(
            "research_object",
            _bind_values(
                research_values,
                chunks,
                field_anchors=_field_anchor_candidates(card, "research_object"),
                allow_recovery=False,
            ),
        )
    )
    rows.append(
        _concept_row(
            "intervention_factor",
            _bind_values(_values(context.get("condition")), chunks, allow_recovery=True),
        )
    )
    rows.append(
        _concept_row(
            "comparator",
            _bind_values(_values(context.get("groups")), chunks, allow_recovery=True),
        )
    )
    rows.append(
        _concept_row(
            "method",
            _bind_values(
                _values(card.get("method")),
                chunks,
                field_anchors=_field_anchor_candidates(card, "method"),
                allow_recovery=False,
            ),
        )
    )
    metric_bindings: list[dict[str, Any]] = []
    for item in card.get("key_data", []) if isinstance(card.get("key_data"), list) else []:
        if not isinstance(item, dict):
            continue
        metric_bindings.extend(
            _bind_values(
                _values(item.get("metric")),
                chunks,
                field_anchors=[item],
                allow_recovery=False,
                field_kind="item_anchor",
            )
        )
    rows.append(_concept_row("metric", metric_bindings))
    boundary_values = _values(card.get("boundary_conditions"))
    boundary_bindings = _bind_values(
        boundary_values,
        chunks,
        field_anchors=_field_anchor_candidates(card, "boundary_conditions"),
        allow_recovery=False,
    )
    boundary_bindings.extend(
        _bind_values(
            _values(context.get("time")) + _values(context.get("applicability")),
            chunks,
            allow_recovery=True,
        )
    )
    rows.append(_concept_row("boundary_condition", boundary_bindings))
    rows.append(
        _concept_row(
            "scale_context",
            _bind_values(
                _values(context.get("scale")) + _values(context.get("scenario")),
                chunks,
                allow_recovery=True,
            ),
        )
    )

    anchored_claim_texts: list[str] = []
    claim_anchors: list[dict[str, str]] = []
    for field in ("author_conclusion", "key_results"):
        values = _values(card.get(field))
        bindings = _bind_values(
            values,
            chunks,
            field_anchors=_field_anchor_candidates(card, field),
            allow_recovery=False,
        )
        for binding in bindings:
            if binding["status"] == "anchored":
                anchored_claim_texts.append(binding["value"])
                claim_anchors.extend(deepcopy(binding["anchors"]))
    language = str(card.get("language", "und"))
    claim_type, direction = (
        _claim_type_and_direction(anchored_claim_texts)
        if language == "en" and anchored_claim_texts
        else (None, None)
    )
    claim_bindings = []
    if claim_type:
        claim_bindings.append(
            {
                "value": claim_type,
                "status": "anchored",
                "anchor_kind": "derived_claim_anchor",
                "anchors": claim_anchors,
            }
        )
    rows.append(_concept_row("claim_type", claim_bindings))
    if tuple(row["concept"] for row in rows) != CONCEPT_ORDER:
        raise EvidenceRejected("EIGHT_CONCEPT_ORDER_DRIFT")
    limited = [
        row["concept"]
        for row in rows
        if row["source_coverage"] not in {"fully_anchored", "not_applicable"}
    ]
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_COMPARISON_CONCEPT_PROJECTION_V2",
            "card_id": card_id,
            "paper_id": card["paper_id"],
            "card_content_hash": _hash_copy(card["content_hash"]),
            "chunks_content_hash": _hash_copy(member["chunks_content_hash"]),
            "source_snapshot_ref": source_snapshot["snapshot_id"],
            "source_snapshot_hash": _hash_copy(source_snapshot["content_hash"]),
            "language": language,
            "concept_count": 8,
            "concepts": rows,
            "conclusion_direction": direction,
            "claim_type_persisted_to_card": False,
            "coverage_status": "coverage_limited" if limited else "complete",
            "coverage_limited_facets": limited,
        }
    )


def _gap_type(block: str, quote: str) -> str:
    if block == "limitations":
        return "author_stated_limitation"
    folded = normalize_text(quote).casefold()
    if any(token in folded for token in ("research gap", "remains unknown", "not understood", "lack of evidence", "open question")):
        return "author_stated_field_gap"
    return "author_stated_future_work"


def extract_author_stated_gaps(
    card: Mapping[str, Any],
    chunks: Mapping[str, str],
    source_snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validate_source_card(card)
    _require_content_hash(source_snapshot, "SOURCE_SNAPSHOT_HASH_MISMATCH")
    card_id = str(card["card_id"])
    member = _member(source_snapshot, card_id)
    if member.get("card_content_hash") != card.get("content_hash"):
        raise EvidenceRejected("GAP_CARD_HASH_MISMATCH")
    if member.get("chunks_content_hash") != typed_payload_hash(dict(chunks)):
        raise EvidenceRejected("GAP_CHUNKS_HASH_MISMATCH")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    root = card.get("author_limitations_outlook")
    if not isinstance(root, dict):
        return accepted, rejected
    for block in ("limitations", "outlook"):
        entries = root.get(block, [])
        if not isinstance(entries, list):
            rejected.append({"field": f"author_limitations_outlook.{block}", "reason": "BLOCK_NOT_LIST"})
            continue
        for index, item in enumerate(entries):
            path = f"author_limitations_outlook.{block}[{index}]"
            if not isinstance(item, dict):
                rejected.append({"field": path, "reason": "ITEM_NOT_OBJECT"})
                continue
            quote = item.get("quote")
            chunk_id = item.get("chunk_id")
            if not isinstance(quote, str) or not quote.strip():
                rejected.append({"field": path, "reason": "QUOTE_MISSING"})
                continue
            if not isinstance(chunk_id, str) or not isinstance(chunks.get(chunk_id), str):
                rejected.append({"field": path, "reason": "CHUNK_NOT_RESOLVABLE"})
                continue
            if not _text_contains(chunks[chunk_id], quote):
                rejected.append({"field": path, "reason": "QUOTE_NOT_IN_CHUNK"})
                continue
            evidence_id = "gap_" + sha256_bytes(
                (
                    source_snapshot["content_hash"]["value"]
                    + "\n"
                    + card_id
                    + "\n"
                    + path
                    + "\n"
                    + normalize_text(quote)
                ).encode("utf-8")
            )[:24].lower()
            accepted.append(
                with_content_hash(
                    {
                        "schema_version": "RESEARCH_OPPORTUNITIES_AUTHOR_STATED_GAP_EVIDENCE_V2",
                        "evidence_id": evidence_id,
                        "gap_type": _gap_type(block, quote),
                        "author_statement_path": path,
                        "quote": quote,
                        "chunk_id": chunk_id,
                        "card_id": card_id,
                        "paper_id": card["paper_id"],
                        "card_content_hash": _hash_copy(card["content_hash"]),
                        "chunks_content_hash": _hash_copy(member["chunks_content_hash"]),
                        "source_snapshot_ref": source_snapshot["snapshot_id"],
                        "source_snapshot_hash": _hash_copy(source_snapshot["content_hash"]),
                        "source_scope": "FROZEN_ACTIVE_CARD_AND_CHUNKS_ONLY",
                        "mention_count_scope": "FROZEN_SOURCE_SNAPSHOT_ONLY",
                        "frozen_corpus_mention_count": 1,
                        "possibly_addressed_in_frozen_corpus": False,
                        "scientific_judgment_status": "NOT_ASSESSED",
                    }
                )
            )
    return sorted(accepted, key=lambda item: item["evidence_id"]), rejected


def make_pair_ledger(source_snapshot: Mapping[str, Any], *, cluster_id: str) -> dict[str, Any]:
    _require_content_hash(source_snapshot, "SOURCE_SNAPSHOT_HASH_MISMATCH")
    cluster = next(
        (
            item
            for item in source_snapshot.get("clusters", [])
            if isinstance(item, dict) and item.get("cluster_id") == cluster_id
        ),
        None,
    )
    if not cluster:
        raise EvidenceRejected("PAIR_CLUSTER_NOT_IN_SOURCE_SNAPSHOT")
    ordered = list(cluster["ordered_card_ids"])
    maximum = source_snapshot.get("limits", {}).get("max_pairs_per_cluster")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        raise EvidenceRejected("PAIR_LIMIT_INVALID")
    pairs: list[dict[str, Any]] = []
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if len(pairs) == maximum:
                break
            card_ids = sorted((first, second))
            pair_id = "pair_" + sha256_bytes(
                (
                    source_snapshot["content_hash"]["value"]
                    + "\n"
                    + cluster_id
                    + "\n"
                    + "\n".join(card_ids)
                ).encode("utf-8")
            )[:24].lower()
            pairs.append({"pair_id": pair_id, "card_ids": card_ids})
        if len(pairs) == maximum:
            break
    ledger_id = "plg_" + sha256_bytes(
        (source_snapshot["content_hash"]["value"] + "\n" + cluster_id).encode("utf-8")
    )[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_PAIR_LEDGER_V1",
            "ledger_id": ledger_id,
            "source_snapshot_ref": source_snapshot["snapshot_id"],
            "source_snapshot_hash": _hash_copy(source_snapshot["content_hash"]),
            "configuration_policy_hash": _hash_copy(source_snapshot["configuration_policy_hash"]),
            "cluster_id": cluster_id,
            "ordered_topk_card_ids": ordered,
            "max_pairs_per_cluster": maximum,
            "pairs": pairs,
            "pair_count": len(pairs),
        }
    )


def _projection_map(projection: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    _require_content_hash(projection, "PROJECTION_HASH_MISMATCH")
    if set(projection) != PROJECTION_KEYS:
        raise EvidenceRejected("PROJECTION_EXACT_KEYS_MISMATCH")
    if projection.get("schema_version") != "RESEARCH_OPPORTUNITIES_COMPARISON_CONCEPT_PROJECTION_V2":
        raise EvidenceRejected("PROJECTION_SCHEMA_MISMATCH")
    if projection.get("concept_count") != 8:
        raise EvidenceRejected("PROJECTION_DECLARED_CONCEPT_COUNT_MISMATCH")
    rows = projection.get("concepts")
    if not isinstance(rows, list) or len(rows) != 8:
        raise EvidenceRejected("PROJECTION_CONCEPT_COUNT_MISMATCH")
    if tuple(row.get("concept") for row in rows if isinstance(row, dict)) != CONCEPT_ORDER:
        raise EvidenceRejected("PROJECTION_CONCEPT_EXACT_SET_MISMATCH")
    for row in rows:
        if set(row) != {"concept", "values", "value_status", "source_coverage", "bindings"}:
            raise EvidenceRejected("PROJECTION_CONCEPT_ROW_EXACT_KEYS_MISMATCH")
        values = row.get("values")
        bindings = row.get("bindings")
        if (
            not isinstance(values, list)
            or not all(isinstance(value, str) and bool(value) for value in values)
            or not isinstance(bindings, list)
        ):
            raise EvidenceRejected("PROJECTION_CONCEPT_VALUES_OR_BINDINGS_INVALID")
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {
                "value",
                "status",
                "anchor_kind",
                "anchors",
            }:
                raise EvidenceRejected("PROJECTION_BINDING_EXACT_KEYS_MISMATCH")
            anchors = binding.get("anchors")
            if not isinstance(anchors, list) or any(
                not isinstance(anchor, dict)
                or set(anchor) != {"chunk_id", "quote"}
                or not isinstance(anchor.get("chunk_id"), str)
                or not anchor["chunk_id"]
                or not isinstance(anchor.get("quote"), str)
                or not anchor["quote"]
                for anchor in anchors
            ):
                raise EvidenceRejected("PROJECTION_ANCHOR_EXACT_CONTRACT_INVALID")
            status = binding.get("status")
            kind = binding.get("anchor_kind")
            if status == "anchored":
                if kind not in {
                    "field_anchor",
                    "item_anchor",
                    "recovered_chunk_anchor",
                    "derived_claim_anchor",
                } or not anchors:
                    raise EvidenceRejected("PROJECTION_ANCHORED_BINDING_INVALID")
            elif status == "unanchored":
                if kind is not None or anchors:
                    raise EvidenceRejected("PROJECTION_UNANCHORED_BINDING_INVALID")
            else:
                raise EvidenceRejected("PROJECTION_BINDING_STATUS_INVALID")
    return {str(row["concept"]): row for row in rows}


def make_anchor_resolution_receipt(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the Step-2 receipt as a distinct hash-bound audit object."""

    _projection_map(projection)
    records = deepcopy(projection["concepts"])
    anchored = sum(
        binding.get("status") == "anchored"
        for row in records
        for binding in row["bindings"]
    )
    unanchored = sum(
        binding.get("status") == "unanchored"
        for row in records
        for binding in row["bindings"]
    )
    not_applicable = sum(
        row.get("source_coverage") == "not_applicable" for row in records
    )
    receipt_id = "arr_" + sha256_bytes(
        (
            "RESEARCH_OPPORTUNITIES_ANCHOR_RESOLUTION_RECEIPT_V1\n"
            + projection["content_hash"]["value"]
        ).encode("utf-8")
    )[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_ANCHOR_RESOLUTION_RECEIPT_V1",
            "receipt_id": receipt_id,
            "projection_card_id": projection["card_id"],
            "projection_hash": _hash_copy(projection["content_hash"]),
            "source_snapshot_ref": projection["source_snapshot_ref"],
            "source_snapshot_hash": _hash_copy(projection["source_snapshot_hash"]),
            "recovery_scope": "FROZEN_CHUNKS_EXACT_MATCH_ONLY",
            "concept_count": 8,
            "anchored_binding_count": anchored,
            "unanchored_binding_count": unanchored,
            "not_applicable_concept_count": not_applicable,
            "resolution_records": records,
            "coverage_status": projection["coverage_status"],
            "coverage_limited_facets": deepcopy(
                projection["coverage_limited_facets"]
            ),
            "source_card_mutated": False,
            "claim_type_persisted_to_card": False,
        }
    )


def validate_anchor_resolution_receipt(
    receipt: Mapping[str, Any], projection: Mapping[str, Any]
) -> None:
    _require_content_hash(receipt, "ANCHOR_RESOLUTION_RECEIPT_HASH_MISMATCH")
    if set(receipt) != ANCHOR_RESOLUTION_RECEIPT_KEYS:
        raise EvidenceRejected("ANCHOR_RESOLUTION_RECEIPT_EXACT_KEYS_MISMATCH")
    expected = make_anchor_resolution_receipt(projection)
    if dict(receipt) != expected:
        raise EvidenceRejected("ANCHOR_RESOLUTION_RECEIPT_EXPECTED_OBSERVED_MISMATCH")


def _concept_status(first: Mapping[str, Any], second: Mapping[str, Any]) -> str:
    if first.get("source_coverage") not in {"fully_anchored", "not_applicable"}:
        return "missing"
    if second.get("source_coverage") not in {"fully_anchored", "not_applicable"}:
        return "missing"
    first_values = {normalize_text(str(value)).casefold() for value in first.get("values", [])}
    second_values = {normalize_text(str(value)).casefold() for value in second.get("values", [])}
    if first.get("source_coverage") == second.get("source_coverage") == "not_applicable":
        return "not_applicable"
    return "equivalent" if first_values == second_values else "different"


def assess_comparability(
    first_projection: Mapping[str, Any],
    second_projection: Mapping[str, Any],
    *,
    first_anchor_receipt: Mapping[str, Any],
    second_anchor_receipt: Mapping[str, Any],
    pair_ledger: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    _require_content_hash(pair_ledger, "PAIR_LEDGER_HASH_MISMATCH")
    _require_content_hash(source_snapshot, "SOURCE_SNAPSHOT_HASH_MISMATCH")
    if set(source_snapshot) != SOURCE_SNAPSHOT_KEYS:
        raise EvidenceRejected("SOURCE_SNAPSHOT_EXACT_KEYS_MISMATCH")
    if set(pair_ledger) != PAIR_LEDGER_KEYS:
        raise EvidenceRejected("PAIR_LEDGER_EXACT_KEYS_MISMATCH")
    if (
        pair_ledger.get("schema_version") != "RESEARCH_OPPORTUNITIES_PAIR_LEDGER_V1"
        or pair_ledger.get("source_snapshot_ref") != source_snapshot.get("snapshot_id")
        or pair_ledger.get("source_snapshot_hash") != source_snapshot.get("content_hash")
        or pair_ledger.get("configuration_policy_hash") != source_snapshot.get("configuration_policy_hash")
    ):
        raise EvidenceRejected("PAIR_LEDGER_SOURCE_MISMATCH")
    expected_ledger = make_pair_ledger(
        source_snapshot, cluster_id=str(pair_ledger.get("cluster_id"))
    )
    if dict(pair_ledger) != expected_ledger:
        raise EvidenceRejected("PAIR_LEDGER_EXACT_REPLAY_MISMATCH")
    projections = sorted(
        (first_projection, second_projection), key=lambda item: str(item.get("card_id"))
    )
    first_projection, second_projection = projections
    for projection in projections:
        if (
            projection.get("source_snapshot_ref") != source_snapshot.get("snapshot_id")
            or projection.get("source_snapshot_hash") != source_snapshot.get("content_hash")
        ):
            raise EvidenceRejected("PROJECTION_SOURCE_SNAPSHOT_MISMATCH")
        member = _member(source_snapshot, str(projection.get("card_id")))
        if (
            projection.get("paper_id") != member.get("paper_id")
            or projection.get("card_content_hash") != member.get("card_content_hash")
            or projection.get("chunks_content_hash") != member.get("chunks_content_hash")
        ):
            raise EvidenceRejected("PROJECTION_SOURCE_MEMBER_BINDING_MISMATCH")
    first_map = _projection_map(first_projection)
    second_map = _projection_map(second_projection)
    pair = [str(first_projection["card_id"]), str(second_projection["card_id"])]
    receipts = {
        str(receipt.get("projection_card_id")): receipt
        for receipt in (first_anchor_receipt, second_anchor_receipt)
    }
    if set(receipts) != set(pair):
        raise EvidenceRejected("ANCHOR_RESOLUTION_RECEIPT_CARD_EXACT_SET_MISMATCH")
    for projection in (first_projection, second_projection):
        validate_anchor_resolution_receipt(
            receipts[str(projection["card_id"])], projection
        )
    if not any(item.get("card_ids") == pair for item in pair_ledger.get("pairs", [])):
        raise EvidenceRejected("PAIR_NOT_IN_FROZEN_LEDGER")
    rows: list[dict[str, Any]] = []
    for concept in CONCEPT_ORDER:
        first_row = first_map[concept]
        second_row = second_map[concept]
        rows.append(
            {
                "concept": concept,
                "expected_reviewed": True,
                "observed_status": _concept_status(first_row, second_row),
                "first_card_id": pair[0],
                "second_card_id": pair[1],
                "first_values": deepcopy(first_row.get("values", [])),
                "second_values": deepcopy(second_row.get("values", [])),
                "first_source_coverage": first_row.get("source_coverage"),
                "second_source_coverage": second_row.get("source_coverage"),
            }
        )
    status = {row["concept"]: row["observed_status"] for row in rows}
    limited = sorted(row["concept"] for row in rows if row["observed_status"] == "missing")
    language_mismatch = first_projection.get("language") != second_projection.get("language")
    conditional = sorted(
        concept for concept in CONDITIONAL_CONCEPTS if status[concept] == "different"
    )
    if language_mismatch or limited:
        overall = "coverage_limited"
    elif status["research_object"] == "different":
        overall = "not_comparable"
    elif status["metric"] == "different" or status["claim_type"] == "different":
        overall = "conceptually_related_not_direct"
    elif conditional:
        overall = "comparable_with_conditions"
    else:
        overall = "directly_comparable"
    assessment_id = "cmp_" + sha256_bytes(
        (source_snapshot["content_hash"]["value"] + "\n" + "\n".join(pair)).encode("utf-8")
    )[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_COMPARABILITY_ASSESSMENT_V2",
            "assessment_id": assessment_id,
            "source_snapshot_ref": source_snapshot["snapshot_id"],
            "source_snapshot_hash": _hash_copy(source_snapshot["content_hash"]),
            "configuration_policy_hash": _hash_copy(source_snapshot["configuration_policy_hash"]),
            "pair_ledger_ref": pair_ledger["ledger_id"],
            "pair_ledger_hash": _hash_copy(pair_ledger["content_hash"]),
            "pair_card_ids": pair,
            "projection_hashes": {
                pair[0]: _hash_copy(first_projection["content_hash"]),
                pair[1]: _hash_copy(second_projection["content_hash"]),
            },
            "anchor_resolution_receipt_hashes": {
                card_id: _hash_copy(receipts[card_id]["content_hash"])
                for card_id in pair
            },
            "concept_count": 8,
            "concept_results": rows,
            "overall": overall,
            "conditional_difference_facets": conditional,
            "coverage_limited_facets": limited,
            "language_mismatch": language_mismatch,
            "weighted_similarity_used": False,
            "provider_score_used": False,
            "derived_penalty_used": False,
        }
    )


def make_ignore_decision(
    *,
    decision_id: str,
    decided_by: str,
    decided_at: str,
    authority_ref: str,
    authorized_human_authorities: Mapping[str, str],
    ignore_authority_registry_hash: Mapping[str, Any],
    evidence_refs: Sequence[str],
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    _require_content_hash(assessment, "ASSESSMENT_HASH_MISMATCH")
    registry = _validate_ignore_authority_registry(
        authorized_human_authorities, ignore_authority_registry_hash
    )
    canonical_decided_by = _required_text(decided_by, "IGNORE_DECIDED_BY_REQUIRED")
    canonical_authority_ref = _required_text(authority_ref, "IGNORE_AUTHORITY_REF_REQUIRED")
    if registry.get(canonical_authority_ref) != canonical_decided_by:
        raise EvidenceRejected("IGNORE_AUTHORITY_BINDING_MISMATCH")
    refs = sorted(
        {_required_text(item, "IGNORE_EVIDENCE_REF_REQUIRED") for item in evidence_refs},
        key=lambda item: item.encode("utf-8"),
    )
    if not refs:
        raise EvidenceRejected("IGNORE_EVIDENCE_REFS_REQUIRED")
    if assessment.get("assessment_id") not in refs:
        raise EvidenceRejected("IGNORE_ASSESSMENT_REF_NOT_IN_EVIDENCE")
    if canonical_authority_ref not in refs:
        raise EvidenceRejected("IGNORE_AUTHORITY_REF_NOT_IN_EVIDENCE")
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_TENSION_IGNORE_DECISION_V1",
            "decision_id": _required_text(decision_id, "IGNORE_DECISION_ID_REQUIRED"),
            "decided_by": canonical_decided_by,
            "decided_at": _timezone_aware_iso_datetime(
                decided_at, "IGNORE_DECIDED_AT_INVALID"
            ),
            "decision": "IGNORE",
            "authority_kind": IGNORE_AUTHORITY_KIND,
            "authority_ref": canonical_authority_ref,
            "authority_registry_hash": _hash_copy(ignore_authority_registry_hash),
            "evidence_refs": refs,
            "pair_card_ids": deepcopy(assessment["pair_card_ids"]),
            "assessment_ref": assessment["assessment_id"],
            "assessment_hash": _hash_copy(assessment["content_hash"]),
        }
    )


def validate_ignore_decision(
    decision: Mapping[str, Any],
    assessment: Mapping[str, Any],
    *,
    authorized_human_authorities: Mapping[str, str],
    ignore_authority_registry_hash: Mapping[str, Any],
) -> None:
    _require_content_hash(assessment, "ASSESSMENT_HASH_MISMATCH")
    _require_content_hash(decision, "IGNORE_DECISION_HASH_MISMATCH")
    registry = _validate_ignore_authority_registry(
        authorized_human_authorities, ignore_authority_registry_hash
    )
    expected_keys = {
        "schema_version",
        "decision_id",
        "decided_by",
        "decided_at",
        "decision",
        "authority_kind",
        "authority_ref",
        "authority_registry_hash",
        "evidence_refs",
        "pair_card_ids",
        "assessment_ref",
        "assessment_hash",
        "content_hash",
    }
    if set(decision) != expected_keys or decision.get("schema_version") != "RESEARCH_OPPORTUNITIES_TENSION_IGNORE_DECISION_V1":
        raise EvidenceRejected("IGNORE_DECISION_CONTRACT_INVALID")
    if decision.get("decision") != "IGNORE":
        raise EvidenceRejected("IGNORE_DECISION_VALUE_INVALID")
    for field, code in (
        ("decision_id", "IGNORE_DECISION_ID_REQUIRED"),
        ("decided_by", "IGNORE_DECIDED_BY_REQUIRED"),
        ("authority_ref", "IGNORE_AUTHORITY_REF_REQUIRED"),
    ):
        value = decision.get(field)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise EvidenceRejected(code)
    if decision.get("authority_kind") != IGNORE_AUTHORITY_KIND:
        raise EvidenceRejected("IGNORE_AUTHORITY_KIND_INVALID")
    if decision.get("authority_registry_hash") != ignore_authority_registry_hash:
        raise EvidenceRejected("IGNORE_AUTHORITY_REGISTRY_HASH_MISMATCH")
    if registry.get(str(decision["authority_ref"])) != decision.get("decided_by"):
        raise EvidenceRejected("IGNORE_AUTHORITY_BINDING_MISMATCH")
    refs = decision.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise EvidenceRejected("IGNORE_EVIDENCE_REFS_REQUIRED")
    if any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in refs
    ):
        raise EvidenceRejected("IGNORE_EVIDENCE_REF_INVALID")
    if refs != sorted(refs, key=lambda item: item.encode("utf-8")) or len(refs) != len(set(refs)):
        raise EvidenceRejected("IGNORE_EVIDENCE_REFS_NOT_UTF8_SORTED_UNIQUE")
    _timezone_aware_iso_datetime(decision.get("decided_at"), "IGNORE_DECIDED_AT_INVALID")
    if decision.get("pair_card_ids") != assessment.get("pair_card_ids"):
        raise EvidenceRejected("IGNORE_PAIR_MISMATCH")
    if decision.get("assessment_ref") != assessment.get("assessment_id"):
        raise EvidenceRejected("IGNORE_ASSESSMENT_REF_MISMATCH")
    if decision.get("assessment_hash") != assessment.get("content_hash"):
        raise EvidenceRejected("IGNORE_ASSESSMENT_HASH_MISMATCH")
    if decision.get("assessment_ref") not in refs:
        raise EvidenceRejected("IGNORE_ASSESSMENT_REF_NOT_IN_EVIDENCE")
    if decision.get("authority_ref") not in refs:
        raise EvidenceRejected("IGNORE_AUTHORITY_REF_NOT_IN_EVIDENCE")


def classify_tension(
    first_projection: Mapping[str, Any],
    second_projection: Mapping[str, Any],
    *,
    first_anchor_receipt: Mapping[str, Any],
    second_anchor_receipt: Mapping[str, Any],
    assessment: Mapping[str, Any],
    pair_ledger: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    ignore_decision: Mapping[str, Any] | None = None,
    authorized_human_authorities: Mapping[str, str] | None = None,
    ignore_authority_registry_hash: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _require_content_hash(assessment, "ASSESSMENT_HASH_MISMATCH")
    _require_content_hash(pair_ledger, "PAIR_LEDGER_HASH_MISMATCH")
    pair = sorted((str(first_projection.get("card_id")), str(second_projection.get("card_id"))))
    projections = {str(item["card_id"]): item for item in (first_projection, second_projection)}
    for projection in projections.values():
        _projection_map(projection)
    if assessment.get("pair_card_ids") != pair:
        raise EvidenceRejected("TENSION_ASSESSMENT_PAIR_MISMATCH")
    if assessment.get("pair_ledger_ref") != pair_ledger.get("ledger_id") or assessment.get("pair_ledger_hash") != pair_ledger.get("content_hash"):
        raise EvidenceRejected("TENSION_PAIR_LEDGER_MISMATCH")
    expected_projection_hashes = {
        card_id: projection["content_hash"] for card_id, projection in sorted(projections.items())
    }
    if assessment.get("projection_hashes") != expected_projection_hashes:
        raise EvidenceRejected("TENSION_PROJECTION_HASH_MISMATCH")
    expected_assessment = assess_comparability(
        first_projection,
        second_projection,
        first_anchor_receipt=first_anchor_receipt,
        second_anchor_receipt=second_anchor_receipt,
        pair_ledger=pair_ledger,
        source_snapshot=source_snapshot,
    )
    if dict(assessment) != expected_assessment:
        raise EvidenceRejected("TENSION_ASSESSMENT_EXACT_REPLAY_MISMATCH")
    if ignore_decision is not None:
        if authorized_human_authorities is None or ignore_authority_registry_hash is None:
            raise EvidenceRejected("IGNORE_AUTHORITY_REGISTRY_REQUIRED")
        validate_ignore_decision(
            ignore_decision,
            assessment,
            authorized_human_authorities=authorized_human_authorities,
            ignore_authority_registry_hash=ignore_authority_registry_hash,
        )
        classification = "D"
        rationale = "explicit_structured_human_ignore_decision"
    elif assessment.get("overall") in {"coverage_limited", "not_comparable"}:
        classification = "coverage_limited"
        rationale = str(assessment.get("overall"))
    elif assessment.get("overall") == "conceptually_related_not_direct":
        classification = "C"
        rationale = "metric_or_claim_type_difference"
    elif assessment.get("overall") == "comparable_with_conditions":
        classification = "B"
        rationale = "condition_method_boundary_or_scale_difference"
    elif assessment.get("overall") == "directly_comparable":
        directions = {
            card_id: projection.get("conclusion_direction")
            for card_id, projection in projections.items()
        }
        if None not in directions.values() and len(set(directions.values())) == 2:
            classification = "A"
            rationale = "directly_comparable_different_anchored_directions"
        else:
            classification = "C"
            rationale = "no_directional_conflict"
    else:
        raise EvidenceRejected("ASSESSMENT_OVERALL_INVALID")
    tension_id = "ten_" + sha256_bytes(
        (source_snapshot["content_hash"]["value"] + "\n" + assessment["assessment_id"]).encode("utf-8")
    )[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_TENSION_EVIDENCE_V2",
            "tension_id": tension_id,
            "source_snapshot_ref": source_snapshot["snapshot_id"],
            "source_snapshot_hash": _hash_copy(source_snapshot["content_hash"]),
            "configuration_policy_hash": _hash_copy(source_snapshot["configuration_policy_hash"]),
            "pair_card_ids": pair,
            "pair_ledger_ref": pair_ledger["ledger_id"],
            "pair_ledger_hash": _hash_copy(pair_ledger["content_hash"]),
            "assessment_ref": assessment["assessment_id"],
            "assessment_hash": _hash_copy(assessment["content_hash"]),
            "projection_hashes": expected_projection_hashes,
            "classification": classification,
            "rationale_code": rationale,
            "conclusion_direction_by_card": {
                card_id: projections[card_id].get("conclusion_direction") for card_id in pair
            },
            "human_ignore_decision_ref": ignore_decision.get("decision_id") if ignore_decision else None,
            "human_ignore_decision_hash": _hash_copy(ignore_decision["content_hash"]) if ignore_decision else None,
            "scientific_truth_judgment": "NOT_ASSESSED",
            "winner_selected": False,
        }
    )


def build_opportunity_candidate(
    *,
    opportunity_kind: str,
    source_snapshot: Mapping[str, Any],
    gaps: Sequence[Mapping[str, Any]] = (),
    tensions: Sequence[Mapping[str, Any]] = (),
    anchor_resolution_receipts: Sequence[Mapping[str, Any]] = (),
    assessments: Sequence[Mapping[str, Any]] = (),
    pair_ledgers: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if opportunity_kind not in {"gap", "tension", "hybrid"}:
        raise CandidateRejected("OPPORTUNITY_KIND_INVALID")
    _require_content_hash(source_snapshot, "SOURCE_SNAPSHOT_HASH_MISMATCH")
    if (
        set(source_snapshot) != SOURCE_SNAPSHOT_KEYS
        or source_snapshot.get("schema_version") != "RESEARCH_OPPORTUNITIES_OPPORTUNITY_SOURCE_SNAPSHOT_V2"
        or source_snapshot.get("source_profile") != SOURCE_PROFILE
        or source_snapshot.get("source_mode") != "ACTIVE_ONLY_BOUNDED_CLUSTER_TOPK"
        or source_snapshot.get("real_card_reads") != 0
        or source_snapshot.get("public_safe_synthetic_only") is not True
    ):
        raise CandidateRejected("SOURCE_SNAPSHOT_ENVELOPE_INVALID")
    gap_by_id = {str(item.get("evidence_id")): item for item in gaps}
    tension_by_id = {str(item.get("tension_id")): item for item in tensions}
    receipt_by_id = {
        str(item.get("receipt_id")): item for item in anchor_resolution_receipts
    }
    assessment_by_id = {str(item.get("assessment_id")): item for item in assessments}
    ledger_by_id = {str(item.get("ledger_id")): item for item in pair_ledgers}
    if (
        len(gap_by_id) != len(gaps)
        or len(tension_by_id) != len(tensions)
        or len(receipt_by_id) != len(anchor_resolution_receipts)
        or len(assessment_by_id) != len(assessments)
        or len(ledger_by_id) != len(pair_ledgers)
    ):
        raise CandidateRejected("DUPLICATE_EVIDENCE_ID")
    if opportunity_kind == "gap" and (
        not gaps or tensions or anchor_resolution_receipts or assessments or pair_ledgers
    ):
        raise CandidateRejected("GAP_KIND_REQUIRES_GAP_ONLY")
    if opportunity_kind == "tension" and (
        not tensions or gaps or not anchor_resolution_receipts
    ):
        raise CandidateRejected("TENSION_KIND_REQUIRES_TENSION_ONLY")
    if opportunity_kind == "hybrid" and (
        not gaps or not tensions or not anchor_resolution_receipts
    ):
        raise CandidateRejected("HYBRID_KIND_REQUIRES_GAP_AND_TENSION")
    snapshot_hash = source_snapshot["content_hash"]
    for gap in gaps:
        _require_content_hash(gap, "GAP_EVIDENCE_HASH_MISMATCH")
        if (
            set(gap) != GAP_EVIDENCE_KEYS
            or gap.get("schema_version") != "RESEARCH_OPPORTUNITIES_AUTHOR_STATED_GAP_EVIDENCE_V2"
            or gap.get("source_snapshot_ref") != source_snapshot.get("snapshot_id")
            or gap.get("source_snapshot_hash") != snapshot_hash
        ):
            raise CandidateRejected("GAP_EVIDENCE_SOURCE_OR_SCHEMA_MISMATCH")
        try:
            member = _member(source_snapshot, str(gap.get("card_id")))
        except EvidenceRejected as exc:
            raise CandidateRejected("GAP_EVIDENCE_CARD_NOT_IN_SOURCE") from exc
        if (
            gap.get("paper_id") != member.get("paper_id")
            or gap.get("card_content_hash") != member.get("card_content_hash")
            or gap.get("chunks_content_hash") != member.get("chunks_content_hash")
            or gap.get("source_scope") != "FROZEN_ACTIVE_CARD_AND_CHUNKS_ONLY"
            or gap.get("mention_count_scope") != "FROZEN_SOURCE_SNAPSHOT_ONLY"
            or gap.get("frozen_corpus_mention_count") != 1
            or gap.get("possibly_addressed_in_frozen_corpus") is not False
            or gap.get("scientific_judgment_status") != "NOT_ASSESSED"
        ):
            raise CandidateRejected("GAP_EVIDENCE_BINDING_OR_STATE_MISMATCH")
        expected_gap_id = "gap_" + sha256_bytes(
            (
                snapshot_hash["value"]
                + "\n"
                + str(gap["card_id"])
                + "\n"
                + str(gap["author_statement_path"])
                + "\n"
                + normalize_text(str(gap["quote"]))
            ).encode("utf-8")
        )[:24].lower()
        if gap.get("evidence_id") != expected_gap_id:
            raise CandidateRejected("GAP_EVIDENCE_ID_MISMATCH")

    receipts_by_card: dict[str, Mapping[str, Any]] = {}
    for receipt in anchor_resolution_receipts:
        _require_content_hash(receipt, "ANCHOR_RESOLUTION_RECEIPT_HASH_MISMATCH")
        card_id = receipt.get("projection_card_id")
        if (
            set(receipt) != ANCHOR_RESOLUTION_RECEIPT_KEYS
            or receipt.get("schema_version")
            != "RESEARCH_OPPORTUNITIES_ANCHOR_RESOLUTION_RECEIPT_V1"
            or not isinstance(card_id, str)
            or not card_id
            or card_id in receipts_by_card
            or receipt.get("source_snapshot_ref")
            != source_snapshot.get("snapshot_id")
            or receipt.get("source_snapshot_hash") != snapshot_hash
            or receipt.get("recovery_scope")
            != "FROZEN_CHUNKS_EXACT_MATCH_ONLY"
            or receipt.get("concept_count") != 8
            or receipt.get("source_card_mutated") is not False
            or receipt.get("claim_type_persisted_to_card") is not False
            or not typed_hash_is_valid(receipt.get("projection_hash"))
        ):
            raise CandidateRejected("ANCHOR_RESOLUTION_RECEIPT_CONTRACT_MISMATCH")
        expected_receipt_id = "arr_" + sha256_bytes(
            (
                "RESEARCH_OPPORTUNITIES_ANCHOR_RESOLUTION_RECEIPT_V1\n"
                + receipt["projection_hash"]["value"]
            ).encode("utf-8")
        )[:24].lower()
        if receipt.get("receipt_id") != expected_receipt_id:
            raise CandidateRejected("ANCHOR_RESOLUTION_RECEIPT_ID_MISMATCH")
        receipts_by_card[card_id] = receipt

    for ledger in pair_ledgers:
        _require_content_hash(ledger, "PAIR_LEDGER_HASH_MISMATCH")
        if (
            set(ledger) != PAIR_LEDGER_KEYS
            or ledger.get("schema_version") != "RESEARCH_OPPORTUNITIES_PAIR_LEDGER_V1"
            or ledger.get("source_snapshot_ref") != source_snapshot.get("snapshot_id")
            or ledger.get("source_snapshot_hash") != snapshot_hash
            or ledger.get("configuration_policy_hash") != source_snapshot.get("configuration_policy_hash")
        ):
            raise CandidateRejected("PAIR_LEDGER_SOURCE_OR_SCHEMA_MISMATCH")
        try:
            expected_ledger = make_pair_ledger(
                source_snapshot, cluster_id=str(ledger.get("cluster_id"))
            )
        except (ContractRejected, EvidenceRejected) as exc:
            raise CandidateRejected("PAIR_LEDGER_REPLAY_INPUT_INVALID") from exc
        if dict(ledger) != expected_ledger:
            raise CandidateRejected("PAIR_LEDGER_EXACT_REPLAY_MISMATCH")

    for assessment in assessments:
        _require_content_hash(assessment, "ASSESSMENT_HASH_MISMATCH")
        pair = assessment.get("pair_card_ids")
        projection_hashes = assessment.get("projection_hashes")
        receipt_hashes = assessment.get("anchor_resolution_receipt_hashes")
        if (
            set(assessment) != ASSESSMENT_KEYS
            or assessment.get("schema_version") != "RESEARCH_OPPORTUNITIES_COMPARABILITY_ASSESSMENT_V2"
            or assessment.get("source_snapshot_ref") != source_snapshot.get("snapshot_id")
            or assessment.get("source_snapshot_hash") != snapshot_hash
            or assessment.get("configuration_policy_hash") != source_snapshot.get("configuration_policy_hash")
            or not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(item, str) and item.strip() for item in pair)
            or pair != sorted(set(pair))
            or not isinstance(projection_hashes, dict)
            or set(projection_hashes) != set(pair)
            or not all(typed_hash_is_valid(value) for value in projection_hashes.values())
            or not isinstance(receipt_hashes, dict)
            or set(receipt_hashes) != set(pair)
            or not all(typed_hash_is_valid(value) for value in receipt_hashes.values())
            or assessment.get("concept_count") != 8
            or not isinstance(assessment.get("concept_results"), list)
            or len(assessment["concept_results"]) != 8
            or [row.get("concept") for row in assessment["concept_results"] if isinstance(row, dict)]
            != list(CONCEPT_ORDER)
            or assessment.get("weighted_similarity_used") is not False
            or assessment.get("provider_score_used") is not False
            or assessment.get("derived_penalty_used") is not False
        ):
            raise CandidateRejected("ASSESSMENT_CONTRACT_OR_SOURCE_MISMATCH")
        ledger = ledger_by_id.get(str(assessment.get("pair_ledger_ref")))
        if (
            ledger is None
            or assessment.get("pair_ledger_hash") != ledger.get("content_hash")
            or not any(item.get("card_ids") == pair for item in ledger.get("pairs", []))
        ):
            raise CandidateRejected("ASSESSMENT_PAIR_LEDGER_LINK_MISMATCH")
        expected_assessment_id = "cmp_" + sha256_bytes(
            (snapshot_hash["value"] + "\n" + "\n".join(pair)).encode("utf-8")
        )[:24].lower()
        if assessment.get("assessment_id") != expected_assessment_id:
            raise CandidateRejected("ASSESSMENT_ID_MISMATCH")
        for card_id in pair:
            receipt = receipts_by_card.get(card_id)
            if (
                receipt is None
                or receipt.get("projection_hash") != projection_hashes[card_id]
                or receipt.get("content_hash") != receipt_hashes[card_id]
            ):
                raise CandidateRejected("ASSESSMENT_ANCHOR_RECEIPT_LINK_MISMATCH")

    referenced_assessments: set[str] = set()
    referenced_ledgers: set[str] = set()
    referenced_receipt_cards: set[str] = set()
    for tension in tensions:
        _require_content_hash(tension, "TENSION_EVIDENCE_HASH_MISMATCH")
        pair = tension.get("pair_card_ids")
        tension_projection_hashes = tension.get("projection_hashes")
        conclusion_directions = tension.get("conclusion_direction_by_card")
        if (
            set(tension) != TENSION_EVIDENCE_KEYS
            or tension.get("schema_version") != "RESEARCH_OPPORTUNITIES_TENSION_EVIDENCE_V2"
            or tension.get("source_snapshot_ref") != source_snapshot.get("snapshot_id")
            or tension.get("source_snapshot_hash") != snapshot_hash
            or tension.get("configuration_policy_hash") != source_snapshot.get("configuration_policy_hash")
            or not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(item, str) and item.strip() for item in pair)
            or pair != sorted(set(pair))
            or not isinstance(tension_projection_hashes, dict)
            or set(tension_projection_hashes) != set(pair)
            or not all(typed_hash_is_valid(value) for value in tension_projection_hashes.values())
            or not isinstance(conclusion_directions, dict)
            or set(conclusion_directions) != set(pair)
            or tension.get("scientific_truth_judgment") != "NOT_ASSESSED"
            or tension.get("winner_selected") is not False
        ):
            raise CandidateRejected("TENSION_EVIDENCE_SOURCE_OR_SCHEMA_MISMATCH")
        assessment_id = str(tension.get("assessment_ref"))
        assessment = assessment_by_id.get(assessment_id)
        if (
            not assessment
            or tension.get("assessment_hash") != assessment.get("content_hash")
            or tension.get("pair_card_ids") != assessment.get("pair_card_ids")
            or tension.get("projection_hashes") != assessment.get("projection_hashes")
        ):
            raise CandidateRejected("TENSION_ASSESSMENT_LINK_MISMATCH")
        referenced_assessments.add(assessment_id)
        referenced_receipt_cards.update(str(item) for item in pair)
        ledger_id = str(tension.get("pair_ledger_ref"))
        ledger = ledger_by_id.get(ledger_id)
        if (
            not ledger
            or tension.get("pair_ledger_hash") != ledger.get("content_hash")
            or assessment.get("pair_ledger_ref") != ledger_id
            or assessment.get("pair_ledger_hash") != ledger.get("content_hash")
            or not any(item.get("card_ids") == pair for item in ledger.get("pairs", []))
        ):
            raise CandidateRejected("TENSION_PAIR_LEDGER_LINK_MISMATCH")
        referenced_ledgers.add(ledger_id)
        ignore_ref = tension.get("human_ignore_decision_ref")
        ignore_hash = tension.get("human_ignore_decision_hash")
        if (ignore_ref is None) != (ignore_hash is None):
            raise CandidateRejected("TENSION_IGNORE_DECISION_PARTIAL_BINDING")
        if (tension.get("classification") == "D") != (ignore_ref is not None):
            raise CandidateRejected("TENSION_IGNORE_CLASSIFICATION_MISMATCH")
        if ignore_hash is not None and not typed_hash_is_valid(ignore_hash):
            raise CandidateRejected("TENSION_IGNORE_DECISION_HASH_INVALID")
        expected_tension_id = "ten_" + sha256_bytes(
            (snapshot_hash["value"] + "\n" + assessment_id).encode("utf-8")
        )[:24].lower()
        if tension.get("tension_id") != expected_tension_id:
            raise CandidateRejected("TENSION_ID_MISMATCH")
    if (
        referenced_assessments != set(assessment_by_id)
        or referenced_ledgers != set(ledger_by_id)
        or referenced_receipt_cards != set(receipts_by_card)
    ):
        raise CandidateRejected("UNREFERENCED_OR_MISSING_COMPARISON_EVIDENCE")
    gap_refs = sorted(gap_by_id)
    tension_refs = sorted(tension_by_id)
    receipt_refs = sorted(receipt_by_id)
    assessment_refs = sorted(assessment_by_id)
    ledger_refs = sorted(ledger_by_id)
    identity_hashes = sorted(
        item["content_hash"]["value"]
        for item in (
            *gaps,
            *tensions,
            *anchor_resolution_receipts,
            *assessments,
            *pair_ledgers,
        )
    )
    object_id = "opp_" + sha256_bytes(
        (
            opportunity_kind
            + "\n"
            + snapshot_hash["value"]
            + "\n"
            + "\n".join(identity_hashes)
        ).encode("utf-8")
    )[:24].lower()
    coverage = "coverage_limited" if any(
        item.get("classification") == "coverage_limited" for item in tensions
    ) else "complete"
    parent_refs = sorted(
        {
            source_snapshot["snapshot_id"],
            *[str(item["card_id"]) for item in gaps],
            *[str(card_id) for item in tensions for card_id in item["pair_card_ids"]],
        }
    )
    payload = {
        "object_id": object_id,
        "schema_version": "3.1",
        "revision": 1,
        "producer": "RESEARCH_OPPORTUNITIES_RESEARCH_OPPORTUNITIES_OPPORTUNITY_BUILDER_SUCCESSOR003",
        "consumers": ["M06", "M08", "MODEL_EVALUATION"],
        "state": "candidate",
        "parent_refs": parent_refs,
        "provenance_refs": sorted(
            {
                source_snapshot["snapshot_id"],
                *gap_refs,
                *tension_refs,
                *receipt_refs,
                *assessment_refs,
                *ledger_refs,
            }
        ),
        "supersedes": None,
        "immutable_fields": [
            "object_id",
            "schema_version",
            "revision",
            "content_hash",
            "producer",
            "parent_refs",
            "provenance_refs",
            "source_snapshot_hash",
            "evidence_hashes",
        ],
        "failure_semantics": {
            "BLOCKED": "fail_closed",
            "ERROR": "quarantined",
            "FAIL": "fail_closed",
            "NOT_ASSESSED": "no_eligibility",
        },
        "opportunity_kind": opportunity_kind,
        "source_snapshot_ref": source_snapshot["snapshot_id"],
        "source_snapshot_hash": _hash_copy(snapshot_hash),
        "trigger_scope_receipt_hash": _hash_copy(source_snapshot["trigger_scope_receipt_hash"]),
        "service_contracts_registry_hash": _hash_copy(source_snapshot["service_contracts_registry_hash"]),
        "configuration_policy_hash": _hash_copy(source_snapshot["configuration_policy_hash"]),
        "gap_refs": gap_refs,
        "tension_refs": tension_refs,
        "anchor_resolution_receipt_refs": receipt_refs,
        "comparison_assessment_refs": assessment_refs,
        "pair_ledger_refs": ledger_refs,
        "evidence_hashes": {
            **{key: _hash_copy(value["content_hash"]) for key, value in sorted(gap_by_id.items())},
            **{key: _hash_copy(value["content_hash"]) for key, value in sorted(tension_by_id.items())},
            **{key: _hash_copy(value["content_hash"]) for key, value in sorted(receipt_by_id.items())},
            **{key: _hash_copy(value["content_hash"]) for key, value in sorted(assessment_by_id.items())},
            **{key: _hash_copy(value["content_hash"]) for key, value in sorted(ledger_by_id.items())},
        },
        "coverage_status": coverage,
        "scientific_judgment_status": "NOT_ASSESSED",
        "score_snapshot": {
            "evidence_completeness": coverage,
            "gap_evidence_count": len(gaps),
            "tension_classification_counts": {
                label: sum(1 for item in tensions if item.get("classification") == label)
                for label in ("A", "B", "C", "D", "coverage_limited")
            },
            "research_value_total_score": None,
            "state_authority": False,
            "ordering_tie_break": object_id,
        },
        "human_decision_required": True,
        "human_decision": None,
        "automatic_state_transition": False,
    }
    return with_content_hash(payload)


def validate_opportunity_candidate(
    candidate: Mapping[str, Any],
    *,
    source_snapshot: Mapping[str, Any],
    gaps: Sequence[Mapping[str, Any]] = (),
    tensions: Sequence[Mapping[str, Any]] = (),
    anchor_resolution_receipts: Sequence[Mapping[str, Any]] = (),
    assessments: Sequence[Mapping[str, Any]] = (),
    pair_ledgers: Sequence[Mapping[str, Any]] = (),
) -> None:
    _require_content_hash(candidate, "CANDIDATE_HASH_MISMATCH")
    expected = build_opportunity_candidate(
        opportunity_kind=str(candidate.get("opportunity_kind")),
        source_snapshot=source_snapshot,
        gaps=gaps,
        tensions=tensions,
        anchor_resolution_receipts=anchor_resolution_receipts,
        assessments=assessments,
        pair_ledgers=pair_ledgers,
    )
    if dict(candidate) != expected:
        raise CandidateRejected("CANDIDATE_EXACT_CONTRACT_MISMATCH")


def evidence_set_hash(objects: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    mapping: dict[str, Any] = {}
    for item in objects:
        _require_content_hash(item, "EVIDENCE_SET_MEMBER_HASH_MISMATCH")
        object_id = next(
            (
                item.get(key)
                for key in (
                    "evidence_id",
                    "tension_id",
                    "receipt_id",
                    "assessment_id",
                    "ledger_id",
                )
                if item.get(key)
            ),
            None,
        )
        if not isinstance(object_id, str) or object_id in mapping:
            raise CandidateRejected("EVIDENCE_SET_ID_INVALID_OR_DUPLICATE")
        mapping[object_id] = item["content_hash"]
    return typed_payload_hash(mapping)


def finite_number_or_none(value: Any) -> bool:
    return value is None or (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )

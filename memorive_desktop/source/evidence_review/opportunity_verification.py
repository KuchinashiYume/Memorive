"""Role-separated semantic recomputation for RESEARCH-OPPORTUNITIES successor003.

This module deliberately imports only the shared canonical byte primitive.  It
does not import RESEARCH_OPPORTUNITIES source, projection, gap, comparison, tension, or candidate
producer functions.  This separation does not constitute an external review.
"""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from research_opportunities.canonical import (
    content_hash_matches,
    external_hash_is_valid,
    sha256_bytes,
    typed_payload_hash,
    with_content_hash,
)


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
CANDIDATE_KEYS = {
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "consumers",
    "state",
    "parent_refs",
    "provenance_refs",
    "supersedes",
    "immutable_fields",
    "failure_semantics",
    "opportunity_kind",
    "source_snapshot_ref",
    "source_snapshot_hash",
    "trigger_scope_receipt_hash",
    "service_contracts_registry_hash",
    "configuration_policy_hash",
    "gap_refs",
    "tension_refs",
    "anchor_resolution_receipt_refs",
    "comparison_assessment_refs",
    "pair_ledger_refs",
    "evidence_hashes",
    "coverage_status",
    "scientific_judgment_status",
    "score_snapshot",
    "human_decision_required",
    "human_decision",
    "automatic_state_transition",
}
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
TRIGGER_KEYS = {
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
LIMIT_KEYS = (
    "max_clusters",
    "max_cards_per_cluster",
    "max_pairs_per_cluster",
    "max_candidates_total",
)
ALLOWED_TRIGGER_TYPES = frozenset({"manual", "weekly", "knowledge_feedback_influx"})
SCORE_KEYS = {
    "evidence_completeness",
    "gap_evidence_count",
    "tension_classification_counts",
    "research_value_total_score",
    "state_authority",
    "ordering_tie_break",
}
CLASSIFICATION_COUNT_KEYS = {"A", "B", "C", "D", "coverage_limited"}
REF_LIST_FIELDS = (
    "gap_refs",
    "tension_refs",
    "anchor_resolution_receipt_refs",
    "comparison_assessment_refs",
    "pair_ledger_refs",
)
REQUIRED_CHECK_REGISTRY = {
    "source_snapshot_and_trigger_exact_contract": "required.source_snapshot_and_trigger_exact_contract",
    "ignore_authority_registry_binding": "required.ignore_authority_registry_binding",
    "candidate_exact_contract": "required.candidate_exact_contract",
    "candidate_mandatory_identity_and_policy": "required.candidate_mandatory_identity_and_policy",
    "candidate_ref_sets_sorted_unique": "required.candidate_ref_sets_sorted_unique",
    "projection_exact_set_and_recompute": "required.projection_exact_set_and_recompute",
    "anchor_resolution_receipt_exact_set_and_recompute": "required.anchor_resolution_receipt_exact_set_and_recompute",
    "evidence_exact_set_and_recompute": "required.evidence_exact_set_and_recompute",
    "assessment_exact_set_and_recompute": "required.assessment_exact_set_and_recompute",
    "pair_ledger_exact_set_and_recompute": "required.pair_ledger_exact_set_and_recompute",
    "dependency_closure": "required.dependency_closure",
    "candidate_state_and_score_authority": "required.candidate_state_and_score_authority",
}
REVIEW_CAPABILITY = {
    "role_separated_machine_verification": True,
    "research_opportunities_semantic_producer_imported": False,
    "independent_review": "NOT_ASSESSED",
    "third_party_review": "NOT_ASSESSED",
    "scientific_judgment": "NOT_ASSESSED",
}


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _contains(haystack: str, needle: str) -> bool:
    haystack = _norm(haystack).casefold()
    needle = _norm(needle).casefold()
    if not needle:
        return False
    if all(ord(char) < 128 for char in needle):
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack))
    return needle in haystack


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _norm(value)
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


def _unique(values: Sequence[str]) -> list[str]:
    return sorted({_norm(value) for value in values if _norm(value)})


def _field_anchors(card: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    root = card.get("source_anchor")
    by_field = root.get("by_field") if isinstance(root, dict) else None
    raw = by_field.get(field) if isinstance(by_field, dict) else None
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _anchor_valid(anchor: Mapping[str, Any], chunks: Mapping[str, str]) -> bool:
    chunk_id = anchor.get("chunk_id")
    quote = anchor.get("quote")
    return (
        isinstance(chunk_id, str)
        and isinstance(quote, str)
        and bool(quote.strip())
        and isinstance(chunks.get(chunk_id), str)
        and _contains(chunks[chunk_id], quote)
    )


def _bind(
    values: Sequence[str],
    chunks: Mapping[str, str],
    *,
    anchors: Sequence[Mapping[str, Any]] = (),
    recover: bool,
    kind: str = "field_anchor",
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in _unique(values):
        if value.casefold() == "not_applicable":
            result.append({"value": value, "status": "not_applicable", "anchor_kind": None, "anchors": []})
            continue
        matches = [
            {"chunk_id": str(anchor["chunk_id"]), "quote": str(anchor["quote"])}
            for anchor in anchors
            if _anchor_valid(anchor, chunks) and _contains(str(anchor["quote"]), value)
        ]
        observed_kind = kind if matches else None
        if not matches and recover:
            for chunk_id in sorted(chunks):
                if _contains(chunks[chunk_id], value):
                    matches = [{"chunk_id": chunk_id, "quote": value}]
                    observed_kind = "recovered_chunk_anchor"
                    break
        result.append(
            {
                "value": value,
                "status": "anchored" if matches else "unanchored",
                "anchor_kind": observed_kind,
                "anchors": matches,
            }
        )
    return result


def _row(name: str, bindings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    bindings = [deepcopy(dict(item)) for item in bindings]
    if not bindings:
        coverage, status = "missing", "missing"
    elif all(item["status"] == "not_applicable" for item in bindings):
        coverage, status = "not_applicable", "not_applicable"
    elif all(item["status"] == "anchored" for item in bindings):
        coverage, status = "fully_anchored", "present"
    else:
        coverage, status = "artifact_level_no_dedicated_anchor", "present"
    return {
        "concept": name,
        "values": [str(item["value"]) for item in bindings],
        "value_status": status,
        "source_coverage": coverage,
        "bindings": bindings,
    }


def _claim(texts: Sequence[str]) -> tuple[str | None, str | None]:
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


def _expected_projection(
    card: Mapping[str, Any], chunks: Mapping[str, str], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    context = card.get("comparison_context") if isinstance(card.get("comparison_context"), dict) else {}
    rows: list[dict[str, Any]] = []
    research = _values(card.get("research_object")) or _values(context.get("object"))
    rows.append(_row("research_object", _bind(research, chunks, anchors=_field_anchors(card, "research_object"), recover=False)))
    rows.append(_row("intervention_factor", _bind(_values(context.get("condition")), chunks, recover=True)))
    rows.append(_row("comparator", _bind(_values(context.get("groups")), chunks, recover=True)))
    rows.append(_row("method", _bind(_values(card.get("method")), chunks, anchors=_field_anchors(card, "method"), recover=False)))
    metrics: list[dict[str, Any]] = []
    for item in card.get("key_data", []) if isinstance(card.get("key_data"), list) else []:
        if isinstance(item, dict):
            metrics.extend(_bind(_values(item.get("metric")), chunks, anchors=[item], recover=False, kind="item_anchor"))
    rows.append(_row("metric", metrics))
    boundary = _bind(
        _values(card.get("boundary_conditions")),
        chunks,
        anchors=_field_anchors(card, "boundary_conditions"),
        recover=False,
    )
    boundary.extend(_bind(_values(context.get("time")) + _values(context.get("applicability")), chunks, recover=True))
    rows.append(_row("boundary_condition", boundary))
    rows.append(_row("scale_context", _bind(_values(context.get("scale")) + _values(context.get("scenario")), chunks, recover=True)))
    anchored: list[str] = []
    claim_anchors: list[dict[str, str]] = []
    for field in ("author_conclusion", "key_results"):
        for binding in _bind(_values(card.get(field)), chunks, anchors=_field_anchors(card, field), recover=False):
            if binding["status"] == "anchored":
                anchored.append(binding["value"])
                claim_anchors.extend(deepcopy(binding["anchors"]))
    language = str(card.get("language", "und"))
    claim_type, direction = _claim(anchored) if language == "en" and anchored else (None, None)
    claim_bindings = []
    if claim_type:
        claim_bindings.append({"value": claim_type, "status": "anchored", "anchor_kind": "derived_claim_anchor", "anchors": claim_anchors})
    rows.append(_row("claim_type", claim_bindings))
    limited = [row["concept"] for row in rows if row["source_coverage"] not in {"fully_anchored", "not_applicable"}]
    member = next(item for item in snapshot["members"] if item["card_id"] == card["card_id"])
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_COMPARISON_CONCEPT_PROJECTION_V2",
            "card_id": card["card_id"],
            "paper_id": card["paper_id"],
            "card_content_hash": deepcopy(card["content_hash"]),
            "chunks_content_hash": deepcopy(member["chunks_content_hash"]),
            "source_snapshot_ref": snapshot["snapshot_id"],
            "source_snapshot_hash": deepcopy(snapshot["content_hash"]),
            "language": language,
            "concept_count": 8,
            "concepts": rows,
            "conclusion_direction": direction,
            "claim_type_persisted_to_card": False,
            "coverage_status": "coverage_limited" if limited else "complete",
            "coverage_limited_facets": limited,
        }
    )


def _expected_anchor_resolution_receipt(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
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
            "projection_hash": deepcopy(projection["content_hash"]),
            "source_snapshot_ref": projection["source_snapshot_ref"],
            "source_snapshot_hash": deepcopy(projection["source_snapshot_hash"]),
            "recovery_scope": "FROZEN_CHUNKS_EXACT_MATCH_ONLY",
            "concept_count": 8,
            "anchored_binding_count": anchored,
            "unanchored_binding_count": unanchored,
            "not_applicable_concept_count": sum(
                row.get("source_coverage") == "not_applicable" for row in records
            ),
            "resolution_records": records,
            "coverage_status": projection["coverage_status"],
            "coverage_limited_facets": deepcopy(
                projection["coverage_limited_facets"]
            ),
            "source_card_mutated": False,
            "claim_type_persisted_to_card": False,
        }
    )


def _gap_type(block: str, quote: str) -> str:
    if block == "limitations":
        return "author_stated_limitation"
    folded = _norm(quote).casefold()
    if any(token in folded for token in ("research gap", "remains unknown", "not understood", "lack of evidence", "open question")):
        return "author_stated_field_gap"
    return "author_stated_future_work"


def _expected_gaps(card: Mapping[str, Any], chunks: Mapping[str, str], snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    member = next(item for item in snapshot["members"] if item["card_id"] == card["card_id"])
    root = card.get("author_limitations_outlook")
    if not isinstance(root, dict):
        return result
    for block in ("limitations", "outlook"):
        entries = root.get(block, [])
        if not isinstance(entries, list):
            continue
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            quote, chunk_id = item.get("quote"), item.get("chunk_id")
            if not isinstance(quote, str) or not quote.strip() or not isinstance(chunk_id, str):
                continue
            if not isinstance(chunks.get(chunk_id), str) or not _contains(chunks[chunk_id], quote):
                continue
            path = f"author_limitations_outlook.{block}[{index}]"
            evidence_id = "gap_" + sha256_bytes(
                (snapshot["content_hash"]["value"] + "\n" + card["card_id"] + "\n" + path + "\n" + _norm(quote)).encode("utf-8")
            )[:24].lower()
            result.append(
                with_content_hash(
                    {
                        "schema_version": "RESEARCH_OPPORTUNITIES_AUTHOR_STATED_GAP_EVIDENCE_V2",
                        "evidence_id": evidence_id,
                        "gap_type": _gap_type(block, quote),
                        "author_statement_path": path,
                        "quote": quote,
                        "chunk_id": chunk_id,
                        "card_id": card["card_id"],
                        "paper_id": card["paper_id"],
                        "card_content_hash": deepcopy(card["content_hash"]),
                        "chunks_content_hash": deepcopy(member["chunks_content_hash"]),
                        "source_snapshot_ref": snapshot["snapshot_id"],
                        "source_snapshot_hash": deepcopy(snapshot["content_hash"]),
                        "source_scope": "FROZEN_ACTIVE_CARD_AND_CHUNKS_ONLY",
                        "mention_count_scope": "FROZEN_SOURCE_SNAPSHOT_ONLY",
                        "frozen_corpus_mention_count": 1,
                        "possibly_addressed_in_frozen_corpus": False,
                        "scientific_judgment_status": "NOT_ASSESSED",
                    }
                )
            )
    return sorted(result, key=lambda item: item["evidence_id"])


def _expected_ledger(snapshot: Mapping[str, Any], cluster_id: str) -> dict[str, Any]:
    cluster = next(item for item in snapshot["clusters"] if item["cluster_id"] == cluster_id)
    ordered = list(cluster["ordered_card_ids"])
    maximum = snapshot["limits"]["max_pairs_per_cluster"]
    pairs: list[dict[str, Any]] = []
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if len(pairs) == maximum:
                break
            card_ids = sorted((first, second))
            pair_id = "pair_" + sha256_bytes(
                (snapshot["content_hash"]["value"] + "\n" + cluster_id + "\n" + "\n".join(card_ids)).encode("utf-8")
            )[:24].lower()
            pairs.append({"pair_id": pair_id, "card_ids": card_ids})
        if len(pairs) == maximum:
            break
    ledger_id = "plg_" + sha256_bytes((snapshot["content_hash"]["value"] + "\n" + cluster_id).encode("utf-8"))[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_PAIR_LEDGER_V1",
            "ledger_id": ledger_id,
            "source_snapshot_ref": snapshot["snapshot_id"],
            "source_snapshot_hash": deepcopy(snapshot["content_hash"]),
            "configuration_policy_hash": deepcopy(snapshot["configuration_policy_hash"]),
            "cluster_id": cluster_id,
            "ordered_topk_card_ids": ordered,
            "max_pairs_per_cluster": maximum,
            "pairs": pairs,
            "pair_count": len(pairs),
        }
    )


def _status(first: Mapping[str, Any], second: Mapping[str, Any]) -> str:
    if first.get("source_coverage") not in {"fully_anchored", "not_applicable"} or second.get("source_coverage") not in {"fully_anchored", "not_applicable"}:
        return "missing"
    if first.get("source_coverage") == second.get("source_coverage") == "not_applicable":
        return "not_applicable"
    first_values = {_norm(str(value)).casefold() for value in first.get("values", [])}
    second_values = {_norm(str(value)).casefold() for value in second.get("values", [])}
    return "equivalent" if first_values == second_values else "different"


def _expected_assessment(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    first_anchor_receipt: Mapping[str, Any],
    second_anchor_receipt: Mapping[str, Any],
    ledger: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    first, second = sorted((first, second), key=lambda item: str(item["card_id"]))
    pair = [str(first["card_id"]), str(second["card_id"])]
    receipts = {
        str(item["projection_card_id"]): item
        for item in (first_anchor_receipt, second_anchor_receipt)
    }
    first_map = {row["concept"]: row for row in first["concepts"]}
    second_map = {row["concept"]: row for row in second["concepts"]}
    rows = []
    for concept in CONCEPT_ORDER:
        first_row, second_row = first_map[concept], second_map[concept]
        rows.append(
            {
                "concept": concept,
                "expected_reviewed": True,
                "observed_status": _status(first_row, second_row),
                "first_card_id": pair[0],
                "second_card_id": pair[1],
                "first_values": deepcopy(first_row["values"]),
                "second_values": deepcopy(second_row["values"]),
                "first_source_coverage": first_row["source_coverage"],
                "second_source_coverage": second_row["source_coverage"],
            }
        )
    statuses = {row["concept"]: row["observed_status"] for row in rows}
    limited = sorted(row["concept"] for row in rows if row["observed_status"] == "missing")
    language_mismatch = first["language"] != second["language"]
    conditional = sorted(concept for concept in CONDITIONAL_CONCEPTS if statuses[concept] == "different")
    if language_mismatch or limited:
        overall = "coverage_limited"
    elif statuses["research_object"] == "different":
        overall = "not_comparable"
    elif statuses["metric"] == "different" or statuses["claim_type"] == "different":
        overall = "conceptually_related_not_direct"
    elif conditional:
        overall = "comparable_with_conditions"
    else:
        overall = "directly_comparable"
    assessment_id = "cmp_" + sha256_bytes((snapshot["content_hash"]["value"] + "\n" + "\n".join(pair)).encode("utf-8"))[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_COMPARABILITY_ASSESSMENT_V2",
            "assessment_id": assessment_id,
            "source_snapshot_ref": snapshot["snapshot_id"],
            "source_snapshot_hash": deepcopy(snapshot["content_hash"]),
            "configuration_policy_hash": deepcopy(snapshot["configuration_policy_hash"]),
            "pair_ledger_ref": ledger["ledger_id"],
            "pair_ledger_hash": deepcopy(ledger["content_hash"]),
            "pair_card_ids": pair,
            "projection_hashes": {pair[0]: deepcopy(first["content_hash"]), pair[1]: deepcopy(second["content_hash"])},
            "anchor_resolution_receipt_hashes": {
                card_id: deepcopy(receipts[card_id]["content_hash"])
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


def _authority_registry_shape_valid(registry: Any) -> bool:
    return isinstance(registry, Mapping) and all(
        isinstance(authority_ref, str)
        and bool(authority_ref.strip())
        and authority_ref == authority_ref.strip()
        and isinstance(decided_by, str)
        and bool(decided_by.strip())
        and decided_by == decided_by.strip()
        for authority_ref, decided_by in registry.items()
    )


def _ignore_valid(
    decision: Mapping[str, Any],
    assessment: Mapping[str, Any],
    authorized_human_authorities: Mapping[str, str],
    ignore_authority_registry_hash: Mapping[str, Any],
) -> bool:
    required = {
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
    if set(decision) != required or not content_hash_matches(decision):
        return False
    if not _authority_registry_shape_valid(authorized_human_authorities):
        return False
    if typed_payload_hash(dict(authorized_human_authorities)) != ignore_authority_registry_hash:
        return False
    decided_at = decision.get("decided_at")
    if not isinstance(decided_at, str) or decided_at != decided_at.strip():
        return False
    try:
        parsed_decided_at = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed_decided_at.tzinfo is None or parsed_decided_at.utcoffset() is None:
        return False
    decision_id = decision.get("decision_id")
    decided_by = decision.get("decided_by")
    authority_ref = decision.get("authority_ref")
    refs = decision.get("evidence_refs")
    exact_text_fields = (decision_id, decided_by, authority_ref)
    return (
        decision.get("schema_version") == "RESEARCH_OPPORTUNITIES_TENSION_IGNORE_DECISION_V1"
        and decision.get("decision") == "IGNORE"
        and decision.get("authority_kind") == "HUMAN_REVIEW_AUTHORITY"
        and decision.get("authority_registry_hash") == ignore_authority_registry_hash
        and all(
            isinstance(value, str) and bool(value.strip()) and value == value.strip()
            for value in exact_text_fields
        )
        and authorized_human_authorities.get(str(authority_ref)) == decided_by
        and isinstance(refs, list)
        and bool(refs)
        and all(
            isinstance(value, str) and bool(value.strip()) and value == value.strip()
            for value in refs
        )
        and refs == sorted(refs, key=lambda value: value.encode("utf-8"))
        and len(refs) == len(set(refs))
        and decision.get("pair_card_ids") == assessment.get("pair_card_ids")
        and decision.get("assessment_ref") == assessment.get("assessment_id")
        and decision.get("assessment_hash") == assessment.get("content_hash")
        and decision.get("assessment_ref") in refs
        and authority_ref in refs
    )


def _expected_tension(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    assessment: Mapping[str, Any],
    ledger: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    ignore: Mapping[str, Any] | None,
) -> dict[str, Any]:
    projections = {str(item["card_id"]): item for item in (first, second)}
    pair = sorted(projections)
    if ignore is not None:
        classification, rationale = "D", "explicit_structured_human_ignore_decision"
    elif assessment["overall"] in {"coverage_limited", "not_comparable"}:
        classification, rationale = "coverage_limited", assessment["overall"]
    elif assessment["overall"] == "conceptually_related_not_direct":
        classification, rationale = "C", "metric_or_claim_type_difference"
    elif assessment["overall"] == "comparable_with_conditions":
        classification, rationale = "B", "condition_method_boundary_or_scale_difference"
    else:
        directions = {card_id: projections[card_id].get("conclusion_direction") for card_id in pair}
        if None not in directions.values() and len(set(directions.values())) == 2:
            classification, rationale = "A", "directly_comparable_different_anchored_directions"
        else:
            classification, rationale = "C", "no_directional_conflict"
    tension_id = "ten_" + sha256_bytes((snapshot["content_hash"]["value"] + "\n" + assessment["assessment_id"]).encode("utf-8"))[:24].lower()
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_TENSION_EVIDENCE_V2",
            "tension_id": tension_id,
            "source_snapshot_ref": snapshot["snapshot_id"],
            "source_snapshot_hash": deepcopy(snapshot["content_hash"]),
            "configuration_policy_hash": deepcopy(snapshot["configuration_policy_hash"]),
            "pair_card_ids": pair,
            "pair_ledger_ref": ledger["ledger_id"],
            "pair_ledger_hash": deepcopy(ledger["content_hash"]),
            "assessment_ref": assessment["assessment_id"],
            "assessment_hash": deepcopy(assessment["content_hash"]),
            "projection_hashes": {card_id: deepcopy(projections[card_id]["content_hash"]) for card_id in pair},
            "classification": classification,
            "rationale_code": rationale,
            "conclusion_direction_by_card": {card_id: projections[card_id].get("conclusion_direction") for card_id in pair},
            "human_ignore_decision_ref": ignore.get("decision_id") if ignore else None,
            "human_ignore_decision_hash": deepcopy(ignore["content_hash"]) if ignore else None,
            "scientific_truth_judgment": "NOT_ASSESSED",
            "winner_selected": False,
        }
    )


def _compare(expected: Any, observed: Any, path: str, rows: list[dict[str, Any]]) -> None:
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            rows.append({"field": path, "expected": expected, "observed": observed, "pass": False})
            return
        for key in sorted(expected):
            _compare(expected[key], observed.get(key, "__MISSING__"), f"{path}.{key}", rows)
        extras = sorted(set(observed) - set(expected))
        if extras:
            rows.append({"field": f"{path}.__extra_fields__", "expected": [], "observed": extras, "pass": False})
        return
    rows.append({"field": path, "expected": deepcopy(expected), "observed": deepcopy(observed), "pass": observed == expected})


def _check_source(
    *,
    snapshot: Mapping[str, Any],
    cards_by_id: Mapping[str, Mapping[str, Any]],
    chunks_by_card: Mapping[str, Mapping[str, str]],
    trigger_receipt: Mapping[str, Any],
    service_contracts_registry_hash: Mapping[str, Any],
    configuration_policy_hash: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    if set(snapshot) != SOURCE_SNAPSHOT_KEYS:
        issues.append("SOURCE_SNAPSHOT_EXACT_KEYS_MISMATCH")
    if not content_hash_matches(snapshot):
        issues.append("SOURCE_SNAPSHOT_HASH_MISMATCH")
    if snapshot.get("schema_version") != "RESEARCH_OPPORTUNITIES_OPPORTUNITY_SOURCE_SNAPSHOT_V2":
        issues.append("SOURCE_SNAPSHOT_SCHEMA_INVALID")
    try:
        datetime.fromisoformat(str(snapshot.get("source_cutoff")).replace("Z", "+00:00"))
    except ValueError:
        issues.append("SOURCE_CUTOFF_INVALID")
    if (
        snapshot.get("source_profile") != "PUBLIC_SAFE_SYNTHETIC_ONLY"
        or snapshot.get("source_mode") != "ACTIVE_ONLY_BOUNDED_CLUSTER_TOPK"
    ):
        issues.append("SOURCE_PROFILE_OR_MODE_MISMATCH")
    if snapshot.get("real_card_reads") != 0 or snapshot.get("public_safe_synthetic_only") is not True:
        issues.append("SOURCE_EXECUTION_BOUNDARY_MISMATCH")

    if set(trigger_receipt) != TRIGGER_KEYS:
        issues.append("TRIGGER_RECEIPT_EXACT_KEYS_MISMATCH")
    if not content_hash_matches(trigger_receipt):
        issues.append("TRIGGER_RECEIPT_HASH_MISMATCH")
    if trigger_receipt.get("schema_version") != "RESEARCH_OPPORTUNITIES_TRIGGER_SCOPE_RECEIPT_V2":
        issues.append("TRIGGER_RECEIPT_SCHEMA_MISMATCH")
    trigger_type = trigger_receipt.get("trigger_type")
    if trigger_type not in ALLOWED_TRIGGER_TYPES:
        issues.append("TRIGGER_TYPE_NOT_ALLOWED")
    topic_scope = trigger_receipt.get("topic_scope")
    paper_scope = trigger_receipt.get("paper_scope")
    scopes_valid = all(
        isinstance(scope, list)
        and all(isinstance(item, str) and bool(item.strip()) for item in scope)
        and scope == sorted(set(scope))
        for scope in (topic_scope, paper_scope)
    )
    if not scopes_valid or not (topic_scope or paper_scope):
        issues.append("TRIGGER_SCOPE_INVALID")
    trigger_limits = trigger_receipt.get("limits")
    limits_valid = (
        isinstance(trigger_limits, dict)
        and set(trigger_limits) == set(LIMIT_KEYS)
        and all(
            isinstance(trigger_limits.get(key), int)
            and not isinstance(trigger_limits.get(key), bool)
            and trigger_limits[key] > 0
            for key in LIMIT_KEYS
        )
    )
    if not limits_valid:
        issues.append("TRIGGER_LIMITS_INVALID")
    if trigger_receipt.get("scheduler_invoked") is not False or trigger_receipt.get("whole_library_scan") is not False:
        issues.append("TRIGGER_EXECUTION_BOUNDARY_MISMATCH")
    if scopes_valid and (topic_scope or paper_scope) and trigger_type in ALLOWED_TRIGGER_TYPES:
        expected_receipt_id = "trg_" + sha256_bytes(
            (
                str(trigger_type)
                + "\n"
                + "\n".join(topic_scope)
                + "\n--\n"
                + "\n".join(paper_scope)
            ).encode("utf-8")
        )[:24].lower()
        if trigger_receipt.get("receipt_id") != expected_receipt_id:
            issues.append("TRIGGER_RECEIPT_ID_MISMATCH")
    if (
        snapshot.get("trigger_scope_receipt_ref") != trigger_receipt.get("receipt_id")
        or snapshot.get("trigger_scope_receipt_hash") != trigger_receipt.get("content_hash")
        or snapshot.get("limits") != trigger_receipt.get("limits")
    ):
        issues.append("TRIGGER_RECEIPT_BINDING_MISMATCH")

    if not isinstance(snapshot.get("service_contracts_registry_ref"), str) or not snapshot.get("service_contracts_registry_ref", "").strip():
        issues.append("ServiceContracts_REGISTRY_REF_INVALID")
    if not isinstance(snapshot.get("configuration_policy_ref"), str) or not snapshot.get("configuration_policy_ref", "").strip():
        issues.append("Configuration_POLICY_REF_INVALID")
    if not external_hash_is_valid(service_contracts_registry_hash) or snapshot.get("service_contracts_registry_hash") != service_contracts_registry_hash:
        issues.append("ServiceContracts_REGISTRY_HASH_MISMATCH")
    if not external_hash_is_valid(configuration_policy_hash) or snapshot.get("configuration_policy_hash") != configuration_policy_hash:
        issues.append("Configuration_POLICY_HASH_MISMATCH")

    members = snapshot.get("members")
    if not isinstance(members, list) or snapshot.get("member_count") != len(members):
        issues.append("SOURCE_MEMBER_COUNT_MISMATCH")
        return issues
    by_id = {str(item.get("card_id")): item for item in members if isinstance(item, dict)}
    if len(by_id) != len(members) or set(by_id) != set(cards_by_id) or set(by_id) != set(chunks_by_card):
        issues.append("SOURCE_MEMBER_EXACT_SET_MISMATCH")
        return issues
    snapshot_limits = snapshot.get("limits")
    if not limits_valid or snapshot_limits != trigger_limits:
        issues.append("SOURCE_LIMITS_INVALID_OR_UNBOUND")
        safe_limits = {key: 0 for key in LIMIT_KEYS}
    else:
        safe_limits = trigger_limits
    clusters = snapshot.get("clusters")
    if not isinstance(clusters, list):
        issues.append("SOURCE_CLUSTERS_INVALID")
        clusters = []
    if (
        not clusters
        or len(clusters) > safe_limits["max_clusters"]
        or len(members) > safe_limits["max_candidates_total"]
    ):
        issues.append("SOURCE_CLUSTER_OR_MEMBER_LIMIT_EXCEEDED")
    placement: dict[str, tuple[str, int]] = {}
    cluster_ids: set[str] = set()
    for cluster in clusters:
        if not isinstance(cluster, dict) or set(cluster) != {"cluster_id", "ordered_card_ids"}:
            issues.append("SOURCE_CLUSTER_CONTRACT_INVALID")
            continue
        cluster_id = cluster.get("cluster_id")
        ordered = cluster.get("ordered_card_ids")
        if not isinstance(cluster_id, str) or not cluster_id.strip() or cluster_id in cluster_ids:
            issues.append("SOURCE_CLUSTER_ID_INVALID_OR_DUPLICATE")
            continue
        cluster_ids.add(cluster_id)
        if (
            not isinstance(ordered, list)
            or not ordered
            or len(ordered) > safe_limits["max_cards_per_cluster"]
            or not all(isinstance(item, str) and bool(item.strip()) for item in ordered)
            or len(ordered) != len(set(ordered))
        ):
            issues.append("SOURCE_CLUSTER_ORDER_OR_LIMIT_INVALID")
            continue
        for rank, card_id in enumerate(ordered, 1):
            if card_id in placement:
                issues.append("SOURCE_CLUSTER_DUPLICATE_MEMBER")
            placement[card_id] = (cluster_id, rank)
    if set(placement) != set(by_id):
        issues.append("SOURCE_CLUSTER_MEMBER_EXACT_SET_MISMATCH")
    for card_id, card in cards_by_id.items():
        if (
            card.get("schema_version") != 6
            or card.get("review_status") != "active"
            or card.get("doc_type") != "literature"
            or card.get("is_derived") is not False
            or card.get("data_ownership") not in {"self", "entrusted"}
            or card.get("card_id") != card_id
            or not isinstance(card.get("paper_id"), str)
            or not card.get("paper_id", "").strip()
        ):
            issues.append(f"CARD_INELIGIBLE:{card_id}")
        if not content_hash_matches(card):
            issues.append(f"CARD_HASH_MISMATCH:{card_id}")
        member = by_id[card_id]
        if set(member) != SOURCE_MEMBER_KEYS:
            issues.append(f"SOURCE_MEMBER_EXACT_KEYS_MISMATCH:{card_id}")
        if member.get("paper_id") != card.get("paper_id"):
            issues.append(f"SOURCE_PAPER_IDENTITY_MISMATCH:{card_id}")
        if member.get("card_revision") != card.get("revision", 1):
            issues.append(f"SOURCE_CARD_REVISION_MISMATCH:{card_id}")
        if member.get("data_ownership") != card.get("data_ownership"):
            issues.append(f"SOURCE_DATA_OWNERSHIP_MISMATCH:{card_id}")
        if member.get("card_content_hash") != card.get("content_hash"):
            issues.append(f"SOURCE_CARD_HASH_MISMATCH:{card_id}")
        chunks = chunks_by_card[card_id]
        chunks_valid = (
            isinstance(chunks, dict)
            and bool(chunks)
            and all(
                isinstance(key, str)
                and bool(key)
                and isinstance(value, str)
                for key, value in chunks.items()
            )
        )
        if not chunks_valid:
            issues.append(f"SOURCE_CHUNKS_INVALID:{card_id}")
        elif member.get("chunks_content_hash") != typed_payload_hash(dict(chunks)):
            issues.append(f"SOURCE_CHUNKS_HASH_MISMATCH:{card_id}")
        if member.get("rights_classification") != "PUBLIC_SAFE_SYNTHETIC":
            issues.append(f"SOURCE_RIGHTS_MISMATCH:{card_id}")
        if (member.get("cluster_id"), member.get("topk_rank")) != placement.get(card_id):
            issues.append(f"SOURCE_CLUSTER_PLACEMENT_MISMATCH:{card_id}")
    return issues


def verify_opportunity_candidate(
    *,
    candidate: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    trigger_receipt: Mapping[str, Any],
    service_contracts_registry_hash: Mapping[str, Any],
    configuration_policy_hash: Mapping[str, Any],
    cards_by_id: Mapping[str, Mapping[str, Any]],
    chunks_by_card: Mapping[str, Mapping[str, str]],
    projections_by_card: Mapping[str, Mapping[str, Any]],
    anchor_receipts_by_card: Mapping[str, Mapping[str, Any]],
    evidence_by_ref: Mapping[str, Mapping[str, Any]],
    assessments_by_ref: Mapping[str, Mapping[str, Any]],
    pair_ledgers_by_ref: Mapping[str, Mapping[str, Any]],
    authorized_human_authorities: Mapping[str, str],
    ignore_authority_registry_hash: Mapping[str, Any],
    ignore_decisions_by_ref: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recompute every candidate dependency and return a hash-bound report."""

    ignore_decisions_by_ref = ignore_decisions_by_ref or {}
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    authority_registry_shape_valid = _authority_registry_shape_valid(
        authorized_human_authorities
    )
    canonical_authority_registry = (
        dict(authorized_human_authorities) if authority_registry_shape_valid else {}
    )
    computed_authority_registry_hash = typed_payload_hash(canonical_authority_registry)
    _compare(
        True,
        authority_registry_shape_valid,
        "inputs.ignore_authority_registry.shape_valid",
        checks,
    )
    _compare(
        computed_authority_registry_hash,
        ignore_authority_registry_hash,
        "inputs.ignore_authority_registry.hash",
        checks,
    )
    source_codes = _check_source(
        snapshot=source_snapshot,
        cards_by_id=cards_by_id,
        chunks_by_card=chunks_by_card,
        trigger_receipt=trigger_receipt,
        service_contracts_registry_hash=service_contracts_registry_hash,
        configuration_policy_hash=configuration_policy_hash,
    )
    for code in source_codes:
        issues.append({"field": "source_snapshot", "code": code})

    _compare(sorted(CANDIDATE_KEYS), sorted(candidate), "candidate.exact_keys", checks)
    _compare(True, content_hash_matches(candidate), "candidate.content_hash", checks)
    _compare("3.1", candidate.get("schema_version"), "candidate.schema_version", checks)
    _compare("candidate", candidate.get("state"), "candidate.state", checks)
    _compare(False, candidate.get("automatic_state_transition"), "candidate.automatic_state_transition", checks)
    _compare(None, candidate.get("human_decision"), "candidate.human_decision", checks)
    _compare("NOT_ASSESSED", candidate.get("scientific_judgment_status"), "candidate.scientific_judgment_status", checks)
    _compare(source_snapshot.get("snapshot_id"), candidate.get("source_snapshot_ref"), "candidate.source_snapshot_ref", checks)
    _compare(source_snapshot.get("content_hash"), candidate.get("source_snapshot_hash"), "candidate.source_snapshot_hash", checks)
    _compare(trigger_receipt.get("content_hash"), candidate.get("trigger_scope_receipt_hash"), "candidate.trigger_scope_receipt_hash", checks)
    _compare(service_contracts_registry_hash, candidate.get("service_contracts_registry_hash"), "candidate.service_contracts_registry_hash", checks)
    _compare(configuration_policy_hash, candidate.get("configuration_policy_hash"), "candidate.configuration_policy_hash", checks)
    score = candidate.get("score_snapshot") if isinstance(candidate.get("score_snapshot"), dict) else {}
    _compare(sorted(SCORE_KEYS), sorted(score), "candidate.score_snapshot.exact_keys", checks)
    _compare(False, score.get("state_authority"), "candidate.score_snapshot.state_authority", checks)
    _compare(None, score.get("research_value_total_score"), "candidate.score_snapshot.research_value_total_score", checks)
    counts = score.get("tension_classification_counts") if isinstance(score.get("tension_classification_counts"), dict) else {}
    _compare(
        sorted(CLASSIFICATION_COUNT_KEYS),
        sorted(counts),
        "candidate.score_snapshot.tension_classification_counts.exact_keys",
        checks,
    )
    for field in REF_LIST_FIELDS:
        observed_refs = candidate.get(field)
        sorted_unique = (
            isinstance(observed_refs, list)
            and all(isinstance(item, str) and bool(item.strip()) for item in observed_refs)
            and observed_refs == sorted(set(observed_refs))
        )
        _compare(True, sorted_unique, f"candidate.{field}.sorted_unique", checks)

    expected_projections: dict[str, dict[str, Any]] = {}
    if not issues:
        for card_id in sorted(projections_by_card):
            if card_id not in cards_by_id:
                issues.append({"field": f"projections.{card_id}", "code": "PROJECTION_CARD_UNRESOLVED"})
                continue
            expected = _expected_projection(cards_by_id[card_id], chunks_by_card[card_id], source_snapshot)
            expected_projections[card_id] = expected
            _compare(expected, projections_by_card[card_id], f"projections.{card_id}", checks)

    expected_anchor_receipts: dict[str, dict[str, Any]] = {}
    if not issues:
        for card_id, projection in expected_projections.items():
            expected = _expected_anchor_resolution_receipt(projection)
            expected_anchor_receipts[card_id] = expected
            observed = anchor_receipts_by_card.get(card_id)
            if observed is None:
                issues.append(
                    {
                        "field": f"anchor_receipts.{card_id}",
                        "code": "ANCHOR_RESOLUTION_RECEIPT_UNRESOLVED",
                    }
                )
            else:
                _compare(
                    expected,
                    observed,
                    f"anchor_receipts.{card_id}",
                    checks,
                )

    expected_gaps: dict[str, dict[str, Any]] = {}
    if not issues:
        for card_id in sorted(cards_by_id):
            for gap in _expected_gaps(cards_by_id[card_id], chunks_by_card[card_id], source_snapshot):
                expected_gaps[gap["evidence_id"]] = gap

    gap_refs = candidate.get("gap_refs") if isinstance(candidate.get("gap_refs"), list) else []
    tension_refs = candidate.get("tension_refs") if isinstance(candidate.get("tension_refs"), list) else []
    receipt_refs = candidate.get("anchor_resolution_receipt_refs") if isinstance(candidate.get("anchor_resolution_receipt_refs"), list) else []
    assessment_refs = candidate.get("comparison_assessment_refs") if isinstance(candidate.get("comparison_assessment_refs"), list) else []
    ledger_refs = candidate.get("pair_ledger_refs") if isinstance(candidate.get("pair_ledger_refs"), list) else []
    kind = candidate.get("opportunity_kind")
    kind_valid = (
        (kind == "gap" and bool(gap_refs) and not tension_refs and not receipt_refs and not assessment_refs and not ledger_refs)
        or (kind == "tension" and bool(tension_refs) and not gap_refs and bool(receipt_refs) and bool(assessment_refs) and bool(ledger_refs))
        or (kind == "hybrid" and bool(gap_refs) and bool(tension_refs) and bool(receipt_refs) and bool(assessment_refs) and bool(ledger_refs))
    )
    _compare(True, kind_valid, "candidate.opportunity_kind_ref_constraints", checks)
    for ref, observed in evidence_by_ref.items():
        observed_id = observed.get("evidence_id") or observed.get("tension_id")
        if observed_id != ref:
            issues.append({"field": f"evidence.{ref}", "code": "EVIDENCE_MAP_KEY_ID_MISMATCH"})
    for ref in gap_refs:
        expected = expected_gaps.get(ref)
        observed = evidence_by_ref.get(ref)
        if expected is None or observed is None:
            issues.append({"field": f"gap_refs.{ref}", "code": "GAP_UNRESOLVED"})
        else:
            _compare(expected, observed, f"gap_refs.{ref}", checks)

    expected_ledgers: dict[str, dict[str, Any]] = {}
    if not issues:
        for cluster in source_snapshot.get("clusters", []):
            expected = _expected_ledger(source_snapshot, cluster["cluster_id"])
            expected_ledgers[expected["ledger_id"]] = expected
    for ref, observed in pair_ledgers_by_ref.items():
        if observed.get("ledger_id") != ref:
            issues.append({"field": f"pair_ledgers.{ref}", "code": "PAIR_LEDGER_MAP_KEY_ID_MISMATCH"})
    for ref in ledger_refs:
        expected, observed = expected_ledgers.get(ref), pair_ledgers_by_ref.get(ref)
        if expected is None or observed is None:
            issues.append({"field": f"pair_ledger_refs.{ref}", "code": "PAIR_LEDGER_UNRESOLVED"})
        else:
            _compare(expected, observed, f"pair_ledger_refs.{ref}", checks)

    expected_assessments: dict[str, dict[str, Any]] = {}
    if not issues:
        for ref, assessment in assessments_by_ref.items():
            pair = assessment.get("pair_card_ids") if isinstance(assessment.get("pair_card_ids"), list) else []
            ledger = expected_ledgers.get(str(assessment.get("pair_ledger_ref")))
            if assessment.get("assessment_id") != ref:
                issues.append({"field": f"assessments.{ref}", "code": "ASSESSMENT_MAP_KEY_ID_MISMATCH"})
                continue
            if (
                len(pair) != 2
                or not all(isinstance(card_id, str) for card_id in pair)
                or pair != sorted(set(pair))
                or any(card_id not in expected_projections for card_id in pair)
                or ledger is None
            ):
                issues.append({"field": f"assessments.{ref}", "code": "ASSESSMENT_INPUT_UNRESOLVED"})
                continue
            if not any(item.get("card_ids") == pair for item in ledger.get("pairs", [])):
                issues.append({"field": f"assessments.{ref}", "code": "ASSESSMENT_PAIR_OUTSIDE_FROZEN_LEDGER"})
                continue
            expected = _expected_assessment(
                expected_projections[pair[0]],
                expected_projections[pair[1]],
                expected_anchor_receipts[pair[0]],
                expected_anchor_receipts[pair[1]],
                ledger,
                source_snapshot,
            )
            expected_assessments[expected["assessment_id"]] = expected
            _compare(expected, assessment, f"assessments.{ref}", checks)
    for ref in assessment_refs:
        if ref not in assessments_by_ref or ref not in expected_assessments:
            issues.append({"field": f"comparison_assessment_refs.{ref}", "code": "ASSESSMENT_UNRESOLVED"})

    expected_tensions: dict[str, dict[str, Any]] = {}
    used_ignore_refs: set[str] = set()
    if not issues:
        for ref in tension_refs:
            tension = evidence_by_ref.get(ref)
            if tension is None:
                issues.append({"field": f"tension_refs.{ref}", "code": "TENSION_UNRESOLVED"})
                continue
            if tension.get("tension_id") != ref:
                issues.append({"field": f"tension_refs.{ref}", "code": "TENSION_MAP_KEY_ID_MISMATCH"})
                continue
            assessment = expected_assessments.get(str(tension.get("assessment_ref")))
            ledger = expected_ledgers.get(str(tension.get("pair_ledger_ref")))
            pair = tension.get("pair_card_ids") if isinstance(tension.get("pair_card_ids"), list) else []
            if (
                assessment is None
                or ledger is None
                or len(pair) != 2
                or not all(isinstance(card_id, str) for card_id in pair)
                or pair != sorted(set(pair))
                or any(card_id not in expected_projections for card_id in pair)
                or not any(item.get("card_ids") == pair for item in ledger.get("pairs", []))
            ):
                issues.append({"field": f"tension_refs.{ref}", "code": "TENSION_INPUT_UNRESOLVED"})
                continue
            ignore = None
            ignore_ref = tension.get("human_ignore_decision_ref")
            if ignore_ref is not None:
                ignore = ignore_decisions_by_ref.get(str(ignore_ref))
                if ignore is None or not _ignore_valid(
                    ignore,
                    assessment,
                    canonical_authority_registry,
                    computed_authority_registry_hash,
                ):
                    issues.append({"field": f"tension_refs.{ref}", "code": "IGNORE_DECISION_INVALID"})
                    continue
                used_ignore_refs.add(str(ignore_ref))
            expected = _expected_tension(expected_projections[pair[0]], expected_projections[pair[1]], assessment, ledger, source_snapshot, ignore)
            expected_tensions[expected["tension_id"]] = expected
            _compare(expected, tension, f"tension_refs.{ref}", checks)

    tension_assessment_refs = sorted(
        {
            str(evidence_by_ref[ref].get("assessment_ref"))
            for ref in tension_refs
            if ref in evidence_by_ref
        }
    )
    tension_ledger_refs = sorted(
        {
            str(evidence_by_ref[ref].get("pair_ledger_ref"))
            for ref in tension_refs
            if ref in evidence_by_ref
        }
    )
    _compare(sorted(assessment_refs), tension_assessment_refs, "dependency.tension_assessment_refs.exact_closure", checks)
    _compare(sorted(ledger_refs), tension_ledger_refs, "dependency.tension_pair_ledger_refs.exact_closure", checks)
    _compare(sorted(used_ignore_refs), sorted(ignore_decisions_by_ref), "dependency.ignore_decisions.exact_closure", checks)

    expected_receipts_by_ref = {
        receipt["receipt_id"]: receipt
        for receipt in expected_anchor_receipts.values()
    }
    observed_receipt_refs_by_card = {
        card_id: str(receipt.get("receipt_id"))
        for card_id, receipt in anchor_receipts_by_card.items()
    }
    _compare(
        sorted(receipt_refs),
        sorted(expected_receipts_by_ref),
        "dependency.anchor_resolution_receipt_refs.exact_closure",
        checks,
    )
    _compare(
        sorted(expected_anchor_receipts),
        sorted(observed_receipt_refs_by_card),
        "inputs.anchor_receipts.exact_set",
        checks,
    )

    all_objects = [
        *[evidence_by_ref[ref] for ref in gap_refs if ref in evidence_by_ref],
        *[evidence_by_ref[ref] for ref in tension_refs if ref in evidence_by_ref],
        *[
            expected_receipts_by_ref[ref]
            for ref in receipt_refs
            if ref in expected_receipts_by_ref
        ],
        *[assessments_by_ref[ref] for ref in assessment_refs if ref in assessments_by_ref],
        *[pair_ledgers_by_ref[ref] for ref in ledger_refs if ref in pair_ledgers_by_ref],
    ]
    expected_hash_map = {}
    for item in all_objects:
        object_id = next((item.get(key) for key in ("evidence_id", "tension_id", "receipt_id", "assessment_id", "ledger_id") if item.get(key)), None)
        if isinstance(object_id, str):
            expected_hash_map[object_id] = item.get("content_hash")
    _compare(expected_hash_map, candidate.get("evidence_hashes"), "candidate.evidence_hashes", checks)
    _compare(
        sorted(set(gap_refs) | set(tension_refs)),
        sorted(evidence_by_ref),
        "inputs.evidence_by_ref.exact_set",
        checks,
    )
    _compare(sorted(assessment_refs), sorted(assessments_by_ref), "inputs.assessments.exact_set", checks)
    _compare(sorted(ledger_refs), sorted(pair_ledgers_by_ref), "inputs.pair_ledgers.exact_set", checks)
    needed_projection_cards = sorted(
        {
            str(card_id)
            for ref in tension_refs
            if ref in evidence_by_ref
            for card_id in evidence_by_ref[ref].get("pair_card_ids", [])
        }
    )
    _compare(needed_projection_cards, sorted(projections_by_card), "inputs.projections.exact_set", checks)
    _compare(needed_projection_cards, sorted(anchor_receipts_by_card), "inputs.anchor_receipts.needed_card_exact_set", checks)
    expected_evidence_set_hash = typed_payload_hash(expected_hash_map)

    identity_hashes = sorted(
        item["content_hash"]["value"]
        for item in all_objects
        if isinstance(item.get("content_hash"), dict) and isinstance(item["content_hash"].get("value"), str)
    )
    if kind in {"gap", "tension", "hybrid"} and isinstance(source_snapshot.get("content_hash"), dict):
        expected_object_id = "opp_" + sha256_bytes(
            (str(kind) + "\n" + source_snapshot["content_hash"]["value"] + "\n" + "\n".join(identity_hashes)).encode("utf-8")
        )[:24].lower()
        _compare(expected_object_id, candidate.get("object_id"), "candidate.object_id", checks)
    expected_coverage = "coverage_limited" if any(
        expected_tensions.get(ref, {}).get("classification") == "coverage_limited" for ref in tension_refs
    ) else "complete"
    _compare(expected_coverage, candidate.get("coverage_status"), "candidate.coverage_status", checks)
    _compare(expected_coverage, score.get("evidence_completeness"), "candidate.score_snapshot.evidence_completeness", checks)
    expected_parent_refs = sorted(
        {
            source_snapshot.get("snapshot_id"),
            *[
                str(evidence_by_ref[ref].get("card_id"))
                for ref in gap_refs
                if ref in evidence_by_ref
            ],
            *[
                str(card_id)
                for ref in tension_refs
                if ref in evidence_by_ref
                for card_id in evidence_by_ref[ref].get("pair_card_ids", [])
            ],
        }
    )
    expected_provenance_refs = sorted(
        {
            source_snapshot.get("snapshot_id"),
            *gap_refs,
            *tension_refs,
            *receipt_refs,
            *assessment_refs,
            *ledger_refs,
        }
    )
    _compare("RESEARCH_OPPORTUNITIES_RESEARCH_OPPORTUNITIES_OPPORTUNITY_BUILDER_SUCCESSOR003", candidate.get("producer"), "candidate.producer", checks)
    _compare(["M06", "M08", "MODEL_EVALUATION"], candidate.get("consumers"), "candidate.consumers", checks)
    _compare(1, candidate.get("revision"), "candidate.revision", checks)
    _compare(None, candidate.get("supersedes"), "candidate.supersedes", checks)
    _compare(expected_parent_refs, candidate.get("parent_refs"), "candidate.parent_refs", checks)
    _compare(expected_provenance_refs, candidate.get("provenance_refs"), "candidate.provenance_refs", checks)
    _compare(
        [
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
        candidate.get("immutable_fields"),
        "candidate.immutable_fields",
        checks,
    )
    _compare(
        {
            "BLOCKED": "fail_closed",
            "ERROR": "quarantined",
            "FAIL": "fail_closed",
            "NOT_ASSESSED": "no_eligibility",
        },
        candidate.get("failure_semantics"),
        "candidate.failure_semantics",
        checks,
    )
    _compare(True, candidate.get("human_decision_required"), "candidate.human_decision_required", checks)
    _compare(len(gap_refs), score.get("gap_evidence_count"), "candidate.score_snapshot.gap_evidence_count", checks)
    expected_classification_counts = {
        label: sum(
            1
            for ref in tension_refs
            if expected_tensions.get(ref, {}).get("classification") == label
        )
        for label in ("A", "B", "C", "D", "coverage_limited")
    }
    _compare(
        expected_classification_counts,
        score.get("tension_classification_counts"),
        "candidate.score_snapshot.tension_classification_counts",
        checks,
    )
    _compare(candidate.get("object_id"), score.get("ordering_tie_break"), "candidate.score_snapshot.ordering_tie_break", checks)

    def group_passes(*prefixes: str) -> bool:
        selected = [
            row
            for row in checks
            if any(str(row.get("field", "")).startswith(prefix) for prefix in prefixes)
        ]
        related_issues = [
            issue
            for issue in issues
            if any(str(issue.get("field", "")).startswith(prefix) for prefix in prefixes)
        ]
        return bool(selected) and not related_issues and all(row.get("pass") is True for row in selected)

    required_results = {
        "source_snapshot_and_trigger_exact_contract": not source_codes,
        "ignore_authority_registry_binding": group_passes(
            "inputs.ignore_authority_registry."
        ),
        "candidate_exact_contract": group_passes(
            "candidate.exact_keys", "candidate.content_hash", "candidate.schema_version"
        ),
        "candidate_mandatory_identity_and_policy": group_passes(
            "candidate.source_snapshot_",
            "candidate.trigger_scope_receipt_hash",
            "candidate.service_contracts_registry_hash",
            "candidate.configuration_policy_hash",
            "candidate.object_id",
            "candidate.producer",
            "candidate.consumers",
            "candidate.revision",
            "candidate.parent_refs",
            "candidate.provenance_refs",
            "candidate.immutable_fields",
            "candidate.failure_semantics",
        ),
        "candidate_ref_sets_sorted_unique": group_passes(
            "candidate.gap_refs.sorted_unique",
            "candidate.tension_refs.sorted_unique",
            "candidate.anchor_resolution_receipt_refs.sorted_unique",
            "candidate.comparison_assessment_refs.sorted_unique",
            "candidate.pair_ledger_refs.sorted_unique",
            "candidate.opportunity_kind_ref_constraints",
        ),
        "projection_exact_set_and_recompute": group_passes(
            "projections.", "inputs.projections.exact_set"
        ),
        "anchor_resolution_receipt_exact_set_and_recompute": group_passes(
            "anchor_receipts.",
            "inputs.anchor_receipts.",
            "dependency.anchor_resolution_receipt_refs.",
        ),
        "evidence_exact_set_and_recompute": group_passes(
            "gap_refs.",
            "tension_refs.",
            "inputs.evidence_by_ref.exact_set",
            "candidate.evidence_hashes",
        ),
        "assessment_exact_set_and_recompute": group_passes(
            "assessments.", "comparison_assessment_refs.", "inputs.assessments.exact_set"
        ),
        "pair_ledger_exact_set_and_recompute": group_passes(
            "pair_ledger_refs.", "inputs.pair_ledgers.exact_set"
        ),
        "dependency_closure": group_passes("dependency."),
        "candidate_state_and_score_authority": group_passes(
            "candidate.state",
            "candidate.automatic_state_transition",
            "candidate.human_decision",
            "candidate.human_decision_required",
            "candidate.scientific_judgment_status",
            "candidate.coverage_status",
            "candidate.score_snapshot.",
        ),
    }
    for check_id, field in REQUIRED_CHECK_REGISTRY.items():
        _compare(True, required_results[check_id], field, checks)

    failed = [row["field"] for row in checks if not row["pass"]]
    issues.extend({"field": field, "code": "EXPECTED_OBSERVED_MISMATCH"} for field in failed)
    verification = "PASS" if not issues else "FAIL"
    required_check_ids = list(REQUIRED_CHECK_REGISTRY)
    passing_check_ids = [check_id for check_id in required_check_ids if required_results[check_id]]
    failing_check_ids = [check_id for check_id in required_check_ids if not required_results[check_id]]
    coverage_summary = {
        "required_check_count": len(required_check_ids),
        "covered_check_ids": required_check_ids,
        "passing_check_ids": passing_check_ids,
        "failing_check_ids": failing_check_ids,
        "field_check_count": len(checks),
    }
    dynamic_check_commitment = typed_payload_hash({"field_checks": checks})
    return with_content_hash(
        {
            "schema_version": "RESEARCH_OPPORTUNITIES_OPPORTUNITY_VERIFICATION_REPORT_V2",
            "verification_rule_version": "RESEARCH_OPPORTUNITIES_EVIDENCE_REVIEW_ROLE_SEPARATED_RECOMPUTE_SUCCESSOR003_V1",
            "candidate_id": candidate.get("object_id"),
            "candidate_content_hash": deepcopy(candidate.get("content_hash")),
            "source_snapshot_ref": source_snapshot.get("snapshot_id"),
            "source_snapshot_content_hash": deepcopy(source_snapshot.get("content_hash")),
            "trigger_scope_receipt_hash": deepcopy(trigger_receipt.get("content_hash")),
            "service_contracts_registry_hash": deepcopy(service_contracts_registry_hash),
            "configuration_policy_hash": deepcopy(configuration_policy_hash),
            "ignore_authority_registry_hash": computed_authority_registry_hash,
            "evidence_set_hash": expected_evidence_set_hash,
            "verification_result": verification,
            "field_checks": checks,
            "issues": issues,
            "aggregate_pass_does_not_replace_field_evidence": True,
            "required_check_ids": required_check_ids,
            "required_check_registry": deepcopy(REQUIRED_CHECK_REGISTRY),
            "coverage_summary": coverage_summary,
            "dynamic_check_commitment": dynamic_check_commitment,
            "review_capability": deepcopy(REVIEW_CAPABILITY),
            "scientific_judgment_status": "NOT_ASSESSED",
        }
    )

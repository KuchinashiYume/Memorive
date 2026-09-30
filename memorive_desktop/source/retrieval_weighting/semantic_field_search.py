"""Independent offline search over a qualified semantic field overlay.

This module intentionally does not import or mutate the frozen structural
``field_search`` implementation.  It consumes only already-precomputed vectors
whose profile, source identity and rights bindings have passed the additive
semantic overlay contract.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import require_non_empty_string
from .errors import ContractViolation
from .semantic_field_profiles import (
    _source_fields,
    _validated_vector,
    validate_semantic_field_embedding_overlay,
    validate_semantic_field_profile,
)


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractViolation(f"{field_name} must be a positive integer")
    return value


def _strings(value: Any, field_name: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ContractViolation(f"{field_name} must be a non-empty array")
    result = [require_non_empty_string(item, f"{field_name}[]") for item in value]
    if len(result) != len(set(result)):
        raise ContractViolation(f"{field_name} must not contain duplicates")
    return result


def _template_pattern(template: str) -> re.Pattern[str]:
    checked = require_non_empty_string(template, "field_path_templates[]")
    if checked.count("{item}") > 1 or "{" in checked.replace("{item}", ""):
        raise ContractViolation("field path template has unsupported placeholders")
    expression = re.escape(checked).replace(r"\{item\}", r"[^/{}]+")
    return re.compile("^" + expression + "$")


def search_semantic_field_overlays(
    overlay: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    source_records: Sequence[Mapping[str, Any]],
    query_vector: Sequence[float],
    allowed_paper_ids: Sequence[str],
    field_path_templates: Sequence[str],
    top_k: int,
) -> dict[str, Any]:
    """Rank exact eligible semantic fields by L2 dot product.

    Scope and field templates are hard filters.  Underfill is surfaced rather
    than padded, and ties are resolved by field path then overlay vector ID.
    No text is embedded and no provider, index, reranker or network is opened.
    """

    checked_profile = validate_semantic_field_profile(profile)
    checked_overlay = validate_semantic_field_embedding_overlay(
        overlay,
        profile=checked_profile,
        source_records=source_records,
    )
    vector = _validated_vector(
        query_vector,
        checked_profile["embedding_dimension"],
        "query_vector",
    )
    scope = frozenset(_strings(allowed_paper_ids, "allowed_paper_ids"))
    templates = _strings(field_path_templates, "field_path_templates")
    patterns = [_template_pattern(template) for template in templates]
    checked_top_k = _positive_int(top_k, "top_k")
    source_by_id = {field["field_id"]: field for field in _source_fields(source_records)}

    rejected: Counter[str] = Counter()
    scored: list[tuple[float, str, str, dict[str, Any], dict[str, Any]]] = []
    for record in checked_overlay["records"]:
        source = source_by_id[record["field_id"]]
        if source["paper_id"] not in scope:
            rejected["OUT_OF_EXPLICIT_SCOPE"] += 1
            continue
        if not any(pattern.fullmatch(source["field_path"]) for pattern in patterns):
            rejected["FIELD_PATH_NOT_REQUESTED"] += 1
            continue
        score = math.fsum(
            left * right for left, right in zip(vector, record["embedding"])
        )
        scored.append(
            (score, source["field_path"], record["overlay_vector_id"], record, source)
        )

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = scored[:checked_top_k]
    hits: list[dict[str, Any]] = []
    for rank, (score, _path, _vector_id, record, source) in enumerate(selected, 1):
        hits.append(
            {
                "rank": rank,
                "score": score,
                "paper_id": source["paper_id"],
                "card_id": source["card_id"],
                "field_id": source["field_id"],
                "field_path": source["field_path"],
                "overlay_vector_id": record["overlay_vector_id"],
                "source_record_hash": deepcopy(record["source_record_hash"]),
                "source_text_hash": deepcopy(record["source_text_hash"]),
                "provenance_state": source["provenance_state"],
                "profile_id": checked_profile["profile_id"],
            }
        )
    underfill = max(0, checked_top_k - len(hits))
    return {
        "object_type": "SemanticFieldSearchResult",
        "schema_version": "1.0",
        "profile": {
            "profile_id": checked_profile["profile_id"],
            "profile_content_hash": deepcopy(checked_profile["content_hash"]),
            "embedding_dimension": checked_profile["embedding_dimension"],
            "embedding_normalization": checked_profile["embedding_normalization"],
            "similarity_metric": checked_profile["similarity_metric"],
        },
        "overlay": {
            "overlay_id": checked_overlay["overlay_id"],
            "overlay_content_hash": deepcopy(checked_overlay["content_hash"]),
        },
        "field_path_templates": templates,
        "explicit_paper_scope": sorted(scope),
        "requested_top_k": checked_top_k,
        "eligible_count": len(scored),
        "selected_count": len(hits),
        "underfilled": underfill > 0,
        "underfill_count": underfill,
        "rejection_counts": [
            {"reason_code": reason, "count": rejected[reason]}
            for reason in sorted(rejected)
        ],
        "hits": hits,
    }


__all__ = ["search_semantic_field_overlays"]

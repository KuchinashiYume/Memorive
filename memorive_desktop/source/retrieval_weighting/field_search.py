"""Deterministic, offline search over frozen FIELD-VECTORS field-vector records."""

from __future__ import annotations

import math
import re
from collections import Counter
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import require_non_empty_string
from .errors import ContractViolation
from .field_vectors import verify_field_vector_record


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractViolation(f"{field} must be a positive integer")
    return value


def _strings(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ContractViolation(f"{field} must be a non-empty array")
    result = [require_non_empty_string(item, f"{field}[]") for item in value]
    if len(result) != len(set(result)):
        raise ContractViolation(f"{field} must not contain duplicates")
    return result


def _query_vector(value: Any, dimension: int) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContractViolation("query_vector must be an array")
    if len(value) != dimension:
        raise ContractViolation("query_vector dimension differs from the frozen profile")
    result: list[float] = []
    for ordinal, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ContractViolation(f"query_vector[{ordinal}] must be numeric")
        number = float(item)
        if not math.isfinite(number):
            raise ContractViolation(f"query_vector[{ordinal}] must be finite")
        result.append(number)
    norm = math.sqrt(math.fsum(item * item for item in result))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ContractViolation("query_vector must be L2-normalized")
    return result


def _template_pattern(template: str) -> re.Pattern[str]:
    checked = require_non_empty_string(template, "field_path_templates[]")
    if checked.count("{item}") > 1 or "{" in checked.replace("{item}", ""):
        raise ContractViolation("field path template has unsupported placeholders")
    expression = re.escape(checked).replace(r"\{item\}", r"[^/{}]+")
    return re.compile("^" + expression + "$")


def search_field_vectors(
    records: Sequence[Mapping[str, Any]],
    *,
    query_vector: Sequence[float],
    allowed_paper_ids: Sequence[str],
    field_path_templates: Sequence[str],
    top_k: int,
) -> dict[str, Any]:
    """Rank eligible frozen records by dot product on one exact L2 profile.

    This function never embeds text, invokes a model, opens an index, or mutates
    its inputs.  Eligibility failures are reported as underfill; malformed or
    mixed-profile inputs fail closed before ranking.
    """

    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence) or not records:
        raise ContractViolation("records must be a non-empty sequence")
    checked_records = [verify_field_vector_record(record) for record in records]
    profile_fields = (
        "embedding_profile_id",
        "embedding_model_id",
        "embedding_dimension",
        "embedding_normalization",
        "index_id",
        "index_version",
        "collection_id",
    )
    profile = {name: checked_records[0][name] for name in profile_fields}
    for record in checked_records[1:]:
        if any(record[name] != profile[name] for name in profile_fields):
            raise ContractViolation("field-vector input mixes frozen profiles or indexes")
    if profile["embedding_normalization"] != "l2":
        raise ContractViolation("field search requires the frozen L2 profile")

    vector = _query_vector(query_vector, profile["embedding_dimension"])
    scope = frozenset(_strings(allowed_paper_ids, "allowed_paper_ids"))
    templates = _strings(field_path_templates, "field_path_templates")
    patterns = [_template_pattern(template) for template in templates]
    top_k = _positive_int(top_k, "top_k")

    rejected: Counter[str] = Counter()
    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for record in checked_records:
        if record["invalidation_state"] != "active":
            rejected["NOT_ACTIVE"] += 1
            continue
        if record["production_eligible"] is not False:
            raise ContractViolation("field search structural input became production eligible")
        if record["data_ownership"] != "self":
            rejected["RIGHTS_NOT_SELF"] += 1
            continue
        if record["paper_id"] not in scope:
            rejected["OUT_OF_EXPLICIT_SCOPE"] += 1
            continue
        if not any(pattern.fullmatch(record["field_path"]) for pattern in patterns):
            rejected["FIELD_PATH_NOT_REQUESTED"] += 1
            continue
        score = math.fsum(
            left * float(right) for left, right in zip(vector, record["embedding"])
        )
        scored.append((score, record["field_path"], record["vector_id"], record))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = scored[:top_k]
    hits: list[dict[str, Any]] = []
    for rank, (score, _field_path, _vector_id, record) in enumerate(selected, 1):
        hits.append(
            {
                "rank": rank,
                "score": score,
                "paper_id": record["paper_id"],
                "card_id": record["card_id"],
                "card_revision": record["card_revision"],
                "field_path": record["field_path"],
                "field_source_id": record["field_source_id"],
                "source_refs": deepcopy(record["source_refs"]),
                "provenance_refs": deepcopy(record["provenance_refs"]),
                "source_snapshot_id": record["source_snapshot_id"],
                "vector_id": record["vector_id"],
                "record_content_hash": deepcopy(record["content_hash"]),
                "provenance_state": "original",
            }
        )
    underfill = max(0, top_k - len(hits))
    return {
        "object_type": "FieldSearchResult",
        "schema_version": "1.0",
        "profile": profile,
        "field_path_templates": templates,
        "explicit_paper_scope": sorted(scope),
        "requested_top_k": top_k,
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


__all__ = ["search_field_vectors"]

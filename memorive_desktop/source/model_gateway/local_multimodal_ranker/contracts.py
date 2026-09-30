"""Fail-closed contracts for local multimodal generative listwise ranking.

This capability occupies the logical rerank stage but returns an ordered
candidate-id exact set.  It never fabricates cross-encoder relevance scores.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from model_gateway.local_reranker.contracts import render_document_for_reranker


REQUEST_VERSION = "RetrievalCalibrationLocalMultimodalListwiseRankRequest-v1"
RESULT_VERSION = "RetrievalCalibrationLocalMultimodalListwiseRankResult-v1"
RECEIPT_VERSION = "RetrievalCalibrationLocalMultimodalListwiseRankReceipt-v1"
_SHA256 = re.compile(r"^[A-F0-9]{64}$")
_MODEL_DIGEST_PREFIX = re.compile(r"^[a-fA-F0-9]{12,64}$")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_FORBIDDEN_SUBJECT_KEYS = frozenset(
    {
        "labels",
        "critical_positive",
        "supporting_positive",
        "protected_positive",
        "hard_negative",
        "unjudged",
        "relevance_grades",
        "gold",
        "expected",
        "scorer",
    }
)


class LocalMultimodalRankerViolation(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalMultimodalRankerViolation("CANONICAL_JSON_INVALID") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _assert_gold_free(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_SUBJECT_KEYS:
                raise LocalMultimodalRankerViolation("EVALUATOR_DATA_FORBIDDEN")
            _assert_gold_free(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _assert_gold_free(child)


def validate_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    binding = deepcopy(dict(value))
    if binding.get("schema_version") != "RetrievalCalibrationLocalMultimodalRankerBinding-v1":
        raise LocalMultimodalRankerViolation("BINDING_VERSION_INVALID")
    endpoint = binding.get("endpoint")
    if not isinstance(endpoint, str):
        raise LocalMultimodalRankerViolation("ENDPOINT_INVALID")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOOPBACK_HOSTS
        or parsed.path != "/api/chat"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LocalMultimodalRankerViolation("NON_LOOPBACK_OR_UNEXPECTED_ENDPOINT")
    model = binding.get("requested_model")
    if not isinstance(model, str) or not model.strip() or model != model.strip():
        raise LocalMultimodalRankerViolation("MODEL_ID_INVALID")
    digest_prefix = binding.get("expected_model_digest_prefix")
    if not isinstance(digest_prefix, str) or not _MODEL_DIGEST_PREFIX.fullmatch(digest_prefix):
        raise LocalMultimodalRankerViolation("MODEL_DIGEST_PREFIX_INVALID")
    if binding.get("think") is not False:
        raise LocalMultimodalRankerViolation("THINKING_MUST_BE_DISABLED")
    if binding.get("temperature") != 0:
        raise LocalMultimodalRankerViolation("TEMPERATURE_MUST_BE_ZERO")
    seed = binding.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise LocalMultimodalRankerViolation("SEED_INVALID")
    max_candidates = binding.get("max_candidates_per_query")
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 2 <= max_candidates <= 64:
        raise LocalMultimodalRankerViolation("MAX_CANDIDATES_INVALID")
    if binding.get("cloud_fallback") is not False:
        raise LocalMultimodalRankerViolation("CLOUD_FALLBACK_FORBIDDEN")
    if binding.get("dedicated_reranker_fallback") is not False:
        raise LocalMultimodalRankerViolation("DEDICATED_RERANKER_FALLBACK_FORBIDDEN")
    return binding


def _validate_image(value: Mapping[str, Any], allowed_roots: Sequence[Path]) -> dict[str, Any]:
    row = deepcopy(dict(value))
    path_value = row.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise LocalMultimodalRankerViolation("IMAGE_PATH_INVALID")
    path = Path(path_value).resolve(strict=False)
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise LocalMultimodalRankerViolation("IMAGE_PATH_OUTSIDE_ALLOWED_ROOTS")
    if not path.is_file():
        raise LocalMultimodalRankerViolation("IMAGE_MISSING")
    observed = bytes_sha256(path.read_bytes())
    if row.get("sha256") != observed:
        raise LocalMultimodalRankerViolation("IMAGE_HASH_MISMATCH")
    mime_type = row.get("mime_type")
    if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise LocalMultimodalRankerViolation("IMAGE_MIME_INVALID")
    return {"path": str(path), "sha256": observed, "mime_type": mime_type}


def render_document_for_listwise_rank(text: str, provenance: Mapping[str, Any]) -> str:
    """Render either the frozen Installation provenance or the Core live projection."""

    if not isinstance(provenance, Mapping):
        raise LocalMultimodalRankerViolation("DOCUMENT_PROVENANCE_MISSING")
    if provenance.get("schema_version") == "RetrievalCalibrationRerankerDocumentProvenance-v1":
        return render_document_for_reranker(text, provenance)
    if provenance.get("schema_version") != "CoreRetrievalRankingDocumentProvenance-v1":
        raise LocalMultimodalRankerViolation("DOCUMENT_PROVENANCE_VERSION_INVALID")
    accepted = deepcopy(dict(provenance))
    expected_hash = accepted.pop("provenance_sha256", None)
    if expected_hash != canonical_sha256(accepted):
        raise LocalMultimodalRankerViolation("DOCUMENT_PROVENANCE_HASH_INVALID")
    page_family = accepted.get("page_family")
    source_role = accepted.get("source_role")
    metadata_status = accepted.get("metadata_status")
    modifiers = accepted.get("page_family_modifiers")
    page_span = accepted.get("page_span")
    if not isinstance(page_family, str) or not page_family:
        raise LocalMultimodalRankerViolation("PAGE_FAMILY_INVALID")
    if not isinstance(source_role, str) or not source_role:
        raise LocalMultimodalRankerViolation("SOURCE_ROLE_INVALID")
    if not isinstance(metadata_status, str) or not metadata_status:
        raise LocalMultimodalRankerViolation("METADATA_STATUS_INVALID")
    if not isinstance(modifiers, list) or any(not isinstance(item, str) or not item for item in modifiers):
        raise LocalMultimodalRankerViolation("PAGE_FAMILY_MODIFIERS_INVALID")
    if modifiers != sorted(set(modifiers)):
        raise LocalMultimodalRankerViolation("PAGE_FAMILY_MODIFIERS_NOT_CANONICAL")
    if not isinstance(page_span, Mapping) or set(page_span) != {"start", "end"}:
        raise LocalMultimodalRankerViolation("PAGE_SPAN_INVALID")
    span = (
        "unknown"
        if page_span["start"] is None and page_span["end"] is None
        else f"{page_span['start'] if page_span['start'] is not None else 'unknown'}-"
        f"{page_span['end'] if page_span['end'] is not None else 'unknown'}"
    )
    return "\n".join(
        (
            "<SourceProvenance>",
            f"page_family: {page_family}",
            f"page_family_modifiers: {', '.join(modifiers) if modifiers else 'none'}",
            f"page_span: {span}",
            f"source_role: {source_role}",
            f"metadata_status: {metadata_status}",
            "</SourceProvenance>",
            "<Passage>",
            text,
            "</Passage>",
        )
    )


def build_request(
    batches: Sequence[Mapping[str, Any]],
    binding: Mapping[str, Any],
    *,
    allowed_image_roots: Sequence[str | Path] = (),
) -> dict[str, Any]:
    accepted_binding = validate_binding(binding)
    _assert_gold_free(batches)
    roots = tuple(Path(root).resolve(strict=False) for root in allowed_image_roots)
    if not isinstance(batches, Sequence) or isinstance(batches, (str, bytes)) or not batches:
        raise LocalMultimodalRankerViolation("BATCHES_INVALID")
    normalized_batches: list[dict[str, Any]] = []
    query_ids: set[str] = set()
    for value in batches:
        if not isinstance(value, Mapping):
            raise LocalMultimodalRankerViolation("BATCH_INVALID")
        batch = dict(value)
        query_id = batch.get("query_id")
        query = batch.get("query")
        query_intent = batch.get("query_intent", "unknown")
        documents = batch.get("documents")
        if not isinstance(query_id, str) or not query_id or query_id in query_ids:
            raise LocalMultimodalRankerViolation("QUERY_ID_INVALID_OR_DUPLICATE")
        if not isinstance(query, str) or not query.strip():
            raise LocalMultimodalRankerViolation("QUERY_INVALID")
        if not isinstance(query_intent, str) or not query_intent.strip():
            raise LocalMultimodalRankerViolation("QUERY_INTENT_INVALID")
        if (
            not isinstance(documents, Sequence)
            or isinstance(documents, (str, bytes))
            or not 2 <= len(documents) <= accepted_binding["max_candidates_per_query"]
        ):
            raise LocalMultimodalRankerViolation("DOCUMENT_COUNT_INVALID")
        normalized_documents: list[dict[str, Any]] = []
        candidate_ids: set[str] = set()
        for document in documents:
            if not isinstance(document, Mapping):
                raise LocalMultimodalRankerViolation("DOCUMENT_INVALID")
            candidate_id = document.get("candidate_id")
            text = document.get("text")
            if not isinstance(candidate_id, str) or not candidate_id or candidate_id in candidate_ids:
                raise LocalMultimodalRankerViolation("CANDIDATE_ID_INVALID_OR_DUPLICATE")
            if not isinstance(text, str) or not text.strip():
                raise LocalMultimodalRankerViolation("DOCUMENT_TEXT_INVALID")
            provenance = document.get("provenance")
            model_input = render_document_for_listwise_rank(text, provenance)
            images_value = document.get("images", [])
            if not isinstance(images_value, Sequence) or isinstance(images_value, (str, bytes)):
                raise LocalMultimodalRankerViolation("IMAGES_INVALID")
            if images_value and not roots:
                raise LocalMultimodalRankerViolation("IMAGE_ROOTS_REQUIRED")
            images = [_validate_image(image, roots) for image in images_value]
            normalized_documents.append(
                {
                    "candidate_id": candidate_id,
                    "text": text,
                    "text_sha256": bytes_sha256(text.encode("utf-8")),
                    "provenance": deepcopy(dict(provenance)),
                    "model_input": model_input,
                    "model_input_sha256": bytes_sha256(model_input.encode("utf-8")),
                    "images": images,
                }
            )
            candidate_ids.add(candidate_id)
        query_ids.add(query_id)
        normalized_batches.append(
            {
                "query_id": query_id,
                "query": query,
                "query_intent": query_intent,
                "documents": normalized_documents,
            }
        )
    request = {
        "schema_version": REQUEST_VERSION,
        "ranking_mode": "GENERATIVE_LISTWISE_EXACT_SET",
        "binding": accepted_binding,
        "batches": normalized_batches,
        "gold_or_scorer_present": False,
        "synthetic_relevance_scores": False,
    }
    _assert_gold_free(request)
    request["request_sha256"] = canonical_sha256(request)
    return request


def validate_rankings(value: Any, request: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping) or set(value) != {"rankings"}:
        raise LocalMultimodalRankerViolation("OUTPUT_OBJECT_INVALID")
    rows = value.get("rankings")
    if not isinstance(rows, list):
        raise LocalMultimodalRankerViolation("RANKINGS_MISSING")
    expected = {
        batch["query_id"]: [document["candidate_id"] for document in batch["documents"]]
        for batch in request["batches"]
    }
    observed: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"query_id", "candidate_ids"}:
            raise LocalMultimodalRankerViolation("RANKING_ROW_INVALID")
        query_id = row.get("query_id")
        candidate_ids = row.get("candidate_ids")
        if not isinstance(query_id, str) or query_id not in expected or query_id in observed:
            raise LocalMultimodalRankerViolation("RANKING_QUERY_ID_INVALID")
        if (
            not isinstance(candidate_ids, list)
            or len(candidate_ids) != len(expected[query_id])
            or len(set(candidate_ids)) != len(candidate_ids)
            or set(candidate_ids) != set(expected[query_id])
        ):
            raise LocalMultimodalRankerViolation("RANKING_CANDIDATE_EXACT_SET_INVALID")
        observed[query_id] = tuple(candidate_ids)
    if set(observed) != set(expected):
        raise LocalMultimodalRankerViolation("RANKING_QUERY_EXACT_SET_INVALID")
    return tuple(
        {"query_id": query_id, "candidate_ids": list(observed[query_id])}
        for query_id in expected
    )

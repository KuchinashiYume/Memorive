"""Contracts for the RETRIEVAL-CALIBRATION local-only reranker seam.

The subject request deliberately contains no relevance labels or scorer data.
All checks are batch-atomic: one missing, duplicate, foreign, or non-finite
score invalidates the complete result.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
SCHEMA_VERSION = "RetrievalCalibrationLocalRerankerCompatibilityManifest-v1"
REQUEST_VERSION = "RetrievalCalibrationLocalRerankerRequest-v2"
RESULT_VERSION = "RetrievalCalibrationLocalRerankerResult-v2"
WORKER_VERSION = "RetrievalCalibration_QWEN3_LOGIT_WORKER_v2"
PROVENANCE_VERSION = "RetrievalCalibrationRerankerDocumentProvenance-v1"

_PAGE_FAMILIES = frozenset(
    {
        "digital",
        "clean_scan",
        "degraded_scan",
        "multicolumn",
        "role_dense",
        "formula",
        "table_numeric",
        "chart_mixed",
        "unknown",
    }
)

_FORBIDDEN_SUBJECT_KEYS = {
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


class LocalRerankerViolation(ValueError):
    """A stable, receipt-safe local-reranker contract error."""

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
        raise LocalRerankerViolation("CANONICAL_JSON_INVALID") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def file_sha256(path: str | Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _require_sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise LocalRerankerViolation(code)
    return value


def _validate_document_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LocalRerankerViolation("DOCUMENT_PROVENANCE_MISSING")
    provenance = deepcopy(dict(value))
    expected_hash = provenance.pop("provenance_sha256", None)
    if provenance.get("schema_version") != PROVENANCE_VERSION:
        raise LocalRerankerViolation("DOCUMENT_PROVENANCE_VERSION_INVALID")
    _require_sha(provenance.get("retrieval_terminal_sha256"), "Retrieval_TERMINAL_HASH_INVALID")

    page_family = provenance.get("page_family")
    if page_family not in _PAGE_FAMILIES:
        raise LocalRerankerViolation("PAGE_FAMILY_INVALID")
    modifiers = provenance.get("page_family_modifiers")
    if not isinstance(modifiers, list) or any(
        not isinstance(item, str) or not item.strip() for item in modifiers
    ):
        raise LocalRerankerViolation("PAGE_FAMILY_MODIFIERS_INVALID")
    normalized_modifiers = sorted(set(modifiers))
    if modifiers != normalized_modifiers:
        raise LocalRerankerViolation("PAGE_FAMILY_MODIFIERS_NOT_CANONICAL")

    page_span = provenance.get("page_span")
    if not isinstance(page_span, Mapping) or set(page_span) != {"start", "end"}:
        raise LocalRerankerViolation("PAGE_SPAN_INVALID")
    page_start = page_span.get("start")
    page_end = page_span.get("end")
    for page in (page_start, page_end):
        if page is not None and (
            not isinstance(page, int) or isinstance(page, bool) or page < 0
        ):
            raise LocalRerankerViolation("PAGE_SPAN_INVALID")
    if page_start is not None and page_end is not None and page_end < page_start:
        raise LocalRerankerViolation("PAGE_SPAN_INVALID")

    for field, code in (
        ("source_role", "SOURCE_ROLE_INVALID"),
        ("metadata_status", "METADATA_STATUS_INVALID"),
    ):
        field_value = provenance.get(field)
        if not isinstance(field_value, str) or not field_value.strip() or field_value != field_value.strip():
            raise LocalRerankerViolation(code)
    field_provenance = provenance.get("field_provenance")
    if not isinstance(field_provenance, Mapping) or set(field_provenance) != {
        "page_family",
        "source_role",
    }:
        raise LocalRerankerViolation("FIELD_PROVENANCE_INVALID")
    if any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in field_provenance.values()
    ):
        raise LocalRerankerViolation("FIELD_PROVENANCE_INVALID")

    if expected_hash != canonical_sha256(provenance):
        raise LocalRerankerViolation("DOCUMENT_PROVENANCE_HASH_INVALID")
    provenance["provenance_sha256"] = expected_hash
    return provenance


def render_document_for_reranker(text: str, provenance: Mapping[str, Any]) -> str:
    """Render source metadata as neutral evidence inside the model document."""

    if not isinstance(text, str) or not text.strip():
        raise LocalRerankerViolation("DOCUMENT_TEXT_INVALID")
    accepted = _validate_document_provenance(provenance)
    modifiers = accepted["page_family_modifiers"]
    page_span = accepted["page_span"]
    span = (
        "unknown"
        if page_span["start"] is None and page_span["end"] is None
        else f"{page_span['start'] if page_span['start'] is not None else 'unknown'}-"
        f"{page_span['end'] if page_span['end'] is not None else 'unknown'}"
    )
    return "\n".join(
        (
            "<SourceProvenance>",
            f"page_family: {accepted['page_family']}",
            f"page_family_modifiers: {', '.join(modifiers) if modifiers else 'none'}",
            f"page_span: {span}",
            f"source_role: {accepted['source_role']}",
            f"metadata_status: {accepted['metadata_status']}",
            "</SourceProvenance>",
            "<Passage>",
            text,
            "</Passage>",
        )
    )


def _assert_gold_free(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_SUBJECT_KEYS:
                raise LocalRerankerViolation("SUBJECT_GOLD_FIELD_FORBIDDEN")
            _assert_gold_free(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_gold_free(child)


def validate_compatibility_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    manifest = deepcopy(dict(value))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise LocalRerankerViolation("MANIFEST_SCHEMA_VERSION_INVALID")
    if manifest.get("cloud_fallback") is not False:
        raise LocalRerankerViolation("CLOUD_FALLBACK_FORBIDDEN")
    if manifest.get("qualification_offline_required") is not True:
        raise LocalRerankerViolation("OFFLINE_QUALIFICATION_REQUIRED")
    if manifest.get("network_allowlist") != []:
        raise LocalRerankerViolation("NETWORK_ALLOWLIST_NOT_EMPTY")
    for field in (
        "model_id",
        "canonical_revision",
        "model_root",
        "model_sha256sums_sha256",
        "weights_exact_set_sha256",
    ):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise LocalRerankerViolation(f"MANIFEST_{field.upper()}_INVALID")
    _require_sha(manifest["model_sha256sums_sha256"], "MODEL_SUMS_HASH_INVALID")
    _require_sha(manifest["weights_exact_set_sha256"], "WEIGHTS_EXACT_SET_HASH_INVALID")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise LocalRerankerViolation("RUNTIME_BINDING_MISSING")
    for field in ("python_executable", "dependency_lock_sha256", "runtime_sha256sums_sha256"):
        if not isinstance(runtime.get(field), str) or not runtime[field]:
            raise LocalRerankerViolation(f"RUNTIME_{field.upper()}_INVALID")
    _require_sha(runtime["dependency_lock_sha256"], "DEPENDENCY_LOCK_HASH_INVALID")
    _require_sha(runtime["runtime_sha256sums_sha256"], "RUNTIME_SUMS_HASH_INVALID")
    input_contract = manifest.get("input_contract")
    if not isinstance(input_contract, Mapping):
        raise LocalRerankerViolation("INPUT_CONTRACT_MISSING")
    for field in ("batch_size", "max_documents_per_query", "max_input_tokens"):
        if not isinstance(input_contract.get(field), int) or input_contract[field] < 1:
            raise LocalRerankerViolation(f"INPUT_{field.upper()}_INVALID")
    if input_contract.get("candidate_order_must_match_snapshot") is not True:
        raise LocalRerankerViolation("CANDIDATE_ORDER_CONTRACT_INVALID")
    score = manifest.get("score_contract")
    if not isinstance(score, Mapping):
        raise LocalRerankerViolation("SCORE_CONTRACT_MISSING")
    if (
        score.get("direction") != "higher_is_more_relevant"
        or score.get("finite_required") is not True
        or score.get("index_alignment") != "EXACT_UNIQUE_ZERO_BASED"
        or score.get("partial_batch_acceptance") is not False
        or score.get("range") != [0.0, 1.0]
        or score.get("document_mutation_allowed") is not False
    ):
        raise LocalRerankerViolation("SCORE_CONTRACT_INVALID")
    starts = manifest.get("physical_starts_authorized")
    if not isinstance(starts, Mapping) or starts.get("validation_b_exact") != 1:
        raise LocalRerankerViolation("PHYSICAL_START_CONTRACT_INVALID")
    license_binding = manifest.get("license")
    if not isinstance(license_binding, Mapping) or license_binding.get("local_evaluation_authorized") is not True:
        raise LocalRerankerViolation("LOCAL_EVALUATION_LICENSE_NOT_AUTHORIZED")
    _require_sha(license_binding.get("license_file_sha256"), "LICENSE_HASH_INVALID")
    members = manifest.get("model_file_verification", {}).get("members")
    if not isinstance(members, list) or not members:
        raise LocalRerankerViolation("MODEL_MEMBER_EXACT_SET_MISSING")
    paths: set[str] = set()
    for member in members:
        if not isinstance(member, Mapping):
            raise LocalRerankerViolation("MODEL_MEMBER_INVALID")
        path = member.get("path")
        if not isinstance(path, str) or not path or path in paths or Path(path).is_absolute() or ".." in Path(path).parts:
            raise LocalRerankerViolation("MODEL_MEMBER_PATH_INVALID")
        paths.add(path)
        if not isinstance(member.get("bytes"), int) or member["bytes"] < 0:
            raise LocalRerankerViolation("MODEL_MEMBER_SIZE_INVALID")
        _require_sha(member.get("sha256"), "MODEL_MEMBER_HASH_INVALID")
    return manifest


def build_request(
    batches: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    instruction: str | None = None,
) -> dict[str, Any]:
    accepted = validate_compatibility_manifest(manifest)
    input_contract = accepted["input_contract"]
    instruction_value = instruction or input_contract["instruction"]
    if not isinstance(instruction_value, str) or not instruction_value.strip():
        raise LocalRerankerViolation("INSTRUCTION_INVALID")
    normalized_batches: list[dict[str, Any]] = []
    query_ids: set[str] = set()
    for batch in batches:
        _assert_gold_free(batch)
        query_id = batch.get("query_id")
        query = batch.get("query")
        documents = batch.get("documents")
        if not isinstance(query_id, str) or not query_id or query_id in query_ids:
            raise LocalRerankerViolation("QUERY_ID_INVALID")
        if not isinstance(query, str) or not query.strip():
            raise LocalRerankerViolation("QUERY_TEXT_INVALID")
        if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)) or not documents:
            raise LocalRerankerViolation("DOCUMENT_BATCH_INVALID")
        if len(documents) > input_contract["max_documents_per_query"]:
            raise LocalRerankerViolation("DOCUMENT_BATCH_LIMIT_EXCEEDED")
        query_ids.add(query_id)
        normalized_docs: list[dict[str, Any]] = []
        candidate_ids: set[str] = set()
        for index, document in enumerate(documents):
            if not isinstance(document, Mapping):
                raise LocalRerankerViolation("DOCUMENT_INVALID")
            candidate_id = document.get("candidate_id")
            text = document.get("text")
            if not isinstance(candidate_id, str) or not candidate_id or candidate_id in candidate_ids:
                raise LocalRerankerViolation("CANDIDATE_ID_INVALID")
            if not isinstance(text, str) or not text.strip():
                raise LocalRerankerViolation("DOCUMENT_TEXT_INVALID")
            provenance = _validate_document_provenance(document.get("provenance"))
            model_input = render_document_for_reranker(text, provenance)
            candidate_ids.add(candidate_id)
            normalized_docs.append(
                {
                    "index": index,
                    "candidate_id": candidate_id,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest().upper(),
                    "provenance": provenance,
                    "provenance_sha256": provenance["provenance_sha256"],
                    "model_input": model_input,
                    "model_input_sha256": hashlib.sha256(model_input.encode("utf-8")).hexdigest().upper(),
                }
            )
        normalized_batches.append(
            {"query_id": query_id, "query": query, "documents": normalized_docs}
        )
    if not normalized_batches:
        raise LocalRerankerViolation("REQUEST_BATCHES_EMPTY")
    request = {
        "schema_version": REQUEST_VERSION,
        "worker_version": WORKER_VERSION,
        "instruction": instruction_value,
        "model": {
            "model_id": accepted["model_id"],
            "canonical_revision": accepted["canonical_revision"],
            "model_root": accepted["model_root"],
            "model_sha256sums_sha256": accepted["model_sha256sums_sha256"],
            "weights_exact_set_sha256": accepted["weights_exact_set_sha256"],
            "runtime_sha256sums_sha256": accepted["runtime"]["runtime_sha256sums_sha256"],
        },
        "limits": {
            "batch_size": input_contract["batch_size"],
            "max_documents_per_query": input_contract["max_documents_per_query"],
            "max_input_tokens": input_contract["max_input_tokens"],
            "truncation": input_contract["truncation"],
        },
        "device": deepcopy(accepted["device"]),
        "batches": normalized_batches,
        "gold_or_scorer_present": False,
    }
    _assert_gold_free(request)
    request["request_sha256"] = canonical_sha256(request)
    return request


def validate_request(value: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    request = deepcopy(dict(value))
    expected_hash = request.pop("request_sha256", None)
    if request.get("schema_version") != REQUEST_VERSION or expected_hash != canonical_sha256(request):
        raise LocalRerankerViolation("REQUEST_HASH_OR_VERSION_INVALID")
    rebuilt = build_request(request.get("batches", []), manifest, instruction=request.get("instruction"))
    if rebuilt != value:
        raise LocalRerankerViolation("REQUEST_CANONICAL_BINDING_MISMATCH")
    return deepcopy(dict(value))


def validate_worker_result(
    value: Mapping[str, Any],
    request: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    accepted_manifest = validate_compatibility_manifest(manifest)
    accepted_request = validate_request(request, accepted_manifest)
    result = deepcopy(dict(value))
    expected_hash = result.pop("result_sha256", None)
    if result.get("schema_version") != RESULT_VERSION or expected_hash != canonical_sha256(result):
        raise LocalRerankerViolation("RESULT_HASH_OR_VERSION_INVALID")
    if result.get("request_sha256") != accepted_request["request_sha256"]:
        raise LocalRerankerViolation("RESULT_REQUEST_BINDING_MISMATCH")
    identity = result.get("identity")
    if not isinstance(identity, Mapping) or any(
        identity.get(field) != accepted_request["model"].get(field)
        for field in (
            "model_id",
            "canonical_revision",
            "model_sha256sums_sha256",
            "weights_exact_set_sha256",
            "runtime_sha256sums_sha256",
        )
    ):
        raise LocalRerankerViolation("MODEL_IDENTITY_MISMATCH")
    if result.get("side_effects") != {
        "external_requests": 0,
        "network_connect_attempts": 0,
        "cloud_fallback_started": False,
        "document_mutations": 0,
    }:
        raise LocalRerankerViolation("WORKER_SIDE_EFFECTS_INVALID")
    expected_batches = accepted_request["batches"]
    actual_batches = result.get("batches")
    if not isinstance(actual_batches, list) or len(actual_batches) != len(expected_batches):
        raise LocalRerankerViolation("SCORE_BATCH_COUNT_MISMATCH")
    for expected_batch, actual_batch in zip(expected_batches, actual_batches):
        if not isinstance(actual_batch, Mapping) or actual_batch.get("query_id") != expected_batch["query_id"]:
            raise LocalRerankerViolation("SCORE_QUERY_ALIGNMENT_MISMATCH")
        scores = actual_batch.get("scores")
        if not isinstance(scores, list) or len(scores) != len(expected_batch["documents"]):
            raise LocalRerankerViolation("SCORE_COUNT_MISMATCH")
        seen_indices: set[int] = set()
        for expected_document, score_row in zip(expected_batch["documents"], scores):
            if not isinstance(score_row, Mapping):
                raise LocalRerankerViolation("SCORE_ROW_INVALID")
            index = score_row.get("index")
            score = score_row.get("score")
            if not isinstance(index, int) or isinstance(index, bool) or index in seen_indices:
                raise LocalRerankerViolation("SCORE_INDEX_MISMATCH")
            seen_indices.add(index)
            if (
                index != expected_document["index"]
                or score_row.get("candidate_id") != expected_document["candidate_id"]
                or score_row.get("text_sha256") != expected_document["text_sha256"]
                or score_row.get("provenance_sha256") != expected_document["provenance_sha256"]
                or score_row.get("model_input_sha256") != expected_document["model_input_sha256"]
            ):
                raise LocalRerankerViolation("SCORE_INDEX_OR_DOCUMENT_MISMATCH")
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
                raise LocalRerankerViolation("SCORE_NONFINITE")
            if not 0.0 <= float(score) <= 1.0:
                raise LocalRerankerViolation("SCORE_RANGE_INVALID")
        if seen_indices != set(range(len(expected_batch["documents"]))):
            raise LocalRerankerViolation("SCORE_INDEX_MISMATCH")
    if not isinstance(result.get("resources"), Mapping):
        raise LocalRerankerViolation("RESOURCE_RECEIPT_MISSING")
    result["result_sha256"] = expected_hash
    return result

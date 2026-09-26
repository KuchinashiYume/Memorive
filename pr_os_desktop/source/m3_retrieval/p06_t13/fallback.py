"""Deterministic, receipt-bearing local-reranker fallback."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .candidate_features import canonical_sha256
from .pre_rerank_snapshot import restore_snapshot, verify_snapshot


_SNAPSHOT_ERRORS = {"MODEL_MISSING", "MODEL_CORRUPT", "TIMEOUT", "OOM"}
_QUALIFICATION_ERRORS = {
    "SCORE_COUNT_MISMATCH",
    "SCORE_INDEX_MISMATCH",
    "SCORE_NONFINITE",
    "MODEL_HASH_MISMATCH",
}


def apply_fallback(
    error_code: str,
    snapshot: Mapping[str, Any],
    current_candidates: Sequence[Mapping[str, Any]],
    *,
    cloud_requested: bool = False,
) -> dict[str, Any]:
    if cloud_requested:
        receipt = {
            "schema_version": "P06T13FallbackReceipt-v1",
            "status": "FAIL_CLOSED",
            "error_code": "CLOUD_FALLBACK_FORBIDDEN",
            "source_error_code": error_code,
            "snapshot_match": False,
            "cloud_requested": True,
            "cloud_fallback_started": False,
            "qualification_verdict": "FAIL",
            "result_sha256": None,
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return {"result": None, "receipt": receipt}
    snapshot_match = verify_snapshot(snapshot, current_candidates)
    if not snapshot_match:
        receipt = {
            "schema_version": "P06T13FallbackReceipt-v1",
            "status": "FAIL_CLOSED",
            "error_code": "SNAPSHOT_MISMATCH",
            "source_error_code": error_code,
            "snapshot_match": False,
            "cloud_requested": False,
            "cloud_fallback_started": False,
            "qualification_verdict": "FAIL",
            "result_sha256": None,
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return {"result": None, "receipt": receipt}
    if error_code not in _SNAPSHOT_ERRORS | _QUALIFICATION_ERRORS:
        raise ValueError("FALLBACK_ERROR_CODE_UNSUPPORTED")
    result = restore_snapshot(snapshot)
    receipt = {
        "schema_version": "P06T13FallbackReceipt-v1",
        "status": "DEGRADED_EXACT_SNAPSHOT",
        "error_code": error_code,
        "source_error_code": error_code,
        "snapshot_match": True,
        "snapshot_sha256": snapshot.get("snapshot_sha256"),
        "cloud_requested": False,
        "cloud_fallback_started": False,
        "qualification_verdict": "FAIL",
        "result_sha256": canonical_sha256(result),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return {"result": result, "receipt": receipt}

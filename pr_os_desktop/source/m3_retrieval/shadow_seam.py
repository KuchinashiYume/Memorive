"""Default-off P03/T04 observation seam that preserves the M03 baseline object.

The module is intentionally not imported by the current M03 entrypoint.  A
caller must pass ``shadow_enabled=True`` explicitly; no environment variable
can activate it.  Candidate or receipt-sink failures never alter or replace the
already-produced baseline result.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping


def _reject_private_projection(value: Any) -> None:
    forbidden_tokens = ("gold", "oracle", "answerkey", "holdout", "nonce", "privatepayload")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("shadow projection keys must be strings")
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if any(token in normalized for token in forbidden_tokens):
                raise ValueError("shadow observer projection contains evaluator/private data")
            _reject_private_projection(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_private_projection(child)
    elif isinstance(value, str):
        normalized = "".join(character for character in value.casefold() if character.isalnum())
        if any(token in normalized for token in forbidden_tokens):
            raise ValueError("shadow observer projection contains evaluator/private data")


_PROJECTION_KEYS = {
    "candidate_output_ref",
    "candidate_output_sha256",
    "structural_only",
    "semantic_B_eligible",
    "field_semantic_retrieval_quality",
    "external_calls",
    "local_model_calls",
    "provider_tokens",
    "cost_cny",
    "production_mutations",
    "git_mutations",
}


def _validate_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    projection = deepcopy(dict(value))
    if set(projection) != _PROJECTION_KEYS:
        raise ValueError("shadow observer projection exact-set mismatch")
    _reject_private_projection(projection)
    ref = projection["candidate_output_ref"]
    if not isinstance(ref, str) or not ref.startswith("sandbox://"):
        raise ValueError("candidate output ref must be sandbox-only")
    digest = projection["candidate_output_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9A-F]{64}", digest):
        raise ValueError("candidate output SHA-256 is invalid")
    if projection["structural_only"] is not True:
        raise ValueError("observer projection must remain structural-only")
    if projection["semantic_B_eligible"] is not False:
        raise ValueError("observer projection cannot grant semantic eligibility")
    if projection["field_semantic_retrieval_quality"] != "NOT_ASSESSED":
        raise ValueError("observer projection cannot assess semantic quality")
    for name in (
        "external_calls",
        "local_model_calls",
        "provider_tokens",
        "production_mutations",
        "git_mutations",
    ):
        if isinstance(projection[name], bool) or projection[name] != 0:
            raise ValueError(f"observer projection {name} must be integer zero")
    cost = projection["cost_cny"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or float(cost) != 0.0:
        raise ValueError("observer projection cost_cny must be zero")
    return projection


def _write_create_only_receipt(
    receipt: Mapping[str, Any], *, receipt_path: Path, allowed_root: Path
) -> None:
    root = allowed_root.resolve()
    target = receipt_path.resolve()
    if not root.is_dir() or not target.parent.is_dir():
        raise OSError("shadow receipt root or parent is missing")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise OSError("shadow receipt path is outside sandbox allowlist") from exc
    encoded = (
        json.dumps(dict(receipt), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def preserve_baseline_with_shadow_observation(
    baseline_result: Any,
    *,
    shadow_enabled: bool = False,
    observer: Callable[[], Mapping[str, Any]] | None = None,
    receipt_path: str | os.PathLike[str] | None = None,
    allowed_receipt_root: str | os.PathLike[str] | None = None,
) -> Any:
    """Optionally observe a candidate while returning the same baseline object.

    The observer is responsible for sandbox-only output.  Its return value is a
    public-safe receipt projection, not the candidate payload.  This seam never
    writes M11/M12/M13, switches a route, or interprets the observation as an
    acceptance decision.
    """

    if not isinstance(shadow_enabled, bool):
        raise TypeError("shadow_enabled must be an explicit boolean")
    if shadow_enabled is False:
        return baseline_result

    # No candidate work may begin unless a create-only sandbox receipt target
    # is fixed in advance.
    if observer is None or receipt_path is None or allowed_receipt_root is None:
        return baseline_result
    target = Path(receipt_path)
    allowed_root = Path(allowed_receipt_root)
    if os.path.lexists(target):
        return baseline_result

    receipt: dict[str, Any]
    try:
        projection = observer()
        if not isinstance(projection, Mapping):
            raise TypeError("shadow observer projection must be a mapping")
        projection_copy = _validate_projection(projection)
        receipt = {
            "schema_version": "P03_T04_M03_SHADOW_OBSERVATION_RECEIPT_V1",
            "lifecycle_status": "completed",
            "verification_result": "PASS",
            "acceptance_verdict": "NOT_ASSESSED",
            "issue_code": None,
            "semantic_B_eligible": False,
            "observer_projection": projection_copy,
        }
    except Exception as exc:  # baseline must survive an optional observer failure
        receipt = {
            "schema_version": "P03_T04_M03_SHADOW_OBSERVATION_RECEIPT_V1",
            "lifecycle_status": "completed",
            "verification_result": "ERROR",
            "acceptance_verdict": "NOT_ASSESSED",
            "issue_code": "SHADOW_OBSERVER_ERROR",
            "error_type": type(exc).__name__,
            "semantic_B_eligible": False,
        }
    receipt["receipt_payload_sha256"] = hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest().upper()
    try:
        _write_create_only_receipt(receipt, receipt_path=target, allowed_root=allowed_root)
    except Exception:
        pass
    return baseline_result


__all__ = ["preserve_baseline_with_shadow_observation"]

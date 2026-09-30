"""Memorive DECISION-LOG/DECISION_LOG frozen four-axis baseline validation.

This module validates facts; it does not perform state transitions.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping


AXIS_VALUES: dict[str, tuple[str, ...]] = {
    "design_status": ("proposed", "approved", "superseded"),
    "implementation_status": ("not_started", "partial", "implemented"),
    "verification_status": ("untested", "tested", "qualified"),
    "deployment_status": ("disabled", "pilot", "active"),
}


class BaselineValidationError(ValueError):
    """Raised when a frozen baseline or status claim violates G03."""


def load_matrix(path: str | Path) -> dict[str, Any]:
    """Load and validate a four-axis matrix from JSON."""

    with Path(path).open("r", encoding="utf-8") as handle:
        matrix = json.load(handle)
    return validate_matrix(matrix)


def validate_matrix(matrix: Mapping[str, Any]) -> dict[str, Any]:
    """Validate legal values, evidence, uniqueness, and axis independence."""

    if matrix.get("schema_version") != "1.0":
        raise BaselineValidationError("unsupported matrix schema_version")
    contract = matrix.get("axis_contract")
    if not isinstance(contract, Mapping):
        raise BaselineValidationError("axis_contract must be an object")
    for axis, allowed in AXIS_VALUES.items():
        declared = contract.get(axis)
        if not isinstance(declared, list) or set(declared) != set(allowed):
            raise BaselineValidationError(f"axis_contract mismatch for {axis}")

    capabilities = matrix.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise BaselineValidationError("capabilities must be a non-empty list")

    seen: set[str] = set()
    for row in capabilities:
        if not isinstance(row, Mapping):
            raise BaselineValidationError("each capability must be an object")
        capability_id = row.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise BaselineValidationError("capability_id is required")
        if capability_id in seen:
            raise BaselineValidationError(f"duplicate capability_id: {capability_id}")
        seen.add(capability_id)

        basis = row.get("axis_basis")
        evidence = row.get("evidence")
        if not isinstance(basis, Mapping) or not isinstance(evidence, Mapping):
            raise BaselineValidationError(f"{capability_id}: axis_basis and evidence are required")

        for axis, allowed in AXIS_VALUES.items():
            value = row.get(axis)
            if value not in allowed:
                raise BaselineValidationError(f"{capability_id}: illegal {axis}={value!r}")
            if basis.get(axis) != "independent":
                raise BaselineValidationError(
                    f"{capability_id}: {axis} must be independently evidenced, not derived"
                )
            axis_evidence = evidence.get(axis)
            if not isinstance(axis_evidence, list) or not any(
                isinstance(item, str) and item.strip() for item in axis_evidence
            ):
                raise BaselineValidationError(f"{capability_id}: missing evidence for {axis}")

    return copy.deepcopy(dict(matrix))


def get_capability(matrix: Mapping[str, Any], capability_id: str) -> Mapping[str, Any]:
    """Return one validated capability row."""

    validated = validate_matrix(matrix)
    for row in validated["capabilities"]:
        if row["capability_id"] == capability_id:
            return row
    raise BaselineValidationError(f"unknown capability_id: {capability_id}")


def validate_claim(
    matrix: Mapping[str, Any],
    capability_id: str,
    axis: str,
    claimed_value: str,
    *,
    derived_from_axis: str | None = None,
) -> bool:
    """Reject cross-axis inference and claims that contradict the frozen fact."""

    if axis not in AXIS_VALUES:
        raise BaselineValidationError(f"unknown axis: {axis}")
    if claimed_value not in AXIS_VALUES[axis]:
        raise BaselineValidationError(f"illegal claimed value for {axis}: {claimed_value}")
    if derived_from_axis is not None and derived_from_axis != axis:
        raise BaselineValidationError(
            f"cross-axis inference forbidden: {derived_from_axis} -> {axis}"
        )

    row = get_capability(matrix, capability_id)
    actual = row[axis]
    if claimed_value != actual:
        raise BaselineValidationError(
            f"claim contradicts frozen baseline: {capability_id}.{axis}={actual}, "
            f"not {claimed_value}"
        )
    return True


def validate_production_claim(
    matrix: Mapping[str, Any], capability_id: str, claimed_available: bool
) -> bool:
    """Production availability is true only when deployment_status is active."""

    row = get_capability(matrix, capability_id)
    actual_available = row["deployment_status"] == "active"
    if claimed_available is not actual_available:
        raise BaselineValidationError(
            f"production claim contradicts deployment_status={row['deployment_status']}"
        )
    return True


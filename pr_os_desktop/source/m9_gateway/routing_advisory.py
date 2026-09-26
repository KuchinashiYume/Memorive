"""M9-facing read-only routing advisory seam for P03/T07."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from m7_weighting.contracts import make_hashed_payload, verify_hashed_payload
from m14_quality_sentinel.dynamic_routing_advisory import advise_routes


_WINDOW_KEYS = {
    "schema_version",
    "snapshot_id",
    "captured_at",
    "window_start",
    "window_end",
    "candidate_rows",
    "source_refs",
    "real_data_read_count",
    "external_call_count",
    "production_route_write_count",
    "content_hash",
}


class RoutingAdvisorySeamError(ValueError):
    """The M9 advisory snapshot is invalid or attempts a side effect."""


def make_routing_input_window_snapshot(
    *,
    snapshot_id: str,
    captured_at: str,
    window_start: str,
    window_end: str,
    candidate_rows: Sequence[Mapping[str, Any]],
    source_refs: Sequence[str],
) -> dict[str, Any]:
    payload = {
        "schema_version": "P03_T07_ROUTING_INPUT_WINDOW_SNAPSHOT_V1",
        "snapshot_id": snapshot_id,
        "captured_at": captured_at,
        "window_start": window_start,
        "window_end": window_end,
        "candidate_rows": [deepcopy(dict(row)) for row in candidate_rows],
        "source_refs": list(source_refs),
        "real_data_read_count": 0,
        "external_call_count": 0,
        "production_route_write_count": 0,
    }
    return validate_routing_input_window_snapshot(make_hashed_payload(payload))


def validate_routing_input_window_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        snapshot = verify_hashed_payload(value, "routing_input_window_snapshot")
    except Exception as exc:
        raise RoutingAdvisorySeamError("ROUTING_WINDOW_HASH_INVALID") from exc
    if set(snapshot) != _WINDOW_KEYS:
        raise RoutingAdvisorySeamError("ROUTING_WINDOW_EXACT_KEYS_MISMATCH")
    if snapshot["schema_version"] != "P03_T07_ROUTING_INPUT_WINDOW_SNAPSHOT_V1":
        raise RoutingAdvisorySeamError("ROUTING_WINDOW_SCHEMA_UNSUPPORTED")
    for field in ("snapshot_id", "captured_at", "window_start", "window_end"):
        if not isinstance(snapshot[field], str) or not snapshot[field] or snapshot[field] != snapshot[field].strip():
            raise RoutingAdvisorySeamError(f"ROUTING_WINDOW_{field.upper()}_INVALID")
    rows = snapshot["candidate_rows"]
    if not isinstance(rows, list):
        raise RoutingAdvisorySeamError("ROUTING_WINDOW_ROWS_INVALID")
    refs = snapshot["source_refs"]
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs) or refs != sorted(set(refs)):
        raise RoutingAdvisorySeamError("ROUTING_WINDOW_SOURCE_REFS_INVALID")
    for field in ("real_data_read_count", "external_call_count", "production_route_write_count"):
        if isinstance(snapshot[field], bool) or not isinstance(snapshot[field], int) or snapshot[field] != 0:
            raise RoutingAdvisorySeamError("ROUTING_WINDOW_SIDE_EFFECT_NONZERO")
    return snapshot


def preview_route_advice(
    snapshot: Mapping[str, Any], *, role_family: str, requested_group_id: str | None = None
) -> dict[str, Any]:
    checked = validate_routing_input_window_snapshot(snapshot)
    return advise_routes(
        checked["candidate_rows"],
        role_family=role_family,
        requested_group_id=requested_group_id,
    )


__all__ = [
    "RoutingAdvisorySeamError",
    "make_routing_input_window_snapshot",
    "preview_route_advice",
    "validate_routing_input_window_snapshot",
]

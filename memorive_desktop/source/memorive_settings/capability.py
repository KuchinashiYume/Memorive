from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence


AXES = ("available", "enabled", "eligible", "blocked", "reason")


class CapabilityProjection:
    def __init__(self, rows: Sequence[Mapping[str, Any]], *, revision: str):
        self.revision = revision
        accepted = []
        seen = set()
        for row in rows:
            if set(row) != {"capability_id", *AXES}:
                raise ValueError("CAPABILITY_PROJECTION_FIELDS_INVALID")
            capability_id = row["capability_id"]
            if (
                not isinstance(capability_id, str)
                or not capability_id
                or capability_id in seen
            ):
                raise ValueError("CAPABILITY_ID_INVALID")
            seen.add(capability_id)
            for axis in AXES[:-1]:
                if not isinstance(row[axis], bool):
                    raise ValueError(f"CAPABILITY_{axis.upper()}_INVALID")
            if not isinstance(row["reason"], str) or not row["reason"]:
                raise ValueError("CAPABILITY_REASON_INVALID")
            if row["enabled"] and (
                not row["available"] or row["blocked"] or not row["eligible"]
            ):
                raise ValueError("CAPABILITY_AXES_CONFLICT")
            accepted.append(dict(row))
        self._rows = sorted(accepted, key=lambda item: item["capability_id"])

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "CapabilityStateProjection-v1",
            "revision": self.revision,
            "axes": list(AXES),
            "capabilities": deepcopy(self._rows),
            "route_or_profile_mutations": 0,
        }

    def assert_unchanged(self, prior: Mapping[str, Any]) -> bool:
        return self.snapshot() == dict(prior)


__all__ = ["AXES", "CapabilityProjection"]

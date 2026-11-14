"""Bounded resource admission and deterministic recovery planning."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import validate_contract
from .errors import ResourceBlocked
from .types import LocalResourceEnvelope


def admit_resource(envelope: LocalResourceEnvelope, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    validate_contract(envelope)
    required = {
        "cpu_percent": envelope.max_cpu_percent,
        "gpu_memory_mib": envelope.max_gpu_memory_mib,
        "ram_mib": envelope.max_ram_mib,
        "disk_free_mib": envelope.min_disk_free_mib,
    }
    missing = [key for key in required if key not in snapshot]
    if missing:
        raise ResourceBlocked("RESOURCE_SNAPSHOT_INCOMPLETE", details={"missing": missing})
    if int(snapshot.get("active_concurrency", 0)) >= envelope.max_concurrency:
        raise ResourceBlocked("RESOURCE_CONCURRENCY_EXCEEDED")
    if int(snapshot["cpu_percent"]) > envelope.max_cpu_percent:
        raise ResourceBlocked("CPU_BUDGET_EXCEEDED")
    if int(snapshot["gpu_memory_mib"]) > envelope.max_gpu_memory_mib:
        raise ResourceBlocked("GPU_MEMORY_BUDGET_EXCEEDED")
    if int(snapshot["ram_mib"]) > envelope.max_ram_mib:
        raise ResourceBlocked("RAM_BUDGET_EXCEEDED")
    if int(snapshot["disk_free_mib"]) < envelope.min_disk_free_mib:
        raise ResourceBlocked("DISK_HEADROOM_INSUFFICIENT")
    return {
        "envelope_id": envelope.envelope_id,
        "result": "ADMITTED",
        "snapshot": {key: snapshot[key] for key in sorted(snapshot)},
        "automatic_expansion": False,
        "implicit_truncation": False,
    }


def bounded_recovery(error_code: str, envelope: LocalResourceEnvelope) -> dict[str, Any]:
    if error_code == "OOM" and envelope.oom_fallback_profile_id:
        return {
            "action": "NEW_A_ATTEMPT_WITH_FROZEN_SMALLER_PROFILE",
            "profile_id": envelope.oom_fallback_profile_id,
            "retry_in_same_attempt": False,
            "cloud_fallback": False,
        }
    return {
        "action": "KEEP_DISABLED_AND_FREEZE_FAILURE",
        "profile_id": None,
        "retry_in_same_attempt": False,
        "cloud_fallback": False,
    }

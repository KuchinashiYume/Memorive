from __future__ import annotations

import pytest

from model_gateway.local_capability import LocalResourceEnvelope
from model_gateway.local_capability.errors import ResourceBlocked
from model_gateway.local_capability.resource import admit_resource, bounded_recovery
def envelope() -> LocalResourceEnvelope:
    return LocalResourceEnvelope(
        envelope_id="resource-001",
        max_context_tokens=65536,
        max_pages=1,
        max_image_pixels=10_000_000,
        max_batch_size=4,
        max_concurrency=1,
        max_cpu_percent=80,
        max_gpu_memory_mib=20000,
        max_ram_mib=32000,
        min_disk_free_mib=2048,
        timeout_seconds=120,
        max_attempts=1,
        reserved_headroom_percent=10,
        oom_fallback_profile_id="profile-32k",
    )


def snapshot() -> dict[str, int]:
    return {
        "cpu_percent": 20,
        "gpu_memory_mib": 1000,
        "ram_mib": 4000,
        "disk_free_mib": 10000,
        "active_concurrency": 0,
    }


def test_resource_admission_does_not_expand_or_truncate() -> None:
    result = admit_resource(envelope(), snapshot())
    assert result["result"] == "ADMITTED"
    assert result["automatic_expansion"] is False
    assert result["implicit_truncation"] is False


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("cpu_percent", 81, "CPU_BUDGET_EXCEEDED"),
        ("gpu_memory_mib", 20001, "GPU_MEMORY_BUDGET_EXCEEDED"),
        ("ram_mib", 32001, "RAM_BUDGET_EXCEEDED"),
        ("disk_free_mib", 2047, "DISK_HEADROOM_INSUFFICIENT"),
        ("active_concurrency", 1, "RESOURCE_CONCURRENCY_EXCEEDED"),
    ],
)
def test_each_resource_hard_gate_fails_closed(field: str, value: int, code: str) -> None:
    observed = snapshot()
    observed[field] = value
    with pytest.raises(ResourceBlocked, match=code):
        admit_resource(envelope(), observed)


def test_resource_snapshot_must_be_complete() -> None:
    observed = snapshot()
    del observed["ram_mib"]
    with pytest.raises(ResourceBlocked, match="RESOURCE_SNAPSHOT_INCOMPLETE"):
        admit_resource(envelope(), observed)


def test_oom_recovery_is_a_new_frozen_a_attempt_not_an_inline_retry() -> None:
    result = bounded_recovery("OOM", envelope())
    assert result["action"] == "NEW_A_ATTEMPT_WITH_FROZEN_SMALLER_PROFILE"
    assert result["profile_id"] == "profile-32k"
    assert result["retry_in_same_attempt"] is False
    assert result["cloud_fallback"] is False


def test_unknown_failure_keeps_role_disabled() -> None:
    result = bounded_recovery("UNKNOWN", envelope())
    assert result["action"] == "KEEP_DISABLED_AND_FREEZE_FAILURE"
    assert result["cloud_fallback"] is False

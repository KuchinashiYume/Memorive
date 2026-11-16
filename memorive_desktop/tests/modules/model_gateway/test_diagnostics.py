from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from memorive.models.gateway.local_capability import ActivationStatus, ExecutionStatus, FileIdentity, LocalExecutionReceipt, LocalRoleProfile, LocalRuntimeAssetManifest, LogicalRole, OwnershipClass
from memorive.models.gateway.local_capability.diagnostics import local_diagnose, local_preflight, local_status
from memorive.models.gateway.local_capability.errors import LocalCapabilityError
def manifest() -> LocalRuntimeAssetManifest:
    file = FileIdentity(locator="C:/asset", sha256="A" * 64, bytes=1)
    return LocalRuntimeAssetManifest(
        manifest_id="manifest-001",
        runtime_name="ollama",
        runtime_version="0.12.10",
        executable=file,
        endpoint="http://127.0.0.1:11434/api/generate",
        launch_mode="USE_EXISTING_ONLY",
        allowed_parent_processes=("worker.exe",),
        model_tag="qwen3.8:27b-q4_K_M",
        model_manifest=file,
        model_blob=file,
        projector=file,
        source_locator="local://ollama",
        acquired_date="2026-08-17",
        license_file=file,
        license_verdict="LOCAL_EVALUATION_ONLY",
        model_root="G:/Ollama/models",
        acl_policy="READ_ONLY",
        encryption_status="NOT_ASSESSED",
        backup_policy="USER_OWNED",
        update_policy="NO_UPDATE",
        resource_baseline={},
    )


def profiles() -> dict[LogicalRole, LocalRoleProfile]:
    return {
        role: LocalRoleProfile(
            profile_id=f"profile-{role.value.lower()}",
            logical_role=role,
            profile_revision="v1",
            runtime_asset_manifest_id="manifest-001",
            input_contract_version="v1",
            output_contract_version="v1",
            allowed_ownership_classes=tuple(OwnershipClass),
            resource_envelope_id="resource-001",
            route_eligibility=0,
            activation_status=ActivationStatus.DISABLED,
        )
        for role in LogicalRole
    }


def receipt(error: str | None, status: ExecutionStatus) -> LocalExecutionReceipt:
    return LocalExecutionReceipt(
        receipt_id=f"receipt-{status.value.lower()}",
        request_id="request-001",
        attempt=1,
        logical_role=LogicalRole.LOCAL_ANALYSIS,
        profile_id="profile-local_analysis",
        requested_runtime="ollama",
        actual_runtime=None,
        requested_model="qwen",
        actual_model=None,
        manifest_id="manifest-001",
        started_at="2026-08-18T00:00:00Z",
        ended_at="2026-08-18T00:00:01Z",
        latency_ms=0,
        metrics={},
        resource_peaks={},
        schema_verdict="NOT_ASSESSED",
        quality_verdict="NOT_ASSESSED",
        zero_egress_verdict="NOT_ASSESSED_NO_RUNTIME_REQUEST",
        output_locator=None,
        output_sha256=None,
        error_class=error,
        fallback_path=None,
        final_status=status,
    )


def test_local_status_is_read_only_and_lists_exactly_six_disabled_roles() -> None:
    result = local_status(manifest(), profiles())
    assert len(result["roles"]) == 6
    assert all(row["route_eligibility"] == 0 for row in result["roles"])
    assert all(row["activation_status"] == "DISABLED" for row in result["roles"])
    assert result["model_process_started"] is False
    assert result["model_request_count"] == 0


def test_local_status_reports_the_model_bound_to_each_role() -> None:
    qwen = manifest()
    gemma = replace(qwen, manifest_id="manifest-gemma", model_tag="gemma4:26b")
    role_profiles = profiles()
    role_profiles[LogicalRole.LOCAL_ANALYSIS] = replace(
        role_profiles[LogicalRole.LOCAL_ANALYSIS],
        runtime_asset_manifest_id=gemma.manifest_id,
    )

    result = local_status({qwen.manifest_id: qwen, gemma.manifest_id: gemma}, role_profiles)

    by_role = {row["logical_role"]: row for row in result["roles"]}
    assert result["model"] == "MULTIPLE_ROLE_BOUND"
    assert by_role["LOCAL_ANALYSIS"]["model"] == "gemma4:26b"
    assert by_role["LOCAL_OCR"]["model"] == "qwen3.8:27b-q4_K_M"
    assert [row["manifest_id"] for row in result["runtime_assets"]] == ["manifest-001", "manifest-gemma"]
    assert result["model_process_started"] is False
    assert result["model_request_count"] == 0


def test_preflight_defaults_to_no_health_and_no_mutation() -> None:
    result = local_preflight(
        identity_ready=True,
        ownership_contract_ready=True,
        resource_headroom_ready=True,
        isolated_root_ready=True,
        zero_egress_observer_ready=False,
        allowed_roots=("G:/sandbox",),
        allowed_processes=("worker.exe",),
    )
    assert result["result"] == "BLOCKED"
    assert result["with_health"] is False
    assert result["model_request_count"] == 0
    assert result["system_policy_mutations"] == 0


def test_health_mode_requires_separate_authority() -> None:
    with pytest.raises(LocalCapabilityError, match="HEALTH_MODE_NOT_AUTHORIZED"):
        local_preflight(
            identity_ready=True,
            ownership_contract_ready=True,
            resource_headroom_ready=True,
            isolated_root_ready=True,
            zero_egress_observer_ready=True,
            allowed_roots=("G:/sandbox",),
            allowed_processes=("worker.exe",),
            with_health=True,
        )


@pytest.mark.parametrize(
    ("error", "status", "plane"),
    [
        ("OWNERSHIP_IDENTITY_MISSING", ExecutionStatus.BLOCKED, "IDENTITY"),
        ("ZERO_EGRESS_NOT_PROVEN", ExecutionStatus.BLOCKED, "OBSERVATION"),
        ("OUTPUT_SCHEMA_INVALID", ExecutionStatus.ERROR, "OUTPUT"),
        (None, ExecutionStatus.COMPLETED, "NONE"),
    ],
)
def test_diagnose_reports_plane_without_repair(error: str | None, status: ExecutionStatus, plane: str) -> None:
    result = local_diagnose(receipt(error, status))
    assert result["failure_plane"] == plane
    assert result["automatic_repair_performed"] is False
    assert result["model_process_started"] is False


def test_cli_status_does_not_start_or_contact_a_model() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "memorive.models.gateway.local_capability", "local-status"],
        cwd=Path(__file__).resolve().parents[3] / "source",
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["command"] == "local-status"
    assert payload["model_process_started"] is False
    assert payload["model_request_count"] == 0
    assert payload["external_request_count"] == 0
    assert payload["production_mutations"] == 0

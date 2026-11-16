from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from memorive.models.gateway.local_capability import ActivationStatus, ExecutionStatus, FileIdentity, LocalCapabilityBroker, LocalEgressEvidence, LocalExecutionEnvelope, LocalResourceEnvelope, LocalRoleProfile, LocalRuntimeAssetManifest, LogicalRole, OwnershipClass
from memorive.models.gateway.local_capability.adapters import AnalysisAdapter, DistillAdapter, EmbeddingAdapter, OCRAdapter, RetrievalAdapter, VerifyAdapter
from memorive.models.gateway.local_capability.errors import LocalCapabilityError
SHA = "A" * 64


def file_identity(name: str) -> FileIdentity:
    return FileIdentity(locator=f"C:/assets/{name}", sha256=SHA, bytes=1)


def manifest(*, manifest_id: str = "manifest-001", model_tag: str = "qwen3.8:27b-q4_K_M") -> LocalRuntimeAssetManifest:
    return LocalRuntimeAssetManifest(
        manifest_id=manifest_id,
        runtime_name="ollama",
        runtime_version="0.12.10",
        executable=file_identity("ollama.exe"),
        endpoint="http://127.0.0.1:11434/api/generate",
        launch_mode="USE_EXISTING_ONLY_NO_START_STOP_RESTART",
        allowed_parent_processes=("broker-worker.exe",),
        model_tag=model_tag,
        model_manifest=file_identity("manifest"),
        model_blob=file_identity("blob"),
        projector=file_identity("projector"),
        source_locator="local://ollama",
        acquired_date="2026-08-17",
        license_file=file_identity("license"),
        license_verdict="LOCAL_EVALUATION_ONLY",
        model_root="G:/Ollama/models",
        acl_policy="READ_ONLY",
        encryption_status="NOT_ASSESSED",
        backup_policy="USER_OWNED",
        update_policy="NO_UPDATE",
        resource_baseline={},
    )


def resource() -> LocalResourceEnvelope:
    return LocalResourceEnvelope(
        envelope_id="resource-001",
        max_context_tokens=65536,
        max_pages=1,
        max_image_pixels=20_000_000,
        max_batch_size=4,
        max_concurrency=1,
        max_cpu_percent=90,
        max_gpu_memory_mib=24000,
        max_ram_mib=64000,
        min_disk_free_mib=100,
        timeout_seconds=120,
        max_attempts=1,
        reserved_headroom_percent=10,
        oom_fallback_profile_id=None,
    )


OUTPUTS: dict[LogicalRole, dict[str, Any]] = {
    LogicalRole.LOCAL_OCR: {"transcription": "fixture", "anchors": [{"source_id": "page-1"}], "diagnostics": {}},
    LogicalRole.LOCAL_DISTILL: {"facts": [], "source_identity": "segment-1", "uncertainty": 0.0},
    LogicalRole.LOCAL_ANALYSIS: {"candidate": {}, "citations": ["anchor-1"], "abstain": False},
    LogicalRole.LOCAL_VERIFY: {"findings": [], "verdict": "PASS", "structural_only": True},
    LogicalRole.LOCAL_EMBED: {"vectors": [[1.0, 0.0]], "dimension": 2, "normalization": "L2_UNIT"},
    LogicalRole.LOCAL_RETRIEVE: {
        "candidates": [{"candidate_id": "c1", "score": 1.0}],
        "route": "BASELINE_NO_RERANKER",
        "fallback_receipt": {"used": False},
    },
}


ADAPTER_TYPES = {
    LogicalRole.LOCAL_OCR: OCRAdapter,
    LogicalRole.LOCAL_DISTILL: DistillAdapter,
    LogicalRole.LOCAL_ANALYSIS: AnalysisAdapter,
    LogicalRole.LOCAL_VERIFY: VerifyAdapter,
    LogicalRole.LOCAL_EMBED: EmbeddingAdapter,
    LogicalRole.LOCAL_RETRIEVE: RetrievalAdapter,
}


def build_broker(*, identity_admitted: bool = True, runner_override: Any = None) -> LocalCapabilityBroker:
    profiles = {
        role: LocalRoleProfile(
            profile_id=f"profile-{role.value.lower()}",
            logical_role=role,
            profile_revision="v1",
            runtime_asset_manifest_id="manifest-001",
            input_contract_version="v1",
            output_contract_version="v1",
            allowed_ownership_classes=tuple(OwnershipClass),
            resource_envelope_id="resource-001",
            same_model_independence="STRUCTURAL_ONLY" if role is LogicalRole.LOCAL_VERIFY else "NOT_INDEPENDENT",
            route_eligibility=0,
            activation_status=ActivationStatus.DISABLED,
            qualification_mode_allowed=True,
        )
        for role in LogicalRole
    }
    adapters = {}
    for role in LogicalRole:
        if runner_override is None:
            runner = lambda envelope, payload, role=role: OUTPUTS[role]
        else:
            runner = runner_override
        adapters[role] = ADAPTER_TYPES[role](runner)
    return LocalCapabilityBroker(
        manifest=manifest(),
        profiles=profiles,
        resource_envelopes={"resource-001": resource()},
        adapters=adapters,
        identity_admitted=identity_admitted,
        live_execution_authorized=False,
        security_ready=False,
        clock=lambda: "2026-08-18T00:00:00.000Z",
    )


def envelope(role: LogicalRole, *, mode: str = "OFFLINE_FAKE_A1", request_suffix: str = "1") -> LocalExecutionEnvelope:
    return LocalExecutionEnvelope(
        request_id=f"request-{role.value.lower()}-{request_suffix}",
        attempt=1,
        logical_role=role,
        profile_id=f"profile-{role.value.lower()}",
        ownership_identity={
            "identity_id": "owner-001",
            "ownership_class": "entrusted",
            "provenance_sha256": "B" * 64,
        },
        input_locator="sandbox://fixture/input",
        input_sha256="C" * 64,
        limits={"items": 1},
        timeout_seconds=30,
        max_attempts=1,
        allowed_roots=("G:/Memorive-sandbox/run",),
        allowed_processes=("broker-worker.exe",),
        network_policy="NO_RUNTIME_OFFLINE_FAKE" if mode == "OFFLINE_FAKE_A1" else "EXACT_PROCESS_NON_LOOPBACK_DENY",
        expected_output_schema="schema://role-v1",
        expected_output_schema_sha256="D" * 64,
        execution_mode=mode,
    )


def egress() -> LocalEgressEvidence:
    return LocalEgressEvidence(
        evidence_id="egress-001",
        execution_mode="OFFLINE_FAKE_A1",
        prevention_policy="NO_RUNTIME",
        prevention_active=False,
        observer_method="STATIC_INJECTED_RUNNER",
        observer_complete=True,
        window_started_at="2026-08-18T00:00:00Z",
        window_ended_at="2026-08-18T00:00:01Z",
        process_tree_identity_match=True,
        interface_set=(),
        dns_events=0,
        tcp_events=0,
        udp_events=0,
        http_events=0,
        localhost_request_count=0,
        forbidden_destination_count=0,
        unattributed_event_count=0,
        runtime_request_count=0,
        final_zero_egress_verdict="PASS_OFFLINE_NO_RUNTIME",
    )


def local_egress(mode: str = "LOCAL_QUALIFICATION_A") -> LocalEgressEvidence:
    return LocalEgressEvidence(
        evidence_id=f"egress-{mode.lower()}",
        execution_mode=mode,
        prevention_policy="EXACT_PROCESS_NON_LOOPBACK_DENY",
        prevention_active=True,
        observer_method="TEST_COMPLETE_PROCESS_OBSERVER",
        observer_complete=True,
        window_started_at="2026-08-20T00:00:00Z",
        window_ended_at="2026-08-20T00:00:01Z",
        process_tree_identity_match=True,
        interface_set=("loopback",),
        dns_events=0,
        tcp_events=0,
        udp_events=0,
        http_events=0,
        localhost_request_count=1,
        forbidden_destination_count=0,
        unattributed_event_count=0,
        runtime_request_count=1,
        final_zero_egress_verdict="PASS_ZERO_EGRESS",
    )


RESOURCE_SNAPSHOT = {
    "cpu_percent": 10,
    "gpu_memory_mib": 0,
    "ram_mib": 1000,
    "disk_free_mib": 10000,
    "active_concurrency": 0,
}


OPERATIONS = {
    LogicalRole.LOCAL_OCR: "vision_transcribe_page",
    LogicalRole.LOCAL_DISTILL: "distill_segment",
    LogicalRole.LOCAL_ANALYSIS: "analyze_context_pack",
    LogicalRole.LOCAL_VERIFY: "verify_analysis",
    LogicalRole.LOCAL_EMBED: "embed_text",
    LogicalRole.LOCAL_RETRIEVE: "retrieve_context",
}


@pytest.mark.parametrize("role", list(LogicalRole))
def test_six_broker_operations_run_only_through_injected_offline_adapters(role: LogicalRole) -> None:
    broker = build_broker()
    operation = getattr(broker, OPERATIONS[role])
    outcome = operation(envelope(role), {"fixture_id": "public-safe"}, resource_snapshot=RESOURCE_SNAPSHOT, egress_evidence=egress())
    assert outcome.output == OUTPUTS[role]
    assert outcome.receipt.final_status is ExecutionStatus.COMPLETED
    assert outcome.receipt.actual_runtime is None
    assert outcome.receipt.actual_model is None
    assert outcome.receipt.provider_cost_cny == "0"
    assert outcome.receipt.payload_persisted is False
    assert outcome.route_decision.cloud_eligibility is False
    assert broker.adapters[role].call_count == 1


def test_live_runtime_request_remains_fail_closed_and_never_reaches_adapter() -> None:
    broker = build_broker()
    role = LogicalRole.LOCAL_ANALYSIS
    outcome = broker.execute(
        envelope(role, mode="LOCAL_RUNTIME"),
        {"fixture_id": "x"},
        resource_snapshot=RESOURCE_SNAPSHOT,
        egress_evidence=egress(),
    )
    assert outcome.output is None
    assert outcome.receipt.final_status is ExecutionStatus.BLOCKED
    assert outcome.receipt.error_class == "LIVE_LOCAL_EXECUTION_NOT_AUTHORIZED"
    assert broker.adapters[role].call_count == 0


def test_identity_failure_returns_structured_receipt_without_worker_call() -> None:
    broker = build_broker(identity_admitted=False)
    role = LogicalRole.LOCAL_OCR
    outcome = broker.execute(envelope(role), {}, resource_snapshot=RESOURCE_SNAPSHOT, egress_evidence=egress())
    assert outcome.receipt.error_class == "RUNTIME_MODEL_IDENTITY_NOT_ADMITTED"
    assert outcome.route_decision.local_route_eligibility is False
    assert broker.adapters[role].call_count == 0


def test_missing_ownership_returns_fail_closed_decision_and_no_model_request() -> None:
    broker = build_broker()
    role = LogicalRole.LOCAL_DISTILL
    invalid = replace(envelope(role), ownership_identity={"ownership_class": "self"})
    outcome = broker.execute(invalid, {}, resource_snapshot=RESOURCE_SNAPSHOT, egress_evidence=egress())
    assert outcome.output is None
    assert outcome.receipt.error_class == "OWNERSHIP_IDENTITY_MISSING"
    assert outcome.route_decision.cloud_eligibility is False
    assert broker.adapters[role].call_count == 0


def test_injected_worker_failure_is_frozen_once_without_retry_or_fallback() -> None:
    calls = 0

    def fail(envelope: LocalExecutionEnvelope, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise TimeoutError("injected timeout")

    broker = build_broker(runner_override=fail)
    role = LogicalRole.LOCAL_VERIFY
    outcome = broker.execute(envelope(role), {}, resource_snapshot=RESOURCE_SNAPSHOT, egress_evidence=egress())
    assert calls == 1
    assert outcome.receipt.final_status is ExecutionStatus.ERROR
    assert outcome.receipt.error_class == "INJECTED_WORKER_ERROR:TimeoutError"
    assert outcome.receipt.fallback_path is None


def test_same_request_attempt_cannot_be_executed_twice() -> None:
    broker = build_broker()
    request = envelope(LogicalRole.LOCAL_EMBED)
    broker.execute(request, {}, resource_snapshot=RESOURCE_SNAPSHOT, egress_evidence=egress())
    with pytest.raises(LocalCapabilityError, match="DUPLICATE_REQUEST_ATTEMPT"):
        broker.execute(request, {}, resource_snapshot=RESOURCE_SNAPSHOT, egress_evidence=egress())


def test_resource_and_egress_failures_happen_before_adapter() -> None:
    broker = build_broker()
    role = LogicalRole.LOCAL_RETRIEVE
    poor_resource = dict(RESOURCE_SNAPSHOT, disk_free_mib=0)
    outcome = broker.execute(envelope(role, request_suffix="resource"), {}, resource_snapshot=poor_resource, egress_evidence=egress())
    assert outcome.receipt.error_class == "DISK_HEADROOM_INSUFFICIENT"
    assert broker.adapters[role].call_count == 0
    bad_egress = replace(egress(), process_tree_identity_match=False)
    outcome = broker.execute(envelope(role, request_suffix="egress"), {}, resource_snapshot=RESOURCE_SNAPSHOT, egress_evidence=bad_egress)
    assert outcome.receipt.error_class == "ZERO_EGRESS_NOT_PROVEN"
    assert broker.adapters[role].call_count == 0


@pytest.mark.parametrize("mode", ["LOCAL_QUALIFICATION_A", "LOCAL_ONLY_QUALIFICATION_B"])
def test_local_qualification_uses_role_specific_manifest_without_enabling_profile(mode: str) -> None:
    qwen = manifest()
    gemma = manifest(manifest_id="manifest-gemma", model_tag="gemma4:26b")
    profiles = {
        role: LocalRoleProfile(
            profile_id=f"profile-{role.value.lower()}",
            logical_role=role,
            profile_revision="v2",
            runtime_asset_manifest_id=(gemma.manifest_id if role is LogicalRole.LOCAL_ANALYSIS else qwen.manifest_id),
            input_contract_version="v1",
            output_contract_version="v1",
            allowed_ownership_classes=tuple(OwnershipClass),
            resource_envelope_id="resource-001",
            same_model_independence="NOT_INDEPENDENT",
            route_eligibility=0,
            activation_status=ActivationStatus.DISABLED,
            qualification_mode_allowed=True,
        )
        for role in LogicalRole
    }
    adapters = {
        role: ADAPTER_TYPES[role](lambda request, payload, role=role: OUTPUTS[role])
        for role in LogicalRole
    }
    broker = LocalCapabilityBroker(
        manifests={qwen.manifest_id: qwen, gemma.manifest_id: gemma},
        profiles=profiles,
        resource_envelopes={"resource-001": resource()},
        adapters=adapters,
        identity_admitted={qwen.manifest_id: True, gemma.manifest_id: True},
        live_execution_authorized=True,
        security_ready=True,
        clock=lambda: "2026-08-20T00:00:00.000Z",
    )

    outcome = broker.analyze_context_pack(
        envelope(LogicalRole.LOCAL_ANALYSIS, mode=mode),
        {"fixture_id": "public-safe"},
        resource_snapshot=RESOURCE_SNAPSHOT,
        egress_evidence=local_egress(mode),
    )

    assert outcome.output == OUTPUTS[LogicalRole.LOCAL_ANALYSIS]
    assert outcome.receipt.manifest_id == gemma.manifest_id
    assert outcome.receipt.requested_model == "gemma4:26b"
    assert outcome.receipt.actual_model == "gemma4:26b"
    assert profiles[LogicalRole.LOCAL_ANALYSIS].activation_status is ActivationStatus.DISABLED
    assert profiles[LogicalRole.LOCAL_ANALYSIS].route_eligibility == 0


def test_multimodel_broker_rejects_profile_with_unbound_manifest() -> None:
    profiles = {
        role: LocalRoleProfile(
            profile_id=f"profile-{role.value.lower()}",
            logical_role=role,
            profile_revision="v2",
            runtime_asset_manifest_id="missing-manifest",
            input_contract_version="v1",
            output_contract_version="v1",
            allowed_ownership_classes=tuple(OwnershipClass),
            resource_envelope_id="resource-001",
            route_eligibility=0,
            activation_status=ActivationStatus.DISABLED,
            qualification_mode_allowed=True,
        )
        for role in LogicalRole
    }
    adapters = {
        role: ADAPTER_TYPES[role](lambda request, payload, role=role: OUTPUTS[role])
        for role in LogicalRole
    }
    with pytest.raises(ValueError, match="missing runtime asset manifest"):
        LocalCapabilityBroker(
            manifests={"manifest-001": manifest()},
            profiles=profiles,
            resource_envelopes={"resource-001": resource()},
            adapters=adapters,
            identity_admitted={"manifest-001": True},
        )


def test_production_mode_stays_blocked_when_profile_is_disabled_even_with_local_authority() -> None:
    broker = build_broker()
    broker.live_execution_authorized = True
    broker.security_ready = True
    role = LogicalRole.LOCAL_ANALYSIS
    outcome = broker.execute(
        envelope(role, mode="LOCAL_RUNTIME"),
        {"fixture_id": "public-safe"},
        resource_snapshot=RESOURCE_SNAPSHOT,
        egress_evidence=local_egress("LOCAL_RUNTIME"),
    )
    assert outcome.output is None
    assert outcome.receipt.error_class == "PROFILE_DISABLED_OR_ROUTE_INELIGIBLE"
    assert broker.adapters[role].call_count == 0


def test_qualification_fails_closed_when_egress_mode_does_not_match_envelope() -> None:
    broker = build_broker()
    broker.live_execution_authorized = True
    broker.security_ready = True
    role = LogicalRole.LOCAL_OCR
    outcome = broker.execute(
        envelope(role, mode="LOCAL_QUALIFICATION_A"),
        {"fixture_id": "public-safe"},
        resource_snapshot=RESOURCE_SNAPSHOT,
        egress_evidence=local_egress("LOCAL_ONLY_QUALIFICATION_B"),
    )
    assert outcome.receipt.error_class == "EGRESS_EXECUTION_MODE_MISMATCH"
    assert broker.adapters[role].call_count == 0

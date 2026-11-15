from __future__ import annotations

from dataclasses import replace
import json

import pytest
from jsonschema import Draft202012Validator

from model_gateway.local_capability import ActivationStatus, CombinedLocalChainVerdict, ExecutionStatus, FileIdentity, LocalEgressEvidence, LocalExecutionEnvelope, LocalExecutionReceipt, LocalOwnershipRouteDecision, LocalResourceEnvelope, LocalRoleProfile, LocalRoleQualificationVerdict, LocalRuntimeAssetManifest, LogicalRole, OwnershipClass, QualificationStatus, combine_role_verdicts, validate_contract
from model_gateway.local_capability.contracts import SCHEMA_DIR
from model_gateway.local_capability.errors import ContractViolation
def file_identity(label: str, character: str = "A") -> FileIdentity:
    return FileIdentity(locator=f"C:/assets/{label}", sha256=character * 64, bytes=10)


def manifest() -> LocalRuntimeAssetManifest:
    return LocalRuntimeAssetManifest(
        manifest_id="manifest-001",
        runtime_name="ollama",
        runtime_version="0.12.10",
        executable=file_identity("ollama.exe", "A"),
        endpoint="http://127.0.0.1:11434/api/generate",
        launch_mode="USE_EXISTING_ONLY_NO_START_STOP_RESTART",
        allowed_parent_processes=("broker-worker.exe",),
        model_tag="qwen3.8:27b-q4_K_M",
        model_manifest=file_identity("manifest", "B"),
        model_blob=file_identity("blob", "C"),
        projector=file_identity("projector", "D"),
        source_locator="local://ollama/library/qwen3.8",
        acquired_date="2026-08-17",
        license_file=file_identity("license", "E"),
        license_verdict="APACHE_2_0_LOCAL_EVALUATION_CANDIDATE_ONLY",
        model_root="G:/Ollama/models",
        acl_policy="READ_ONLY_AFTER_SECURITY_PRECHECK",
        encryption_status="NOT_ASSESSED",
        backup_policy="USER_OWNED_NO_MUTATION",
        update_policy="NO_DOWNLOAD_NO_UPDATE",
        resource_baseline={"gpu": "frozen"},
    )


def profile(role: LogicalRole = LogicalRole.LOCAL_OCR) -> LocalRoleProfile:
    return LocalRoleProfile(
        profile_id=f"profile-{role.value.lower()}",
        logical_role=role,
        profile_revision="v1",
        runtime_asset_manifest_id="manifest-001",
        input_contract_version="v1",
        output_contract_version="v1",
        allowed_ownership_classes=tuple(OwnershipClass),
        resource_envelope_id="resource-001",
        same_model_independence="STRUCTURAL_ONLY" if role is LogicalRole.LOCAL_VERIFY else "NOT_INDEPENDENT",
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
        max_gpu_memory_mib=22000,
        max_ram_mib=64000,
        min_disk_free_mib=1024,
        timeout_seconds=120,
        max_attempts=1,
        reserved_headroom_percent=10,
        oom_fallback_profile_id="profile-small",
    )


def ownership_identity(kind: str = "entrusted") -> dict[str, object]:
    return {"identity_id": "owner-001", "ownership_class": kind, "provenance_sha256": "F" * 64}


def envelope(role: LogicalRole = LogicalRole.LOCAL_OCR) -> LocalExecutionEnvelope:
    return LocalExecutionEnvelope(
        request_id=f"request-{role.value.lower()}",
        attempt=1,
        logical_role=role,
        profile_id=f"profile-{role.value.lower()}",
        ownership_identity=ownership_identity(),
        input_locator="sandbox://fixture/input",
        input_sha256="1" * 64,
        limits={"items": 1},
        timeout_seconds=30,
        max_attempts=1,
        allowed_roots=("G:/Memorive-sandbox/run",),
        allowed_processes=("broker-worker.exe",),
        network_policy="NO_RUNTIME_OFFLINE_FAKE",
        expected_output_schema="schema://role-output-v1",
        expected_output_schema_sha256="2" * 64,
    )


def egress() -> LocalEgressEvidence:
    return LocalEgressEvidence(
        evidence_id="egress-001",
        execution_mode="OFFLINE_FAKE_A1",
        prevention_policy="NOT_ACTIVATED_NO_RUNTIME",
        prevention_active=False,
        observer_method="OFFLINE_STATIC_CALL_GRAPH_AND_INJECTED_RUNNER",
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


def receipt() -> LocalExecutionReceipt:
    return LocalExecutionReceipt(
        receipt_id="receipt-001",
        request_id="request-001",
        attempt=1,
        logical_role=LogicalRole.LOCAL_OCR,
        profile_id="profile-local_ocr",
        requested_runtime="ollama",
        actual_runtime=None,
        requested_model="qwen3.8:27b-q4_K_M",
        actual_model=None,
        manifest_id="manifest-001",
        started_at="2026-08-18T00:00:00Z",
        ended_at="2026-08-18T00:00:01Z",
        latency_ms=0,
        metrics={"pages": 1},
        resource_peaks={},
        schema_verdict="PASS",
        quality_verdict="PASS_OFFLINE_CONTRACT_ONLY",
        zero_egress_verdict="PASS_OFFLINE_NO_RUNTIME",
        output_locator="memory://output",
        output_sha256="3" * 64,
        error_class=None,
        fallback_path=None,
        final_status=ExecutionStatus.COMPLETED,
    )


def role_verdict(role: LogicalRole, verdict: QualificationStatus = QualificationStatus.QUALIFIED_LOCAL_ONLY) -> LocalRoleQualificationVerdict:
    return LocalRoleQualificationVerdict(
        logical_role=role,
        profile_id=f"profile-{role.value.lower()}",
        verdict=verdict,
        public_category="qualified" if verdict.value.startswith("QUALIFIED") else "keep-disabled",
        limits=(),
        route_eligibility=0,
        activation_status=ActivationStatus.DISABLED,
        evidence_refs=("sandbox://evidence",),
    )


def test_all_ten_schemas_are_strict_draft_2020_12() -> None:
    paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
    assert len(paths) == 10
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "value",
    [
        manifest(),
        profile(),
        envelope(),
        receipt(),
        LocalOwnershipRouteDecision(
            decision_id="decision-001",
            source_identity_id="owner-001",
            source_ownership=OwnershipClass.ENTRUSTED,
            provenance_sha256="F" * 64,
            effective_ownership=OwnershipClass.ENTRUSTED,
            merge_reason="DIRECT_IDENTITY",
            selected_role=LogicalRole.LOCAL_OCR,
            selected_profile_id="profile-local_ocr",
            fail_closed_reason=None,
            cloud_eligibility=False,
            local_route_eligibility=True,
        ),
        resource(),
        egress(),
        role_verdict(LogicalRole.LOCAL_OCR),
        CombinedLocalChainVerdict(
            chain_id="chain-001",
            verdict="FULL_LOCAL_CHAIN_QUALIFIED",
            role_verdicts=tuple(role_verdict(role) for role in LogicalRole),
            ownership_hard_gate="PASS",
            zero_egress_hard_gate="PASS",
        ),
    ],
)
def test_contracts_validate_against_their_schemas(value: object) -> None:
    assert validate_contract(value)


def test_manifest_cannot_claim_product_bundling() -> None:
    with pytest.raises(ContractViolation, match="PRODUCT_BUNDLING_NOT_ADMITTED"):
        validate_contract(replace(manifest(), product_bundling_allowed=True))


def test_envelope_cannot_persist_payload() -> None:
    with pytest.raises(ContractViolation):
        validate_contract(replace(envelope(), payload_persisted=True))


def test_receipt_cannot_claim_cloud_or_provider_cost() -> None:
    with pytest.raises(ContractViolation):
        validate_contract(replace(receipt(), cloud_eligibility=True))
    with pytest.raises(ContractViolation):
        validate_contract(replace(receipt(), provider_cost_cny="0.01"))


def test_chain_combination_never_averages_a_hard_blocker() -> None:
    verdicts = [role_verdict(role) for role in LogicalRole]
    verdicts[0] = role_verdict(verdicts[0].logical_role, QualificationStatus.BLOCKED_SECURITY)
    combined = combine_role_verdicts("chain-blocked", verdicts, ownership_hard_gate="PASS", zero_egress_hard_gate="PASS")
    assert combined.verdict == "LOCAL_CHAIN_BLOCKED_REQUIRED"
    assert combined.production_route == "KEEP_DISABLED"
    assert combined.activation_credit is False


def test_structural_only_verify_cannot_form_full_semantic_chain() -> None:
    verdicts = [role_verdict(role) for role in LogicalRole]
    index = next(index for index, row in enumerate(verdicts) if row.logical_role is LogicalRole.LOCAL_VERIFY)
    verdicts[index] = replace(verdicts[index], limits=("STRUCTURAL_ONLY",))
    combined = combine_role_verdicts("chain-limited", verdicts, ownership_hard_gate="PASS", zero_egress_hard_gate="PASS")
    assert combined.verdict == "PARTIAL_LOCAL_CHAIN_KEEP_DISABLED"

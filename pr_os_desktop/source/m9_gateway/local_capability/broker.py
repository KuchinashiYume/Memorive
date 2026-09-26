"""Process-local broker for six fail-closed local capability roles."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from .adapters import InjectedRoleAdapter
from .contracts import canonical_sha256, validate_contract
from .egress import evaluate_zero_egress
from .errors import LocalCapabilityError
from .ownership import require_local_route, route_ownership
from .receipts import ReceiptLedger
from .resource import admit_resource
from .types import (
    BrokerOutcome,
    ExecutionStatus,
    LocalEgressEvidence,
    LocalExecutionEnvelope,
    LocalExecutionReceipt,
    LocalOwnershipRouteDecision,
    LocalResourceEnvelope,
    LocalRoleProfile,
    LocalRuntimeAssetManifest,
    LogicalRole,
    OwnershipClass,
)


Clock = Callable[[], str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LocalCapabilityBroker:
    """The only process-local entry point for T11 role adapters.

    The default is deliberately inert: live execution requires both an explicit
    constructor authorization and a profile that is activated and route-eligible.
    Offline A1 uses injected workers and cannot identify an actual runtime/model.
    """

    def __init__(
        self,
        *,
        manifest: LocalRuntimeAssetManifest | None = None,
        manifests: Mapping[str, LocalRuntimeAssetManifest] | None = None,
        profiles: Mapping[LogicalRole, LocalRoleProfile],
        resource_envelopes: Mapping[str, LocalResourceEnvelope],
        adapters: Mapping[LogicalRole, InjectedRoleAdapter],
        identity_admitted: bool | Mapping[str, bool],
        live_execution_authorized: bool = False,
        security_ready: bool = False,
        receipt_ledger: ReceiptLedger | None = None,
        clock: Clock = utc_now,
    ) -> None:
        if (manifest is None) == (manifests is None):
            raise ValueError("provide exactly one of manifest or manifests")
        manifest_map = ({manifest.manifest_id: manifest} if manifest is not None else dict(manifests or {}))
        if not manifest_map:
            raise ValueError("broker requires at least one runtime asset manifest")
        for manifest_id, bound_manifest in manifest_map.items():
            validate_contract(bound_manifest)
            if manifest_id != bound_manifest.manifest_id:
                raise ValueError(f"runtime asset manifest key mismatch: {manifest_id}")
        if set(profiles) != set(LogicalRole) or set(adapters) != set(LogicalRole):
            raise ValueError("broker requires exactly six role profiles and adapters")
        for role, profile in profiles.items():
            validate_contract(profile)
            if role is not profile.logical_role:
                raise ValueError(f"profile role mismatch: {role.value}")
            if profile.runtime_asset_manifest_id not in manifest_map:
                raise ValueError(f"missing runtime asset manifest: {profile.runtime_asset_manifest_id}")
            if profile.resource_envelope_id not in resource_envelopes:
                raise ValueError(f"missing resource envelope: {profile.resource_envelope_id}")
        for envelope in resource_envelopes.values():
            validate_contract(envelope)
        self.manifest = manifest
        self.manifests = manifest_map
        self.profiles = dict(profiles)
        self.resource_envelopes = dict(resource_envelopes)
        self.adapters = dict(adapters)
        if isinstance(identity_admitted, bool):
            self.identity_admitted = {manifest_id: identity_admitted for manifest_id in manifest_map}
        else:
            admitted = dict(identity_admitted)
            if set(admitted) != set(manifest_map) or any(not isinstance(value, bool) for value in admitted.values()):
                raise ValueError("identity admission must bind every runtime asset manifest exactly once")
            self.identity_admitted = admitted
        self.live_execution_authorized = live_execution_authorized
        self.security_ready = security_ready
        self.receipts = receipt_ledger if receipt_ledger is not None else ReceiptLedger()
        self.clock = clock

    @staticmethod
    def _receipt_id(envelope: LocalExecutionEnvelope) -> str:
        return f"receipt-{canonical_sha256({'request_id': envelope.request_id, 'attempt': envelope.attempt})[:24].lower()}"

    @staticmethod
    def _blocked_decision(envelope: LocalExecutionEnvelope, code: str) -> LocalOwnershipRouteDecision:
        identity = envelope.ownership_identity
        try:
            source = OwnershipClass(str(identity.get("ownership_class", "unknown")).lower())
        except ValueError:
            source = OwnershipClass.UNKNOWN
        provenance = str(identity.get("provenance_sha256") or ("0" * 64)).upper()
        if len(provenance) != 64 or any(character not in "0123456789ABCDEF" for character in provenance):
            provenance = "0" * 64
        return LocalOwnershipRouteDecision(
            decision_id=f"decision-blocked-{canonical_sha256({'request': envelope.request_id, 'code': code})[:16].lower()}",
            source_identity_id=str(identity.get("identity_id") or "missing"),
            source_ownership=source,
            provenance_sha256=provenance,
            effective_ownership=OwnershipClass.UNKNOWN,
            merge_reason="FAIL_CLOSED_BEFORE_ROUTE",
            selected_role=envelope.logical_role,
            selected_profile_id=None,
            fail_closed_reason=code,
            cloud_eligibility=False,
            local_route_eligibility=False,
        )

    def _make_receipt(
        self,
        envelope: LocalExecutionEnvelope,
        *,
        manifest: LocalRuntimeAssetManifest,
        started_at: str,
        ended_at: str,
        status: ExecutionStatus,
        egress_verdict: str,
        schema_verdict: str,
        quality_verdict: str,
        metrics: Mapping[str, int | float | str] | None = None,
        resource_peaks: Mapping[str, int | float | str] | None = None,
        output_locator: str | None = None,
        output_sha256: str | None = None,
        error_class: str | None = None,
        fallback_path: str | None = None,
    ) -> LocalExecutionReceipt:
        receipt = LocalExecutionReceipt(
            receipt_id=self._receipt_id(envelope),
            request_id=envelope.request_id,
            attempt=envelope.attempt,
            logical_role=envelope.logical_role,
            profile_id=envelope.profile_id,
            requested_runtime=manifest.runtime_name,
            actual_runtime=None if envelope.execution_mode == "OFFLINE_FAKE_A1" else manifest.runtime_name,
            requested_model=manifest.model_tag,
            actual_model=None if envelope.execution_mode == "OFFLINE_FAKE_A1" else manifest.model_tag,
            manifest_id=manifest.manifest_id,
            started_at=started_at,
            ended_at=ended_at,
            latency_ms=0,
            metrics=dict(metrics or {}),
            resource_peaks=dict(resource_peaks or {}),
            schema_verdict=schema_verdict,
            quality_verdict=quality_verdict,
            zero_egress_verdict=egress_verdict,
            output_locator=output_locator,
            output_sha256=output_sha256,
            error_class=error_class,
            fallback_path=fallback_path,
            final_status=status,
            cloud_eligibility=False,
            payload_persisted=False,
            provider_cost_cny="0",
        )
        self.receipts.record(receipt)
        return receipt

    def execute(
        self,
        envelope: LocalExecutionEnvelope,
        payload: Mapping[str, Any],
        *,
        resource_snapshot: Mapping[str, Any],
        egress_evidence: LocalEgressEvidence,
    ) -> BrokerOutcome:
        validate_contract(envelope)
        receipt_id = self._receipt_id(envelope)
        if self.receipts.get(receipt_id) is not None:
            raise LocalCapabilityError(
                "DUPLICATE_REQUEST_ATTEMPT",
                plane="OUTPUT",
                details={"receipt_id": receipt_id},
            )
        started = self.clock()
        decision: LocalOwnershipRouteDecision | None = None
        profile = self.profiles[envelope.logical_role]
        manifest = self.manifests[profile.runtime_asset_manifest_id]
        try:
            if envelope.profile_id != profile.profile_id:
                raise LocalCapabilityError("PROFILE_ID_MISMATCH", plane="IDENTITY")
            if not self.identity_admitted[manifest.manifest_id]:
                raise LocalCapabilityError("RUNTIME_MODEL_IDENTITY_NOT_ADMITTED", plane="IDENTITY")
            qualification_mode = envelope.execution_mode in {
                "OFFLINE_FAKE_A1",
                "LOCAL_QUALIFICATION_A",
                "LOCAL_ONLY_QUALIFICATION_B",
            }
            runtime_request_mode = envelope.execution_mode != "OFFLINE_FAKE_A1"
            if runtime_request_mode and (not self.live_execution_authorized or not self.security_ready):
                raise LocalCapabilityError("LIVE_LOCAL_EXECUTION_NOT_AUTHORIZED", plane="OBSERVATION")
            decision = route_ownership(envelope.ownership_identity, profile, qualification_mode=qualification_mode)
            require_local_route(decision, envelope.logical_role)
            resource = self.resource_envelopes[profile.resource_envelope_id]
            admit_resource(resource, resource_snapshot)
            if egress_evidence.execution_mode != envelope.execution_mode:
                raise LocalCapabilityError("EGRESS_EXECUTION_MODE_MISMATCH", plane="OBSERVATION")
            egress = evaluate_zero_egress(egress_evidence)
            result = self.adapters[envelope.logical_role].execute(envelope, payload)
            output_hash = result.output_sha256 or canonical_sha256(result.output)
            output_locator = result.output_locator or f"memory://p06-t11/{envelope.request_id}/attempt-{envelope.attempt}"
            ended = self.clock()
            receipt = self._make_receipt(
                envelope,
                manifest=manifest,
                started_at=started,
                ended_at=ended,
                status=ExecutionStatus.COMPLETED,
                egress_verdict=str(egress["result"]),
                schema_verdict="PASS",
                quality_verdict=(
                    "PASS_OFFLINE_CONTRACT_ONLY"
                    if envelope.execution_mode == "OFFLINE_FAKE_A1"
                    else "PASS_LOCAL_QUALIFICATION_CONTRACT"
                    if qualification_mode
                    else "PASS"
                ),
                metrics=result.metrics,
                resource_peaks=result.resource_peaks,
                output_locator=output_locator,
                output_sha256=output_hash,
            )
            return BrokerOutcome(decision, receipt, result.output)
        except LocalCapabilityError as exc:
            decision = decision or self._blocked_decision(envelope, exc.code)
            ended = self.clock()
            receipt = self._make_receipt(
                envelope,
                manifest=manifest,
                started_at=started,
                ended_at=ended,
                status=ExecutionStatus.BLOCKED if exc.plane in {"IDENTITY", "OBSERVATION"} else ExecutionStatus.ERROR,
                egress_verdict="NOT_ASSESSED_NO_RUNTIME_REQUEST",
                schema_verdict="FAIL" if exc.plane == "OUTPUT" else "NOT_ASSESSED",
                quality_verdict="NOT_ASSESSED",
                error_class=exc.code,
            )
            return BrokerOutcome(decision, receipt, None)
        except Exception as exc:  # injected worker failures must be frozen, never retried or sent elsewhere
            decision = decision or self._blocked_decision(envelope, "INJECTED_WORKER_ERROR")
            ended = self.clock()
            receipt = self._make_receipt(
                envelope,
                manifest=manifest,
                started_at=started,
                ended_at=ended,
                status=ExecutionStatus.ERROR,
                egress_verdict="NOT_ASSESSED_AFTER_INJECTED_ERROR",
                schema_verdict="NOT_ASSESSED",
                quality_verdict="NOT_ASSESSED",
                error_class=f"INJECTED_WORKER_ERROR:{type(exc).__name__}",
            )
            return BrokerOutcome(decision, receipt, None)

    def vision_transcribe_page(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any], **kwargs: Any) -> BrokerOutcome:
        return self._role_execute(LogicalRole.LOCAL_OCR, envelope, payload, kwargs)

    def distill_segment(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any], **kwargs: Any) -> BrokerOutcome:
        return self._role_execute(LogicalRole.LOCAL_DISTILL, envelope, payload, kwargs)

    def analyze_context_pack(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any], **kwargs: Any) -> BrokerOutcome:
        return self._role_execute(LogicalRole.LOCAL_ANALYSIS, envelope, payload, kwargs)

    def verify_analysis(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any], **kwargs: Any) -> BrokerOutcome:
        return self._role_execute(LogicalRole.LOCAL_VERIFY, envelope, payload, kwargs)

    def embed_text(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any], **kwargs: Any) -> BrokerOutcome:
        return self._role_execute(LogicalRole.LOCAL_EMBED, envelope, payload, kwargs)

    def retrieve_context(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any], **kwargs: Any) -> BrokerOutcome:
        return self._role_execute(LogicalRole.LOCAL_RETRIEVE, envelope, payload, kwargs)

    def _role_execute(
        self,
        role: LogicalRole,
        envelope: LocalExecutionEnvelope,
        payload: Mapping[str, Any],
        kwargs: Mapping[str, Any],
    ) -> BrokerOutcome:
        if envelope.logical_role is not role:
            raise LocalCapabilityError("BROKER_OPERATION_ROLE_MISMATCH", plane="IDENTITY")
        return self.execute(
            envelope,
            payload,
            resource_snapshot=kwargs["resource_snapshot"],
            egress_evidence=kwargs["egress_evidence"],
        )

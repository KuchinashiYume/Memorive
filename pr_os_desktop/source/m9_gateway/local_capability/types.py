"""Thin, immutable object contracts for P06/T11.

The objects intentionally carry locators, hashes and bounded measurements. They
never carry a source document, image, prompt, model output, credential or Gold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class LogicalRole(str, Enum):
    LOCAL_OCR = "LOCAL_OCR"
    LOCAL_DISTILL = "LOCAL_DISTILL"
    LOCAL_ANALYSIS = "LOCAL_ANALYSIS"
    LOCAL_VERIFY = "LOCAL_VERIFY"
    LOCAL_EMBED = "LOCAL_EMBED"
    LOCAL_RETRIEVE = "LOCAL_RETRIEVE"


class OwnershipClass(str, Enum):
    SELF = "self"
    ENTRUSTED = "entrusted"
    UNKNOWN = "unknown"
    MIXED = "mixed"


class ActivationStatus(str, Enum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"


class QualificationStatus(str, Enum):
    QUALIFIED_LOCAL_ONLY = "QUALIFIED_LOCAL_ONLY"
    QUALIFIED_WITH_LIMITS = "QUALIFIED_WITH_LIMITS"
    KEEP_DISABLED_QUALITY = "KEEP_DISABLED_QUALITY"
    KEEP_DISABLED_RESOURCE = "KEEP_DISABLED_RESOURCE"
    BLOCKED_IDENTITY = "BLOCKED_IDENTITY"
    BLOCKED_SECURITY = "BLOCKED_SECURITY"
    NOT_ASSESSED_UPSTREAM = "NOT_ASSESSED_UPSTREAM"


class ExecutionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


def to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return to_primitive(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [to_primitive(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FileIdentity:
    locator: str
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalRuntimeAssetManifest:
    manifest_id: str
    runtime_name: str
    runtime_version: str
    executable: FileIdentity
    endpoint: str
    launch_mode: str
    allowed_parent_processes: tuple[str, ...]
    model_tag: str
    model_manifest: FileIdentity
    model_blob: FileIdentity
    projector: FileIdentity | None
    source_locator: str
    acquired_date: str
    license_file: FileIdentity
    license_verdict: str
    model_root: str
    acl_policy: str
    encryption_status: str
    backup_policy: str
    update_policy: str
    resource_baseline: Mapping[str, Any]
    product_bundling_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalRoleProfile:
    profile_id: str
    logical_role: LogicalRole
    profile_revision: str
    runtime_asset_manifest_id: str
    input_contract_version: str
    output_contract_version: str
    allowed_ownership_classes: tuple[OwnershipClass, ...]
    resource_envelope_id: str
    allowed_deterministic_fallbacks: tuple[str, ...] = ()
    same_model_independence: str = "NOT_INDEPENDENT"
    route_eligibility: int = 0
    activation_status: ActivationStatus = ActivationStatus.DISABLED
    qualification_mode_allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalExecutionEnvelope:
    request_id: str
    attempt: int
    logical_role: LogicalRole
    profile_id: str
    ownership_identity: Mapping[str, Any]
    input_locator: str
    input_sha256: str
    limits: Mapping[str, int]
    timeout_seconds: int
    max_attempts: int
    allowed_roots: tuple[str, ...]
    allowed_processes: tuple[str, ...]
    network_policy: str
    expected_output_schema: str
    expected_output_schema_sha256: str
    execution_mode: str = "OFFLINE_FAKE_A1"
    payload_persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalExecutionReceipt:
    receipt_id: str
    request_id: str
    attempt: int
    logical_role: LogicalRole
    profile_id: str
    requested_runtime: str
    actual_runtime: str | None
    requested_model: str
    actual_model: str | None
    manifest_id: str
    started_at: str
    ended_at: str
    latency_ms: int
    metrics: Mapping[str, int | float | str]
    resource_peaks: Mapping[str, int | float | str]
    schema_verdict: str
    quality_verdict: str
    zero_egress_verdict: str
    output_locator: str | None
    output_sha256: str | None
    error_class: str | None
    fallback_path: str | None
    final_status: ExecutionStatus
    cloud_eligibility: bool = False
    payload_persisted: bool = False
    provider_cost_cny: str = "0"

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalOwnershipRouteDecision:
    decision_id: str
    source_identity_id: str
    source_ownership: OwnershipClass
    provenance_sha256: str
    effective_ownership: OwnershipClass
    merge_reason: str
    selected_role: LogicalRole
    selected_profile_id: str | None
    fail_closed_reason: str | None
    cloud_eligibility: bool
    local_route_eligibility: bool
    decision_policy_revision: str = "P06_T11_OWNERSHIP_ROUTE_V1"

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalResourceEnvelope:
    envelope_id: str
    max_context_tokens: int
    max_pages: int
    max_image_pixels: int
    max_batch_size: int
    max_concurrency: int
    max_cpu_percent: int
    max_gpu_memory_mib: int
    max_ram_mib: int
    min_disk_free_mib: int
    timeout_seconds: int
    max_attempts: int
    reserved_headroom_percent: int
    oom_fallback_profile_id: str | None
    automatic_expansion_allowed: bool = False
    implicit_truncation_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalEgressEvidence:
    evidence_id: str
    execution_mode: str
    prevention_policy: str
    prevention_active: bool
    observer_method: str
    observer_complete: bool
    window_started_at: str
    window_ended_at: str
    process_tree_identity_match: bool
    interface_set: tuple[str, ...]
    dns_events: int
    tcp_events: int
    udp_events: int
    http_events: int
    localhost_request_count: int
    forbidden_destination_count: int
    unattributed_event_count: int
    runtime_request_count: int
    final_zero_egress_verdict: str

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class LocalRoleQualificationVerdict:
    logical_role: LogicalRole
    profile_id: str
    verdict: QualificationStatus
    public_category: str
    limits: tuple[str, ...]
    route_eligibility: int
    activation_status: ActivationStatus
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class CombinedLocalChainVerdict:
    chain_id: str
    verdict: str
    role_verdicts: tuple[LocalRoleQualificationVerdict, ...]
    ownership_hard_gate: str
    zero_egress_hard_gate: str
    production_route: str = "KEEP_DISABLED"
    activation_credit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    output: Mapping[str, Any]
    metrics: Mapping[str, int | float | str] = field(default_factory=dict)
    resource_peaks: Mapping[str, int | float | str] = field(default_factory=dict)
    output_locator: str | None = None
    output_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class BrokerOutcome:
    route_decision: LocalOwnershipRouteDecision
    receipt: LocalExecutionReceipt
    output: Mapping[str, Any] | None

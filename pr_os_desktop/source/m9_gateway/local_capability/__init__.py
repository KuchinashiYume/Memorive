"""P06/T11 local capability broker (disabled-by-default candidate)."""

from .broker import LocalCapabilityBroker
from .contracts import canonical_sha256, combine_role_verdicts, contract_schema_sha256, validate_contract
from .diagnostics import local_diagnose, local_preflight, local_status
from .errors import LocalCapabilityError
from .receipts import ReceiptLedger
from .t13_retrieval import T13MultimodalRetrievalRunner
from .types import (
    ActivationStatus,
    AdapterResult,
    BrokerOutcome,
    CombinedLocalChainVerdict,
    ExecutionStatus,
    FileIdentity,
    LocalEgressEvidence,
    LocalExecutionEnvelope,
    LocalExecutionReceipt,
    LocalOwnershipRouteDecision,
    LocalResourceEnvelope,
    LocalRoleProfile,
    LocalRoleQualificationVerdict,
    LocalRuntimeAssetManifest,
    LogicalRole,
    OwnershipClass,
    QualificationStatus,
)

__all__ = [
    "ActivationStatus",
    "AdapterResult",
    "BrokerOutcome",
    "CombinedLocalChainVerdict",
    "ExecutionStatus",
    "FileIdentity",
    "LocalCapabilityBroker",
    "LocalCapabilityError",
    "LocalEgressEvidence",
    "LocalExecutionEnvelope",
    "LocalExecutionReceipt",
    "LocalOwnershipRouteDecision",
    "LocalResourceEnvelope",
    "LocalRoleProfile",
    "LocalRoleQualificationVerdict",
    "LocalRuntimeAssetManifest",
    "LogicalRole",
    "OwnershipClass",
    "QualificationStatus",
    "ReceiptLedger",
    "T13MultimodalRetrievalRunner",
    "canonical_sha256",
    "combine_role_verdicts",
    "contract_schema_sha256",
    "local_diagnose",
    "local_preflight",
    "local_status",
    "validate_contract",
]

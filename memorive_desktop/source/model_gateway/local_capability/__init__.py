"""LOCAL-INFERENCE local capability broker (disabled-by-default candidate)."""

from .broker import LocalCapabilityBroker
from .contracts import canonical_sha256, combine_role_verdicts, contract_schema_sha256, validate_contract
from .diagnostics import local_diagnose, local_preflight, local_status
from .errors import LocalCapabilityError
from .receipts import ReceiptLedger
from .installation_retrieval import InstallationMultimodalRetrievalRunner
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
    "InstallationMultimodalRetrievalRunner",
    "canonical_sha256",
    "combine_role_verdicts",
    "contract_schema_sha256",
    "local_diagnose",
    "local_preflight",
    "local_status",
    "validate_contract",
]

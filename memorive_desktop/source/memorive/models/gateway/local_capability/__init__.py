"""The local capability broker: which local model may take which role (OCR, embedding, retrieval,
distillation, analysis, verification), with its receipts and diagnostics. Off by default and not
reached from the app's own code; python -m memorive.models.gateway.local_capability shows its status.
"""

from memorive.models.gateway.local_capability.broker import LocalCapabilityBroker
from memorive.models.gateway.local_capability.contracts import canonical_sha256, combine_role_verdicts, contract_schema_sha256, validate_contract
from memorive.models.gateway.local_capability.diagnostics import local_diagnose, local_preflight, local_status
from memorive.models.gateway.local_capability.errors import LocalCapabilityError
from memorive.models.gateway.local_capability.receipts import ReceiptLedger
from memorive.models.gateway.local_capability.installation_retrieval import InstallationMultimodalRetrievalRunner
from memorive.models.gateway.local_capability.types import (
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

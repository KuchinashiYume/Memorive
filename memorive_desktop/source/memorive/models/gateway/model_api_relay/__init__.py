"""Calling a model through another API endpoint or relay: endpoint profiles, protocol adapters, route
planning and receipts (SafeRelayClient). Off by default; nothing in the app calls it (see
DEVELOPING.md).

It does not mutate ``models.yaml`` or activate a production route.
"""

from memorive.models.gateway.model_api_relay.contracts import (
    EndpointProfileCatalog,
    RelayContractError,
    canonical_sha256,
    validate_credential_binding,
    validate_endpoint_profile,
    validate_model_alias_binding,
    validate_protocol_manifest,
)
from memorive.models.gateway.model_api_relay.client import SafeRelayClient
from memorive.models.gateway.model_api_relay.evidence import (
    AttemptLedger,
    build_presend_blocked_receipt,
    build_relay_attempt_receipt,
    project_runtime_log_relay_extension,
)
from memorive.models.gateway.model_api_relay.media import prepare_public_synthetic_ocr
from memorive.models.gateway.model_api_relay.protocols import RelayProtocolRegistry
from memorive.models.gateway.model_api_relay.qualification import (
    build_capability_loss_map,
    build_compatibility_report,
    build_relay_qualification_key,
    classify_backend_identity,
    evaluate_qualification,
)
from memorive.models.gateway.model_api_relay.routing import ProfiledRouter, RelayRoutePlanner

__all__ = [
    "EndpointProfileCatalog",
    "AttemptLedger",
    "ProfiledRouter",
    "RelayContractError",
    "RelayProtocolRegistry",
    "RelayRoutePlanner",
    "SafeRelayClient",
    "build_relay_attempt_receipt",
    "build_presend_blocked_receipt",
    "build_capability_loss_map",
    "build_compatibility_report",
    "build_relay_qualification_key",
    "canonical_sha256",
    "classify_backend_identity",
    "evaluate_qualification",
    "prepare_public_synthetic_ocr",
    "project_runtime_log_relay_extension",
    "validate_credential_binding",
    "validate_endpoint_profile",
    "validate_model_alias_binding",
    "validate_protocol_manifest",
]

"""P05/T12 model API endpoint-profile and relay interchangeability seam.

The package is inactive by default.  It defines a profile control plane,
protocol adapters, explicit route planning and public-safe evidence projection;
it does not mutate ``models.yaml`` or activate a production route.
"""

from .contracts import (
    EndpointProfileCatalog,
    RelayContractError,
    canonical_sha256,
    validate_credential_binding,
    validate_endpoint_profile,
    validate_model_alias_binding,
    validate_protocol_manifest,
)
from .client import SafeRelayClient
from .evidence import (
    AttemptLedger,
    build_presend_blocked_receipt,
    build_relay_attempt_receipt,
    project_m11_relay_extension,
)
from .media import prepare_public_synthetic_ocr
from .protocols import RelayProtocolRegistry
from .qualification import (
    build_capability_loss_map,
    build_compatibility_report,
    build_relay_qualification_key,
    classify_backend_identity,
    evaluate_qualification,
)
from .routing import ProfiledRouter, RelayRoutePlanner

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
    "project_m11_relay_extension",
    "validate_credential_binding",
    "validate_endpoint_profile",
    "validate_model_alias_binding",
    "validate_protocol_manifest",
]

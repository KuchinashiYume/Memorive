"""P07/T08 ordinary model-configuration contracts and offline resolvers."""

from .config_merge import merge_configuration, validate_advanced_config, validate_ordinary_config
from .connection import FakeProvider, check_connection, connection_not_run_receipt
from .contracts import (
    ModelConfigurationError,
    TIER_IDS,
    validate_connection_receipt,
    validate_credential_binding,
    validate_model_profile,
    validate_provider_preset,
    validate_tier_policy,
)
from .credential import InMemoryFakeCredentialStore
from .errors import ERROR_CODE_CATALOG, configuration_error_envelope
from .legacy import legacy_env_name_reference
from .redaction import assert_forbidden_values_absent, redact_public
from .resolver import TierResolver

__all__ = [
    "FakeProvider",
    "ERROR_CODE_CATALOG",
    "InMemoryFakeCredentialStore",
    "ModelConfigurationError",
    "TIER_IDS",
    "TierResolver",
    "assert_forbidden_values_absent",
    "check_connection",
    "configuration_error_envelope",
    "connection_not_run_receipt",
    "legacy_env_name_reference",
    "merge_configuration",
    "redact_public",
    "validate_advanced_config",
    "validate_connection_receipt",
    "validate_credential_binding",
    "validate_model_profile",
    "validate_ordinary_config",
    "validate_provider_preset",
    "validate_tier_policy",
]

"""MODEL-CONFIGURATION ordinary model-configuration contracts and offline resolvers."""

from memorive.models.gateway.model_configuration.config_merge import merge_configuration, validate_advanced_config, validate_ordinary_config
from memorive.models.gateway.model_configuration.connection import FakeProvider, check_connection, connection_not_run_receipt
from memorive.models.gateway.model_configuration.contracts import (
    ModelConfigurationError,
    TIER_IDS,
    validate_connection_receipt,
    validate_credential_binding,
    validate_model_profile,
    validate_provider_preset,
    validate_tier_policy,
)
from memorive.models.gateway.model_configuration.credential import InMemoryFakeCredentialStore
from memorive.models.gateway.model_configuration.errors import ERROR_CODE_CATALOG, configuration_error_envelope
from memorive.models.gateway.model_configuration.legacy import legacy_env_name_reference
from memorive.models.gateway.model_configuration.redaction import assert_forbidden_values_absent, redact_public
from memorive.models.gateway.model_configuration.resolver import TierResolver

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

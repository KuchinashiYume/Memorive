from __future__ import annotations

from dataclasses import dataclass

from .contracts import ModelConfigurationError, validate_connection_receipt, validate_provider_preset
from .credential import InMemoryFakeCredentialStore


@dataclass(frozen=True)
class FakeProvider:
    provider_id: str
    scenario: str = "success"
    returned_model_alias: str | None = None

    def check(self, requested_model_alias: str) -> tuple[str | None, str, bool]:
        if self.scenario == "success":
            return self.returned_model_alias or requested_model_alias, "CONNECTION_OK", False
        if self.scenario == "auth_failure":
            return None, "AUTHENTICATION_FAILED", False
        if self.scenario == "timeout":
            return None, "CONNECTION_TIMEOUT", True
        if self.scenario == "malformed":
            return None, "MALFORMED_PROVIDER_RESPONSE", False
        if self.scenario == "identity_drift":
            return self.returned_model_alias or "unexpected-model", "RETURNED_MODEL_IDENTITY_DRIFT", False
        raise ModelConfigurationError("FAKE_PROVIDER_SCENARIO_UNKNOWN")


def check_connection(
    *,
    store: InMemoryFakeCredentialStore,
    credential_ref: str,
    provider_preset: dict,
    provider: FakeProvider,
    requested_model_alias: str,
    check_id: str,
    checked_at: str,
) -> dict:
    preset = validate_provider_preset(provider_preset)
    binding = store.metadata(credential_ref)
    if binding["provider_id"] != preset["provider_id"] or provider.provider_id != preset["provider_id"]:
        raise ModelConfigurationError("PROVIDER_PRESET_CREDENTIAL_MISMATCH")
    if binding["state"] == "REVOKED" or not store.has_secret(credential_ref):
        raise ModelConfigurationError("CREDENTIAL_REVOKED")
    returned, reason, retryable = provider.check(requested_model_alias)
    verified = reason == "CONNECTION_OK" and returned == requested_model_alias
    if reason == "CONNECTION_OK" and returned != requested_model_alias:
        reason = "RETURNED_MODEL_IDENTITY_DRIFT"
        verified = False
    store.mark_connection(credential_ref, verified=verified)
    return validate_connection_receipt(
        {
            "schema_version": "P07_T08_CONNECTION_CHECK_RECEIPT_V1",
            "check_id": check_id,
            "provider_id": preset["provider_id"],
            "credential_ref": credential_ref,
            "requested_model_alias": requested_model_alias,
            "returned_model_alias": returned,
            "key_state": "KEY_SAVED",
            "connection_state": "CONNECTION_VERIFIED" if verified else "CONNECTION_FAILED",
            "qualification_state": "NOT_ASSESSED",
            "enabled_state": "DISABLED",
            "reason_code": reason,
            "retryable": retryable,
            "checked_at": checked_at,
            "network_requests": 0,
            "external_model_requests": 0,
        }
    )


def connection_not_run_receipt(
    *,
    credential_ref: str,
    provider_id: str,
    requested_model_alias: str,
    check_id: str,
    recorded_at: str,
) -> dict:
    return validate_connection_receipt(
        {
            "schema_version": "P07_T08_CONNECTION_CHECK_RECEIPT_V1",
            "check_id": check_id,
            "provider_id": provider_id,
            "credential_ref": credential_ref,
            "requested_model_alias": requested_model_alias,
            "returned_model_alias": None,
            "key_state": "KEY_SAVED",
            "connection_state": "VERIFICATION_NOT_RUN",
            "qualification_state": "NOT_ASSESSED",
            "enabled_state": "DISABLED",
            "reason_code": "CONNECTION_NOT_VERIFIED",
            "retryable": False,
            "checked_at": recorded_at,
            "network_requests": 0,
            "external_model_requests": 0,
        }
    )


__all__ = ["FakeProvider", "check_connection", "connection_not_run_receipt"]

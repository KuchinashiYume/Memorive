from __future__ import annotations

import hashlib
import re
from typing import Any

from .contracts import ModelConfigurationError, validate_credential_binding


BINDING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class InMemoryFakeCredentialStore:
    """Fake-only store used by Analysis; secret bytes are hashed and immediately discarded."""

    backend_type = "FAKE_IN_MEMORY"

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._digests: dict[str, str] = {}
        self._versions: dict[str, int] = {}

    def save(
        self,
        *,
        binding_id: str,
        provider_id: str,
        secret_value: str,
        account_label: str | None,
        created_at: str,
    ) -> dict[str, Any]:
        if not BINDING_RE.fullmatch(binding_id):
            raise ModelConfigurationError("BINDING_ID_INVALID")
        if not isinstance(secret_value, str) or len(secret_value) < 8:
            raise ModelConfigurationError("FAKE_SECRET_VALUE_INVALID")
        active = [
            item
            for item in self._records.values()
            if item["binding_id"] == binding_id and item["secret_present"]
        ]
        if active:
            raise ModelConfigurationError("CREDENTIAL_REPLACE_REQUIRED")
        version = self._versions.get(binding_id, 0) + 1
        self._versions[binding_id] = version
        ref = f"REF:FAKE:{binding_id}:v{version}"
        record = {
            "schema_version": "MODEL_CONFIGURATION_CREDENTIAL_BINDING_V1",
            "binding_id": binding_id,
            "revision": f"v{version}",
            "provider_id": provider_id,
            "credential_ref": ref,
            "account_label": account_label,
            "state": "STORED_UNVERIFIED",
            "backend_type": self.backend_type,
            "secret_present": True,
            "created_at": created_at,
            "rotated_at": None,
            "revoked_at": None,
            "previous_credential_ref": None,
        }
        self._digests[ref] = hashlib.sha256(secret_value.encode("utf-8")).hexdigest().upper()
        self._records[ref] = validate_credential_binding(record)
        return self.metadata(ref)

    def replace(self, credential_ref: str, *, secret_value: str, rotated_at: str) -> dict[str, Any]:
        current = self._require_active(credential_ref)
        old = dict(current)
        old["state"] = "REPLACED"
        old["secret_present"] = False
        old["rotated_at"] = rotated_at
        self._records[credential_ref] = validate_credential_binding(old)
        self._digests.pop(credential_ref, None)
        replacement = self.save(
            binding_id=current["binding_id"],
            provider_id=current["provider_id"],
            secret_value=secret_value,
            account_label=current["account_label"],
            created_at=rotated_at,
        )
        updated = dict(replacement)
        updated["previous_credential_ref"] = credential_ref
        updated["rotated_at"] = rotated_at
        self._records[updated["credential_ref"]] = validate_credential_binding(updated)
        return self.metadata(updated["credential_ref"])

    def revoke(self, credential_ref: str, *, revoked_at: str) -> dict[str, Any]:
        current = self._require_active(credential_ref)
        updated = dict(current)
        updated["state"] = "REVOKED"
        updated["secret_present"] = False
        updated["revoked_at"] = revoked_at
        self._digests.pop(credential_ref, None)
        self._records[credential_ref] = validate_credential_binding(updated)
        return self.metadata(credential_ref)

    def mark_connection(self, credential_ref: str, *, verified: bool) -> dict[str, Any]:
        current = self._require_active(credential_ref)
        updated = dict(current)
        updated["state"] = "CONNECTION_VERIFIED" if verified else "CONNECTION_FAILED"
        self._records[credential_ref] = validate_credential_binding(updated)
        return self.metadata(credential_ref)

    def metadata(self, credential_ref: str) -> dict[str, Any]:
        try:
            return dict(self._records[credential_ref])
        except KeyError as exc:
            raise ModelConfigurationError("CREDENTIAL_REF_MISSING") from exc

    def has_secret(self, credential_ref: str) -> bool:
        return credential_ref in self._digests

    def _require_active(self, credential_ref: str) -> dict[str, Any]:
        current = self.metadata(credential_ref)
        if current["state"] in {"REPLACED", "REVOKED"} or not current["secret_present"]:
            raise ModelConfigurationError("CREDENTIAL_REVOKED")
        return current


__all__ = ["InMemoryFakeCredentialStore"]

from __future__ import annotations

import ctypes
import hashlib
import re
from typing import Any

from memorive_app.credentials import WindowsCredentialStore

from .contracts import REFERENCE, utc_now


SLUG = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
CUSTOM_PROVIDER_PREFIX = "custom-v1-"


class SyntheticDiscardCredentialBackend:
    def __init__(self):
        self._present: set[str] = set()
        self.write_count = 0
        self.delete_count = 0
        self.read_count = 0

    def write(self, target: str, secret: bytes) -> None:
        if not secret:
            raise ValueError("CREDENTIAL_INPUT_EMPTY")
        self._present.add(target)
        self.write_count += 1

    def read(self, target: str) -> bytes:
        self.read_count += 1
        raise PermissionError("CREDENTIAL_VALUE_READ_FORBIDDEN")

    def delete(self, target: str) -> None:
        self._present.discard(target)
        self.delete_count += 1

    def has(self, target: str) -> bool:
        return target in self._present

    def exists(self, target: str) -> bool:
        return self.has(target)

    def drop_for_test(self, target: str) -> None:
        self._present.discard(target)


class CredentialReferenceManager:
    def __init__(
        self,
        backend: SyntheticDiscardCredentialBackend | None = None,
        *,
        use_windows_backend: bool = False,
    ):
        if backend is not None and use_windows_backend:
            raise ValueError("CREDENTIAL_BACKEND_MODE_CONFLICT")
        self.backend = None if use_windows_backend else (backend or SyntheticDiscardCredentialBackend())
        self.store = WindowsCredentialStore() if use_windows_backend else WindowsCredentialStore(backend=self.backend)
        self._real_write_count = 0
        self._real_read_count = 0
        self._real_delete_count = 0

    @staticmethod
    def provider_segment(provider: str) -> str:
        if (
            not isinstance(provider, str)
            or not provider
            or len(provider) > 64
            or provider != provider.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in provider)
        ):
            raise ValueError("CREDENTIAL_PROVIDER_INVALID")
        if SLUG.fullmatch(provider) and not provider.startswith(CUSTOM_PROVIDER_PREFIX):
            return provider
        digest = hashlib.sha256(provider.encode("utf-8")).hexdigest()[:48]
        return f"{CUSTOM_PROVIDER_PREFIX}{digest}"

    @staticmethod
    def reference(provider: str, logical_key: str) -> str:
        if not isinstance(logical_key, str) or not SLUG.fullmatch(logical_key):
            raise ValueError("CREDENTIAL_LOGICAL_KEY_INVALID")
        provider_segment = CredentialReferenceManager.provider_segment(provider)
        return (
            f"REF:windows:Memorive/{provider_segment}/{logical_key}"
        )

    def bind(
        self,
        *,
        provider: str,
        logical_key: str,
        secret_input: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        reference = self.reference(provider, logical_key)
        if not isinstance(secret_input, str) or not secret_input:
            raise ValueError("CREDENTIAL_INPUT_INVALID")
        encoded = secret_input.encode("utf-8")
        try:
            self.store.bind(reference, encoded)
            if self.backend is None:
                self._real_write_count += 1
        finally:
            encoded = b""
        timestamp = utc_now()
        return {
            "schema_version": "CredentialReferenceReceipt-v1",
            "credential_ref": reference,
            "provider": provider,
            "logical_key": logical_key,
            "status": "STORED_UNVERIFIED",
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
            "secret_material_in_receipt": False,
        }

    def status(self, reference: str) -> dict[str, Any]:
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise ValueError("CREDENTIAL_REFERENCE_INVALID")
        target = WindowsCredentialStore._target(reference)
        checker = getattr(self.store, "exists", None)
        if callable(checker):
            exists = bool(checker(reference))
        else:
            backend = self.backend or getattr(self.store, "_backend", None)
            backend_checker = getattr(backend, "exists", None)
            if not callable(backend_checker):
                backend_checker = getattr(backend, "has", None)
            if not callable(backend_checker):
                api = getattr(backend, "api", None)
                structure = getattr(type(backend), "CREDENTIALW", None)
                credential_type = getattr(backend, "CRED_TYPE_GENERIC", 1)
                if api is None or structure is None:
                    raise RuntimeError("CREDENTIAL_EXISTENCE_CHECK_UNAVAILABLE")
                pointer = ctypes.POINTER(structure)()
                if not api.CredReadW(target, credential_type, 0, ctypes.byref(pointer)):
                    code = ctypes.get_last_error()
                    if code == 1168:
                        exists = False
                    else:
                        raise OSError(code, "CredReadW metadata check failed")
                else:
                    api.CredFree(pointer)
                    exists = True
            else:
                exists = bool(backend_checker(target))
        return {
            "schema_version": "CredentialReferenceStatus-v1",
            "credential_ref": reference,
            "status": "STORED_UNVERIFIED"
            if exists
            else "MISSING",
            "credential_value_read": False,
        }

    def resolve_for_validation(self, reference: str) -> bytes:
        """Resolve secret bytes only for an explicit validation request."""
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise ValueError("CREDENTIAL_REFERENCE_INVALID")
        if self.backend is None:
            self._real_read_count += 1
        return self.store.resolve(reference)

    def delete(self, reference: str) -> dict[str, Any]:
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise ValueError("CREDENTIAL_REFERENCE_INVALID")
        receipt = self.store.delete(reference)
        if self.backend is None:
            self._real_delete_count += 1
        return {
            "schema_version": "CredentialReferenceDeletionReceipt-v1",
            "credential_ref": reference,
            "status": "DELETED",
            "deleted_or_absent": receipt["deleted_or_absent"],
            "secret_material_in_receipt": False,
        }

    @property
    def metrics(self) -> dict[str, int]:
        return {
            "synthetic_writes": 0 if self.backend is None else self.backend.write_count,
            "synthetic_deletes": 0 if self.backend is None else self.backend.delete_count,
            "credential_value_reads": self._real_read_count
            if self.backend is None
            else self.backend.read_count,
            "real_credential_writes": self._real_write_count,
            "real_credential_deletes": self._real_delete_count,
        }


__all__ = [
    "CredentialReferenceManager",
    "SyntheticDiscardCredentialBackend",
]

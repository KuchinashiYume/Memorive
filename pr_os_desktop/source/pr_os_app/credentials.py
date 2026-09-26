from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
from typing import Protocol


_REFERENCE = re.compile(r"^REF:windows:([A-Za-z0-9][A-Za-z0-9_.:/-]{0,191})$")


class CredentialBackend(Protocol):
    def write(self, target: str, secret: bytes) -> None: ...
    def read(self, target: str) -> bytes: ...
    def delete(self, target: str) -> None: ...
    def exists(self, target: str) -> bool: ...


class _Win32CredentialBackend:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("WINDOWS_CREDENTIAL_MANAGER_UNAVAILABLE")
        self.api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self.api.CredWriteW.argtypes = [ctypes.POINTER(self.CREDENTIALW), wintypes.DWORD]
        self.api.CredWriteW.restype = wintypes.BOOL
        self.api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(self.CREDENTIALW))]
        self.api.CredReadW.restype = wintypes.BOOL
        self.api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.api.CredDeleteW.restype = wintypes.BOOL
        self.api.CredFree.argtypes = [ctypes.c_void_p]
        self.api.CredFree.restype = None

    @staticmethod
    def _raise_last(operation: str) -> None:
        code = ctypes.get_last_error()
        raise OSError(code, f"{operation} failed")

    def write(self, target: str, secret: bytes) -> None:
        if not secret or len(secret) > 512:
            raise ValueError("CREDENTIAL_SECRET_SIZE_INVALID")
        buffer = (ctypes.c_ubyte * len(secret)).from_buffer_copy(secret)
        credential = self.CREDENTIALW()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(secret)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "PR-OS"
        if not self.api.CredWriteW(ctypes.byref(credential), 0):
            self._raise_last("CredWriteW")

    def read(self, target: str) -> bytes:
        pointer = ctypes.POINTER(self.CREDENTIALW)()
        if not self.api.CredReadW(target, self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            self._raise_last("CredReadW")
        try:
            credential = pointer.contents
            return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        finally:
            self.api.CredFree(pointer)

    def delete(self, target: str) -> None:
        if not self.api.CredDeleteW(target, self.CRED_TYPE_GENERIC, 0):
            code = ctypes.get_last_error()
            if code != 1168:  # ERROR_NOT_FOUND: deletion remains idempotent.
                raise OSError(code, "CredDeleteW failed")

    def exists(self, target: str) -> bool:
        pointer = ctypes.POINTER(self.CREDENTIALW)()
        if not self.api.CredReadW(target, self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            code = ctypes.get_last_error()
            if code == 1168:
                return False
            raise OSError(code, "CredReadW metadata check failed")
        self.api.CredFree(pointer)
        return True


class WindowsCredentialStore:
    """Reference-only boundary over Windows Credential Manager.

    Receipts contain only the opaque ``REF:windows:`` locator.  Secret bytes
    are returned solely by an explicit ``resolve`` call and are never logged,
    hashed, serialized, or placed in job state.
    """

    def __init__(self, *, backend: CredentialBackend | None = None):
        self._backend = backend or _Win32CredentialBackend()

    @staticmethod
    def _target(reference: str) -> str:
        if not isinstance(reference, str):
            raise ValueError("CREDENTIAL_REFERENCE_INVALID")
        match = _REFERENCE.fullmatch(reference)
        if match is None:
            raise ValueError("CREDENTIAL_REFERENCE_INVALID")
        return match.group(1)

    def bind(self, reference: str, secret: bytes) -> dict[str, object]:
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("CREDENTIAL_SECRET_INVALID")
        self._backend.write(self._target(reference), secret)
        return {
            "schema_version": "CredentialBindingReceipt-v1",
            "credential_ref": reference,
            "backend": "WINDOWS_CREDENTIAL_MANAGER",
            "stored": True,
            "secret_material_in_receipt": False,
        }

    def resolve(self, reference: str) -> bytes:
        return bytes(self._backend.read(self._target(reference)))

    def delete(self, reference: str) -> dict[str, object]:
        self._backend.delete(self._target(reference))
        return {
            "schema_version": "CredentialDeletionReceipt-v1",
            "credential_ref": reference,
            "deleted_or_absent": True,
            "secret_material_in_receipt": False,
        }

    def exists(self, reference: str) -> bool:
        target = self._target(reference)
        checker = getattr(self._backend, "exists", None)
        if callable(checker):
            return bool(checker(target))
        has = getattr(self._backend, "has", None)
        if callable(has):
            return bool(has(target))
        raise RuntimeError("CREDENTIAL_EXISTENCE_CHECK_UNAVAILABLE")


__all__ = ["CredentialBackend", "WindowsCredentialStore"]

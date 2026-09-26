from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import canonical_json_bytes, canonical_sha256, immutable_copy, validate_t01_object


class RegistryViolation(ValueError):
    def __init__(self, code: str, adapter_id: str | None = None):
        self.code = code
        self.adapter_id = adapter_id
        suffix = f": {adapter_id}" if adapter_id else ""
        super().__init__(f"{code}{suffix}")


@dataclass(frozen=True)
class AdapterRecord:
    adapter_id: str
    revision: str
    manifest_sha256: str
    enabled: bool
    stale: bool
    _manifest_bytes: bytes

    @property
    def manifest(self) -> dict[str, Any]:
        import json

        return json.loads(self._manifest_bytes.decode("utf-8"))


class AdapterRegistry:
    """Exact-key, no-probe registry for versioned adapter manifests."""

    def __init__(self, core_schema: Mapping[str, Any]):
        self._core_schema = immutable_copy(core_schema)
        self._records: dict[tuple[str, str], AdapterRecord] = {}

    def register(
        self,
        manifest: Mapping[str, Any],
        *,
        enabled: bool,
        stale: bool = False,
    ) -> AdapterRecord:
        accepted = validate_t01_object(manifest, "AdapterManifest", self._core_schema)
        adapter_id = accepted.get("adapter_id")
        revision = accepted.get("revision")
        if not isinstance(adapter_id, str) or not adapter_id:
            raise RegistryViolation("ADAPTER_ID_INVALID")
        if not isinstance(revision, str) or not revision:
            raise RegistryViolation("ADAPTER_REVISION_INVALID", adapter_id)
        key = (adapter_id, revision)
        if key in self._records:
            raise RegistryViolation("ADAPTER_EXACT_KEY_DUPLICATE", adapter_id)
        encoded = canonical_json_bytes(accepted)
        record = AdapterRecord(
            adapter_id=adapter_id,
            revision=revision,
            manifest_sha256=canonical_sha256(accepted),
            enabled=bool(enabled),
            stale=bool(stale),
            _manifest_bytes=encoded,
        )
        self._records[key] = record
        return record

    def resolve(
        self,
        adapter_id: str,
        revision: str,
        expected_sha256: str,
    ) -> AdapterRecord:
        record = self._records.get((adapter_id, revision))
        if record is None:
            raise RegistryViolation("ADAPTER_MANIFEST_NOT_FOUND", adapter_id)
        if not record.enabled:
            raise RegistryViolation("ADAPTER_MANIFEST_DISABLED", adapter_id)
        if record.stale:
            raise RegistryViolation("ADAPTER_MANIFEST_STALE", adapter_id)
        if record.manifest_sha256 != str(expected_sha256).upper():
            raise RegistryViolation("ADAPTER_MANIFEST_HASH_MISMATCH", adapter_id)
        return record

    def snapshot(self) -> tuple[AdapterRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))


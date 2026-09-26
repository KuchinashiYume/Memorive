from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable, Mapping
import uuid

from run_ledger.errors import ConcurrentWriterError
from run_ledger.writer_lock import KernelFileLock

from .contracts import (
    SETTINGS_SCHEMA_VERSION,
    STORE_SCHEMA_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    default_settings,
    utc_now,
    validate_settings,
)
from .model_capabilities import EMBEDDING, RERANKER, infer_api_model_capability


class SettingsCorrupt(RuntimeError):
    pass


_LOCKS_GUARD = Lock()
_PROFILE_LOCKS: dict[str, Any] = {}


class SettingsStore:
    def __init__(
        self,
        profile_root: Path | str,
        *,
        default_factory: Callable[[], dict[str, Any]] = default_settings,
        fault_injector: Callable[[str, Path], None] | None = None,
    ):
        self.profile_root = Path(profile_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.path = self.profile_root / "settings.json"
        self.recovery_receipt_path = self.profile_root / "settings_recovery.json"
        self.migration_receipt_path = self.profile_root / "settings_migration.json"
        self.default_factory = default_factory
        self.fault_injector = fault_injector
        identity = os.path.normcase(str(self.path.resolve()))
        with _LOCKS_GUARD:
            self._thread_lock = _PROFILE_LOCKS.setdefault(identity, RLock())

    @contextmanager
    def _transaction(self):
        # All instances sharing a profile serialize the revision check and the
        # replacement, including first creation, recovery and schema migration.
        # The existing kernel primitive also excludes a second application process.
        with self._thread_lock:
            kernel = KernelFileLock(self.profile_root / '.settings-write.lock')
            try:
                kernel.acquire()
            except ConcurrentWriterError as exc:
                raise ValueError('SETTINGS_REVISION_CONFLICT:CONCURRENT_WRITER') from exc
            try:
                yield
            finally:
                kernel.release()

    def _envelope(
        self, settings: Mapping[str, Any], revision: int
    ) -> dict[str, Any]:
        accepted = validate_settings(settings)
        return {
            "schema_version": STORE_SCHEMA_VERSION,
            "revision": revision,
            "template_id": f"settings-template-{revision:06d}",
            "updated_at": utc_now(),
            "settings": accepted,
            "settings_sha256": canonical_sha256(accepted),
        }

    def _atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        encoded = canonical_json_bytes(dict(payload)) + b"\n"
        temporary = (
            # Keep atomic writes in the same directory without duplicating the target
            # name: Windows installations may still enforce MAX_PATH.
            path.parent / f".{uuid.uuid4().hex}.tmp"
        )
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if self.fault_injector is not None:
            self.fault_injector("before_replace", temporary)
        os.replace(temporary, path)

    def _validate_envelope(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise SettingsCorrupt("SETTINGS_ENVELOPE_TYPE_INVALID")
        required = {
            "schema_version",
            "revision",
            "template_id",
            "updated_at",
            "settings",
            "settings_sha256",
        }
        if set(value) != required:
            raise SettingsCorrupt("SETTINGS_ENVELOPE_FIELDS_INVALID")
        if value["schema_version"] != STORE_SCHEMA_VERSION:
            raise SettingsCorrupt("SETTINGS_STORE_SCHEMA_UNSUPPORTED")
        revision = value["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise SettingsCorrupt("SETTINGS_REVISION_INVALID")
        accepted = validate_settings(value["settings"])
        if value["settings_sha256"] != canonical_sha256(accepted):
            raise SettingsCorrupt("SETTINGS_CHECKSUM_MISMATCH")
        return {**dict(value), "settings": accepted}

    def _migrate_v1(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if set(value) != {"schema_version", "revision", "settings"}:
            raise SettingsCorrupt("SETTINGS_V1_FIELDS_INVALID")
        settings = deepcopy(value["settings"])
        settings["schema_version"] = SETTINGS_SCHEMA_VERSION
        settings.setdefault("credential_references", [])
        defaults = self.default_factory()
        preferences = settings.setdefault("preferences", {})
        for key, default in defaults["preferences"].items():
            preferences.setdefault(key, deepcopy(default))
        self._upgrade_external_library(settings)
        self._upgrade_current_shape(settings)
        migrated = self._envelope(settings, int(value["revision"]) + 1)
        self._atomic_write(self.path, migrated)
        self._atomic_write(
            self.migration_receipt_path,
            {
                "schema_version": "SettingsMigrationReceipt-v1",
                "from_schema": "SettingsTemplate-v1",
                "to_schema": SETTINGS_SCHEMA_VERSION,
                "result_revision": migrated["revision"],
                "settings_sha256": migrated["settings_sha256"],
                "status": "PASS",
            },
        )
        return migrated

    def _migrate_v2(self, value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version", "revision", "template_id", "updated_at",
            "settings", "settings_sha256",
        }
        if set(value) != required or value.get("schema_version") != "SettingsStoreEnvelope-v2":
            raise SettingsCorrupt("SETTINGS_V2_FIELDS_INVALID")
        settings = deepcopy(value["settings"])
        if not isinstance(settings, Mapping) or settings.get("schema_version") != "SettingsTemplate-v2":
            raise SettingsCorrupt("SETTINGS_V2_TEMPLATE_INVALID")
        if value.get("settings_sha256") != canonical_sha256(settings):
            raise SettingsCorrupt("SETTINGS_V2_CHECKSUM_MISMATCH")
        defaults = self.default_factory()
        settings = deepcopy(settings)
        settings["schema_version"] = SETTINGS_SCHEMA_VERSION
        preferences = settings.setdefault("preferences", {})
        for key, default in defaults["preferences"].items():
            preferences.setdefault(key, deepcopy(default))
        self._upgrade_external_library(settings)
        self._upgrade_current_shape(settings)
        migrated = self._envelope(settings, int(value["revision"]) + 1)
        self._atomic_write(self.path, migrated)
        self._atomic_write(
            self.migration_receipt_path,
            {
                "schema_version": "SettingsMigrationReceipt-v2",
                "from_schema": "SettingsTemplate-v2",
                "to_schema": SETTINGS_SCHEMA_VERSION,
                "result_revision": migrated["revision"],
                "settings_sha256": migrated["settings_sha256"],
                "status": "PASS",
            },
        )
        return migrated

    @staticmethod
    def _upgrade_external_library(settings: dict[str, Any]) -> None:
        directories = settings.get("directories")
        if not isinstance(directories, dict):
            return
        external = directories.get("external_library")
        if not isinstance(external, dict):
            return
        external.setdefault(
            "provider_kind", "CUSTOM" if external.get("enabled") else "NONE"
        )

    def _upgrade_current_shape(self, settings: dict[str, Any]) -> dict[str, Any]:
        defaults = self.default_factory()
        settings.setdefault("cli_services", deepcopy(defaults["cli_services"]))
        workflow = settings.get("workflow")
        if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
            return {
                "embedding_profile_moved": False,
                "legacy_context_profile_ref": None,
                "reranker_profile_preserved": False,
            }
        old_nodes = {
            node.get("node_id"): node
            for node in workflow["nodes"]
            if isinstance(node, dict) and isinstance(node.get("node_id"), str)
        }
        default_nodes = deepcopy(defaults["workflow"]["nodes"])
        legacy_context = old_nodes.get("context_pack", {}).get("profile_ref")
        services = {
            row.get("config_id"): row
            for row in settings.get("model_services", [])
            if isinstance(row, Mapping)
        }
        existing_embedding = old_nodes.get("chunk_embedding", {}).get(
            "profile_ref"
        )
        movable_embedding_profile: str | None = (
            existing_embedding
            if isinstance(existing_embedding, str)
            else None
        )
        independent_reranker_profile: str | None = None
        if movable_embedding_profile is None and isinstance(legacy_context, str):
            if legacy_context.startswith("local:"):
                movable_embedding_profile = legacy_context
            else:
                service = services.get(legacy_context)
                if isinstance(service, Mapping) and infer_api_model_capability(
                    service.get("provider"), service.get("model_name")
                ) == EMBEDDING:
                    movable_embedding_profile = legacy_context
        if isinstance(legacy_context, str):
            service = services.get(legacy_context)
            if isinstance(service, Mapping) and infer_api_model_capability(
                service.get("provider"), service.get("model_name")
            ) == RERANKER:
                independent_reranker_profile = legacy_context
        upgraded_nodes: list[dict[str, Any]] = []
        for default_node in default_nodes:
            node_id = default_node["node_id"]
            old_node = old_nodes.get(node_id)
            if isinstance(old_node, Mapping):
                for field in ("enabled", "retry_count", "profile_ref", "test_score", "fallback_profile_ref"):
                    if field in old_node:
                        default_node[field] = deepcopy(old_node[field])
            if node_id == "chunk_embedding":
                default_node["profile_ref"] = movable_embedding_profile
                if movable_embedding_profile is not None:
                    source_node = (
                        old_nodes.get("chunk_embedding")
                        if isinstance(existing_embedding, str)
                        else old_nodes.get("context_pack")
                    )
                    default_node["test_score"] = deepcopy(
                        (source_node or {}).get("test_score")
                    )
            elif node_id == "context_pack":
                default_node["profile_ref"] = independent_reranker_profile
                default_node["profile_source_node_id"] = None
                default_node["test_score"] = (
                    deepcopy(
                        old_nodes.get("context_pack", {}).get("test_score")
                    )
                    if independent_reranker_profile is not None
                    else None
                )
            upgraded_nodes.append(default_node)
        workflow["nodes"] = upgraded_nodes
        return {
            "embedding_profile_moved": movable_embedding_profile is not None,
            "legacy_context_profile_ref": legacy_context,
            "reranker_profile_preserved": (
                independent_reranker_profile is not None
            ),
        }

    def _migrate_v3(self, value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version", "revision", "template_id", "updated_at",
            "settings", "settings_sha256",
        }
        if set(value) != required or value.get("schema_version") != "SettingsStoreEnvelope-v3":
            raise SettingsCorrupt("SETTINGS_V3_FIELDS_INVALID")
        settings = value.get("settings")
        if not isinstance(settings, Mapping) or settings.get("schema_version") != "SettingsTemplate-v3":
            raise SettingsCorrupt("SETTINGS_V3_TEMPLATE_INVALID")
        if value.get("settings_sha256") != canonical_sha256(settings):
            raise SettingsCorrupt("SETTINGS_V3_CHECKSUM_MISMATCH")
        upgraded = deepcopy(settings)
        upgraded["schema_version"] = SETTINGS_SCHEMA_VERSION
        self._upgrade_external_library(upgraded)
        defaults = self.default_factory()
        preferences = upgraded.setdefault("preferences", {})
        for key, default in defaults["preferences"].items():
            preferences.setdefault(key, deepcopy(default))
        self._upgrade_current_shape(upgraded)
        migrated = self._envelope(upgraded, int(value["revision"]) + 1)
        self._atomic_write(self.path, migrated)
        self._atomic_write(
            self.migration_receipt_path,
            {
                "schema_version": "SettingsMigrationReceipt-v3",
                "from_schema": "SettingsTemplate-v3",
                "to_schema": SETTINGS_SCHEMA_VERSION,
                "result_revision": migrated["revision"],
                "settings_sha256": migrated["settings_sha256"],
                "external_library_provider_migrated": True,
                "status": "PASS",
            },
        )
        return migrated

    def _migrate_v4(self, value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version", "revision", "template_id", "updated_at",
            "settings", "settings_sha256",
        }
        if set(value) != required or value.get("schema_version") != "SettingsStoreEnvelope-v4":
            raise SettingsCorrupt("SETTINGS_V4_FIELDS_INVALID")
        settings = value.get("settings")
        if not isinstance(settings, Mapping) or settings.get("schema_version") != "SettingsTemplate-v4":
            raise SettingsCorrupt("SETTINGS_V4_TEMPLATE_INVALID")
        if value.get("settings_sha256") != canonical_sha256(settings):
            raise SettingsCorrupt("SETTINGS_V4_CHECKSUM_MISMATCH")
        upgraded = deepcopy(settings)
        upgraded["schema_version"] = SETTINGS_SCHEMA_VERSION
        metadata = self._upgrade_current_shape(upgraded)
        migrated = self._envelope(upgraded, int(value["revision"]) + 1)
        self._atomic_write(self.path, migrated)
        self._atomic_write(
            self.migration_receipt_path,
            {
                "schema_version": "SettingsMigrationReceipt-v5",
                "from_schema": "SettingsTemplate-v4",
                "to_schema": SETTINGS_SCHEMA_VERSION,
                "result_revision": migrated["revision"],
                "settings_sha256": migrated["settings_sha256"],
                "embedding_profile_moved": metadata["embedding_profile_moved"],
                "legacy_context_profile_ref": metadata["legacy_context_profile_ref"],
                "context_pack_independent_profile": True,
                "reranker_profile_preserved": metadata[
                    "reranker_profile_preserved"
                ],
                "status": "PASS",
            },
        )
        return migrated

    def _migrate_v5(self, value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version", "revision", "template_id", "updated_at",
            "settings", "settings_sha256",
        }
        if (
            set(value) != required
            or value.get("schema_version") != "SettingsStoreEnvelope-v5"
        ):
            raise SettingsCorrupt("SETTINGS_V5_FIELDS_INVALID")
        settings = value.get("settings")
        if (
            not isinstance(settings, Mapping)
            or settings.get("schema_version") != "SettingsTemplate-v5"
        ):
            raise SettingsCorrupt("SETTINGS_V5_TEMPLATE_INVALID")
        if value.get("settings_sha256") != canonical_sha256(settings):
            raise SettingsCorrupt("SETTINGS_V5_CHECKSUM_MISMATCH")
        upgraded = deepcopy(settings)
        upgraded["schema_version"] = SETTINGS_SCHEMA_VERSION
        metadata = self._upgrade_current_shape(upgraded)
        migrated = self._envelope(upgraded, int(value["revision"]) + 1)
        self._atomic_write(self.path, migrated)
        self._atomic_write(
            self.migration_receipt_path,
            {
                "schema_version": "SettingsMigrationReceipt-v6",
                "from_schema": "SettingsTemplate-v5",
                "to_schema": SETTINGS_SCHEMA_VERSION,
                "result_revision": migrated["revision"],
                "settings_sha256": migrated["settings_sha256"],
                "embedding_profile_preserved": (
                    metadata["embedding_profile_moved"]
                ),
                "legacy_derived_context_binding_removed": True,
                "context_pack_profile_ref": None,
                "profile_source_node_id": None,
                "status": "PASS",
            },
        )
        return migrated

    def _recover(self, raw: bytes, reason: str) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest().upper()
        quarantine = self.profile_root / f"settings.corrupt.{digest[:16]}.json"
        if not quarantine.exists() and self.path.exists():
            os.replace(self.path, quarantine)
        recovered = self._envelope(self.default_factory(), 0)
        self._atomic_write(self.path, recovered)
        self._atomic_write(
            self.recovery_receipt_path,
            {
                "schema_version": "SettingsRecoveryReceipt-v1",
                "reason_code": reason,
                "corrupt_bytes": len(raw),
                "corrupt_sha256": digest,
                "quarantine_name": quarantine.name,
                "raw_content_in_receipt": False,
                "status": "RECOVERED_TO_SAFE_DEFAULT",
            },
        )
        return recovered

    def load(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        with self._transaction():
            return self._load_unlocked(recover_corruption=recover_corruption)

    def _load_unlocked(self, *, recover_corruption: bool = True) -> dict[str, Any]:
        if not self.path.exists():
            initial = self._envelope(self.default_factory(), 0)
            self._atomic_write(self.path, initial)
            return deepcopy(initial)
        raw = self.path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
            if (
                isinstance(value, Mapping)
                and value.get("schema_version") == "SettingsTemplate-v1"
            ):
                return deepcopy(self._migrate_v1(value))
            if (
                isinstance(value, Mapping)
                and value.get("schema_version") == "SettingsStoreEnvelope-v2"
            ):
                return deepcopy(self._migrate_v2(value))
            if (
                isinstance(value, Mapping)
                and value.get("schema_version") == "SettingsStoreEnvelope-v3"
            ):
                return deepcopy(self._migrate_v3(value))
            if (
                isinstance(value, Mapping)
                and value.get("schema_version") == "SettingsStoreEnvelope-v4"
            ):
                return deepcopy(self._migrate_v4(value))
            if (
                isinstance(value, Mapping)
                and value.get("schema_version") == "SettingsStoreEnvelope-v5"
            ):
                return deepcopy(self._migrate_v5(value))
            return deepcopy(self._validate_envelope(value))
        except OSError:
            # A failed migration write is an I/O failure, not corrupt settings.
            # Never replace valid user settings with defaults in this case.
            raise
        except Exception as exc:
            if not recover_corruption:
                if isinstance(exc, SettingsCorrupt):
                    raise
                raise SettingsCorrupt("SETTINGS_DECODE_FAILED") from exc
            return deepcopy(self._recover(raw, str(exc).split(":")[0]))

    def save(
        self, settings: Mapping[str, Any], *, expected_revision: int
    ) -> dict[str, Any]:
        with self._transaction():
            current = self._load_unlocked(recover_corruption=False)
            if current["revision"] != expected_revision:
                raise ValueError(
                    f"SETTINGS_REVISION_CONFLICT:{expected_revision}:{current['revision']}"
                )
            accepted = self._envelope(settings, expected_revision + 1)
            self._atomic_write(self.path, accepted)
            return deepcopy(accepted)


__all__ = ["SettingsCorrupt", "SettingsStore"]

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
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


class SettingsFromNewerVersion(SettingsCorrupt):
    """The file was written by a newer version of the app. It is left as it is."""


def _store_version(schema: Any) -> int:
    match = re.fullmatch(r"SettingsStoreEnvelope-v(\d+)", schema) if isinstance(schema, str) else None
    return int(match.group(1)) if match else 0


_LOCKS_GUARD = Lock()
_PROFILE_LOCKS: dict[str, Any] = {}
_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "revision", "template_id", "updated_at", "settings", "settings_sha256"}
)

# Files written by earlier releases, keyed by the "schema_version" at the top of
# the file: file version, receipt version, whether missing preferences and the
# external library provider are filled in, and the receipt fields of that step.
_OLD_FILES = {
    "SettingsTemplate-v1": (1, "v1", True, lambda shape: {}),
    "SettingsStoreEnvelope-v2": (2, "v2", True, lambda shape: {}),
    "SettingsStoreEnvelope-v3": (
        3, "v3", True, lambda shape: {"external_library_provider_migrated": True},
    ),
    "SettingsStoreEnvelope-v4": (4, "v5", False, lambda shape: {
        "embedding_profile_moved": shape["embedding_profile_moved"],
        "legacy_context_profile_ref": shape["legacy_context_profile_ref"],
        "context_pack_independent_profile": True,
        "reranker_profile_preserved": shape["reranker_profile_preserved"],
    }),
    "SettingsStoreEnvelope-v5": (5, "v6", False, lambda shape: {
        "embedding_profile_preserved": shape["embedding_profile_moved"],
        "legacy_derived_context_binding_removed": True,
        "context_pack_profile_ref": None,
        "profile_source_node_id": None,
    }),
    "SettingsStoreEnvelope-v6": (6, "v7", False, lambda shape: {
        "added_nodes": ["data_review", "logic_review"],
        "old_task_snapshots_modified": False,
    }),
}


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
        """Read a file of the current version.

        The file is the user's: a value changed by hand or a field written by a
        newer version is not damage. Unknown fields are left out, missing sections
        and preferences get their defaults, and the checksum is not compared. It
        is recomputed here and written again by the next save.
        """
        if not isinstance(value, Mapping):
            raise SettingsCorrupt("SETTINGS_ENVELOPE_TYPE_INVALID")
        if not {"schema_version", "revision", "settings"} <= set(value):
            raise SettingsCorrupt("SETTINGS_ENVELOPE_FIELDS_INVALID")
        if value["schema_version"] != STORE_SCHEMA_VERSION:
            raise SettingsCorrupt("SETTINGS_STORE_SCHEMA_UNSUPPORTED")
        revision = value["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise SettingsCorrupt("SETTINGS_REVISION_INVALID")
        settings = value["settings"]
        try:
            accepted = validate_settings(settings, drop_unknown_fields=True)
        except ValueError as error:
            if "_MISSING_FIELD" not in str(error) or not isinstance(settings, dict):
                raise
            defaults = self.default_factory()
            for key, default in defaults.items():
                settings.setdefault(key, deepcopy(default))
            if isinstance(settings["preferences"], dict):
                for key, default in defaults["preferences"].items():
                    settings["preferences"].setdefault(key, deepcopy(default))
            accepted = validate_settings(settings, drop_unknown_fields=True)
        return {
            "schema_version": STORE_SCHEMA_VERSION,
            "revision": revision,
            "template_id": value.get("template_id") or f"settings-template-{revision:06d}",
            "updated_at": value.get("updated_at") or utc_now(),
            "settings": accepted,
            "settings_sha256": canonical_sha256(accepted),
        }

    def _migrate(self, value: Mapping[str, Any]) -> dict[str, Any]:
        version, receipt, fill_defaults, receipt_fields = _OLD_FILES[value["schema_version"]]
        if version == 1:
            # v1 has no envelope and no checksum: the template plus a revision.
            if set(value) != {"schema_version", "revision", "settings"}:
                raise SettingsCorrupt("SETTINGS_V1_FIELDS_INVALID")
            settings = deepcopy(value["settings"])
        else:
            if set(value) != _ENVELOPE_FIELDS:
                raise SettingsCorrupt(f"SETTINGS_V{version}_FIELDS_INVALID")
            settings = value["settings"]
            if (
                not isinstance(settings, Mapping)
                or settings.get("schema_version") != f"SettingsTemplate-v{version}"
            ):
                raise SettingsCorrupt(f"SETTINGS_V{version}_TEMPLATE_INVALID")
            settings = deepcopy(settings)
        settings["schema_version"] = SETTINGS_SCHEMA_VERSION
        if version == 1:
            settings.setdefault("credential_references", [])
        if fill_defaults:
            defaults = self.default_factory()
            preferences = settings.setdefault("preferences", {})
            for key, default in defaults["preferences"].items():
                preferences.setdefault(key, deepcopy(default))
            self._upgrade_external_library(settings)
        shape = self._upgrade_current_shape(settings)
        migrated = self._envelope(settings, int(value["revision"]) + 1)
        self._atomic_write(self.path, migrated)
        self._atomic_write(
            self.migration_receipt_path,
            {
                "schema_version": f"SettingsMigrationReceipt-{receipt}",
                "from_schema": f"SettingsTemplate-v{version}",
                "to_schema": SETTINGS_SCHEMA_VERSION,
                "result_revision": migrated["revision"],
                "settings_sha256": migrated["settings_sha256"],
                **receipt_fields(shape),
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
            schema = value.get("schema_version") if isinstance(value, Mapping) else None
            if isinstance(schema, str) and schema in _OLD_FILES:
                return deepcopy(self._migrate(value))
            if _store_version(schema) <= _store_version(STORE_SCHEMA_VERSION):
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
        # Written by a newer version of the app. Resetting it would throw away the
        # settings of someone who went back to an older version, so it stays as it is.
        raise SettingsFromNewerVersion(f"SETTINGS_STORE_SCHEMA_NEWER:{schema}")

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


__all__ = ["SettingsCorrupt", "SettingsFromNewerVersion", "SettingsStore"]

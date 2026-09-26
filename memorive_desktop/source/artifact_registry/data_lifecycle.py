"""Synthetic-only migration, backup, verification and rollback for DIRECTORY-ROLES.

The engine refuses to operate unless every path is below one caller-supplied
sandbox root carrying the exact synthetic marker.  It cannot perform a real
product switch, restore, uninstall, cleanup, or deletion.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Iterable, Mapping

from runtime_log.product_paths import DirectoryRole


SYNTHETIC_MARKER = ".memorive_directory_roles_synthetic_root.json"


class DataLifecycleViolation(ValueError):
    """Fail-closed lifecycle boundary violation."""


class Persistence(str, Enum):
    IMMUTABLE = "IMMUTABLE"
    PERSISTENT = "PERSISTENT"
    REBUILDABLE = "REBUILDABLE"
    EPHEMERAL = "EPHEMERAL"


class MigrationState(str, Enum):
    DISCOVERED = "DISCOVERED"
    SOURCE_INVENTORIED = "SOURCE_INVENTORIED"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    BACKUP_STAGED = "BACKUP_STAGED"
    DESTINATION_STAGED = "DESTINATION_STAGED"
    CONTENT_VERIFIED = "CONTENT_VERIFIED"
    SWITCH_READY = "SWITCH_READY"
    SWITCH_SIMULATED = "SWITCH_SIMULATED"
    HEALTH_VERIFIED = "HEALTH_VERIFIED"
    OLD_READONLY_SIMULATED = "OLD_READONLY_SIMULATED"
    COMMITTED_SYNTHETIC = "COMMITTED_SYNTHETIC"
    ROLLBACK_STARTED = "ROLLBACK_STARTED"
    SWITCH_BLOCKED = "SWITCH_BLOCKED"
    SOURCE_UNCHANGED = "SOURCE_UNCHANGED"
    SOURCE_RESTORED_OR_UNCHANGED = "SOURCE_RESTORED_OR_UNCHANGED"
    ROLLBACK_VERIFIED = "ROLLBACK_VERIFIED"
    INTERRUPTED_RESUMABLE = "INTERRUPTED_RESUMABLE"


class FaultPoint(str, Enum):
    NONE = "NONE"
    PREFLIGHT_PERMISSION_DENIED = "PREFLIGHT_PERMISSION_DENIED"
    PREFLIGHT_DISK_INSUFFICIENT = "PREFLIGHT_DISK_INSUFFICIENT"
    AFTER_BACKUP = "AFTER_BACKUP"
    CANCEL_AFTER_BACKUP = "CANCEL_AFTER_BACKUP"
    AFTER_FIRST_COPY = "AFTER_FIRST_COPY"
    CRASH_AFTER_FIRST_COPY = "CRASH_AFTER_FIRST_COPY"
    BACKUP_CORRUPT = "BACKUP_CORRUPT"
    CHECKSUM_DRIFT = "CHECKSUM_DRIFT"
    HEALTH_FAIL = "HEALTH_FAIL"
    ROLLBACK_VERIFICATION_FAIL = "ROLLBACK_VERIFICATION_FAIL"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if not value or "\\" in value or pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise DataLifecycleViolation("ASSET_RELATIVE_PATH_UNSAFE")
    return Path(*pure.parts)


def _is_within(path: Path, root: Path) -> bool:
    accepted = path.resolve(strict=False)
    boundary = root.resolve(strict=False)
    return accepted == boundary or boundary in accepted.parents


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    source_role: DirectoryRole
    destination_role: DirectoryRole
    relative_path: str
    persistence: Persistence
    data_class: str
    schema_version: str
    expected_sha256: str
    expected_bytes: int
    backup_required: bool
    restore_required: bool
    owner: str = "APPLICATION_USER"
    producer: str = "MEMORIVE"
    consumers: tuple[str, ...] = ("MEMORIVE",)
    sensitivity: str = "SELF_SYNTHETIC_NON_SECRET"
    lock_policy: str = "SINGLE_WRITER"
    retention_policy: str = "RETAIN_BY_DEFAULT"
    uninstall_default: str = "RETAIN"
    delete_authority: str = "INDEPENDENT_USER_AUTHORIZATION_REQUIRED"

    def validate(self) -> None:
        if not self.asset_id or any(character.isspace() for character in self.asset_id):
            raise DataLifecycleViolation("ASSET_ID_INVALID")
        _safe_relative(self.relative_path)
        if len(self.expected_sha256) != 64 or any(c not in "0123456789ABCDEF" for c in self.expected_sha256):
            raise DataLifecycleViolation("ASSET_SHA256_INVALID")
        if self.expected_bytes < 0:
            raise DataLifecycleViolation("ASSET_BYTES_INVALID")
        if not self.owner or not self.producer or not self.consumers:
            raise DataLifecycleViolation("ASSET_OWNERSHIP_INVALID")
        if self.sensitivity != "SELF_SYNTHETIC_NON_SECRET":
            raise DataLifecycleViolation("SYNTHETIC_DATA_CLASS_REQUIRED")
        if self.lock_policy != "SINGLE_WRITER":
            raise DataLifecycleViolation("SINGLE_WRITER_REQUIRED")
        if self.delete_authority != "INDEPENDENT_USER_AUTHORIZATION_REQUIRED":
            raise DataLifecycleViolation("DELETE_AUTHORITY_MUST_REMAIN_EXTERNAL")


@dataclass(frozen=True)
class MigrationPlan:
    plan_id: str
    from_version: str
    to_version: str
    source_root: Path
    destination_root: Path
    backup_root: Path
    allowed_sandbox_root: Path
    assets: tuple[AssetRecord, ...]
    supported_source_versions: tuple[str, ...] = ("1",)
    allowed_target_versions: tuple[str, ...] = ("2",)
    old_readonly_required: bool = True
    single_writer_required: bool = True
    synthetic_only: bool = True
    real_switch_authorized: bool = False
    real_restore_authorized: bool = False
    cleanup_authorized: bool = False

    def validate(self) -> None:
        if not self.synthetic_only:
            raise DataLifecycleViolation("SYNTHETIC_ONLY_REQUIRED")
        if self.real_switch_authorized or self.real_restore_authorized or self.cleanup_authorized:
            raise DataLifecycleViolation("REAL_OPERATION_AUTHORITY_FORBIDDEN")
        if not self.plan_id or any(character.isspace() for character in self.plan_id):
            raise DataLifecycleViolation("PLAN_ID_INVALID")
        if self.from_version not in self.supported_source_versions:
            raise DataLifecycleViolation("SOURCE_SCHEMA_VERSION_UNSUPPORTED")
        if self.to_version not in self.allowed_target_versions:
            raise DataLifecycleViolation("TARGET_SCHEMA_VERSION_UNSUPPORTED")
        if self.from_version.isdigit() and self.to_version.isdigit() and int(self.to_version) <= int(self.from_version):
            raise DataLifecycleViolation("DOWNGRADE_OR_NOOP_BLOCKED")
        if not self.old_readonly_required or not self.single_writer_required:
            raise DataLifecycleViolation("READONLY_AND_SINGLE_WRITER_REQUIRED")
        roots = (self.source_root, self.destination_root, self.backup_root)
        if any(not path.is_absolute() for path in (*roots, self.allowed_sandbox_root)):
            raise DataLifecycleViolation("MIGRATION_ROOT_MUST_BE_ABSOLUTE")
        if any(not _is_within(path, self.allowed_sandbox_root) for path in roots):
            raise DataLifecycleViolation("MIGRATION_ROOT_OUTSIDE_SYNTHETIC_BOUNDARY")
        marker = self.allowed_sandbox_root / SYNTHETIC_MARKER
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DataLifecycleViolation("SYNTHETIC_ROOT_MARKER_INVALID") from exc
        if value != {"schema_version": "DirectoryRolesSyntheticRoot-v1", "synthetic_only": True}:
            raise DataLifecycleViolation("SYNTHETIC_ROOT_MARKER_INVALID")
        if len({asset.asset_id for asset in self.assets}) != len(self.assets) or not self.assets:
            raise DataLifecycleViolation("ASSET_EXACT_SET_INVALID")
        if len({asset.relative_path for asset in self.assets}) != len(self.assets):
            raise DataLifecycleViolation("ASSET_PATH_DUPLICATE")
        for asset in self.assets:
            asset.validate()


class SyntheticMigrationEngine:
    def __init__(self, plan: MigrationPlan) -> None:
        plan.validate()
        self.plan = plan

    def _event(self, state: MigrationState, **details: object) -> dict[str, object]:
        return {"state": state.value, **details}

    @property
    def lock_path(self) -> Path:
        return self.plan.allowed_sandbox_root / f".{self.plan.plan_id}.migration-lock.json"

    def _acquire_lock(self) -> bool:
        if self.lock_path.exists():
            return False
        with self.lock_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {
                    "schema_version": "DirectoryRolesSyntheticMigrationLock-v1",
                    "plan_id": self.plan.plan_id,
                    "synthetic_only": True,
                },
                handle,
                sort_keys=True,
            )
            handle.write("\n")
        return True

    def _release_lock(self) -> None:
        if self.lock_path.exists():
            self.lock_path.unlink()

    def _assert_no_links(self, paths: Iterable[Path]) -> None:
        for path in paths:
            current = path
            boundary = self.plan.allowed_sandbox_root.resolve(strict=False)
            while _is_within(current, boundary):
                is_junction = bool(
                    current.exists()
                    and hasattr(current, "is_junction")
                    and current.is_junction()
                )
                if current.exists() and (current.is_symlink() or is_junction):
                    raise DataLifecycleViolation("SYMLINK_OR_REPARSE_FORBIDDEN")
                if current.resolve(strict=False) == boundary:
                    break
                current = current.parent

    def _source_inventory(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for asset in self.plan.assets:
            relative = _safe_relative(asset.relative_path)
            source = self.plan.source_root / relative
            self._assert_no_links((source,))
            if not source.is_file():
                raise DataLifecycleViolation(f"SOURCE_ASSET_MISSING:{asset.asset_id}")
            observed = sha256_file(source)
            size = source.stat().st_size
            if observed != asset.expected_sha256 or size != asset.expected_bytes:
                raise DataLifecycleViolation(f"SOURCE_ASSET_HASH_OR_SIZE_MISMATCH:{asset.asset_id}")
            rows.append({"asset_id": asset.asset_id, "relative_path": asset.relative_path, "sha256": observed, "bytes": size})
        return rows

    def execute(
        self,
        *,
        fault: FaultPoint = FaultPoint.NONE,
        simulated_available_bytes: int | None = None,
    ) -> dict[str, object]:
        events = [self._event(MigrationState.DISCOVERED)]
        inventory = self._source_inventory()
        events.append(self._event(MigrationState.SOURCE_INVENTORIED, asset_count=len(inventory)))
        required = sum(int(row["bytes"]) for row in inventory) * 2
        if fault is FaultPoint.PREFLIGHT_PERMISSION_DENIED:
            return self._blocked("PREFLIGHT_PERMISSION_DENIED", events, inventory)
        if simulated_available_bytes is not None and simulated_available_bytes < required:
            return self._blocked("PREFLIGHT_DISK_INSUFFICIENT", events, inventory)
        if fault is FaultPoint.PREFLIGHT_DISK_INSUFFICIENT:
            return self._blocked("PREFLIGHT_DISK_INSUFFICIENT", events, inventory)
        if self.plan.destination_root.exists():
            return self._blocked("DESTINATION_ALREADY_EXISTS", events, inventory)
        events.append(self._event(MigrationState.PREFLIGHT_PASSED, required_bytes=required))

        if not self._acquire_lock():
            return self._blocked("SINGLE_WRITER_OR_STALE_LOCK_BLOCKED", events, inventory)

        snapshot = self.plan.backup_root / self.plan.plan_id / "source_snapshot"
        staging = self.plan.destination_root.parent / f".{self.plan.destination_root.name}.{self.plan.plan_id}.staging"
        self._assert_no_links((snapshot, staging))
        if snapshot.exists() or staging.exists():
            self._release_lock()
            return self._blocked("CREATE_ONLY_STAGING_ALREADY_EXISTS", events, inventory)
        snapshot.mkdir(parents=True, exist_ok=False)
        for asset in self.plan.assets:
            relative = _safe_relative(asset.relative_path)
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.plan.source_root / relative, target)
        events.append(self._event(MigrationState.BACKUP_STAGED, snapshot=str(snapshot)))
        if fault is FaultPoint.BACKUP_CORRUPT:
            first = snapshot / _safe_relative(self.plan.assets[0].relative_path)
            first.write_bytes(first.read_bytes() + b"corrupt")
        for asset in self.plan.assets:
            target = snapshot / _safe_relative(asset.relative_path)
            if sha256_file(target) != asset.expected_sha256:
                return self._rollback("BACKUP_CHECKSUM_MISMATCH", events, inventory, staging)
        if fault is FaultPoint.AFTER_BACKUP:
            return self._rollback("FAULT_AFTER_BACKUP", events, inventory, staging)
        if fault is FaultPoint.CANCEL_AFTER_BACKUP:
            return self._rollback("CANCELLED_BEFORE_SWITCH", events, inventory, staging)

        staging.mkdir(parents=True, exist_ok=False)
        for index, asset in enumerate(self.plan.assets):
            relative = _safe_relative(asset.relative_path)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.plan.source_root / relative, target)
            if index == 0 and fault is FaultPoint.AFTER_FIRST_COPY:
                return self._rollback("FAULT_AFTER_FIRST_COPY", events, inventory, staging)
            if index == 0 and fault is FaultPoint.ROLLBACK_VERIFICATION_FAIL:
                return self._rollback(FaultPoint.ROLLBACK_VERIFICATION_FAIL.value, events, inventory, staging)
            if index == 0 and fault is FaultPoint.CRASH_AFTER_FIRST_COPY:
                events.append(self._event(MigrationState.INTERRUPTED_RESUMABLE, copied_assets=1))
                return {
                    "schema_version": "DirectoryRolesSyntheticMigrationReceipt-v1",
                    "plan_id": self.plan.plan_id,
                    "status": "INTERRUPTED_RESUMABLE",
                    "terminal_state": MigrationState.INTERRUPTED_RESUMABLE.value,
                    "events": events,
                    "inventory": inventory,
                    "resume_lock_preserved": True,
                    "real_switch_count": 0,
                    "real_restore_count": 0,
                    "cleanup_count": 0,
                }
        events.append(self._event(MigrationState.DESTINATION_STAGED, staging=str(staging)))
        if fault is FaultPoint.CHECKSUM_DRIFT:
            first = staging / _safe_relative(self.plan.assets[0].relative_path)
            first.write_bytes(first.read_bytes() + b"drift")

        verification = []
        for asset in self.plan.assets:
            target = staging / _safe_relative(asset.relative_path)
            observed = sha256_file(target)
            verification.append({"asset_id": asset.asset_id, "sha256": observed})
            if observed != asset.expected_sha256:
                return self._rollback("STAGED_CHECKSUM_MISMATCH", events, inventory, staging)
        events.append(self._event(MigrationState.CONTENT_VERIFIED, asset_count=len(verification)))
        events.append(self._event(MigrationState.SWITCH_READY, synthetic_only=True))
        os.replace(staging, self.plan.destination_root)
        events.append(self._event(MigrationState.SWITCH_SIMULATED, real_switch=False))
        if fault is FaultPoint.HEALTH_FAIL:
            return self._rollback_destination("HEALTH_CHECK_FAILED", events, inventory)
        events.append(self._event(MigrationState.HEALTH_VERIFIED))
        events.append(self._event(MigrationState.OLD_READONLY_SIMULATED, source_mutated=False))
        events.append(self._event(MigrationState.COMMITTED_SYNTHETIC))
        self._release_lock()
        return {
            "schema_version": "DirectoryRolesSyntheticMigrationReceipt-v1",
            "plan_id": self.plan.plan_id,
            "status": "PASS",
            "terminal_state": MigrationState.COMMITTED_SYNTHETIC.value,
            "events": events,
            "inventory": inventory,
            "verification": verification,
            "backup_assessment": "PASS",
            "restore_assessment": "NOT_ASSESSED_NO_REAL_RESTORE",
            "real_switch_count": 0,
            "real_restore_count": 0,
            "cleanup_count": 0,
        }

    def resume(self) -> dict[str, object]:
        """Resume only an interrupted synthetic plan owned by its exact lock."""

        self.plan.validate()
        try:
            lock = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DataLifecycleViolation("RESUME_LOCK_MISSING_OR_INVALID") from exc
        if lock != {
            "schema_version": "DirectoryRolesSyntheticMigrationLock-v1",
            "plan_id": self.plan.plan_id,
            "synthetic_only": True,
        }:
            raise DataLifecycleViolation("RESUME_LOCK_IDENTITY_MISMATCH")
        inventory = self._source_inventory()
        snapshot = self.plan.backup_root / self.plan.plan_id / "source_snapshot"
        staging = self.plan.destination_root.parent / f".{self.plan.destination_root.name}.{self.plan.plan_id}.staging"
        if not snapshot.is_dir() or not staging.is_dir() or self.plan.destination_root.exists():
            raise DataLifecycleViolation("RESUME_STATE_INVALID")
        events = [self._event(MigrationState.INTERRUPTED_RESUMABLE), self._event(MigrationState.ROLLBACK_STARTED, action="RESUME_FORWARD_SYNTHETIC")]
        for asset in self.plan.assets:
            relative = _safe_relative(asset.relative_path)
            source = self.plan.source_root / relative
            if sha256_file(snapshot / relative) != asset.expected_sha256:
                raise DataLifecycleViolation("RESUME_BACKUP_HASH_MISMATCH")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256_file(target) != asset.expected_sha256:
                raise DataLifecycleViolation("RESUME_STAGING_HASH_MISMATCH")
        events.append(self._event(MigrationState.CONTENT_VERIFIED, asset_count=len(self.plan.assets)))
        events.append(self._event(MigrationState.SWITCH_READY, synthetic_only=True))
        os.replace(staging, self.plan.destination_root)
        events.append(self._event(MigrationState.SWITCH_SIMULATED, real_switch=False))
        events.append(self._event(MigrationState.HEALTH_VERIFIED))
        events.append(self._event(MigrationState.OLD_READONLY_SIMULATED, source_mutated=False))
        events.append(self._event(MigrationState.COMMITTED_SYNTHETIC))
        self._release_lock()
        return {
            "schema_version": "DirectoryRolesSyntheticMigrationReceipt-v1",
            "plan_id": self.plan.plan_id,
            "status": "PASS_RESUMED",
            "terminal_state": MigrationState.COMMITTED_SYNTHETIC.value,
            "events": events,
            "inventory": inventory,
            "backup_assessment": "PASS",
            "restore_assessment": "NOT_ASSESSED_NO_REAL_RESTORE",
            "real_switch_count": 0,
            "real_restore_count": 0,
            "cleanup_count": 0,
        }

    def _blocked(self, code: str, events: list[dict[str, object]], inventory: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema_version": "DirectoryRolesSyntheticMigrationReceipt-v1",
            "plan_id": self.plan.plan_id,
            "status": "BLOCKED",
            "error_code": code,
            "terminal_state": events[-1]["state"],
            "events": events,
            "inventory": inventory,
            "backup_assessment": "NOT_ASSESSED",
            "restore_assessment": "NOT_ASSESSED",
            "real_switch_count": 0,
            "real_restore_count": 0,
            "cleanup_count": 0,
        }

    def _rollback(
        self,
        code: str,
        events: list[dict[str, object]],
        inventory: list[dict[str, object]],
        staging: Path,
    ) -> dict[str, object]:
        events.append(self._event(MigrationState.ROLLBACK_STARTED, error_code=code))
        events.append(self._event(MigrationState.SWITCH_BLOCKED, real_switch=False))
        if code == FaultPoint.ROLLBACK_VERIFICATION_FAIL.value:
            return self._failure_receipt("ROLLBACK_VERIFICATION_FAILED", events, inventory, terminal=MigrationState.ROLLBACK_STARTED)
        if staging.exists():
            shutil.rmtree(staging)
        events.append(self._event(MigrationState.SOURCE_UNCHANGED))
        events.append(self._event(MigrationState.SOURCE_RESTORED_OR_UNCHANGED, source_mutated=False))
        events.append(self._event(MigrationState.ROLLBACK_VERIFIED, staging_absent=not staging.exists()))
        self._release_lock()
        return self._failure_receipt(code, events, inventory)

    def _rollback_destination(
        self,
        code: str,
        events: list[dict[str, object]],
        inventory: list[dict[str, object]],
    ) -> dict[str, object]:
        events.append(self._event(MigrationState.ROLLBACK_STARTED, error_code=code))
        events.append(self._event(MigrationState.SWITCH_BLOCKED, real_switch=False))
        if self.plan.destination_root.exists():
            shutil.rmtree(self.plan.destination_root)
        events.append(self._event(MigrationState.SOURCE_UNCHANGED))
        events.append(self._event(MigrationState.SOURCE_RESTORED_OR_UNCHANGED, source_mutated=False))
        events.append(self._event(MigrationState.ROLLBACK_VERIFIED, destination_absent=not self.plan.destination_root.exists()))
        self._release_lock()
        return self._failure_receipt(code, events, inventory)

    def _failure_receipt(
        self,
        code: str,
        events: list[dict[str, object]],
        inventory: list[dict[str, object]],
        terminal: MigrationState = MigrationState.ROLLBACK_VERIFIED,
    ) -> dict[str, object]:
        return {
            "schema_version": "DirectoryRolesSyntheticMigrationReceipt-v1",
            "plan_id": self.plan.plan_id,
            "status": (
                "FAIL_ROLLBACK_INCOMPLETE"
                if terminal is not MigrationState.ROLLBACK_VERIFIED
                else "FAIL_ROLLED_BACK"
            ),
            "error_code": code,
            "terminal_state": terminal.value,
            "events": events,
            "inventory": inventory,
            "backup_assessment": "PASS" if any(row["state"] == MigrationState.BACKUP_STAGED.value for row in events) else "NOT_ASSESSED",
            "restore_assessment": "NOT_ASSESSED_NO_REAL_RESTORE",
            "real_switch_count": 0,
            "real_restore_count": 0,
            "cleanup_count": 0,
        }


def build_asset_record(
    *,
    asset_id: str,
    source_role: DirectoryRole,
    destination_role: DirectoryRole,
    relative_path: str,
    source_file: Path,
    persistence: Persistence = Persistence.PERSISTENT,
    data_class: str = "SELF_SYNTHETIC",
    schema_version: str = "v1",
    backup_required: bool = True,
    restore_required: bool = True,
) -> AssetRecord:
    record = AssetRecord(
        asset_id=asset_id,
        source_role=source_role,
        destination_role=destination_role,
        relative_path=relative_path,
        persistence=persistence,
        data_class=data_class,
        schema_version=schema_version,
        expected_sha256=sha256_file(source_file),
        expected_bytes=source_file.stat().st_size,
        backup_required=backup_required,
        restore_required=restore_required,
    )
    record.validate()
    return record


def data_inventory(records: Iterable[AssetRecord]) -> dict[str, object]:
    accepted = tuple(records)
    for record in accepted:
        record.validate()
    return {
        "schema_version": "DirectoryRolesDataInventory-v1",
        "asset_count": len(accepted),
        "assets": [
            {
                "asset_id": row.asset_id,
                "source_role": row.source_role.value,
                "destination_role": row.destination_role.value,
                "relative_path": row.relative_path,
                "persistence": row.persistence.value,
                "data_class": row.data_class,
                "schema_version": row.schema_version,
                "sha256": row.expected_sha256,
                "bytes": row.expected_bytes,
                "backup_required": row.backup_required,
                "restore_required": row.restore_required,
                "owner": row.owner,
                "producer": row.producer,
                "consumers": list(row.consumers),
                "sensitivity": row.sensitivity,
                "lock_policy": row.lock_policy,
                "retention_policy": row.retention_policy,
                "uninstall_default": row.uninstall_default,
                "delete_authority": row.delete_authority,
            }
            for row in accepted
        ],
        "real_payload_read": False,
    }

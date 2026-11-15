from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from runtime_log.product_paths import DirectoryRole
from artifact_registry.data_lifecycle import SYNTHETIC_MARKER, DataLifecycleViolation, FaultPoint, MigrationPlan, MigrationState, SyntheticMigrationEngine, build_asset_record, data_inventory
def synthetic_root(tmp_path: Path) -> Path:
    root = tmp_path / "隔离 synthetic root"
    root.mkdir(parents=True)
    (root / SYNTHETIC_MARKER).write_text(
        json.dumps({"schema_version": "DirectoryRolesSyntheticRoot-v1", "synthetic_only": True}),
        encoding="utf-8",
    )
    return root


def make_plan(tmp_path: Path, *, plan_id: str = "migration-001") -> MigrationPlan:
    root = synthetic_root(tmp_path)
    source = root / "旧版 source"
    source.mkdir()
    (source / "配置").mkdir()
    (source / "配置" / "settings.json").write_text('{"version":1}', encoding="utf-8")
    (source / "notes.txt").write_text("synthetic user data", encoding="utf-8")
    records = (
        build_asset_record(
            asset_id="config-001",
            source_role=DirectoryRole.CONFIG_ROOT,
            destination_role=DirectoryRole.CONFIG_ROOT,
            relative_path="配置/settings.json",
            source_file=source / "配置" / "settings.json",
        ),
        build_asset_record(
            asset_id="data-001",
            source_role=DirectoryRole.USER_DATA_ROOT,
            destination_role=DirectoryRole.USER_DATA_ROOT,
            relative_path="notes.txt",
            source_file=source / "notes.txt",
        ),
    )
    return MigrationPlan(
        plan_id=plan_id,
        from_version="1",
        to_version="2",
        source_root=source,
        destination_root=root / "新版本 destination",
        backup_root=root / "backup staging",
        allowed_sandbox_root=root,
        assets=records,
    )


def test_synthetic_unicode_migration_backup_and_switch_simulation_pass(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    receipt = SyntheticMigrationEngine(plan).execute()
    assert receipt["status"] == "PASS"
    assert receipt["terminal_state"] == MigrationState.COMMITTED_SYNTHETIC.value
    assert receipt["backup_assessment"] == "PASS"
    assert receipt["restore_assessment"] == "NOT_ASSESSED_NO_REAL_RESTORE"
    assert receipt["real_switch_count"] == 0
    assert (plan.destination_root / "配置" / "settings.json").is_file()
    assert (plan.backup_root / plan.plan_id / "source_snapshot" / "notes.txt").is_file()


@pytest.mark.parametrize(
    "fault,expected",
    [
        (FaultPoint.AFTER_BACKUP, "FAULT_AFTER_BACKUP"),
        (FaultPoint.AFTER_FIRST_COPY, "FAULT_AFTER_FIRST_COPY"),
        (FaultPoint.CHECKSUM_DRIFT, "STAGED_CHECKSUM_MISMATCH"),
        (FaultPoint.HEALTH_FAIL, "HEALTH_CHECK_FAILED"),
    ],
)
def test_partial_or_health_failure_rolls_back_without_source_mutation(
    tmp_path: Path, fault: FaultPoint, expected: str
) -> None:
    plan = make_plan(tmp_path)
    before = [(asset.relative_path, asset.expected_sha256) for asset in plan.assets]
    receipt = SyntheticMigrationEngine(plan).execute(fault=fault)
    assert receipt["status"] == "FAIL_ROLLED_BACK"
    assert receipt["error_code"] == expected
    assert receipt["terminal_state"] == MigrationState.ROLLBACK_VERIFIED.value
    assert not plan.destination_root.exists()
    after = [
        (asset.relative_path, build_asset_record(
            asset_id=asset.asset_id,
            source_role=asset.source_role,
            destination_role=asset.destination_role,
            relative_path=asset.relative_path,
            source_file=plan.source_root / Path(asset.relative_path),
        ).expected_sha256)
        for asset in plan.assets
    ]
    assert after == before


def test_disk_and_permission_preflight_block_before_backup(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    for kwargs in (
        {"simulated_available_bytes": 0},
        {"fault": FaultPoint.PREFLIGHT_PERMISSION_DENIED},
    ):
        receipt = SyntheticMigrationEngine(plan).execute(**kwargs)
        assert receipt["status"] == "BLOCKED"
        assert receipt["backup_assessment"] == "NOT_ASSESSED"
        assert not plan.backup_root.exists()


def test_create_only_plan_refuses_reuse(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    assert SyntheticMigrationEngine(plan).execute()["status"] == "PASS"
    receipt = SyntheticMigrationEngine(plan).execute()
    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] == "DESTINATION_ALREADY_EXISTS"


def test_boundary_marker_real_authority_and_path_traversal_fail_closed(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    invalid = MigrationPlan(**{**plan.__dict__, "real_switch_authorized": True})
    with pytest.raises(DataLifecycleViolation, match="REAL_OPERATION"):
        invalid.validate()

    outside = MigrationPlan(**{**plan.__dict__, "destination_root": tmp_path.parent / "outside"})
    with pytest.raises(DataLifecycleViolation, match="OUTSIDE_SYNTHETIC"):
        outside.validate()

    bad_record = plan.assets[0].__class__(**{**plan.assets[0].__dict__, "relative_path": "../escape"})
    with pytest.raises(DataLifecycleViolation, match="RELATIVE_PATH_UNSAFE"):
        bad_record.validate()


def test_inventory_is_public_metadata_and_delete_authority_stays_external(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    inventory = data_inventory(plan.assets)
    assert inventory["asset_count"] == 2
    assert inventory["real_payload_read"] is False
    assert all(
        row["delete_authority"] == "INDEPENDENT_USER_AUTHORIZATION_REQUIRED"
        for row in inventory["assets"]
    )
    assert all(row["sensitivity"] == "SELF_SYNTHETIC_NON_SECRET" for row in inventory["assets"])
    assert all(row["lock_policy"] == "SINGLE_WRITER" for row in inventory["assets"])


def test_cancel_and_corrupt_backup_fail_before_switch(tmp_path: Path) -> None:
    for fault, expected in (
        (FaultPoint.CANCEL_AFTER_BACKUP, "CANCELLED_BEFORE_SWITCH"),
        (FaultPoint.BACKUP_CORRUPT, "BACKUP_CHECKSUM_MISMATCH"),
    ):
        plan = make_plan(tmp_path / fault.value)
        receipt = SyntheticMigrationEngine(plan).execute(fault=fault)
        assert receipt["status"] == "FAIL_ROLLED_BACK"
        assert receipt["error_code"] == expected
        assert receipt["real_switch_count"] == 0
        assert not plan.destination_root.exists()


def test_crash_preserves_exact_lock_and_resume_finishes_synthetic_switch(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    engine = SyntheticMigrationEngine(plan)
    interrupted = engine.execute(fault=FaultPoint.CRASH_AFTER_FIRST_COPY)
    assert interrupted["status"] == "INTERRUPTED_RESUMABLE"
    assert engine.lock_path.is_file()
    resumed = engine.resume()
    assert resumed["status"] == "PASS_RESUMED"
    assert resumed["real_switch_count"] == 0
    assert not engine.lock_path.exists()
    assert (plan.destination_root / "notes.txt").is_file()


def test_single_writer_stale_lock_blocks_without_mutation(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    engine = SyntheticMigrationEngine(plan)
    engine.lock_path.write_text('{"owner":"other"}', encoding="utf-8")
    receipt = engine.execute()
    assert receipt["status"] == "BLOCKED"
    assert receipt["error_code"] == "SINGLE_WRITER_OR_STALE_LOCK_BLOCKED"
    assert not plan.backup_root.exists()
    assert not plan.destination_root.exists()


def test_schema_mismatch_downgrade_and_non_synthetic_data_class_are_blocked(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    with pytest.raises(DataLifecycleViolation, match="SOURCE_SCHEMA_VERSION_UNSUPPORTED"):
        MigrationPlan(**{**plan.__dict__, "from_version": "99"}).validate()
    with pytest.raises(DataLifecycleViolation, match="DOWNGRADE"):
        MigrationPlan(
            **{
                **plan.__dict__,
                "from_version": "2",
                "to_version": "1",
                "supported_source_versions": ("2",),
                "allowed_target_versions": ("1",),
            }
        ).validate()
    unsafe = plan.assets[0].__class__(**{**plan.assets[0].__dict__, "sensitivity": "PRIVATE"})
    with pytest.raises(DataLifecycleViolation, match="SYNTHETIC_DATA_CLASS_REQUIRED"):
        unsafe.validate()


def test_rollback_verification_failure_is_preserved_not_misreported(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    engine = SyntheticMigrationEngine(plan)
    receipt = engine.execute(fault=FaultPoint.ROLLBACK_VERIFICATION_FAIL)
    assert receipt["status"] == "FAIL_ROLLBACK_INCOMPLETE"
    assert receipt["error_code"] == "ROLLBACK_VERIFICATION_FAILED"
    assert receipt["terminal_state"] == MigrationState.ROLLBACK_STARTED.value
    assert engine.lock_path.exists()


def test_symlink_or_reparse_path_is_rejected_before_copy(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    link = plan.allowed_sandbox_root / "linked destination parent"
    try:
        link.symlink_to(plan.source_root, target_is_directory=True)
    except OSError:
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(plan.source_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if created.returncode != 0:
            pytest.skip("platform symlink and junction creation unavailable")
    linked_plan = MigrationPlan(**{**plan.__dict__, "destination_root": link / "destination"})
    with pytest.raises(DataLifecycleViolation, match="SYMLINK_OR_REPARSE_FORBIDDEN"):
        SyntheticMigrationEngine(linked_plan).execute()

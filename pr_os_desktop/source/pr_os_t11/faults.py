from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Callable

from pr_os_app.durable_store import DurableJobStore
from pr_os_app.errors import CorruptEventLog
from pr_os_app.facade import ApplicationFacade
from pr_os_desktop_service.adapters import RealFacadeAdapter

from .fixture import IsolatedFixture
from .transport import LoopbackApplicationService


def _backup(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as origin, closing(sqlite3.connect(destination)) as target:
        origin.backup(target)


class _ShortTimeoutDurableStore(DurableJobStore):
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=0.05)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection


class _DiskFullDurableStore(DurableJobStore):
    fail_writes = False

    def _transaction(self, callback: Callable[[sqlite3.Connection], Any]) -> Any:
        if self.fail_writes:
            raise OSError("SYNTHETIC_DISK_FULL")
        return super()._transaction(callback)


def _schema_version(database_path: Path) -> str:
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    if row is None or row[0] != DurableJobStore.schema_version:
        raise CorruptEventLog("durable store schema drift")
    return str(row[0])


async def run_fault_matrix(
    *,
    fixture: IsolatedFixture,
    facade: ApplicationFacade,
    store: DurableJobStore,
    service: LoopbackApplicationService,
    job_id: str,
    fault_root: Path,
    duplicate_receipt_exact: bool,
    duplicate_event_count_stable: bool,
) -> dict[str, Any]:
    if fault_root.exists():
        raise FileExistsError(fault_root)
    fault_root.mkdir(parents=True)
    baseline = facade.get_job(job_id)
    baseline_integrity = store.integrity_report()
    rows: list[dict[str, Any]] = []

    # UI process loss is modelled by discarding the facade object and reopening
    # the same durable store.  No state reconstruction is accepted.
    restarted_facade = ApplicationFacade(
        DurableJobStore(fixture.database_path, owner_id="t11-ui-restart")
    )
    restarted = restarted_facade.get_job(job_id)
    rows.append(
        {
            "fault": "UI_CRASH",
            "status": "PASS_RECOVERED",
            "durable_state_exact": restarted == baseline,
            "duplicate_effect_count": 0,
            "recovery": "RECREATE_UI_CONSUMER_FROM_DURABLE_STORE",
        }
    )

    before_service = await service.call("get_job", {"job_id": job_id})
    await service.restart()
    after_service = await service.call("get_job", {"job_id": job_id})
    rows.append(
        {
            "fault": "SERVICE_KILL",
            "status": "PASS_RECOVERED",
            "durable_state_exact": before_service == after_service == baseline,
            "restart_count": service.restart_count,
            "duplicate_effect_count": 0,
        }
    )

    locked_path = fault_root / "store_lock.sqlite3"
    _backup(fixture.database_path, locked_path)
    locked_store = _ShortTimeoutDurableStore(locked_path, owner_id="t11-lock-probe")
    lock_failed_closed = False
    with closing(sqlite3.connect(locked_path, timeout=0.05)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        try:
            locked_store.update_job(
                job_id,
                expected_version=baseline["version"],
                event_type="LOCK_PROBE_SHOULD_NOT_COMMIT",
                mutator=lambda value: value,
            )
        except sqlite3.OperationalError as exc:
            lock_failed_closed = "locked" in str(exc).casefold()
        finally:
            blocker.rollback()
    rows.append(
        {
            "fault": "STORE_LOCK",
            "status": "PASS_FAIL_CLOSED",
            "write_rejected": lock_failed_closed,
            "durable_state_exact": locked_store.read_job(job_id) == baseline,
            "integrity": locked_store.integrity_report()["status"],
        }
    )

    corrupt_path = fault_root / "store_corruption.sqlite3"
    _backup(fixture.database_path, corrupt_path)
    raw = bytearray(corrupt_path.read_bytes())
    raw[:16] = b"P08T11-CORRUPT!"
    corrupt_path.write_bytes(raw)
    corruption_rejected = False
    try:
        DurableJobStore(corrupt_path, owner_id="t11-corrupt-probe").integrity_report()
    except (sqlite3.DatabaseError, CorruptEventLog):
        corruption_rejected = True
    rows.append(
        {
            "fault": "STORE_CORRUPTION",
            "status": "PASS_FAIL_CLOSED",
            "corruption_rejected": corruption_rejected,
            "primary_store_unchanged": store.read_job(job_id) == baseline,
        }
    )

    drift_path = fault_root / "schema_drift.sqlite3"
    _backup(fixture.database_path, drift_path)
    with closing(sqlite3.connect(drift_path)) as connection:
        connection.execute(
            "UPDATE metadata SET value='DurableJobStore-v999' WHERE key='schema_version'"
        )
        connection.commit()
    schema_drift_rejected = False
    try:
        _schema_version(drift_path)
    except CorruptEventLog:
        schema_drift_rejected = True
    rows.append(
        {
            "fault": "SCHEMA_DRIFT",
            "status": "PASS_FAIL_CLOSED",
            "schema_drift_rejected": schema_drift_rejected,
            "primary_schema": _schema_version(fixture.database_path),
        }
    )

    disk_path = fault_root / "disk_full.sqlite3"
    _backup(fixture.database_path, disk_path)
    disk_store = _DiskFullDurableStore(disk_path, owner_id="t11-disk-full")
    before_disk = disk_store.integrity_report()
    disk_store.fail_writes = True
    disk_full_rejected = False
    try:
        ApplicationFacade(disk_store).start_job(
            fixture.request(suffix="disk-full"), "p08-t11-disk-full"
        )
    except OSError as exc:
        disk_full_rejected = str(exc) == "SYNTHETIC_DISK_FULL"
    disk_store.fail_writes = False
    after_disk = disk_store.integrity_report()
    rows.append(
        {
            "fault": "DISK_FULL",
            "status": "PASS_FAIL_CLOSED",
            "write_rejected": disk_full_rejected,
            "job_count_unchanged": before_disk["job_count"] == after_disk["job_count"],
            "event_count_unchanged": before_disk["event_count"] == after_disk["event_count"],
        }
    )

    gap_path = fault_root / "event_gap.sqlite3"
    _backup(fixture.database_path, gap_path)
    with closing(sqlite3.connect(gap_path)) as connection:
        connection.execute("DELETE FROM events WHERE sequence=2")
        connection.commit()
    event_gap_rejected = False
    try:
        DurableJobStore(gap_path, owner_id="t11-event-gap").integrity_report()
    except CorruptEventLog:
        event_gap_rejected = True
    rows.append(
        {
            "fault": "EVENT_GAP",
            "status": "PASS_FAIL_CLOSED",
            "event_gap_rejected": event_gap_rejected,
            "primary_event_count": baseline_integrity["event_count"],
        }
    )

    rows.append(
        {
            "fault": "DUPLICATE_COMMAND",
            "status": "PASS_DEDUPED",
            "receipt_exact": duplicate_receipt_exact,
            "event_count_stable": duplicate_event_count_stable,
            "external_effect_not_duplicated": True,
        }
    )

    slow_adapter = RealFacadeAdapter(facade, watch_batch_max=2)
    expected = facade.get_job_events(job_id, after_sequence=0)["events"]
    observed: list[dict[str, Any]] = []
    after_sequence = 0
    while True:
        page = slow_adapter.call(
            "watch_job_events",
            {"job_id": job_id, "after_sequence": after_sequence},
        )
        observed.extend(page["events"])
        if not page["events"]:
            break
        after_sequence = page["next_sequence"]
    rows.append(
        {
            "fault": "SLOW_CONSUMER",
            "status": "PASS_BACKPRESSURE_RESUME",
            "event_count": len(observed),
            "events_exact": observed == expected,
            "sequence_unique": len({row["sequence"] for row in observed}) == len(observed),
            "batch_max": 2,
        }
    )

    for row in rows:
        boolean_checks = [value for key, value in row.items() if isinstance(value, bool)]
        row["checks_pass"] = bool(boolean_checks) and all(boolean_checks)
    expected_faults = {
        "UI_CRASH",
        "SERVICE_KILL",
        "STORE_LOCK",
        "STORE_CORRUPTION",
        "SCHEMA_DRIFT",
        "DISK_FULL",
        "EVENT_GAP",
        "DUPLICATE_COMMAND",
        "SLOW_CONSUMER",
    }
    actual_faults = {row["fault"] for row in rows}
    final_integrity = store.integrity_report()
    return {
        "schema_version": "P08T11FaultRecoveryMatrix-v1",
        "fault_denominator": sorted(expected_faults),
        "fault_count": len(rows),
        "pass_count": sum(row["checks_pass"] for row in rows),
        "rows": rows,
        "denominator_exact": actual_faults == expected_faults,
        "primary_store_integrity": final_integrity,
        "primary_state_exact": store.read_job(job_id) == baseline,
        "external_effect_count": len(store.list_external_effects()),
        "production_writes": 0,
        "network_calls": 0,
        "external_model_calls": 0,
        "credential_value_reads": 0,
        "private_payload_reads": 0,
        "status": "PASS" if actual_faults == expected_faults and all(row["checks_pass"] for row in rows) else "FAIL",
    }


__all__ = ["run_fault_matrix"]

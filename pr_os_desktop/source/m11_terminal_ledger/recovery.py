"""Evidence-bound stale-lock classification and sandbox-only recovery."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .errors import ConcurrentWriterError
from .writer_lock import (
    LEASE_FILENAME,
    LOCK_FILENAME,
    LOCK_SENTINEL,
    KernelFileLock,
    LockLease,
    archive_lease,
    atomic_write_lease,
    canonical_json,
    host_id_hash,
    ledger_prefix_sha256,
    parse_lease,
    process_identity_status,
    process_start_marker,
    read_lease_bytes,
    sha256_bytes,
    utc_now,
)


SANDBOX_MARKER_NAME = ".p06-t03-sandbox-root"
SANDBOX_MARKER_BYTES = b"P06_T03_SANDBOX_V1\n"
RECOVERY_GUARD_NAME = ".writer.recovery.guard"


class RecoveryBlocked(RuntimeError):
    def __init__(self, classification: str, details: tuple[str, ...] = ()):
        self.classification = classification
        self.details = details
        suffix = f": {'; '.join(details)}" if details else ""
        super().__init__(f"{classification}{suffix}")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp offset required")
    return parsed.astimezone(timezone.utc)


def _lease_expired(lease: LockLease, now: datetime) -> bool:
    age = (now - _parse_time(lease.heartbeat_at)).total_seconds()
    return age > lease.lease_seconds


def _ledger_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(root.glob("*.jsonl"), key=lambda item: item.name)
    }


def _validate_ledger(root: Path) -> tuple[str, ...]:
    seen: set[str] = set()
    problems: list[str] = []
    for path in sorted(root.glob("*.jsonl"), key=lambda item: item.name):
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            problems.append(f"{path.name}:TRUNCATED_TAIL")
            continue
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            problems.append(f"{path.name}:INVALID_UTF8")
            continue
        for line_no, line in enumerate(lines, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"{path.name}:{line_no}:INVALID_JSON")
                continue
            identity = row.get("record_id") or row.get("ledger_entry_id")
            if not isinstance(identity, str) or not identity:
                problems.append(f"{path.name}:{line_no}:IDENTITY_MISSING")
            elif identity in seen:
                problems.append(f"{path.name}:{line_no}:DUPLICATE_ID:{identity}")
            else:
                seen.add(identity)
    return tuple(problems)


class RecoveryManager:
    """Recovery is allowed only below an explicitly marked P06/T03 sandbox."""

    def __init__(
        self,
        ledger_root: Path | str,
        *,
        sandbox_root: Path | str,
        writer_protocol_epoch: str,
    ):
        self.ledger_root = Path(ledger_root).resolve()
        self.sandbox_root = Path(sandbox_root).resolve()
        if writer_protocol_epoch not in {"MIXED_LEGACY", "KERNEL_LOCK_ONLY"}:
            raise ValueError("invalid writer protocol epoch")
        self.writer_protocol_epoch = writer_protocol_epoch
        marker = self.sandbox_root / SANDBOX_MARKER_NAME
        try:
            marker_bytes = marker.read_bytes()
        except OSError as exc:
            raise RecoveryBlocked("SANDBOX_MARKER_REQUIRED", (str(marker),)) from exc
        if marker_bytes != SANDBOX_MARKER_BYTES:
            raise RecoveryBlocked("SANDBOX_MARKER_INVALID", (str(marker),))
        try:
            self.ledger_root.relative_to(self.sandbox_root)
        except ValueError as exc:
            raise RecoveryBlocked("LEDGER_OUTSIDE_SANDBOX") from exc
        if self.ledger_root == self.sandbox_root:
            raise RecoveryBlocked("LEDGER_ROOT_TOO_BROAD")

    def classify(self) -> dict:
        try:
            with KernelFileLock(self.ledger_root / LOCK_FILENAME) as kernel:
                return self._classify_locked(kernel)
        except ConcurrentWriterError:
            return {
                "classification": "ACTIVE_KERNEL_HELD",
                "recoverable": False,
                "details": ["second writer could not acquire OS kernel lock"],
            }

    def _classify_locked(self, kernel: KernelFileLock) -> dict:
        problems = _validate_ledger(self.ledger_root)
        if problems:
            return {
                "classification": "CORRUPT_LEDGER",
                "recoverable": False,
                "details": list(problems),
            }
        if kernel.preexisting_bytes not in {b"", LOCK_SENTINEL}:
            return {
                "classification": "BLOCKED_UNKNOWN_LEGACY",
                "recoverable": False,
                "details": ["PID-only lock marker remains in kernel-lock path"],
            }
        first = read_lease_bytes(self.ledger_root)
        second = read_lease_bytes(self.ledger_root)
        if first != second:
            return {
                "classification": "BLOCKED_METADATA_DRIFT",
                "recoverable": False,
                "details": ["LockLease changed across double read"],
            }
        if first is None:
            classification = (
                "RECOVERABLE_METADATA_DAMAGE"
                if self.writer_protocol_epoch == "KERNEL_LOCK_ONLY"
                else "BLOCKED_UNKNOWN_LEGACY"
            )
            return {
                "classification": classification,
                "recoverable": classification.startswith("RECOVERABLE"),
                "details": ["LockLease sidecar is absent"],
                "lease_sha256": None,
            }
        try:
            lease = parse_lease(first)
        except ValueError as exc:
            classification = (
                "RECOVERABLE_METADATA_DAMAGE"
                if self.writer_protocol_epoch == "KERNEL_LOCK_ONLY"
                else "BLOCKED_UNKNOWN_LEGACY"
            )
            return {
                "classification": classification,
                "recoverable": classification.startswith("RECOVERABLE"),
                "details": [str(exc)],
                "lease_sha256": sha256_bytes(first),
            }
        if lease.state in {"RELEASED", "RECOVERED"}:
            return {
                "classification": "NO_RECOVERY_REQUIRED",
                "recoverable": False,
                "details": [f"lease state is {lease.state}"],
                "lease_sha256": sha256_bytes(first),
            }
        identity = process_identity_status(lease.pid, lease.process_start_marker)
        expired = _lease_expired(lease, datetime.now(timezone.utc))
        if self.writer_protocol_epoch == "MIXED_LEGACY":
            return {
                "classification": "BLOCKED_UNKNOWN_LEGACY",
                "recoverable": False,
                "details": [f"identity={identity}", f"lease_expired={expired}", "epoch=MIXED_LEGACY"],
                "lease_sha256": sha256_bytes(first),
            }
        if lease.host_id_hash != host_id_hash():
            return {
                "classification": "BLOCKED_UNKNOWN_LEGACY",
                "recoverable": False,
                "details": ["lease host identity differs from current host"],
                "lease_sha256": sha256_bytes(first),
            }
        if identity == "SAME_PROCESS_ALIVE":
            return {
                "classification": "BLOCKED_LIVE_PROCESS_METADATA",
                "recoverable": False,
                "details": ["lease owner process is still alive"],
                "lease_sha256": sha256_bytes(first),
            }
        if identity == "UNKNOWN":
            return {
                "classification": "BLOCKED_UNKNOWN_LEGACY",
                "recoverable": False,
                "details": ["process identity cannot be proven"],
                "lease_sha256": sha256_bytes(first),
            }
        if not expired:
            return {
                "classification": "BLOCKED_LEASE_NOT_EXPIRED",
                "recoverable": False,
                "details": [f"identity={identity}"],
                "lease_sha256": sha256_bytes(first),
            }
        return {
            "classification": "RECOVERABLE_STALE",
            "recoverable": True,
            "details": [f"identity={identity}", "lease_expired=True"],
            "lease_sha256": sha256_bytes(first),
        }

    def recover(
        self,
        *,
        operator_id: str,
        operation: Callable[[Path], str | None] | None = None,
    ) -> dict:
        if not operator_id:
            raise ValueError("operator_id is required")
        try:
            kernel = KernelFileLock(self.ledger_root / LOCK_FILENAME).acquire()
        except ConcurrentWriterError as exc:
            raise RecoveryBlocked("ACTIVE_KERNEL_HELD") from exc
        try:
            try:
                guard = KernelFileLock(self.ledger_root / RECOVERY_GUARD_NAME).acquire()
            except ConcurrentWriterError as exc:
                raise RecoveryBlocked("RECOVERY_GUARD_HELD") from exc
            try:
                classification = self._classify_locked(kernel)
                if not classification["recoverable"]:
                    raise RecoveryBlocked(
                        classification["classification"], tuple(classification.get("details") or ())
                    )
                before_lease = read_lease_bytes(self.ledger_root)
                expected_lease_sha = classification.get("lease_sha256")
                observed_lease_sha = sha256_bytes(before_lease) if before_lease is not None else None
                if expected_lease_sha != observed_lease_sha:
                    raise RecoveryBlocked(
                        "BLOCKED_METADATA_DRIFT",
                        (f"classified={expected_lease_sha}", f"observed={observed_lease_sha}"),
                    )
                before_ledger = _ledger_bytes(self.ledger_root)
                before_prefix = ledger_prefix_sha256(self.ledger_root)
                evidence_path = None
                if before_lease is not None:
                    evidence_path = archive_lease(
                        self.ledger_root, before_lease, evidence_dir_name="_recovery_evidence"
                    )
                now = utc_now()
                lease = LockLease(
                    owner_id=f"recovery:{operator_id}",
                    pid=os.getpid(),
                    process_start_marker=process_start_marker(os.getpid()),
                    host_id_hash=host_id_hash(),
                    acquired_at=now,
                    heartbeat_at=now,
                    lease_seconds=60,
                    ledger_prefix_sha256=before_prefix,
                    state="RECOVERY_ACTIVE",
                )
                atomic_write_lease(self.ledger_root, lease)
                operation_result = operation(self.ledger_root) if operation is not None else "no_operation"
                after_ledger = _ledger_bytes(self.ledger_root)
                for name, before in before_ledger.items():
                    after = after_ledger.get(name)
                    if after is None or not after.startswith(before):
                        raise RuntimeError(f"ledger prefix changed during recovery: {name}")
                problems = _validate_ledger(self.ledger_root)
                if problems:
                    raise RuntimeError(f"ledger invalid after recovery: {problems}")
                after_prefix = ledger_prefix_sha256(self.ledger_root)
                finished = replace(
                    lease,
                    heartbeat_at=utc_now(),
                    ledger_prefix_sha256=after_prefix,
                    state="RECOVERED",
                )
                after_lease = atomic_write_lease(self.ledger_root, finished)
                receipt = {
                    "schema_version": "RecoveryReceipt-v1",
                    "classification": classification["classification"],
                    "operator_id": operator_id,
                    "writer_protocol_epoch": self.writer_protocol_epoch,
                    "recovery_policy_revision": lease.recovery_policy_revision,
                    "before_lease_sha256": sha256_bytes(before_lease) if before_lease is not None else None,
                    "preserved_lease_evidence": str(evidence_path) if evidence_path is not None else None,
                    "after_lease_sha256": sha256_bytes(after_lease),
                    "ledger_prefix_before": before_prefix,
                    "ledger_prefix_after": after_prefix,
                    "preexisting_file_sizes": {name: len(value) for name, value in before_ledger.items()},
                    "post_file_sizes": {name: len(value) for name, value in after_ledger.items()},
                    "operation_result": operation_result,
                    "completed_at": utc_now(),
                    "production_root_touched": False,
                }
                receipt_id = hashlib.sha256(canonical_json(receipt).encode("utf-8")).hexdigest()
                receipt["receipt_id"] = f"recovery-{receipt_id[:24]}"
                evidence_root = self.ledger_root / "_recovery_evidence"
                evidence_root.mkdir(parents=True, exist_ok=True)
                receipt_path = evidence_root / f"{receipt['receipt_id']}.json"
                encoded = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
                with receipt_path.open("xb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                return receipt
            finally:
                guard.release()
        finally:
            kernel.release()


__all__ = [
    "RECOVERY_GUARD_NAME",
    "RecoveryBlocked",
    "RecoveryManager",
    "SANDBOX_MARKER_BYTES",
    "SANDBOX_MARKER_NAME",
]

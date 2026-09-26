"""Manifest-driven, resumable ASSET-MIGRATION migration over the append-only Registry.

Planning is strictly read-only.  Applying is restricted to an explicitly
bound isolated Registry root and records an append-only receipt chain.  The
authoritative JSONL remains the source of truth when a crash separates a
Registry append from its receipt commit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .errors import (
    ArtifactConflictError,
    ConcurrentWriterError,
    CorruptRegistryError,
    EnvelopeValidationError,
    UnknownArtifactError,
)
from .migration_contracts import (
    CAPABILITY_MODE,
    ISOLATED_ROOT_CLASS,
    MigrationContractError,
    artifact_id_from_identity,
    build_item_envelope,
    canonical_bytes,
    canonical_digest,
    item_contract_rejection,
    load_jsonl_receipts,
    now_iso,
    planned_new_disposition,
    sha256_bytes,
    validate_batch,
    validate_receipt_record,
)
from .registry_v2 import ArtifactRegistry
from .schema_v2 import (
    canonical_json,
    content_hash_value,
    resolved_parent_links,
    validate_event,
)


_YEAR_FILE = re.compile(r"^[0-9]{4}\.jsonl$")
_NO_REGISTRY_DISPOSITIONS = {
    "REJECTED_BY_CONTRACT",
    "CONFLICT_BLOCKED",
    "ERROR_PARTIAL",
    "NOT_RUN",
}


class MigrationPreflightError(MigrationContractError):
    """The entire batch must stop before the first Registry append."""


class MigrationDivergenceError(MigrationContractError):
    """Receipt and authoritative Registry truth cannot be reconciled safely."""


class PlannedMigrationFault(RuntimeError):
    """Deterministic test/B fault used to exercise one frozen crash window."""


def registry_baseline(registry_root: Path | str) -> dict[str, Any]:
    root = Path(registry_root)
    events = read_registry_events_without_side_effects(root)
    annual: list[dict[str, Any]] = []
    for path in _year_files(root):
        raw = path.read_bytes()
        annual.append(
            {
                "name": path.name,
                "bytes": len(raw),
                "raw_sha256": sha256_bytes(raw),
            }
        )
    normalized = b"".join(canonical_bytes(event) for event in events)
    return {
        "annual_file_manifest": annual,
        "record_count": len(events),
        "last_record_id": events[-1]["event_id"] if events else None,
        "registry_content_hash": sha256_bytes(normalized),
    }


def read_registry_events_without_side_effects(root: Path | str) -> list[dict[str, Any]]:
    """Validate Registry bytes without creating corruption evidence or locks."""

    registry_root = Path(root)
    events: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    registrations: dict[str, dict[str, Any]] = {}
    head_event_ids: dict[str, str] = {}
    for path in _year_files(registry_root):
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise MigrationPreflightError(
                "SOURCE_CORRUPT_BLOCKED", f"truncated Registry tail: {path.name}"
            )
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise MigrationPreflightError(
                "SOURCE_CORRUPT_BLOCKED", f"invalid Registry UTF-8: {path.name}"
            ) from exc
        for line_number, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
                validate_event(event)
            except (json.JSONDecodeError, EnvelopeValidationError) as exc:
                raise MigrationPreflightError(
                    "SOURCE_CORRUPT_BLOCKED",
                    f"invalid Registry event {path.name}:{line_number}",
                ) from exc
            event_id = event["event_id"]
            if event_id in seen_event_ids:
                raise MigrationPreflightError(
                    "SOURCE_CORRUPT_BLOCKED", f"duplicate Registry event_id {event_id}"
                )
            seen_event_ids.add(event_id)
            event_type = event["event_type"]
            payload = event["payload"]
            if event_type == "artifact_registered":
                artifact_id = payload["artifact_id"]
                if artifact_id in registrations:
                    raise MigrationPreflightError(
                        "SOURCE_CORRUPT_BLOCKED",
                        f"duplicate artifact registration {artifact_id}",
                    )
                registrations[artifact_id] = copy.deepcopy(payload)
                head_event_ids[artifact_id] = event_id
            elif event_type == "lineage_resolution_event":
                artifact_id = payload["artifact_id"]
                if artifact_id not in registrations:
                    raise MigrationPreflightError(
                        "SOURCE_CORRUPT_BLOCKED", "lineage event references unknown artifact"
                    )
                if payload["prior_head_event_id"] != head_event_ids[artifact_id]:
                    raise MigrationPreflightError(
                        "SOURCE_CORRUPT_BLOCKED", "lineage head chain is discontinuous"
                    )
                if content_hash_value(payload["new_envelope"]) != content_hash_value(
                    registrations[artifact_id]
                ):
                    raise MigrationPreflightError(
                        "SOURCE_CORRUPT_BLOCKED", "lineage event changes artifact bytes"
                    )
                head_event_ids[artifact_id] = event_id
            elif event_type in {
                "artifact_location_event",
                "state_transition_observed",
                "lineage_resolution",
                "locator_updated",
            }:
                if payload["artifact_id"] not in registrations:
                    raise MigrationPreflightError(
                        "SOURCE_CORRUPT_BLOCKED", "domain event references unknown artifact"
                    )
            events.append(event)
    return events


class MigrationPlanner:
    def __init__(
        self,
        *,
        allowed_sandbox_root: Path | str | None = None,
        production_roots: tuple[Path | str, ...] = (),
        allow_production: bool = False,
    ) -> None:
        self.allowed_sandbox_root = (
            Path(allowed_sandbox_root).resolve() if allowed_sandbox_root is not None else None
        )
        self.production_roots = tuple(Path(path).resolve() for path in production_roots)
        self.allow_production = allow_production

    def plan(self, batch: Mapping[str, Any], target_registry_root: Path | str) -> dict[str, Any]:
        normalized = validate_batch(batch, allow_production=self.allow_production)
        target = Path(target_registry_root).resolve()
        self._validate_target(normalized, target)
        current_baseline = registry_baseline(target)
        expected_baseline = copy.deepcopy(dict(normalized["target_registry_baseline"]))
        if canonical_json(current_baseline) != canonical_json(expected_baseline):
            return self._failed_plan(
                normalized,
                target,
                current_baseline,
                "TARGET_BASELINE_DRIFT",
                "target Registry baseline does not match the frozen batch",
            )

        source_checks: dict[str, dict[str, Any]] = {}
        for item in normalized["items"]:
            rejection = item_contract_rejection(item)
            if rejection is not None:
                source_checks[item["item_id"]] = {
                    "status": "NOT_READ_CONTRACT_REJECTED",
                    "rejection_code": rejection,
                }
                continue
            path = Path(item["source_locator"])
            if not path.is_file():
                return self._failed_plan(
                    normalized,
                    target,
                    current_baseline,
                    "SOURCE_DRIFT",
                    f"listed source is unavailable: {item['item_id']}",
                )
            raw = path.read_bytes()
            actual = {"bytes": len(raw), "sha256": sha256_bytes(raw)}
            source_checks[item["item_id"]] = actual
            if actual["bytes"] != item["expected_bytes"] or actual["sha256"] != item["expected_sha256"]:
                return self._failed_plan(
                    normalized,
                    target,
                    current_baseline,
                    "SOURCE_DRIFT",
                    f"listed source bytes/hash changed: {item['item_id']}",
                )

        events = read_registry_events_without_side_effects(target)
        base, effective = _artifact_maps(events)
        plan_items: list[dict[str, Any]] = []
        blocked = False
        for item in normalized["items"]:
            envelope: dict[str, Any] | None = None
            error_code: str | None = None
            if blocked:
                disposition = "NOT_RUN"
            else:
                rejection = item_contract_rejection(item)
                if rejection is not None:
                    disposition = "REJECTED_BY_CONTRACT"
                    error_code = rejection
                else:
                    try:
                        envelope = build_item_envelope(normalized, item)
                        disposition, error_code = _predict_disposition(
                            envelope,
                            base=base,
                            effective=effective,
                        )
                    except (EnvelopeValidationError, UnknownArtifactError, ArtifactConflictError) as exc:
                        disposition = "CONFLICT_BLOCKED"
                        error_code = type(exc).__name__.upper()
                    if disposition == "CONFLICT_BLOCKED":
                        blocked = True
                    elif envelope is not None and disposition in {"IMPORTED", "UNRESOLVED_ACCEPTED"}:
                        base[envelope["artifact_id"]] = copy.deepcopy(envelope)
                        effective[envelope["artifact_id"]] = copy.deepcopy(envelope)
            if item["expected_disposition"] != disposition:
                return self._failed_plan(
                    normalized,
                    target,
                    current_baseline,
                    "EXPECTED_DISPOSITION_MISMATCH",
                    f"{item['item_id']} expected {item['expected_disposition']} but planned {disposition}",
                )
            plan_items.append(
                {
                    "item_id": item["item_id"],
                    "item_order": item["item_order"],
                    "item_sha256": canonical_digest(item),
                    "artifact_id": item["expected_artifact_id"],
                    "disposition": disposition,
                    "error_code": error_code,
                    "envelope": envelope,
                    "source_check": source_checks[item["item_id"]],
                }
            )

        plan = {
            "plan_schema_version": "asset_migration-migration-plan-v1",
            "batch_id": normalized["batch_id"],
            "manifest_sha256": canonical_digest(normalized),
            "source_inventory_sha256": normalized["source_inventory_sha256"],
            "target_registry_root_class": normalized["target_registry_root_class"],
            "target_registry_baseline": current_baseline,
            "target_baseline_sha256": canonical_digest(current_baseline),
            "status": "DRY_RUN_PASS",
            "error_code": None,
            "error_message": None,
            "items": plan_items,
            "registry_writes": 0,
            "receipt_writes": 0,
            "lock_writes": 0,
            "unlisted_sibling_reads": 0,
            "external_calls": 0,
        }
        plan["plan_sha256"] = canonical_digest(plan)
        return plan

    def _validate_target(self, batch: Mapping[str, Any], target: Path) -> None:
        if batch["mode"] == CAPABILITY_MODE:
            if batch["target_registry_root_class"] != ISOLATED_ROOT_CLASS:
                raise MigrationContractError(
                    "CAPABILITY_TARGET_INVALID", "capability mode needs ISOLATED_SANDBOX"
                )
            if any(_same_or_below(target, root) for root in self.production_roots):
                raise MigrationContractError(
                    "PRODUCTION_ROOT_REJECTED", "capability mode cannot target a production root"
                )
            if self.allowed_sandbox_root is not None and not _same_or_below(
                target, self.allowed_sandbox_root
            ):
                raise MigrationContractError(
                    "SANDBOX_ROOT_ESCAPE", "target is outside the authorized sandbox root"
                )

    @staticmethod
    def _failed_plan(
        batch: Mapping[str, Any],
        target: Path,
        current_baseline: Mapping[str, Any],
        code: str,
        message: str,
    ) -> dict[str, Any]:
        plan = {
            "plan_schema_version": "asset_migration-migration-plan-v1",
            "batch_id": batch["batch_id"],
            "manifest_sha256": canonical_digest(batch),
            "source_inventory_sha256": batch["source_inventory_sha256"],
            "target_registry_root_class": batch["target_registry_root_class"],
            "target_registry_baseline": copy.deepcopy(dict(current_baseline)),
            "target_baseline_sha256": canonical_digest(current_baseline),
            "status": "FAILED_PREFLIGHT",
            "error_code": code,
            "error_message": message,
            "items": [],
            "registry_writes": 0,
            "receipt_writes": 0,
            "lock_writes": 0,
            "unlisted_sibling_reads": 0,
            "external_calls": 0,
        }
        plan["plan_sha256"] = canonical_digest(plan)
        return plan


class MigrationExecutor:
    def __init__(
        self,
        *,
        allowed_sandbox_root: Path | str,
        production_roots: tuple[Path | str, ...] = (),
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self.allowed_sandbox_root = Path(allowed_sandbox_root).resolve()
        self.production_roots = tuple(Path(path).resolve() for path in production_roots)
        self.clock = clock

    def apply_or_resume(
        self,
        batch: Mapping[str, Any],
        plan: Mapping[str, Any],
        *,
        target_registry_root: Path | str,
        receipt_path: Path | str,
        fault_hook: Callable[[str, Mapping[str, Any], Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        normalized = validate_batch(batch, allow_production=False)
        target = Path(target_registry_root).resolve()
        receipt_target = Path(receipt_path).resolve()
        self._validate_boundaries(normalized, target, receipt_target)
        _validate_plan(normalized, plan)
        if plan["status"] != "DRY_RUN_PASS":
            raise MigrationPreflightError(
                "PLAN_NOT_APPLICABLE", "only a DRY_RUN_PASS plan may be applied"
            )
        registry = ArtifactRegistry(
            target,
            corruption_evidence_root=receipt_target.parent / "corruption_evidence",
            clock=self.clock,
        )
        receipt_target.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_lock(receipt_target.with_suffix(receipt_target.suffix + ".writer.lock")):
            records = load_jsonl_receipts(receipt_target)
            self._verify_existing_receipts(normalized, registry, records)
            committed = {
                record["item_id"]: record
                for record in records
                if record["phase"] == "RECEIPT_COMMITTED"
            }
            blocked = any(
                record["disposition"] in {"CONFLICT_BLOCKED", "ERROR_PARTIAL"}
                for record in committed.values()
            )
            for plan_item, item in zip(plan["items"], normalized["items"], strict=True):
                if item["item_id"] in committed:
                    continue
                if blocked:
                    records = self._commit_without_registry(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        disposition="NOT_RUN",
                        outcome="blocked_by_prior_item",
                        error_code="PRIOR_ITEM_BLOCKED",
                    )
                    continue
                disposition = plan_item["disposition"]
                if disposition in {"REJECTED_BY_CONTRACT", "NOT_RUN"}:
                    records = self._commit_without_registry(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        disposition=disposition,
                        outcome="contract_rejected" if disposition == "REJECTED_BY_CONTRACT" else "not_run",
                        error_code=plan_item["error_code"],
                    )
                    continue
                if disposition == "CONFLICT_BLOCKED":
                    records = self._commit_without_registry(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        disposition=disposition,
                        outcome="predicted_conflict",
                        error_code=plan_item["error_code"],
                    )
                    blocked = True
                    continue
                if not _has_phase(records, item["item_id"], "ITEM_PREPARED"):
                    records = self._append_phase(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        phase="ITEM_PREPARED",
                        outcome="prepared",
                        disposition=None,
                        event_id=None,
                        proof={"plan_sha256": plan["plan_sha256"]},
                        recovery_class=None,
                        error_code=None,
                    )
                context = {"target_registry_root": str(target), "receipt_path": str(receipt_target)}
                if fault_hook is not None:
                    fault_hook("BEFORE_REGISTRY_APPEND", item, context)
                envelope = plan_item["envelope"]
                try:
                    outcome = registry.register(envelope)
                    event_id = _registration_event_id(registry, item["expected_artifact_id"])
                    recovery = None
                    if outcome == "noop" and plan_item["disposition"] != "NOOP_EXACT":
                        recovery = "RECOVERED_AS_NOOP"
                    if fault_hook is not None:
                        fault_hook("AFTER_REGISTRY_APPEND_BEFORE_RECEIPT_COMMIT", item, context)
                    records = self._append_phase(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        phase="REGISTRY_OUTCOME",
                        outcome=outcome,
                        disposition=None,
                        event_id=event_id,
                        proof={"artifact_id": item["expected_artifact_id"]},
                        recovery_class=recovery,
                        error_code=None,
                    )
                    records = self._append_phase(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        phase="RECEIPT_COMMITTED",
                        outcome=outcome,
                        disposition=disposition,
                        event_id=event_id,
                        proof={
                            "artifact_id": item["expected_artifact_id"],
                            "registry_envelope_sha256": canonical_digest(envelope),
                        },
                        recovery_class=recovery,
                        error_code=None,
                    )
                    if fault_hook is not None:
                        fault_hook("AFTER_RECEIPT_COMMIT", item, context)
                except PlannedMigrationFault:
                    raise
                except (
                    ArtifactConflictError,
                    ConcurrentWriterError,
                    CorruptRegistryError,
                    EnvelopeValidationError,
                    UnknownArtifactError,
                ) as exc:
                    records = self._append_phase(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        phase="REGISTRY_OUTCOME",
                        outcome="blocked",
                        disposition=None,
                        event_id=None,
                        proof={"exception_type": type(exc).__name__},
                        recovery_class=None,
                        error_code=type(exc).__name__.upper(),
                    )
                    records = self._append_phase(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        phase="RECEIPT_COMMITTED",
                        outcome="blocked",
                        disposition="CONFLICT_BLOCKED",
                        event_id=None,
                        proof={"exception_type": type(exc).__name__},
                        recovery_class=None,
                        error_code=type(exc).__name__.upper(),
                    )
                    blocked = True
                except Exception as exc:
                    records = self._append_phase(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        phase="REGISTRY_OUTCOME",
                        outcome="error",
                        disposition=None,
                        event_id=None,
                        proof={"exception_type": type(exc).__name__},
                        recovery_class=None,
                        error_code="UNEXPECTED_RUNTIME_ERROR",
                    )
                    records = self._append_phase(
                        normalized,
                        plan,
                        item,
                        records,
                        receipt_target,
                        phase="RECEIPT_COMMITTED",
                        outcome="error",
                        disposition="ERROR_PARTIAL",
                        event_id=None,
                        proof={"exception_type": type(exc).__name__},
                        recovery_class=None,
                        error_code="UNEXPECTED_RUNTIME_ERROR",
                    )
                    blocked = True
            self._verify_existing_receipts(normalized, registry, records)
            return _batch_summary(normalized, records, registry_baseline(target))

    def _commit_without_registry(
        self,
        batch: Mapping[str, Any],
        plan: Mapping[str, Any],
        item: Mapping[str, Any],
        records: list[dict[str, Any]],
        receipt_path: Path,
        *,
        disposition: str,
        outcome: str,
        error_code: str | None,
    ) -> list[dict[str, Any]]:
        if not _has_phase(records, item["item_id"], "ITEM_PREPARED"):
            records = self._append_phase(
                batch,
                plan,
                item,
                records,
                receipt_path,
                phase="ITEM_PREPARED",
                outcome="prepared",
                disposition=None,
                event_id=None,
                proof={"plan_sha256": plan["plan_sha256"]},
                recovery_class=None,
                error_code=error_code,
            )
        return self._append_phase(
            batch,
            plan,
            item,
            records,
            receipt_path,
            phase="RECEIPT_COMMITTED",
            outcome=outcome,
            disposition=disposition,
            event_id=None,
            proof={"registry_write": False},
            recovery_class=None,
            error_code=error_code,
        )

    def _append_phase(
        self,
        batch: Mapping[str, Any],
        plan: Mapping[str, Any],
        item: Mapping[str, Any],
        records: list[dict[str, Any]],
        path: Path,
        *,
        phase: str,
        outcome: str | None,
        disposition: str | None,
        event_id: str | None,
        proof: Mapping[str, Any],
        recovery_class: str | None,
        error_code: str | None,
    ) -> list[dict[str, Any]]:
        ordinal = len(records) + 1
        receipt_id = "receipt-" + hashlib.sha256(
            f"{batch['batch_id']}|{item['item_id']}|{phase}|{ordinal}".encode("utf-8")
        ).hexdigest()[:24]
        previous_sha = sha256_bytes(canonical_bytes(records[-1])) if records else None
        record = validate_receipt_record(
            {
                "receipt_schema_version": "asset_migration-migration-receipt-v1",
                "receipt_id": receipt_id,
                "batch_id": batch["batch_id"],
                "item_id": item["item_id"],
                "item_order": item["item_order"],
                "phase": phase,
                "recorded_at": self.clock(),
                "manifest_sha256": plan["manifest_sha256"],
                "item_sha256": canonical_digest(item),
                "target_baseline_sha256": plan["target_baseline_sha256"],
                "outcome": outcome,
                "disposition": disposition,
                "event_id": event_id,
                "proof": copy.deepcopy(dict(proof)),
                "recovery_class": recovery_class,
                "error_code": error_code,
                "previous_receipt_sha256": previous_sha,
            }
        )
        encoded = canonical_bytes(record)
        descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return records + [record]

    def _verify_existing_receipts(
        self,
        batch: Mapping[str, Any],
        registry: ArtifactRegistry,
        records: list[Mapping[str, Any]],
    ) -> None:
        events = read_registry_events_without_side_effects(registry.root)
        base, _effective = _artifact_maps(events)
        items = {item["item_id"]: item for item in batch["items"]}
        for record in records:
            if record["batch_id"] != batch["batch_id"] or record["item_id"] not in items:
                raise MigrationDivergenceError(
                    "RECEIPT_BATCH_DIVERGENCE", "receipt references another batch or item"
                )
            if record["item_sha256"] != canonical_digest(items[record["item_id"]]):
                raise MigrationDivergenceError(
                    "RECEIPT_ITEM_DIVERGENCE", "receipt item hash no longer matches manifest"
                )
            if record["phase"] != "RECEIPT_COMMITTED":
                continue
            if record["disposition"] in _NO_REGISTRY_DISPOSITIONS:
                continue
            item = items[record["item_id"]]
            artifact_id = item["expected_artifact_id"]
            if artifact_id not in base:
                raise MigrationDivergenceError(
                    "RECEIPT_REGISTRY_DIVERGENCE",
                    f"committed receipt has no Registry fact for {artifact_id}",
                )
            expected = build_item_envelope(batch, item)
            if canonical_json(base[artifact_id]) != canonical_json(expected):
                raise MigrationDivergenceError(
                    "RECEIPT_REGISTRY_DIVERGENCE",
                    f"committed receipt Registry facts differ for {artifact_id}",
                )

    def _validate_boundaries(self, batch: Mapping[str, Any], target: Path, receipt: Path) -> None:
        if batch["mode"] != CAPABILITY_MODE or batch["target_registry_root_class"] != ISOLATED_ROOT_CLASS:
            raise MigrationContractError(
                "PRODUCTION_AUTHORITY_REQUIRED", "executor is capability-sandbox-only"
            )
        if not _same_or_below(target, self.allowed_sandbox_root):
            raise MigrationContractError("SANDBOX_ROOT_ESCAPE", "Registry target escapes sandbox")
        if not _same_or_below(receipt, self.allowed_sandbox_root):
            raise MigrationContractError("SANDBOX_ROOT_ESCAPE", "receipt path escapes sandbox")
        if any(_same_or_below(target, root) for root in self.production_roots):
            raise MigrationContractError("PRODUCTION_ROOT_REJECTED", "production ARTIFACT_REGISTRY target rejected")


class MigrationReconciler:
    def reconcile(
        self,
        batch: Mapping[str, Any],
        *,
        registry_root: Path | str,
        receipt_path: Path | str,
    ) -> dict[str, Any]:
        normalized = validate_batch(batch, allow_production=False)
        records = load_jsonl_receipts(receipt_path)
        registry = ArtifactRegistry(registry_root)
        events = read_registry_events_without_side_effects(registry_root)
        base, _effective = _artifact_maps(events)
        items = {item["item_id"]: item for item in normalized["items"]}
        prepared = {record["item_id"] for record in records if record["phase"] == "ITEM_PREPARED"}
        committed = {
            record["item_id"]: record
            for record in records
            if record["phase"] == "RECEIPT_COMMITTED"
        }
        pending: list[dict[str, Any]] = []
        for item_id in sorted(prepared - set(committed), key=lambda value: items[value]["item_order"]):
            item = items[item_id]
            envelope = build_item_envelope(normalized, item)
            existing = base.get(item["expected_artifact_id"])
            if existing is None:
                state = "PREPARED_REGISTRY_MISSING_RETRYABLE"
            elif canonical_json(existing) == canonical_json(envelope):
                state = "CRASH_AFTER_APPEND_RECOVERABLE"
            else:
                state = "CONFLICT_BLOCKED"
            pending.append({"item_id": item_id, "state": state})
        for item_id, record in committed.items():
            if record["disposition"] in _NO_REGISTRY_DISPOSITIONS:
                continue
            item = items[item_id]
            if item["expected_artifact_id"] not in base:
                raise MigrationDivergenceError(
                    "RECEIPT_REGISTRY_DIVERGENCE", "committed receipt lacks Registry fact"
                )
        return {
            "batch_id": normalized["batch_id"],
            "receipt_count": len(records),
            "committed_item_count": len(committed),
            "pending": pending,
            "registry_baseline": registry_baseline(registry_root),
        }


def _validate_plan(batch: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    if not isinstance(plan, Mapping):
        raise MigrationContractError("PLAN_NOT_OBJECT", "plan must be an object")
    supplied = copy.deepcopy(dict(plan))
    plan_hash = supplied.pop("plan_sha256", None)
    if plan_hash != canonical_digest(supplied):
        raise MigrationContractError("PLAN_HASH_MISMATCH", "plan_sha256 mismatch")
    if plan.get("batch_id") != batch["batch_id"]:
        raise MigrationContractError("PLAN_BATCH_MISMATCH", "plan batch_id mismatch")
    if plan.get("manifest_sha256") != canonical_digest(batch):
        raise MigrationContractError("PLAN_MANIFEST_MISMATCH", "plan manifest hash mismatch")


def _predict_disposition(
    envelope: Mapping[str, Any],
    *,
    base: Mapping[str, Mapping[str, Any]],
    effective: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str | None]:
    artifact_id = envelope["artifact_id"]
    if artifact_id in base:
        existing = base[artifact_id]
        if content_hash_value(existing) != content_hash_value(envelope):
            return "CONFLICT_BLOCKED", "IDENTITY_HASH_CONFLICT"
        if canonical_json(existing) != canonical_json(envelope):
            return "CONFLICT_BLOCKED", "IMMUTABLE_FACT_CONFLICT"
        return "NOOP_EXACT", None
    for link in resolved_parent_links(envelope):
        parent_id = link["parent_artifact_id"]
        if parent_id not in effective:
            return "CONFLICT_BLOCKED", "PARENT_NOT_REGISTERED"
        if content_hash_value(effective[parent_id]) != link["parent_content_hash"]:
            return "CONFLICT_BLOCKED", "PARENT_HASH_CONFLICT"
    projected = {key: copy.deepcopy(dict(value)) for key, value in effective.items()}
    projected[artifact_id] = copy.deepcopy(dict(envelope))
    if _has_lineage_cycle(projected):
        return "CONFLICT_BLOCKED", "LINEAGE_CYCLE"
    return planned_new_disposition(envelope), None


def _artifact_maps(
    events: list[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    base: dict[str, dict[str, Any]] = {}
    effective: dict[str, dict[str, Any]] = {}
    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        if event_type == "artifact_registered":
            base[payload["artifact_id"]] = copy.deepcopy(payload)
            effective[payload["artifact_id"]] = copy.deepcopy(payload)
        elif event_type == "lineage_resolution_event":
            effective[payload["artifact_id"]] = copy.deepcopy(payload["new_envelope"])
        elif event_type == "artifact_location_event":
            effective[payload["artifact_id"]]["locator"] = copy.deepcopy(payload["locator"])
        elif event_type == "lineage_resolution":
            envelope = effective[payload["artifact_id"]]
            existing = {canonical_json(link) for link in resolved_parent_links(envelope)}
            for link in payload["resolved_parent_links"]:
                if canonical_json(link) not in existing:
                    envelope["lineage"]["resolved_parent_links"].append(copy.deepcopy(link))
        elif event_type == "locator_updated":
            effective[payload["artifact_id"]]["locator"] = copy.deepcopy(payload["locator"])
    return base, effective


def _has_lineage_cycle(artifacts: Mapping[str, Mapping[str, Any]]) -> bool:
    graph = {
        artifact_id: [
            link["parent_artifact_id"]
            for link in resolved_parent_links(envelope)
            if link["parent_artifact_id"] in artifacts
        ]
        for artifact_id, envelope in artifacts.items()
    }
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1
        for parent in graph[node]:
            if state.get(parent) == 1:
                return True
            if state.get(parent, 0) == 0 and visit(parent):
                return True
        state[node] = 2
        return False

    return any(state.get(node, 0) == 0 and visit(node) for node in sorted(graph))


def _registration_event_id(registry: ArtifactRegistry, artifact_id: str) -> str:
    for event in registry.read_events():
        if event["event_type"] == "artifact_registered" and event["payload"]["artifact_id"] == artifact_id:
            return str(event["event_id"])
    raise MigrationDivergenceError(
        "REGISTRY_EVENT_MISSING", f"registration event not found for {artifact_id}"
    )


def _batch_summary(
    batch: Mapping[str, Any], records: list[Mapping[str, Any]], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    terminals = {
        record["item_id"]: record
        for record in records
        if record["phase"] == "RECEIPT_COMMITTED"
    }
    dispositions = {item: record["disposition"] for item, record in terminals.items()}
    if len(terminals) != len(batch["items"]):
        status = "APPLYING"
    elif any(value in {"CONFLICT_BLOCKED", "ERROR_PARTIAL"} for value in dispositions.values()):
        status = "PARTIAL_BLOCKED"
    else:
        status = "COMPLETED"
    return {
        "batch_id": batch["batch_id"],
        "status": status,
        "item_count": len(batch["items"]),
        "terminal_item_count": len(terminals),
        "dispositions": dispositions,
        "receipt_record_count": len(records),
        "registry_baseline": copy.deepcopy(dict(baseline)),
        "unresolved_is_failure": False,
    }


def _has_phase(records: list[Mapping[str, Any]], item_id: str, phase: str) -> bool:
    return any(record["item_id"] == item_id and record["phase"] == phase for record in records)


def _year_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.iterdir() if path.is_file() and _YEAR_FILE.fullmatch(path.name)
    )


def _same_or_below(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ConcurrentWriterError(f"receipt writer lock already exists: {path}") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)

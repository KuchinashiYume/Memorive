from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

from pr_os_app.import_binding import FileImportBinder
from pr_os_folder_management.policy import windows_io_path

from .contracts import (
    CONTRACT_REVISION,
    DISPLAY_STATES,
    InboxError,
    batch_id_for,
    canonical_sha256,
    immutable,
    item_id_for,
    require_identifier,
    utc_now,
)
from .path_policy import PathSecurityPolicy
from .store import InboxStore
from .workload import workload_projection


class SimulatedCrash(RuntimeError):
    pass


class InboxController:
    """Part4 import custody and durable dispatch-intent orchestration."""

    def __init__(
        self,
        *,
        source_roots: Sequence[Path | str],
        data_root: Path | str,
        maximum_bytes: int = 64 * 1024 * 1024,
        minimum_free_bytes: int = 1024 * 1024,
        probe_overrides: Mapping[str, Mapping[str, Any]] | None = None,
        include_sample_projection: bool = True,
        job_state_provider=None,
    ):
        self.data_root = Path(data_root).resolve(strict=False)
        windows_io_path(self.data_root).mkdir(parents=True, exist_ok=True)
        self.bound_root = self.data_root / "bound"
        self.staging_root = self.data_root / "staging"
        self.trash_root = self.data_root / "session_trash"
        for root in (self.bound_root, self.staging_root, self.trash_root):
            windows_io_path(root).mkdir(parents=True, exist_ok=True)
            if not root.resolve(strict=False).is_relative_to(self.data_root):
                raise InboxError("INBOX_DATA_ROOT_ESCAPE")
        self.path_policy = PathSecurityPolicy(
            source_roots,
            self.bound_root,
            maximum_bytes=maximum_bytes,
            minimum_free_bytes=minimum_free_bytes,
            probe_overrides=probe_overrides,
        )
        self.store = InboxStore(self.data_root / "inbox_store.sqlite3")
        self.binder = FileImportBinder(self.bound_root)
        self.include_sample_projection = bool(include_sample_projection)
        self.job_state_provider = job_state_provider

    @staticmethod
    def get_contract() -> dict[str, Any]:
        return {
            "schema_version": "InboxImportContract-v1",
            "contract_revision": CONTRACT_REVISION,
            "copy_semantics": "COPY_SOURCE_BYTES_TO_CONTENT_ADDRESSED_SANDBOX_CUSTODY",
            "source_move_or_delete": False,
            "dedupe_identity": "SHA256_OF_BOUND_BYTES",
            "same_name_different_content": "ALLOW_WITH_EXPLICIT_CONFLICT_RECEIPT",
            "display_states": immutable(DISPLAY_STATES),
            "dispatch_boundary": "DURABLE_INTENT_THEN_PHASE1_WORKER",
            "retention": {
                "soft_delete": "SESSION_TRASH",
                "undo_redo": "UNTIL_APPLICATION_CLOSE",
                "permanent_delete": False,
                "close_action": "SYSTEM_RECYCLE_HANDOFF_PLAN_ONLY",
            },
            "office_preview": "PROPERTY_ONLY",
            "external_calls": 0,
        }

    def _locator(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.data_root):
            raise InboxError("INBOX_BOUND_PATH_OUTSIDE_DATA_ROOT")
        return resolved.relative_to(self.data_root).as_posix()

    def _path(self, locator: str) -> Path:
        if not isinstance(locator, str) or not locator or ".." in Path(locator).parts:
            raise InboxError("INBOX_LOCATOR_INVALID")
        resolved = (self.data_root / locator).resolve(strict=False)
        if not resolved.is_relative_to(self.data_root):
            raise InboxError("INBOX_LOCATOR_OUTSIDE_DATA_ROOT")
        return resolved

    @staticmethod
    def _error_payload(error: InboxError) -> dict[str, Any]:
        return {
            "schema_version": "InboxFileError-v1",
            "code": error.code,
            "retryable": error.retryable,
            "scope": "FILE",
            "global_capability_block": False,
        }

    def _mark_error(self, item_id: str, error: InboxError) -> dict[str, Any]:
        return self.store.update_item(
            item_id,
            "IMPORT_FAILED",
            lambda row: row.update({"state": "ERROR", "error": self._error_payload(error), "newly_imported": False}),
        )

    def _finalize_binding(self, item_id: str, preflight: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
        bound_path = Path(str(binding["bound_path"]))
        locator = self._locator(bound_path)
        content_sha = str(binding["sha256"])
        duplicate = self.store.find_by_content(content_sha, exclude_item_id=item_id)
        if duplicate is not None:
            return self.store.update_item(
                item_id,
                "IMPORT_DEDUPLICATED",
                lambda row: row.update(
                    {
                        "state": "DEDUPLICATED",
                        "content_sha256": content_sha,
                        "bound_locator": locator,
                        "duplicate_of": duplicate["item_id"],
                        "dedupe_receipt": {
                            "schema_version": "InboxDedupeReceipt-v1",
                            "status": "DUPLICATE_REUSED",
                            "content_sha256": content_sha,
                            "existing_item_id": duplicate["item_id"],
                            "new_dispatch_effects": 0,
                        },
                        "error": None,
                    }
                ),
            )
        same_name = [
            row
            for row in self.store.list_items()
            if row["item_id"] != item_id
            and row.get("source_name", "").casefold() == str(preflight["source_name"]).casefold()
            and row.get("content_sha256") not in {None, content_sha}
            and row["state"] not in {"TRASHED", "DEDUPLICATED"}
        ]
        workload = workload_projection(bound_path, str(preflight["extension"]), int(binding["bytes"]))
        return self.store.update_item(
            item_id,
            "IMPORT_BOUND",
            lambda row: row.update(
                {
                    "state": "QUEUED",
                    "content_sha256": content_sha,
                    "bound_locator": locator,
                    "bytes": int(binding["bytes"]),
                    "extension": str(preflight["extension"]),
                    "source_identity": immutable(binding["source_identity"]),
                    "source_handle_stable_during_copy": True,
                    "workload": workload,
                    "same_name_different_content": bool(same_name),
                    "conflicting_item_ids": [row["item_id"] for row in same_name],
                    "newly_imported": True,
                    "error": None,
                }
            ),
        )

    def _bind_item(
        self,
        item_id: str,
        source: Path | str,
        *,
        failpoint: str | None = None,
    ) -> dict[str, Any]:
        preflight = self.path_policy.inspect(source)
        if preflight["injected_changed"]:
            raise InboxError("IMPORT_SOURCE_CHANGED_DURING_BIND", retryable=True)
        if preflight["injected_copy_interrupt"]:
            partial = self.staging_root / f"{item_id}.partial"
            source_io = windows_io_path(Path(str(preflight["source_path"])))
            partial_io = windows_io_path(partial)
            with source_io.open("rb") as source_stream, partial_io.open("xb") as target:
                target.write(source_stream.read(max(1, int(preflight["bytes"]) // 2)))
                target.flush()
                os.fsync(target.fileno())
            self.store.update_item(
                item_id,
                "IMPORT_COPY_INTERRUPTED",
                lambda row: row.update({"pending_staging_locator": self._locator(partial)}),
            )
            raise SimulatedCrash("SIMULATED_COPY_INTERRUPTION")
        binding = self.binder.bind(str(preflight["source_path"]))
        pending = {
            "content_sha256": binding["sha256"],
            "bound_locator": self._locator(Path(str(binding["bound_path"]))),
            "bytes": binding["bytes"],
            "extension": preflight["extension"],
            "source_identity": binding["source_identity"],
            "preflight": immutable(preflight),
        }
        self.store.update_item(
            item_id,
            "IMPORT_COPY_DURABLE",
            lambda row: row.update({"pending_binding": pending}),
        )
        if failpoint == "after_copy":
            raise SimulatedCrash("SIMULATED_CRASH_AFTER_COPY")
        return self._finalize_binding(item_id, preflight, binding)

    def import_paths(
        self,
        paths: Sequence[Path | str],
        *,
        request_id: str,
        source_kind: str = "picker",
        failpoint: str | None = None,
    ) -> dict[str, Any]:
        accepted_request = require_identifier(request_id, "request_id")
        if source_kind not in {"picker", "drop", "sample"}:
            raise InboxError("INBOX_IMPORT_SOURCE_KIND_INVALID")
        if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)) or not 1 <= len(paths) <= 32:
            raise InboxError("INBOX_IMPORT_PATH_COUNT_INVALID")
        batch_id = batch_id_for(accepted_request)
        resolved_paths = [Path(os.fspath(path)).resolve(strict=False) for path in paths]
        if source_kind == "sample":
            sample_manifest = []
            for path in resolved_paths:
                path_io = windows_io_path(path)
                digest = hashlib.sha256()
                with path_io.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                sample_manifest.append(
                    {
                        "name": path.name,
                        "bytes": path_io.stat().st_size,
                        "sha256": digest.hexdigest().upper(),
                    }
                )
            locator_set_sha256 = canonical_sha256(sample_manifest)
        else:
            locator_set_sha256 = canonical_sha256([str(path) for path in resolved_paths])
        batch, replayed_batch = self.store.create_or_get_batch(
            {
                "schema_version": "InboxImportBatch-v1",
                "batch_id": batch_id,
                "request_id": accepted_request,
                "request_sha256": locator_set_sha256,
                "source_kind": source_kind,
                "state": "IMPORTING",
                "item_ids": [],
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        results = []
        for index, source in enumerate(paths):
            source_name = Path(os.fspath(source)).name
            item_id = item_id_for(accepted_request, index, source_name)
            item, replayed = self.store.create_or_get_item(
                {
                    "schema_version": "InboxItem-v1",
                    "item_id": item_id,
                    "batch_id": batch_id,
                    "source_name": source_name,
                    "source_kind": source_kind,
                    "state": "IMPORTING",
                    "display_state": None,
                    "content_sha256": None,
                    "bound_locator": None,
                    "job_id": "",
                    "bytes": None,
                    "extension": None,
                    "source_identity": None,
                    "workload": None,
                    "starred": False,
                    "newly_imported": False,
                    "error": None,
                    "dispatch_attempt": 0,
                    "version": 1,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            if item_id not in batch["item_ids"]:
                batch = self.store.update_batch(batch_id, lambda row, value=item_id: row["item_ids"].append(value))
            if replayed and item["state"] != "IMPORTING":
                results.append({"item": item, "replayed": True})
                continue
            if failpoint == "after_placeholder":
                raise SimulatedCrash("SIMULATED_CRASH_AFTER_PLACEHOLDER")
            try:
                item = self._bind_item(item_id, source, failpoint=failpoint)
            except SimulatedCrash:
                raise
            except InboxError as error:
                item = self._mark_error(item_id, error)
            except ValueError as error:
                item = self._mark_error(item_id, InboxError(str(error), retryable=True))
            results.append({"item": item, "replayed": replayed})
        states = [row["item"]["state"] for row in results]
        final_state = "COMPLETE" if all(state in {"QUEUED", "DEDUPLICATED"} for state in states) else "PARTIAL_ERROR"
        batch = self.store.update_batch(batch_id, lambda row: row.update({"state": final_state}))
        auto_dispatch = []
        if self.get_auto_run()["enabled"]:
            for result in results:
                if result["item"]["state"] == "QUEUED":
                    auto_dispatch.append(
                        self.dispatch(
                            result["item"]["item_id"],
                            idempotency_key=f"auto:{result['item']['item_id']}",
                        )
                    )
        return {
            "schema_version": "InboxImportBatchReceipt-v1",
            "batch": batch,
            "items": results,
            "replayed_batch": replayed_batch,
            "auto_dispatch": auto_dispatch,
            "source_files_moved_or_deleted": 0,
            "production_ingestion_effects": 0,
        }

    def enqueue_session_refinement(
        self,
        *,
        local_projection_id: str,
        display_name: str,
        source_snapshot_sha256: str,
        selected_profile_ref: str,
        message_count: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        projection_id = require_identifier(
            local_projection_id, "local_projection_id"
        )
        key = require_identifier(idempotency_key, "idempotency_key")
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 160
        ):
            raise InboxError("INBOX_REFINEMENT_DISPLAY_NAME_INVALID")
        if (
            not isinstance(source_snapshot_sha256, str)
            or not re.fullmatch(r"[0-9A-Fa-f]{64}", source_snapshot_sha256)
        ):
            raise InboxError("INBOX_REFINEMENT_SNAPSHOT_INVALID")
        if (
            not isinstance(selected_profile_ref, str)
            or not selected_profile_ref.strip()
            or len(selected_profile_ref.strip()) > 192
        ):
            raise InboxError("INBOX_REFINEMENT_PROFILE_INVALID")
        if (
            isinstance(message_count, bool)
            or not isinstance(message_count, int)
            or not 1 <= message_count <= 100_000
        ):
            raise InboxError("INBOX_REFINEMENT_MESSAGE_COUNT_INVALID")
        fingerprint = canonical_sha256(
            {
                "local_projection_id": projection_id,
                "source_snapshot_sha256": source_snapshot_sha256.upper(),
                "selected_profile_ref": selected_profile_ref.strip(),
            }
        )
        item_id = f"inbox_refine_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"
        batch_id = f"batch_refine_{hashlib.sha256(('batch\0' + key).encode('utf-8')).hexdigest()[:20]}"
        now = utc_now()
        self.store.create_or_get_batch(
            {
                "schema_version": "InboxImportBatch-v1",
                "batch_id": batch_id,
                "request_id": key,
                "request_sha256": fingerprint,
                "source_kind": "session_refinement",
                "state": "COMPLETE",
                "item_ids": [item_id],
                "created_at": now,
                "updated_at": now,
            }
        )
        item, replayed = self.store.create_or_get_item(
            {
                "schema_version": "InboxItem-v1",
                "item_id": item_id,
                "batch_id": batch_id,
                "item_kind": "SESSION_REFINEMENT",
                "source_name": display_name.strip(),
                "source_kind": "session_refinement",
                "local_projection_id": projection_id,
                "source_snapshot_sha256": source_snapshot_sha256.upper(),
                "selected_profile_ref": selected_profile_ref.strip(),
                "message_count": message_count,
                "job_id": "",
                "state": "QUEUED",
                "display_state": "已排队",
                "content_sha256": source_snapshot_sha256.upper(),
                "bound_locator": None,
                "bytes": 0,
                "extension": None,
                "source_identity": None,
                "workload": {"kind": "MESSAGES", "label": "消息", "value": message_count},
                "starred": False,
                "newly_imported": True,
                "error": None,
                "dispatch_attempt": 0,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
        if replayed:
            if (
                item.get("local_projection_id") != projection_id
                or item.get("source_snapshot_sha256") != source_snapshot_sha256.upper()
                or item.get("selected_profile_ref") != selected_profile_ref.strip()
            ):
                raise InboxError("INBOX_REFINEMENT_IDEMPOTENCY_CONFLICT")
        return {
            "schema_version": "InboxSessionRefinementEnqueueReceipt-v1",
            "item": item,
            "replayed": replayed,
            "status": "ALREADY_QUEUED" if replayed else "QUEUED",
        }

    def find_session_refinement(
        self, local_projection_id: str
    ) -> dict[str, Any] | None:
        projection_id = require_identifier(
            local_projection_id, "local_projection_id"
        )
        matches = [
            item
            for item in self.store.list_items()
            if item.get("item_kind") == "SESSION_REFINEMENT"
            and item.get("local_projection_id") == projection_id
            and item.get("state") in {"QUEUED", "PROCESSING", "ERROR"}
        ]
        matches.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return immutable(matches[0]) if matches else None

    def begin_session_refinement(
        self, item_id: str, *, job_id: str
    ) -> dict[str, Any]:
        require_identifier(item_id, "item_id")
        require_identifier(job_id, "job_id")
        item = self.store.read_item(item_id)
        if item.get("item_kind") != "SESSION_REFINEMENT":
            raise InboxError("INBOX_REFINEMENT_ITEM_REQUIRED")
        if item.get("state") == "PROCESSING" and item.get("job_id") == job_id:
            return self.store.update_item(item_id, 'REFINEMENT_ADMITTED', lambda row: row.update(execution_waiting=False))
        if item.get("state") != "QUEUED":
            raise InboxError("INBOX_REFINEMENT_START_STATE_INVALID")
        return self.store.update_item(
            item_id,
            "SESSION_REFINEMENT_STARTED",
            lambda row: row.update(
                {
                    "state": "PROCESSING",
                    "execution_waiting": False,
                    "display_state": "处理中",
                    "job_id": job_id,
                    "newly_imported": False,
                    "dispatch_attempt": int(row.get("dispatch_attempt", 0)) + 1,
                }
            ),
        )

    def complete_session_refinement(
        self,
        item_id: str,
        *,
        succeeded: bool,
        error_code: str = "",
    ) -> dict[str, Any]:
        require_identifier(item_id, "item_id")
        item = self.store.read_item(item_id)
        if item.get("item_kind") != "SESSION_REFINEMENT":
            raise InboxError("INBOX_REFINEMENT_ITEM_REQUIRED")
        if item.get("state") in {"TRASHED", "HANDED_OFF"}:
            return item
        if succeeded:
            return self.store.update_item(
                item_id,
                "SESSION_REFINEMENT_COMPLETED",
                lambda row: row.update(
                    {
                        "state": "HANDED_OFF",
                        "display_state": None,
                        "error": None,
                    }
                ),
            )
        accepted_error = (
            error_code
            if isinstance(error_code, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_:-]{0,159}", error_code)
            else "SESSION_REFINEMENT_EXECUTION_FAILED"
        )
        return self.store.update_item(
            item_id,
            "SESSION_REFINEMENT_FAILED",
            lambda row: row.update(
                {
                    "state": "ERROR",
                    "display_state": "异常",
                    "error": self._error_payload(
                        InboxError(accepted_error, retryable=True)
                    ),
                }
            ),
        )

    def cancel_session_refinement(
        self, item_id: str, *, operation_id: str
    ) -> dict[str, Any]:
        require_identifier(item_id, "item_id")
        require_identifier(operation_id, "operation_id")
        item = self.store.read_item(item_id)
        if item.get("item_kind") != "SESSION_REFINEMENT":
            raise InboxError("INBOX_REFINEMENT_ITEM_REQUIRED")
        if item.get("state") == "TRASHED":
            return {
                "schema_version": "InboxSessionRefinementCancelReceipt-v1",
                "item_id": item_id,
                "replayed": True,
                "permanent_deletes": 0,
                "status": "CANCELLED",
            }
        if item.get("state") not in {"QUEUED", "PROCESSING", "ERROR"}:
            raise InboxError("INBOX_REFINEMENT_CANCEL_STATE_INVALID")
        if item.get("state") != "ERROR":
            item = self.store.update_item(
                item_id,
                "SESSION_REFINEMENT_CANCELLED",
                lambda row: row.update(
                    {
                        "state": "ERROR",
                        "display_state": "异常",
                        "error": self._error_payload(
                            InboxError("SESSION_REFINEMENT_CANCELLED", retryable=False)
                        ),
                    }
                ),
            )
        removal = self.soft_delete([item_id], operation_id=operation_id)
        return {
            "schema_version": "InboxSessionRefinementCancelReceipt-v1",
            "item_id": item_id,
            "previous_state": item.get("state"),
            "removal": removal,
            "replayed": False,
            "permanent_deletes": 0,
            "status": "CANCELLED",
        }

    def get_auto_run(self) -> dict[str, Any]:
        return self.store.get_setting(
            "auto_run",
            {
                "schema_version": "InboxAutoRunSetting-v1",
                "enabled": False,
                "dispatch_boundary": "DURABLE_INTENT_ONLY",
                "updated_at": None,
            },
        )

    def set_auto_run(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise InboxError("INBOX_AUTO_RUN_VALUE_INVALID")
        setting = self.store.set_setting(
            "auto_run",
            {
                "schema_version": "InboxAutoRunSetting-v1",
                "enabled": enabled,
                "dispatch_boundary": "DURABLE_INTENT_ONLY",
                "updated_at": utc_now(),
                "production_ingestion": True,
            },
        )
        dispatched_item_ids: list[str] = []
        new_dispatch_count = 0
        if enabled:
            candidates = sorted(
                (
                    item
                    for item in self.store.list_items()
                    if item.get("state") == "QUEUED"
                    and item.get("item_kind", "FILE") == "FILE"
                    and item.get("source_kind") in {"picker", "drop"}
                    and item.get("content_sha256")
                    and item.get("bound_locator")
                ),
                key=lambda item: (
                    0 if item.get("starred") else 1,
                    str(item.get("created_at") or ""),
                    str(item.get("item_id") or ""),
                ),
            )
            for item in candidates:
                next_attempt = int(item.get("dispatch_attempt", 0)) + 1
                receipt = self.dispatch(
                    str(item["item_id"]),
                    idempotency_key=(
                        f"phase1-auto:{item['item_id']}:{next_attempt}"
                    ),
                )
                dispatched_item_ids.append(str(item["item_id"]))
                if receipt.get("replayed") is not True:
                    new_dispatch_count += 1
        return {
            **setting,
            "dispatched_item_ids": dispatched_item_ids,
            "new_dispatch_count": new_dispatch_count,
        }

    def retry_import(self, item_id: str, source: Path | str, *, retry_id: str) -> dict[str, Any]:
        require_identifier(retry_id, "retry_id")
        current = self.store.read_item(item_id)
        if current["state"] != "ERROR" or not current.get("error", {}).get("retryable"):
            raise InboxError("INBOX_IMPORT_RETRY_NOT_ALLOWED")
        self.store.update_item(
            item_id,
            "IMPORT_RETRY_STARTED",
            lambda row: row.update({"state": "IMPORTING", "error": None, "dispatch_attempt": int(row["dispatch_attempt"]) + 1}),
        )
        try:
            return self._bind_item(item_id, source)
        except InboxError as error:
            return self._mark_error(item_id, error)

    def recover(self) -> dict[str, Any]:
        recovered = []
        for item in self.store.list_items():
            if item["state"] != "IMPORTING":
                continue
            pending = item.get("pending_binding")
            if pending:
                path = windows_io_path(self._path(pending["bound_locator"]))
                observed = hashlib.sha256(path.read_bytes()).hexdigest().upper() if path.is_file() else None
                if observed == pending["content_sha256"]:
                    binding = {
                        "bound_path": str(path),
                        "sha256": pending["content_sha256"],
                        "bytes": pending["bytes"],
                        "source_identity": pending["source_identity"],
                    }
                    recovered.append(self._finalize_binding(item["item_id"], pending["preflight"], binding))
                    continue
            recovered.append(self._mark_error(item["item_id"], InboxError("IMPORT_RECOVERY_INCOMPLETE", retryable=True)))
        pending_intents = []
        for intent in self.store.list_intents():
            # Recover a crash between the durable cancellation receipt and its UI projection.
            if intent["state"] == "CANCELLED":
                self.reconcile_phase1_cancellation(intent)
            if intent["state"] != "PENDING":
                continue
            item = self.store.read_item(intent["item_id"])
            # A durable outbox intent is only a queued request.  The item must
            # not appear as active until the worker has created and bound the
            # real Phase 1 job.
            if item["state"] == "PROCESSING" and not item.get("job_id"):
                self.store.update_item(
                    item["item_id"],
                    "DISPATCH_RECOVERED",
                    lambda row: row.update({"state": "QUEUED", "display_state": "已排队", "error": None}),
                )
            pending_intents.append(intent["intent_id"])
        return {
            "schema_version": "InboxRecoveryReceipt-v1",
            "recovered_item_ids": [row["item_id"] for row in recovered],
            "pending_dispatch_intent_ids": pending_intents,
            "duplicate_effects": 0,
            "status": "PASS",
        }

    def reconcile_phase1_cancellation(self, intent: Mapping[str, Any]) -> bool:
        """Close the inbox projection of a cancelled task; retain its source and task history."""
        receipt = intent.get("receipt") or {}
        if (intent.get("state") != "CANCELLED"
                or receipt.get("schema_version") != "P08Build118Phase1ExecutionControl-v1"
                or receipt.get("status") != "CANCELLED"
                or receipt.get("item_id") != intent.get("item_id")):
            return False
        item = self.store.read_item(intent["item_id"])
        job_id = receipt.get("job_id")
        if (not job_id or item.get("job_id") != job_id
                or item.get("item_kind") == "SESSION_REFINEMENT"
                or item.get("state") != "PROCESSING"):
            return False
        def close_current(row):
            # A delayed receipt must never close a newer successor binding.
            if row.get("job_id") == job_id and row.get("state") == "PROCESSING":
                row.update(state="HANDED_OFF", terminal_state="CANCELLED",
                           execution_waiting=False, display_state=None, error=None)
        self.store.update_item(item["item_id"], "PHASE1_CANCELLATION_RECONCILED", close_current)
        return True

    def dispatch(self, item_id: str, *, idempotency_key: str, failpoint: str | None = None) -> dict[str, Any]:
        key = require_identifier(idempotency_key, "idempotency_key")
        item = self.store.read_item(item_id)
        if item["state"] not in {"QUEUED", "ERROR", "PROCESSING"}:
            raise InboxError("INBOX_DISPATCH_STATE_NOT_ALLOWED")
        if not item.get("content_sha256") or not item.get("bound_locator"):
            raise InboxError("INBOX_DISPATCH_INPUT_NOT_BOUND")
        params = {
            "contract_revision": CONTRACT_REVISION,
            "item_id": item_id,
            "batch_id": item["batch_id"],
            "content_sha256": item["content_sha256"],
            "bound_locator": item["bound_locator"],
            "bytes": item["bytes"],
            "effect": "START_JOB_INTENT",
            "production_ingestion_performed": False,
        }
        params_sha = canonical_sha256(params)
        intent_id = "intent_" + hashlib.sha256(f"{key}\0{params_sha}".encode("utf-8")).hexdigest()[:24]
        intent, replayed = self.store.enqueue_intent(
            {
                "schema_version": "InboxDispatchIntent-v1",
                "intent_id": intent_id,
                "item_id": item_id,
                "idempotency_key": key,
                "params": params,
                "params_sha256": params_sha,
                "state": "PENDING",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "provider_calls": 0,
                "external_model_calls": 0,
            }
        )
        if failpoint == "after_enqueue":
            raise SimulatedCrash("SIMULATED_CRASH_AFTER_OUTBOX_ENQUEUE")
        current = self.store.read_item(item_id)
        if current["state"] == "ERROR":
            self.store.update_item(
                item_id,
                "DISPATCH_QUEUED",
                lambda row: row.update({"state": "QUEUED", "display_state": "已排队", "error": None}),
            )
        return {"schema_version": "InboxDispatchEnqueueReceipt-v1", "intent": intent, "replayed": replayed}

    def bind_phase1_job(self, item_id: str, *, job_id: str) -> dict[str, Any]:
        """Bind the durable Phase 1 job created for one pending dispatch intent."""

        require_identifier(item_id, "item_id")
        require_identifier(job_id, "job_id")
        item = self.store.read_item(item_id)
        if item.get("item_kind") == "SESSION_REFINEMENT":
            raise InboxError("INBOX_PHASE1_DOCUMENT_ITEM_REQUIRED")
        existing = item.get("job_id")
        if existing:
            if existing != job_id:
                raise InboxError("INBOX_PHASE1_JOB_BINDING_CONFLICT")
            return item
        if item.get("state") not in {"QUEUED", "PROCESSING"}:
            raise InboxError("INBOX_PHASE1_JOB_BINDING_STATE_INVALID")
        return self.store.update_item(
            item_id,
            "PHASE1_JOB_BOUND",
            lambda row: row.update(
                {
                    "state": "PROCESSING", "execution_waiting": True,
                    "job_id": job_id,
                    "display_state": "处理中",
                    "newly_imported": False,
                    "dispatch_attempt": int(row["dispatch_attempt"]) + 1,
                }
            ),
        )

    def dispatch_phase1_successor(
        self,
        item_id: str,
        *,
        predecessor_job_id: str,
        successor_job_id: str,
        resume_from_node_id: str,
        idempotency_key: str,
        expected_current_job_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an exact successor intent while preserving predecessor evidence."""

        require_identifier(item_id, "item_id")
        require_identifier(predecessor_job_id, "predecessor_job_id")
        require_identifier(successor_job_id, "successor_job_id")
        require_identifier(resume_from_node_id, "resume_from_node_id")
        key = require_identifier(idempotency_key, "idempotency_key")
        item = self.store.read_item(item_id)
        if item.get("item_kind") == "SESSION_REFINEMENT":
            raise InboxError("INBOX_PHASE1_DOCUMENT_ITEM_REQUIRED")
        if item.get("state") not in {"ERROR", "HANDED_OFF", "PROCESSING"}:
            raise InboxError("INBOX_PHASE1_SUCCESSOR_STATE_INVALID")
        current_head = expected_current_job_id or predecessor_job_id
        require_identifier(current_head, "expected_current_job_id")
        if item.get("job_id") not in {current_head, successor_job_id}:
            raise InboxError("INBOX_PHASE1_SUCCESSOR_PREDECESSOR_MISMATCH")
        params = {
            "contract_revision": CONTRACT_REVISION,
            "item_id": item_id,
            "batch_id": item["batch_id"],
            "content_sha256": item["content_sha256"],
            "bound_locator": item["bound_locator"],
            "bytes": item["bytes"],
            "effect": "RETRY_PHASE1_FROM_COMPLETED_NODE",
            "predecessor_job_id": predecessor_job_id,
            "successor_job_id": successor_job_id,
            "resume_from_node_id": resume_from_node_id,
            "production_ingestion_performed": False,
        }
        params_sha = canonical_sha256(params)
        intent_id = "intent_" + hashlib.sha256(
            f"{key}\0{params_sha}".encode("utf-8")
        ).hexdigest()[:24]
        intent, replayed = self.store.enqueue_intent(
            {
                "schema_version": "InboxDispatchIntent-v1",
                "intent_id": intent_id,
                "item_id": item_id,
                "idempotency_key": key,
                "params": params,
                "params_sha256": params_sha,
                "state": "PENDING",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "provider_calls": 0,
                "external_model_calls": 0,
            }
        )
        current = self.store.read_item(item_id)
        if (
            current.get("state") != "PROCESSING"
            or current.get("job_id") != successor_job_id
            or current.get("error") is not None
        ):
            self.store.update_item(
                item_id,
                "PHASE1_SUCCESSOR_DISPATCHED",
                lambda row: row.update(
                    {
                        "state": "PROCESSING",
                        "execution_waiting": True,
                        "display_state": "处理中",
                        "error": None,
                        "job_id": successor_job_id,
                        "newly_imported": False,
                        "dispatch_attempt": int(row["dispatch_attempt"]) + 1,
                    }
                ),
            )
        return {
            "schema_version": "P08Build074InboxPhase1SuccessorIntent-v1",
            "intent": intent,
            "replayed": replayed,
            "predecessor_immutable": True,
            "status": "PASS",
        }

    def record_dispatch_receipt(
        self,
        idempotency_key: str,
        *,
        succeeded: bool,
        terminal: bool,
        reason_code: str = "NONE",
        production_ingestion_performed: bool = False,
        job_id: str | None = None,
        terminal_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(production_ingestion_performed, bool):
            raise InboxError("INBOX_PRODUCTION_INGESTION_FLAG_INVALID")
        if production_ingestion_performed:
            require_identifier(str(job_id or ""), "job_id")
            if not isinstance(terminal_receipt, Mapping):
                raise InboxError("INBOX_PHASE1_TERMINAL_RECEIPT_REQUIRED")
            terminal_job_id = terminal_receipt.get("job_id")
            terminal_state = terminal_receipt.get("terminal_state")
            if terminal_job_id != job_id or terminal_state not in {
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
            }:
                raise InboxError("INBOX_PHASE1_TERMINAL_RECEIPT_INVALID")
        state = "TERMINAL_SUCCESS" if succeeded and terminal else "ACKNOWLEDGED" if succeeded else "FAILED"
        receipt = {
            "schema_version": "InboxDispatchTerminalReceipt-v1",
            "succeeded": bool(succeeded),
            "terminal": bool(terminal),
            "reason_code": reason_code,
            "synthetic_test_receipt": not production_ingestion_performed,
            "production_ingestion_performed": production_ingestion_performed,
            "job_id": job_id,
            "terminal_receipt_sha256": (
                terminal_receipt.get("receipt_sha256")
                if isinstance(terminal_receipt, Mapping)
                else None
            ),
        }
        intent, replayed = self.store.update_intent(idempotency_key, state, receipt)
        if succeeded:
            next_state = "HANDED_OFF" if terminal else "PROCESSING"
            error = None
        else:
            next_state = "ERROR"
            error = {
                "schema_version": "InboxFileError-v1",
                "code": reason_code,
                "retryable": True,
                "scope": "FILE",
                "global_capability_block": False,
            }
        current = self.store.read_item(intent["item_id"])
        if job_id and current.get("job_id") not in {None, "", job_id}:
            raise InboxError("INBOX_PHASE1_JOB_BINDING_CONFLICT")
        if current["state"] != next_state or current.get("error") != error:
            self.store.update_item(
                intent["item_id"],
                "DISPATCH_RECEIPT_RECORDED",
                lambda row: row.update(
                    {
                        "state": next_state,
                        "display_state": None if next_state == "HANDED_OFF" else (
                            "异常" if next_state == "ERROR" else "处理中"
                        ),
                        "error": error,
                        "job_id": job_id or row.get("job_id"),
                    }
                ),
            )
        return {"schema_version": "InboxDispatchReceiptResult-v1", "intent": intent, "replayed": replayed}

    def cancel_dispatch(self, item_id: str, *, idempotency_key: str) -> dict[str, Any]:
        item = self.store.read_item(item_id)
        if item["state"] not in {"QUEUED", "PROCESSING"}:
            raise InboxError("INBOX_DISPATCH_CANCEL_NOT_ALLOWED")
        intent, replayed = self.store.update_intent(
            idempotency_key,
            "CANCELLED",
            {"schema_version": "InboxDispatchCancelReceipt-v1", "cancelled_before_external_effect": True},
        )
        self.store.update_item(item_id, "DISPATCH_CANCELLED", lambda row: row.update({"state": "QUEUED", "error": None}))
        return {"schema_version": "InboxDispatchCancelResult-v1", "intent": intent, "replayed": replayed}

    def set_starred(self, item_id: str, starred: bool) -> dict[str, Any]:
        if not isinstance(starred, bool):
            raise InboxError("INBOX_STAR_VALUE_INVALID")
        current = self.store.read_item(item_id)
        if current["state"] not in {"QUEUED", "ERROR"}:
            raise InboxError("INBOX_STAR_STATE_NOT_ALLOWED")
        return self.store.update_item(item_id, "STAR_CHANGED", lambda row: row.update({"starred": starred}))

    def soft_delete(self, item_ids: Sequence[str], *, operation_id: str) -> dict[str, Any]:
        require_identifier(operation_id, "operation_id")
        if not item_ids:
            raise InboxError("INBOX_DELETE_SELECTION_EMPTY")
        accepted_item_ids = list(dict.fromkeys(item_ids))
        existing = self.store.read_operation(operation_id)
        if existing is not None:
            recorded_ids = [
                row.get("item_id")
                for row in existing.get("items", [])
                if isinstance(row, Mapping)
            ]
            if (
                existing.get("kind") != "SOFT_DELETE_BATCH"
                or recorded_ids != accepted_item_ids
            ):
                raise InboxError("INBOX_DELETE_OPERATION_REPLAY_CONFLICT")
            return {
                "schema_version": "InboxSoftDeleteReceipt-v1",
                "operation": existing,
                "success_count": len(recorded_ids),
                "skip_count": 0,
                "skipped": [],
                "permanent_deletes": 0,
                "replayed": True,
            }
        moved = []
        skipped = []
        for item_id in accepted_item_ids:
            item = self.store.read_item(item_id)
            if item["state"] not in {"QUEUED", "ERROR"}:
                skipped.append({"item_id": item_id, "reason": f"STATE_{item['state']}_INELIGIBLE"})
                continue
            cancelled_intent_ids = []
            for intent in self.store.list_intents():
                if intent.get("item_id") != item_id or intent.get("state") != "PENDING":
                    continue
                cancelled, _ = self.store.update_intent(
                    intent["idempotency_key"],
                    "CANCELLED",
                    {
                        "schema_version": "InboxDispatchCancelReceipt-v1",
                        "cancelled_before_external_effect": True,
                        "reason": "ITEM_SOFT_DELETED",
                    },
                )
                cancelled_intent_ids.append(cancelled["intent_id"])
            locator = item.get("bound_locator")
            trash_locator = None
            if locator:
                source = self._path(locator)
                io_source = windows_io_path(source)
                if io_source.exists():
                    target = self.trash_root / f"{item_id}_{source.name}"
                    io_target = windows_io_path(target)
                    if io_target.exists():
                        raise InboxError("INBOX_TRASH_COLLISION")
                    os.replace(io_source, io_target)
                    trash_locator = self._locator(target)
            self.store.update_item(
                item_id,
                "ITEM_SOFT_DELETED",
                lambda row, previous=item["state"], trash=trash_locator: row.update(
                    {"state": "TRASHED", "pre_trash_state": previous, "trash_locator": trash}
                ),
            )
            moved.append(
                {
                    "item_id": item_id,
                    "previous_state": item["state"],
                    "bound_locator": locator,
                    "trash_locator": trash_locator,
                    "content_sha256": item.get("content_sha256"),
                    "cancelled_intent_ids": cancelled_intent_ids,
                }
            )
        if not operation_id.startswith("refinement-"):
            self.store.discard_user_operations("UNDONE")
        operation = self.store.create_operation(
            {
                "schema_version": "InboxUndoOperation-v1",
                "operation_id": operation_id,
                "kind": "SOFT_DELETE_BATCH",
                "state": "APPLIED",
                "items": moved,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        return {
            "schema_version": "InboxSoftDeleteReceipt-v1",
            "operation": operation,
            "success_count": len(moved),
            "skip_count": len(skipped),
            "skipped": skipped,
            "permanent_deletes": 0,
            "replayed": False,
        }

    def undo(self) -> dict[str, Any]:
        operation = self.store.latest_operation("APPLIED", include_internal=False)
        if operation is None:
            raise InboxError("INBOX_UNDO_NOT_AVAILABLE")
        restored = []
        for entry in operation["items"]:
            if entry["trash_locator"]:
                source = self._path(entry["trash_locator"])
                target = self._path(entry["bound_locator"])
                io_source = windows_io_path(source); io_target = windows_io_path(target)
                if io_target.exists():
                    observed = hashlib.sha256(io_target.read_bytes()).hexdigest().upper()
                    if observed != entry["content_sha256"]:
                        raise InboxError("INBOX_UNDO_BOUND_CONFLICT")
                elif io_source.exists():
                    os.replace(io_source, io_target)
            self.store.update_item(
                entry["item_id"],
                "ITEM_SOFT_DELETE_UNDONE",
                lambda row, previous=entry["previous_state"]: row.update({"state": previous, "trash_locator": None}),
            )
            restored.append(entry["item_id"])
        self.store.update_operation(operation["operation_id"], "UNDONE")
        return {"schema_version": "InboxUndoReceipt-v1", "operation_id": operation["operation_id"], "restored_item_ids": restored}

    def redo(self) -> dict[str, Any]:
        operation = self.store.latest_operation(
            "UNDONE", include_internal=False, redo_order=True
        )
        if operation is None:
            raise InboxError("INBOX_REDO_NOT_AVAILABLE")
        removed = []
        for entry in operation["items"]:
            if entry["bound_locator"]:
                source = self._path(entry["bound_locator"])
                target = self._path(entry["trash_locator"])
                io_source = windows_io_path(source); io_target = windows_io_path(target)
                if io_source.exists():
                    os.replace(io_source, io_target)
            self.store.update_item(
                entry["item_id"],
                "ITEM_SOFT_DELETE_REDONE",
                lambda row, trash=entry["trash_locator"]: row.update({"state": "TRASHED", "trash_locator": trash}),
            )
            removed.append(entry["item_id"])
        self.store.update_operation(operation["operation_id"], "APPLIED")
        return {"schema_version": "InboxRedoReceipt-v1", "operation_id": operation["operation_id"], "removed_item_ids": removed}

    def refresh(self) -> dict[str, Any]:
        changed = []
        for item in self.store.list_items():
            if item["state"] not in {"QUEUED", "PROCESSING", "ERROR"} or not item.get("bound_locator"):
                continue
            path = windows_io_path(self._path(item["bound_locator"]))
            observed = hashlib.sha256(path.read_bytes()).hexdigest().upper() if path.is_file() else None
            if observed != item.get("content_sha256"):
                error = InboxError("INBOX_BOUND_FILE_MISSING_OR_CHANGED", retryable=False)
                self._mark_error(item["item_id"], error)
                changed.append(item["item_id"])
            elif item.get("newly_imported"):
                self.store.update_item(item["item_id"], "REFRESH_STABILIZED", lambda row: row.update({"newly_imported": False}))
        return {
            "schema_version": "InboxRefreshReceipt-v1",
            "changed_item_ids": changed,
            "running_snapshots_reset": 0,
            "duplicate_items_created": 0,
            "trash_items_modified": 0,
        }

    def _execution_presentation(self, item):
        state = item["state"]
        if state == "PROCESSING" and item.get("job_id") and self.job_state_provider:
            try:
                job = self.job_state_provider(item["job_id"])
            except Exception:
                return {"display_state": "状态待刷新", "execution_state": "UNKNOWN"}
            control = job.get("control_state")
            if control in {"PAUSED", "PAUSE_REQUESTED"}:
                return {"display_state": "已暂停" if control == "PAUSED" else "暂停中",
                        "execution_state": control}
            if control in {"RUNNING","QUEUED"} and job.get("pause_node_id"):
                return {"display_state":"处理中 · 待暂停","execution_state":"PAUSE_SCHEDULED",
                        "pause_node_id":job["pause_node_id"]}
        visible = "QUEUED" if state == "PROCESSING" and item.get("execution_waiting") else state
        return {"display_state": DISPLAY_STATES.get(visible), "execution_state": visible}

    def projection(self, *, batch_mode: bool = False) -> dict[str, Any]:
        visible = [
            item
            for item in self.store.list_items()
            if item["state"] not in {"TRASHED", "HANDED_OFF", "DEDUPLICATED"}
            and (self.include_sample_projection or item.get("source_kind") != "sample")
        ]

        def key(item: Mapping[str, Any]) -> tuple[Any, ...]:
            state = item["state"]
            group = (
                0 if state == "PROCESSING" else
                1 if state == "QUEUED" and item.get("starred") else
                2 if state == "IMPORTING" else
                3 if state == "QUEUED" and item.get("newly_imported") else
                4 if state == "QUEUED" else
                5
            )
            return (group, "".join(reversed(str(item["created_at"]))) if group == 3 else str(item["created_at"]), item["item_id"])

        cards = []
        for item in sorted(visible, key=key):
            state = item["state"]
            is_refinement = item.get("item_kind") == "SESSION_REFINEMENT"
            selectable = state in {"QUEUED", "ERROR"} and not is_refinement
            cards.append(
                {
                    "item_id": item["item_id"],
                    "item_kind": item.get("item_kind", "FILE"),
                    "local_projection_id": item.get("local_projection_id"),
                    "selected_profile_ref": item.get("selected_profile_ref"),
                    "message_count": item.get("message_count"),
                    "columns": ["selection_or_star", "name_and_workload", "centered_status", "delete_slot"],
                    "name": "new file" if state == "IMPORTING" else item["source_name"],
                    "state": state,
                    **self._execution_presentation(item),
                    "execution_waiting": bool(item.get('execution_waiting')),
                    "starred": bool(item.get("starred")),
                    "workload": item.get("workload"),
                    "error": item.get("error"),
                    "delete_visible": (
                        state
                        in (
                            {"QUEUED", "PROCESSING", "ERROR"}
                            if is_refinement
                            else {"QUEUED", "ERROR"}
                        )
                        and not batch_mode
                    ),
                    "selectable": selectable if batch_mode else False,
                    "selection_disabled_reason": None if selectable else ("处理中不可批量操作" if state == "PROCESSING" else "导入中不可批量操作"),
                    "preview_mode": "NONE"
                    if item.get("item_kind") == "SESSION_REFINEMENT"
                    else "PROPERTY_ONLY"
                    if item.get("extension") in {".docx", ".pptx", ".xlsx"}
                    else "READ_ONLY_PREVIEW",
                    "extension": item.get("extension"),
                    "sample": item.get("source_kind") == "sample",
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
            )
        history = {
            "undo_available": self.store.latest_operation(
                "APPLIED", include_internal=False
            )
            is not None,
            "redo_available": self.store.latest_operation(
                "UNDONE", include_internal=False, redo_order=True
            )
            is not None,
        }
        return {
            "schema_version": "InboxProjection-v1",
            "route": "inbox",
            "toolbar_order": ["add", "refresh", "batch", "undo", "redo", "queued_count", "auto_run"],
            "sort_control": False,
            "search_control": False,
            "batch_mode": bool(batch_mode),
            "auto_run": self.get_auto_run(),
            "queued_count": sum(1 for item in visible if item['state'] == 'QUEUED' or (item['state'] == 'PROCESSING' and item.get('execution_waiting'))),
            "visible_count": len(cards),
            "cards": cards,
            "history": history,
            "global_capability_block_changes_file_state": False,
        }

    def item_detail(self, item_id: str) -> dict[str, Any]:
        item = self.store.read_item(item_id)
        if item["state"] in {"TRASHED", "DEDUPLICATED"}:
            raise InboxError("INBOX_ITEM_NOT_VISIBLE")
        return {
            "schema_version": "InboxItemDetailProjection-v1",
            "item_id": item["item_id"],
            "name": item["source_name"],
            "state": item["state"],
            **self._execution_presentation(item),
            "execution_waiting": bool(item.get("execution_waiting")),
            "extension": item.get("extension"),
            "bytes": item.get("bytes"),
            "workload": immutable(item.get("workload")),
            "bound_locator": item.get("bound_locator"),
            "content_sha256": item.get("content_sha256"),
            "source_kind": item.get("source_kind"),
            "item_kind": item.get("item_kind", "FILE"),
            "local_projection_id": item.get("local_projection_id"),
            "source_snapshot_sha256": item.get("source_snapshot_sha256"),
            "selected_profile_ref": item.get("selected_profile_ref"),
            "message_count": item.get("message_count"),
            "job_id": item.get("job_id"),
            "error": immutable(item.get("error")),
            "sample": item.get("source_kind") == "sample",
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "preview_mode": "NONE"
            if item.get("item_kind") == "SESSION_REFINEMENT"
            else "PROPERTY_ONLY"
            if item.get("extension") in {".docx", ".pptx", ".xlsx"}
            else "READ_ONLY_PREVIEW",
        }

    def close_session(self) -> dict[str, Any]:
        pending = [item for item in self.store.list_items() if item["state"] == "TRASHED"]
        cleared = self.store.clear_operation_history()
        handoff = []
        for item in pending:
            locator = item.get("trash_locator")
            if not locator:
                continue
            path = self._path(locator)
            if windows_io_path(path).exists():
                handoff.append(str(windows_io_path(path)))
        return {
            "schema_version": "InboxSessionCloseReceipt-v1",
            "undo_history_entries_cleared": cleared,
            "recycle_handoff_plan": handoff,
            "system_recycle_calls_performed": 0,
            "permanent_deletes": 0,
        }

    def effect_metrics(self) -> dict[str, Any]:
        return {
            "schema_version": "InboxEffectMetrics-v1",
            "external_network_calls": 0,
            "external_model_calls": 0,
            "provider_calls": 0,
            "production_writes": 0,
            "production_ingestion": 0,
            "route_or_profile_enablements": 0,
            "real_credential_reads": 0,
            "real_credential_writes": 0,
            "permanent_deletes": 0,
            "dispatch_intents": len(self.store.list_intents()),
            "dispatch_external_effects": 0,
            "cost_usd": 0,
            "cost_cny": 0,
        }


__all__ = ["InboxController", "SimulatedCrash"]

from __future__ import annotations

import asyncio
import threading
from functools import cached_property
from concurrent.futures import ThreadPoolExecutor
from memorive_app.errors import VersionConflict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable, Mapping, Protocol
import uuid

from memorive_folder_management import ProfileFolderManager

from memorive_app.contracts import canonical_sha256, utc_now

from .contracts import (
    Core_NODE_IDS, E2_NODE_IDS, DATA_NODE, node_ids_for, CORE_WORKER_CAPACITY,
    CoreArtifact,
    CoreExecutionContext,
    CoreExecutionResult,
)
from .task_controls import TaskControlStore
from memorive_settings.call_ledger import execution_control, ExecutionControlSignal


_SHA256 = re.compile(r"^[A-F0-9]{64}$")


class CoreWorkerInterrupted(RuntimeError):
    """Crash-style interruption: leave the durable intent pending for restart."""


class CoreExecutor(Protocol):
    def execute(
        self,
        context: CoreExecutionContext,
        *,
        completed_node_ids: tuple[str, ...],
        on_node_completed: Callable[[str, int], None],
    ) -> CoreExecutionResult: ...


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class _CheckpointStore:
    def __init__(self, run_root: Path, node_ids=Core_NODE_IDS):
        self.node_ids=tuple(node_ids)
        self.schema="DesktopCoreCheckpoint-v2" if self.node_ids==E2_NODE_IDS else "DesktopCoreCheckpoint-v1"
        self.path = run_root / "core_checkpoint.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "schema_version": self.schema,
                "completed_node_ids": [],
                "updated_at": None,
            }
        value = json.loads(self.path.read_text(encoding="utf-8"))
        completed = value.get("completed_node_ids") if isinstance(value, Mapping) else None
        if (
            value.get("schema_version") != self.schema
            or not isinstance(completed, list)
            or any(node_id not in self.node_ids for node_id in completed)
            or completed != list(dict.fromkeys(completed))
        ):
            raise ValueError("Core_CHECKPOINT_INVALID")
        expected_prefix = list(self.node_ids[: len(completed)])
        if completed != expected_prefix:
            raise ValueError("Core_CHECKPOINT_NON_PREFIX")
        return deepcopy(dict(value))

    def complete(self, node_id: str) -> dict[str, Any]:
        current = self.load()
        completed = list(current["completed_node_ids"])
        expected = self.node_ids[len(completed)] if len(completed) < len(self.node_ids) else None
        if node_id != expected:
            if node_id in completed:
                return current
            raise ValueError(f"Core_CHECKPOINT_ORDER_INVALID:{node_id}:{expected}")
        completed.append(node_id)
        updated = {
            "schema_version": self.schema,
            "completed_node_ids": completed,
            "updated_at": utc_now(),
        }
        _atomic_json(self.path, updated)
        return updated

    def initialize_prefix(self, completed_node_ids: tuple[str, ...]) -> dict[str, Any]:
        expected = tuple(self.node_ids[: len(completed_node_ids)])
        if completed_node_ids != expected:
            raise ValueError("Core_CHECKPOINT_PREFIX_INVALID")
        current = self.load()
        observed = tuple(current["completed_node_ids"])
        if observed:
            if observed != completed_node_ids:
                raise ValueError("Core_CHECKPOINT_PREFIX_CONFLICT")
            return current
        updated = {
            "schema_version": self.schema,
            "completed_node_ids": list(completed_node_ids),
            "updated_at": utc_now(),
        }
        _atomic_json(self.path, updated)
        return updated


class CoreVerticalWorker:
    """Consume Inbox intents and project one Core run through the shared facade."""

    def __init__(
        self,
        *,
        facade: Any,
        inbox: Any,
        profile_root: Path | str,
        workflow_snapshot_provider: Callable[[], Mapping[str, Any]],
        executor: CoreExecutor,
        task_control_store: TaskControlStore | None = None,
    ):
        self.facade = facade
        self.inbox = inbox
        self.profile_root = Path(profile_root).resolve(strict=False)
        self.workflow_snapshot_provider = workflow_snapshot_provider
        self.executor = executor
        self.task_control_store = task_control_store
        self._stop_event = None
        self.run_root = ProfileFolderManager(self.profile_root).path_for("Core_JOBS")
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.review_service = None

    def _execution_interrupt(self, job_id: str) -> None:
        if self._stop_event is not None and self._stop_event.is_set():
            raise ExecutionControlSignal('STOPPED')
        state = self.facade.get_job(job_id)['control_state']
        if state not in {'RUNNING', 'PAUSED', 'PAUSE_REQUESTED', 'RESUME_REQUESTED', 'CANCEL_REQUESTED'}:
            raise ExecutionControlSignal(state)

    def _execution_checkpoint(self, job_id: str) -> None:
        from . import node_progress
        while True:
            if self._stop_event is not None and self._stop_event.is_set():
                raise ExecutionControlSignal('STOPPED')
            observed = self.facade.get_job(job_id)
            state = observed['control_state']
            node_progress.control(observed)
            if state == 'RUNNING':
                return
            if state in {'PAUSED', 'PAUSE_REQUESTED', 'RESUME_REQUESTED', 'CANCEL_REQUESTED'}:
                time.sleep(0.1)
                continue
            raise ExecutionControlSignal(state)

    def _cancelled_receipt(self, job_id, item, intent, checkpoint, *, mainline_result=None):
        receipt = {'schema_version':'DesktopCoreExecutionControl-v1',
                   'status':'CANCELLED','job_id':job_id,'item_id':item['item_id'],
                   'completed_node_ids':checkpoint.load()['completed_node_ids'],
                   'artifact_ids':[a.artifact_id for a in mainline_result.artifacts] if mainline_result is not None else [], 'updated_at':utc_now(),
                   'production_ingestion_performed':mainline_result is not None,
                   'mainline_complete':mainline_result is not None,
                   'partial_stage_evidence_preserved':True}
        cancelled, _ = self.inbox.store.update_intent(intent['idempotency_key'], 'CANCELLED', receipt)
        self.inbox.reconcile_core_cancellation(cancelled)
        controls = getattr(self, 'task_control_store', None)
        if controls is not None:
            retry = self.facade.get_job(job_id).get('request', {}).get('core_retry')
            if isinstance(retry, Mapping):
                controls.update_rollback(str(retry['rollback_request_id']),
                                         status='CANCELLED', successor_job_id=job_id)
        _atomic_json(self.run_root / job_id / 'worker_cancelled_receipt.json', receipt)
        return receipt

    def _reconcile_rollback_lifecycle(self):
        if self.task_control_store is None:
            return
        requests = {r['request_id']:r for r in self.task_control_store.list_rollback_requests()}
        for projection in self.facade.list_jobs()['jobs']:
            job = self.facade.get_job(projection['job_id'])
            retry = job.get('request', {}).get('core_retry')
            if not isinstance(retry, Mapping): continue
            request = requests.get(retry.get('rollback_request_id'))
            if not request: continue
            state = job['control_state']
            if state in {'CANCELLED', 'SUCCEEDED', 'FAILED'} and request['status'] == 'DISPATCHED':
                self.task_control_store.update_rollback(request['request_id'],
                    status=state, successor_job_id=job['job_id'])
            elif state == 'QUEUED' and not job.get('artifact_bindings') and request['status'] == 'FAILED':
                self._terminal_failure(job['job_id'], 'Core_SUCCESSOR_DISPATCH_FAILED')

    def _prepare_pending_rollback(self) -> dict[str, Any] | None:
        if self.task_control_store is None:
            return None
        request = self.task_control_store.next_rollback_request()
        if request is None:
            return None
        request_id = str(request["request_id"])
        predecessor_job_id = str(request["predecessor_job_id"])
        successor_job_id = None
        try:
            predecessor = self.facade.get_job(predecessor_job_id)
            if predecessor["control_state"] not in {"FAILED", "SUCCEEDED", "CANCELLED"}:
                raise ValueError("Core_ROLLBACK_PREDECESSOR_NOT_TERMINAL")
            node_ids=node_ids_for(predecessor["snapshots"]["workflow_definition"]["content"])
            resume_from = str(request["resume_from_node_id"])
            predecessor_checkpoint = _CheckpointStore(
                self.run_root / predecessor_job_id, node_ids
            ).load()
            completed = tuple(predecessor_checkpoint["completed_node_ids"])
            # A failed node is necessarily absent from the completed prefix.
            # Permit that exact next node on FAILED jobs; rollback of completed
            # nodes keeps its previous behavior, and forward skipping is rejected.
            next_node = node_ids[len(completed)] if len(completed) < len(node_ids) else None
            retry_failed_node = predecessor['control_state'] == 'FAILED' and resume_from == next_node
            if resume_from not in completed and not retry_failed_node:
                raise ValueError("Core_ROLLBACK_NODE_NOT_COMPLETED")
            item = self.inbox.store.read_item(str(request['item_id']))
            head_id = str(item.get('job_id') or '')
            head = self.facade.get_job(head_id)
            if head['control_state'] not in {'FAILED', 'SUCCEEDED', 'CANCELLED'}:
                raise ValueError('Core_ROLLBACK_CURRENT_TASK_NOT_TERMINAL')
            head_input = head.get('request', {}).get('resolved_task_input', {})
            if (head_input.get('item_id') != request['item_id'] or
                    head_input.get('content_sha256') != item.get('content_sha256')):
                raise ValueError('Core_ROLLBACK_CURRENT_INPUT_MISMATCH')
            policy = {
                "request_id": f"core-rollback-request-{request_id}",
                "correlation_id": f"core-rollback-{request_id}",
                "resume_from_node_id": resume_from,
                "rollback_request_id": request_id,
                "workflow_config": deepcopy(request["workflow_config"]),
            }
            successor = self.facade.retry_job(
                predecessor_job_id,
                policy,
                f"core-rollback-{request_id}",
            )
            successor_job_id = str(successor["job_id"])
            self.inbox.dispatch_core_successor(
                str(request["item_id"]),
                predecessor_job_id=predecessor_job_id,
                successor_job_id=successor_job_id,
                resume_from_node_id=resume_from,
                idempotency_key=f"core-rollback-{request_id}",
                expected_current_job_id=head_id,
            )
            if self.review_service and self.review_service.store.get(predecessor_job_id):
                self.review_service.store.update(predecessor_job_id,'SUPERSEDED',{'stale':True,'successor_job_id':successor_job_id})
            self.task_control_store.update_rollback(
                request_id,
                status="DISPATCHED",
                successor_job_id=successor_job_id,
            )
            return {
                "status": "DISPATCHED",
                "request_id": request_id,
                "predecessor_job_id": predecessor_job_id,
                "successor_job_id": successor_job_id,
                "resume_from_node_id": resume_from,
            }
        except Exception as error:
            if successor_job_id:
                successor = self.facade.get_job(successor_job_id)
                if successor['control_state'] == 'QUEUED' and not successor.get('artifact_bindings'):
                    self._terminal_failure(successor_job_id, 'Core_SUCCESSOR_DISPATCH_FAILED')
            code = re.sub(r"[^A-Z0-9_:-]", "_", str(error).upper())[:120]
            self.task_control_store.update_rollback(
                request_id,
                status="FAILED",
                successor_job_id=successor_job_id,
                error_code=code or type(error).__name__.upper(),
            )
            return {
                "schema_version": "DesktopCoreRollbackPreparationFailure-v1",
                "status": "ROLLBACK_REQUEST_FAILED",
                "request_id": request_id,
                "predecessor_job_id": predecessor_job_id,
                "reason_code": code or type(error).__name__.upper(),
                "production_ingestion_performed": False,
            }

    def _prepare_successor_run(
        self,
        *,
        job: Mapping[str, Any],
        checkpoint: _CheckpointStore,
        run_root: Path,
    ) -> tuple[tuple[str, ...], dict[str, Any] | None]:
        node_ids=node_ids_for(job["snapshots"]["workflow_definition"]["content"])
        retry = job.get("request", {}).get("core_retry")
        if not isinstance(retry, Mapping):
            return tuple(checkpoint.load()["completed_node_ids"]), None
        resume_from = str(retry.get("resume_from_node_id") or "")
        if resume_from not in node_ids:
            raise ValueError("Core_ROLLBACK_RESUME_NODE_INVALID")
        prefix = tuple(node_ids[: node_ids.index(resume_from)])
        observed = tuple(checkpoint.load()['completed_node_ids'])
        if observed:
            if observed[:len(prefix)] != prefix:
                raise ValueError('Core_ROLLBACK_CHECKPOINT_PREFIX_CONFLICT')
        else:
            checkpoint.initialize_prefix(prefix)
        resume_completed = observed or prefix
        predecessor_job_id = str(retry.get("predecessor_job_id") or "")
        predecessor_stage = self.run_root / predecessor_job_id / "core_stages"
        successor_stage = run_root / "core_stages"
        required: list[str] = []
        resume_index = node_ids.index(resume_from)
        if resume_index > node_ids.index('03_CARD_DISTILL'):
            predecessor = self.facade.get_job(predecessor_job_id)
            admitted = resume_index > node_ids.index('05_CARD_ADMISSION')
            seed_name = '05_admitted_card' if admitted else '03_card_seed'
            original_seed = predecessor_stage / (seed_name + '.md')
            source = None
            expected_hash = None
            if original_seed.is_file():
                source = original_seed
                seed_binding = predecessor_stage / (seed_name + '_binding.json')
                if seed_binding.is_file():
                    expected_hash = json.loads(seed_binding.read_text(encoding='utf8'))['sha256']
                else:
                    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest().upper()
            else:
                card_bindings = [b for b in predecessor.get('artifact_bindings', []) if b.get('artifact_id') == 'core-card']
                if len(card_bindings) == 1:
                    binding = card_bindings[0]
                    prefix_locator = 'core-artifact:'+predecessor_job_id+':'
                    locator = str(binding.get('locator') or '')
                    if not locator.startswith(prefix_locator):
                        raise ValueError('Core_ROLLBACK_CARD_LOCATOR_INVALID')
                    artifact_root = (self.run_root / predecessor_job_id / 'artifacts').resolve()
                    source = (artifact_root / locator[len(prefix_locator):]).resolve(strict=True)
                    if not source.is_relative_to(artifact_root):
                        raise ValueError('Core_ROLLBACK_CARD_LOCATOR_INVALID')
                    expected_hash = str(binding['sha256']).upper()
                elif predecessor['control_state'] == 'FAILED':
                    failed_seed = predecessor_stage / '04_card_revision/card_input.snapshot'
                    if admitted:
                        receipt_path = predecessor_stage / '04_05_review_admission.json'
                        receipt = json.loads(receipt_path.read_text(encoding='utf8'))
                        candidates = list((predecessor_stage / '04_card_revision').glob('[[]Card]*.md'))
                        if len(candidates) != 1 or receipt.get('outcome') != 'active':
                            raise ValueError('Core_ROLLBACK_ADMITTED_CARD_MISSING')
                        failed_seed = candidates[0]
                        expected_hash = str(receipt['card_sha256']).upper()
                    if failed_seed.is_file():
                        source = failed_seed
                        expected_hash = expected_hash or hashlib.sha256(source.read_bytes()).hexdigest().upper()
                else:
                    raise ValueError('Core_ROLLBACK_CARD_BINDING_MISSING')
            if source is not None:
                if hashlib.sha256(source.read_bytes()).hexdigest().upper() != expected_hash:
                    raise ValueError('Core_ROLLBACK_CARD_HASH_MISMATCH')
                successor_stage.mkdir(parents=True,exist_ok=True)
                seed = successor_stage / (seed_name + '.md')
                if not seed.exists(): shutil.copy2(source,seed)
                if hashlib.sha256(seed.read_bytes()).hexdigest().upper() != expected_hash:
                    raise ValueError('Core_ROLLBACK_CARD_SEED_MISMATCH')
                _atomic_json(successor_stage / (seed_name + '_binding.json'), {
                    'sha256':expected_hash, 'predecessor_job_id':predecessor_job_id,
                    'source_sha256':job['request']['input_hashes']['input']})
        if resume_index > node_ids.index("06_CONTEXT_PACK"):
            required.extend(["06_context_pack.pkl", "06_context_pack.json"])
        if resume_index > node_ids.index("07_ANALYSIS"):
            required.extend(["07_analysis.pkl", "07_analysis.json", "07_analysis.md"])
        if resume_index > node_ids.index("08_JUDGMENT_CROSS_CHECK"):
            required.append("08_judgment.json")
        if resume_from == '05_CARD_ADMISSION':
            # Node 04 already completed. Carry its source-bound private review
            # receipt so admission can publish without another model review.
            review_root = predecessor_stage / '04_card_revision'
            reviewed = list(review_root.glob('[[]Card]*.md'))
            if len(reviewed) != 1:
                raise ValueError('Core_ROLLBACK_REVIEW_CARDINALITY')
            receipt = json.loads((predecessor_stage / '04_05_review_admission.json').read_text(encoding='utf8'))
            if receipt.get('outcome') != 'active' or receipt.get('card_sha256') != hashlib.sha256(reviewed[0].read_bytes()).hexdigest().upper():
                raise ValueError('Core_ROLLBACK_REVIEW_HASH_MISMATCH')
            required.extend(['04_05_review_admission.json', '04_card_revision/card_input.snapshot',
                             '04_card_revision/' + reviewed[0].name])
        for name in required:
            source = predecessor_stage / name
            if not source.is_file():
                raise ValueError(f"Core_ROLLBACK_SEED_ARTIFACT_MISSING:{name}")
            destination = successor_stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file():
                shutil.copy2(source, destination)
            if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                raise ValueError(f'Core_ROLLBACK_SEED_HASH_MISMATCH:{name}')
        return resume_completed, deepcopy(dict(retry))

    @staticmethod
    def _profile_snapshot(
        *, input_sha256: str, settings_sha256: str, created_at: str
    ) -> dict[str, Any]:
        return {
            "profile_id": f"desktop-workflow-core-{settings_sha256[:16].lower()}",
            "role": "core_vertical_pipeline",
            "capability_revision": "Desktop_Core_VERTICAL_Capabilities_SUCCESSOR_V2",
            "config_revision": settings_sha256,
            "config_sha256": settings_sha256,
            "credential_refs": [],
            "execution_channel": "Desktop_DESKTOP_Core_BRIDGE",
            "fallback_policy": "FAIL_CLOSED",
            "resource_policy": {"single_writer_per_paper": True},
            "budget_policy": {"local_model_call_limit": None},
            "egress_policy": "PER_NODE_SAVED_PROFILE_ONLY",
            "data_classification": "USER_DOCUMENT",
            "module_revisions": {
                "M01": "Core_SNAPSHOT",
                "M02": "Core_SNAPSHOT",
                "M03": "Core_SNAPSHOT",
                "M04": "Core_SNAPSHOT",
                "M06": "Core_SNAPSHOT",
                "M08": "Core_SNAPSHOT",
                "M09": "Core_SNAPSHOT_PLUS_Desktop_BINDING",
                "RUNTIME_LOG": "Core_SNAPSHOT",
                "LONG_DOCUMENT": "CURRENT_FORMAL_SEGMENTED_DISTILL_SUCCESSOR",
            },
            "source_hashes": {"input": input_sha256},
            # The Inbox item's immutable creation timestamp keeps the complete
            # start_job request byte-identical across process restarts.
            "created_at": created_at,
            "resolver_revision": "Desktop_SETTINGS_V5_BINDING_V1",
        }

    def _request(
        self,
        item: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        settings_sha256 = str(snapshot.get("settings_sha256") or "").upper()
        input_sha256 = str(item.get("content_sha256") or "").upper()
        if (
            snapshot.get("configuration_ready") is not True
            or not _SHA256.fullmatch(settings_sha256)
            or not _SHA256.fullmatch(input_sha256)
            or not isinstance(snapshot.get("workflow_definition"), Mapping)
            or not isinstance(snapshot.get("workflow_config"), Mapping)
        ):
            raise ValueError("Core_WORKFLOW_SNAPSHOT_NOT_READY")
        item_id = str(item["item_id"])
        return {
            "contract_revision": "1.0",
            "request_id": f"core-request-{item_id}",
            "correlation_id": f"inbox-{item_id}",
            "job_type": "memorive-core",
            "payload_ref": f"inbox:{item_id}",
            "input_hashes": {"input": input_sha256},
            "profile_snapshot": self._profile_snapshot(
                input_sha256=input_sha256,
                settings_sha256=settings_sha256,
                created_at=str(item.get("created_at") or ""),
            ),
            "capabilities": [
                {
                    "capability_id": "core-vertical-pipeline",
                    "requirement": "required",
                    "availability": "ready",
                    "qualification": "qualified",
                    "effective_enabled": True,
                    "reason_code": "NONE",
                    "required_action": "none",
                }
            ],
            "pause_supported": True,
            "resolved_task_input": {
                "classification": "USER_DOCUMENT",
                "item_id": item_id,
                "display_name": str(item.get("source_name") or item_id),
                "content_sha256": input_sha256,
                "bound_locator": str(item.get("bound_locator") or ""),
            },
            "workflow_definition": deepcopy(snapshot["workflow_definition"]),
            "workflow_config": deepcopy(snapshot["workflow_config"]),
        }

    def _source_path(self, item: Mapping[str, Any]) -> Path:
        path = self.inbox._path(str(item.get("bound_locator") or ""))
        if not path.is_file():
            raise ValueError("Core_BOUND_SOURCE_MISSING")
        observed = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if observed != str(item.get("content_sha256") or "").upper():
            raise ValueError("Core_BOUND_SOURCE_HASH_MISMATCH")
        return path

    def _bind_artifact(self, context: CoreExecutionContext, artifact: CoreArtifact) -> None:
        path = Path(artifact.path).resolve(strict=True)
        root = context.artifact_root.resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError("Core_ARTIFACT_OUTSIDE_JOB_ROOT")
        observed = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if observed != artifact.sha256.upper() or not _SHA256.fullmatch(observed):
            raise ValueError("Core_ARTIFACT_HASH_MISMATCH")
        relative = path.relative_to(root).as_posix()
        locator = f"core-artifact:{context.job_id}:{relative}"
        for attempt in range(12):
            job=self.facade.get_job(context.job_id)
            existing={row['artifact_id']:row for row in job.get('artifact_bindings',[])}
            if artifact.artifact_id in existing:
                row=existing[artifact.artifact_id]
                if row.get('sha256')!=observed or row.get('locator')!=locator:raise ValueError('Core_ARTIFACT_REPLAY_CONFLICT')
                return
            try:
                self.facade.bind_artifact(context.job_id,expected_version=job['version'],artifact_id=artifact.artifact_id,locator=locator,sha256=observed)
                return
            except VersionConflict:
                if attempt==11:raise


    def _terminal_failure(self, job_id: str, reason_code: str) -> None:
        job = self.facade.get_job(job_id)
        if job['control_state'] == 'QUEUED' and not job.get('artifact_bindings'):
            # No executor has run this orphan. Use the lifecycle's queued
            # cancellation edge; a queued job cannot transition to FAILED.
            self.facade.cancel_job(job_id, expected_version=job['version'],
                                   idempotency_key='core-dispatch-failed:'+job_id)
            return
        if job["control_state"] not in {"FAILED", "CANCELLED", "SUCCEEDED"}:
            self.facade._transition(
                job_id,
                job["version"],
                "FAILED",
                f"Core_FAILED:{reason_code}",
            )

    @cached_property
    def _dispatch_state(self):
        return {'lock': threading.Lock(), 'intents': set(), 'sources': set()}

    def run_pending_once(self) -> dict[str, Any]:
        if self.review_service:
            review_job=self.review_service.claim()
            if review_job:return self.review_service.run_claimed(review_job)
        with self._dispatch_state['lock']:
            claimed = self._claim_pending_intent()
        if isinstance(claimed, dict):
            return claimed
        intent, item = claimed
        try:
            return self._run_intent(intent, item)
        except VersionConflict:
            # A control change between queue claim and mark_running is not a
            # provider failure. No model operation has been replayed here.
            return {'status': 'CONTROL_CHANGED', 'item_id': item['item_id']}
        except (ExecutionControlSignal, CoreWorkerInterrupted):
            raise
        except Exception as error:
            # Preparation failures must be visible and must not kill the whole
            # queue. Execution failures already produce per-job receipts below.
            receipt = {'status':'FAILED', 'item_id':item['item_id'],
                       'error_type':type(error).__name__, 'reason_code':'Core_DISPATCH_FAILED',
                       'updated_at':utc_now(), 'production_ingestion_performed':False}
            self.inbox.store.update_intent(intent['idempotency_key'], 'FAILED', receipt)
            _atomic_json(self.run_root / ('dispatch_failure_'+str(intent['intent_id'])+'.json'), receipt)
            bound = self.inbox.store.read_item(item['item_id']).get('job_id')
            if bound:
                self._terminal_failure(bound, 'Core_DISPATCH_FAILED:'+type(error).__name__)
            return receipt
        finally:
            with self._dispatch_state['lock']:
                self._dispatch_state['intents'].discard(str(intent['intent_id']))
                self._dispatch_state['sources'].discard(str(item.get('content_sha256') or item['item_id']))

    def _claim_pending_intent(self):
        rollback_preparation = self._prepare_pending_rollback()
        if rollback_preparation is not None and rollback_preparation.get("status") == "ROLLBACK_REQUEST_FAILED":
            return rollback_preparation
        pending: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        skipped_non_core = 0
        for intent in self.inbox.store.list_intents():
            if intent.get("state") != "PENDING":
                continue
            item = self.inbox.store.read_item(str(intent.get("item_id") or ""))
            # The Inbox outbox is shared with session refinement and historical
            # fixture samples.  The document worker must never consume either
            # class when a profile later starts in production mode.
            if (
                item.get("item_kind") == "SESSION_REFINEMENT"
                or item.get("source_kind") not in {"picker", "drop"}
            ):
                skipped_non_core += 1
                continue
            if self.review_service and item.get('job_id') and self.review_service.blocks_main_claim(item['job_id']):
                continue
            identity = str(intent['intent_id'])
            source = str(item.get('content_sha256') or item['item_id'])
            if identity not in self._dispatch_state['intents'] and source not in self._dispatch_state['sources']:
                pending.append((intent, item))
        if not pending:
            return {
                "schema_version": "DesktopCoreWorkerReceipt-v1",
                "status": "IDLE",
                "production_ingestion_performed": False,
                "skipped_non_core_intent_count": skipped_non_core,
            }
        intent, item = pending[0]
        self._dispatch_state['intents'].add(str(intent['intent_id']))
        self._dispatch_state['sources'].add(str(item.get('content_sha256') or item['item_id']))
        return intent, item

    def _run_intent(self, intent, item) -> dict[str, Any]:
        intent_params = intent.get("params") if isinstance(intent.get("params"), Mapping) else {}
        successor_intent = intent_params.get("effect") == "RETRY_Core_FROM_COMPLETED_NODE"
        if not successor_intent and item.get('job_id'):
            try:
                prior = self.facade.lookup_start_by_idempotency_key(intent['idempotency_key'])
            except ValueError as error:
                if 'START_IDEMPOTENCY_KEY_NOT_FOUND' not in str(error):
                    raise
                prior = None
            bound_id = str(item['job_id'])
            if not prior or prior.get('job_id') != bound_id:
                orphan_id = prior.get('job_id') if prior else None
                if orphan_id:
                    orphan = self.facade.get_job(orphan_id)
                    if orphan['control_state'] == 'QUEUED' and not orphan.get('artifact_bindings'):
                        self.facade.cancel_job(orphan_id, expected_version=orphan['version'],
                                              idempotency_key='deduplicate:'+intent['intent_id'])
                    elif orphan['control_state'] not in {'CANCELLED','FAILED'}:
                        raise ValueError('INBOX_DUPLICATE_JOB_ALREADY_EXECUTED')
                receipt = {'status':'DUPLICATE_DISPATCH_RECONCILED','job_id':bound_id,
                           'duplicate_job_id':orphan_id,'production_ingestion_performed':False}
                self.inbox.store.update_intent(intent['idempotency_key'], 'CANCELLED', receipt)
                return receipt
        if successor_intent:
            job_id = str(intent_params.get("successor_job_id") or "")
            job = self.facade.get_job(job_id)
        else:
            frozen=intent_params.get('workflow_snapshot')
            if frozen is None:
                # Legacy intents bind once; restart uses the facade's prior snapshots.
                try:
                    prior=self.facade.lookup_start_by_idempotency_key(intent['idempotency_key'])
                except ValueError:
                    prior=None
                if prior:
                    previous=self.facade.get_job(prior['job_id'])
                    frozen={**self.workflow_snapshot_provider(), 'configuration_ready':True,
                        'workflow_definition':previous['snapshots']['workflow_definition']['content'],
                        'workflow_config':previous['snapshots']['workflow_config']['content']}
                    frozen['settings_sha256']=previous['request']['profile_snapshot']['config_sha256']
            snapshot = deepcopy(dict(frozen or self.workflow_snapshot_provider()))
            if intent_params.get('literature_review') is not None:
                snapshot['workflow_config']['literature_review']=deepcopy(intent_params['literature_review'])
            request = self._request(item, snapshot)
            handle = self.facade.start_job(request, intent["idempotency_key"])
            job_id = str(handle["job_id"])
            self.inbox.bind_core_job(item["item_id"], job_id=job_id)
            job = self.facade.get_job(job_id)
        source_path = self._source_path(item)
        if job["control_state"] == "QUEUED":
            status = self.facade.mark_running(job_id, expected_version=job["version"])
            status = self.facade.set_safe_checkpoint(
                job_id,
                expected_version=status["observed_version"],
                safe=True,
            )
            job = self.facade.get_job(job_id)
        elif job["control_state"] not in {"RUNNING", "PAUSED", "CANCELLED"}:
            raise ValueError(f"Core_JOB_NOT_RUNNABLE:{job['control_state']}")

        run_root = self.run_root / job_id
        artifact_root = run_root / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        definition=job['snapshots']['workflow_definition']['content']
        node_ids=node_ids_for(definition)
        checkpoint = _CheckpointStore(run_root,node_ids)
        if self.task_control_store is not None:self.task_control_store.bind_topology(job_id,definition)
        if job['control_state'] == 'CANCELLED':
            return self._cancelled_receipt(job_id, item, intent, checkpoint)
        completed, retry_metadata = self._prepare_successor_run(
            job=job,
            checkpoint=checkpoint,
            run_root=run_root,
        )
        if self.task_control_store is not None:
            for node_id in completed:
                self.task_control_store.mark_started(job_id, node_id)
        context = CoreExecutionContext(
            job_id=job_id,
            attempt_id=job["attempt_id"],
            item_id=str(item["item_id"]),
            source_path=source_path,
            source_name=str(item.get("source_name") or source_path.name),
            source_sha256=str(item["content_sha256"]).upper(),
            run_root=run_root,
            artifact_root=artifact_root,
            workflow_definition=job["snapshots"]["workflow_definition"]["content"],
            workflow_config=job["snapshots"]["workflow_config"]["content"],
        )

        if self.review_service and DATA_NODE in node_ids:self.review_service.register(context)

        def on_activity(state: str, node_id: str | None = None) -> None:
            # PAUSED_BEFORE runs under the pause-point transaction. Never wait
            # for resume there: the resume operation needs the same lock.
            if state != 'PAUSED_BEFORE':
                self._execution_checkpoint(job_id)
            else:
                self._execution_interrupt(job_id)
            done = len(checkpoint.load()['completed_node_ids'])
            current = node_id or node_ids[min(done, len(node_ids)-1)]
            from . import node_progress
            node_progress.activity(state, current)
            if state == 'PAUSED_BEFORE':
                self.facade.record_progress(job_id,completed_units=done,total_units=len(node_ids),
                    phase_code='WAITING_'+current,phase_label=current,
                    last_checkpoint_ref=f'core-checkpoint:{job_id}')
                for attempt in range(3):
                    fresh = self.facade.get_job(job_id)
                    if fresh['control_state'] == 'PAUSED':
                        return
                    try:
                        self.facade.pause_job(job_id,expected_version=fresh['version'],
                            idempotency_key=f'node-pause-{job_id}-{current}-{fresh["version"]}')
                        return
                    except Exception as error:
                        if attempt == 2 or getattr(error,'code',str(error)) != 'EXPECTED_VERSION_CONFLICT':
                            raise
                return
            self.facade.record_progress(
                job_id, completed_units=done, total_units=len(node_ids),
                phase_code=('WAITING_' if state == 'WAITING' else '') + current,
                phase_label=current, last_checkpoint_ref=f'core-checkpoint:{job_id}',
            )
            self.inbox.store.update_item(item['item_id'], 'EXECUTION_ACTIVITY',
                lambda row: row.update(execution_waiting=state == 'WAITING'))

        def on_node_completed(node_id: str, completed_units: int) -> None:
            self._execution_checkpoint(job_id)
            if node_id not in node_ids:
                raise ValueError("Core_PROGRESS_NODE_UNKNOWN")
            expected_units = node_ids.index(node_id) + 1
            if completed_units != expected_units:
                raise ValueError("Core_PROGRESS_UNIT_MISMATCH")
            if self.task_control_store is not None:
                self.task_control_store.mark_started(job_id, node_id)
            state = checkpoint.complete(node_id)
            from . import node_progress
            node_progress.activity('CLOSED', node_id)
            self.facade.record_progress(
                job_id,
                completed_units=len(state["completed_node_ids"]),
                total_units=len(node_ids),
                phase_code=node_id,
                phase_label=node_id,
                last_checkpoint_ref=f"core-checkpoint:{job_id}",
            )

        try:
            from memorive_settings.task_scheduling import activity_scope
            from .node_progress import session_scope, facade_session
            with execution_control(lambda: self._execution_checkpoint(job_id),
                                   interrupt=lambda: self._execution_interrupt(job_id)), activity_scope(on_activity), session_scope(facade_session(self.facade, job_id, job['attempt_id'])):
                on_activity('WAITING')
                self._execution_checkpoint(job_id)
                result = self.review_service.restore_result(job_id) if self.review_service else None
                if result is None:
                    result = self.executor.execute(
                        context, completed_node_ids=completed, on_node_completed=on_node_completed,
                    )
            self._execution_checkpoint(job_id)
            if not isinstance(result, CoreExecutionResult):
                raise ValueError("Core_EXECUTOR_RESULT_INVALID")
            final_checkpoint = checkpoint.load()
            if tuple(final_checkpoint["completed_node_ids"]) != node_ids:
                raise ValueError("Core_EXECUTOR_INCOMPLETE")
            for artifact in result.artifacts:
                self._execution_checkpoint(job_id)
                self._bind_artifact(context, artifact)
            self._execution_checkpoint(job_id)
            if self.review_service and DATA_NODE in node_ids:
                if not self.review_service.mainline_done(context,result):
                    return {'status':'WAITING_REVIEW','job_id':job_id,'mainline_complete':True,'production_ingestion_performed':True}
                review=self.review_service.store.get(job_id)
                if review['logic_state']=='CANCELLED':
                    current=self.facade.get_job(job_id)
                    self.facade.cancel_job(job_id,expected_version=current['version'],idempotency_key='review-cancel:'+job_id)
                    return self._cancelled_receipt(job_id,item,intent,checkpoint,mainline_result=result)
            job = self.facade.get_job(job_id)
            if job["control_state"] != "SUCCEEDED":
                self.facade._transition(
                    job_id,
                    job["version"],
                    "SUCCEEDED",
                    "Core_VERTICAL_PIPELINE_SUCCEEDED",
                )
            terminal = self.facade.get_terminal_receipt(job_id)
            inbox_receipt = self.inbox.record_dispatch_receipt(
                intent["idempotency_key"],
                succeeded=True,
                terminal=True,
                reason_code="NONE",
                production_ingestion_performed=True,
                job_id=job_id,
                terminal_receipt=terminal,
            )
            receipt = {
                "schema_version": "DesktopCoreWorkerReceipt-v1",
                "status": "SUCCEEDED",
                "job_id": job_id,
                "item_id": item["item_id"],
                "completed_node_ids": list(final_checkpoint["completed_node_ids"]),
                "artifact_ids": [row.artifact_id for row in result.artifacts],
                "metrics": deepcopy(dict(result.metrics)),
                "terminal_receipt_sha256": terminal["receipt_sha256"],
                "inbox_intent_state": inbox_receipt["intent"]["state"],
                "production_ingestion_performed": True,
                "predecessor_job_id": (
                    retry_metadata.get("predecessor_job_id")
                    if isinstance(retry_metadata, Mapping)
                    else None
                ),
                "resume_from_node_id": (
                    retry_metadata.get("resume_from_node_id")
                    if isinstance(retry_metadata, Mapping)
                    else None
                ),
            }
            if isinstance(retry_metadata, Mapping) and self.task_control_store is not None:
                self.task_control_store.update_rollback(
                    str(retry_metadata["rollback_request_id"]),
                    status="SUCCEEDED",
                    successor_job_id=job_id,
                )
            _atomic_json(run_root / "worker_terminal_receipt.json", receipt)
            return receipt
        except ExecutionControlSignal as signal:
            if signal.state == 'CANCELLED':
                return self._cancelled_receipt(job_id, item, intent, checkpoint)
            if signal.state == 'STOPPED':
                return {'status':'STOPPED','job_id':job_id,'durable_intent':'PENDING'}
            raise CoreWorkerInterrupted('Core_EXECUTION_STATE_'+signal.state)
        except CoreWorkerInterrupted:
            raise
        except Exception as error:
            failed_checkpoint = checkpoint.load()
            if self.facade.get_job(job_id)['control_state'] == 'CANCELLED':
                return self._cancelled_receipt(job_id, item, intent, checkpoint)
            failed_completed = tuple(failed_checkpoint["completed_node_ids"])
            failed_node_id = node_ids[
                min(len(failed_completed), len(node_ids) - 1)
            ]
            self.facade.record_progress(
                job_id,
                completed_units=len(failed_completed),
                total_units=len(node_ids),
                phase_code=failed_node_id,
                phase_label=failed_node_id,
                last_checkpoint_ref=f"core-checkpoint:{job_id}",
            )
            code = re.sub(r"[^A-Z0-9_:-]", "_", str(error).upper())[:120]
            code = code or type(error).__name__.upper()
            root_reason_code = getattr(error, "root_reason_code", None) or code
            caused_by = getattr(error, "caused_by", None)
            self._terminal_failure(job_id, code)
            self.inbox.record_dispatch_receipt(
                intent["idempotency_key"],
                succeeded=False,
                terminal=True,
                reason_code=code,
                production_ingestion_performed=True,
                job_id=job_id,
                terminal_receipt=self.facade.get_terminal_receipt(job_id),
            )
            failure = {
                "schema_version": "DesktopCoreWorkerFailure-v1",
                "status": "FAILED",
                "job_id": job_id,
                "item_id": item["item_id"],
                "reason_code": code,
                "root_reason_code": root_reason_code,
                "caused_by": caused_by,
                "error_type": type(error).__name__,
                "error_node_id": failed_node_id,
                "completed_node_ids": list(failed_completed),
                "production_ingestion_performed": True,
                "updated_at": utc_now(),
            }
            if isinstance(retry_metadata, Mapping) and self.task_control_store is not None:
                self.task_control_store.update_rollback(
                    str(retry_metadata["rollback_request_id"]),
                    status="FAILED",
                    successor_job_id=job_id,
                    error_code=code,
                )
            _atomic_json(run_root / "worker_failure_receipt.json", failure)
            return failure

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        idle_seconds: float = 0.4,
    ) -> None:
        self._stop_event = stop_event
        self._dispatch_state  # initialize before entering pool threads
        if hasattr(self, "task_control_store"):
            self._reconcile_rollback_lifecycle()
        pool = ThreadPoolExecutor(max_workers=CORE_WORKER_CAPACITY, thread_name_prefix='MemoCore')
        active = set()
        try:
            while not stop_event.is_set():
                for future in list(active):
                    if not future.done():
                        continue
                    active.remove(future)
                    try:
                        future.result()
                    except (ExecutionControlSignal, CoreWorkerInterrupted):
                        # Cooperative interruption leaves the durable prefix;
                        # unrelated queued work remains schedulable.
                        pass
                    except Exception as error:
                        _atomic_json(self.run_root / ('scheduler_failure_'+uuid.uuid4().hex+'.json'),
                                     {'error_type':type(error).__name__, 'updated_at':utc_now(),
                                      'reason_code':'Core_SCHEDULER_ITERATION_FAILED'})
                if len(active) < CORE_WORKER_CAPACITY:
                    active.add(pool.submit(self.run_pending_once))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=idle_seconds)
                except TimeoutError:
                    pass
        finally:
            # Dedicated pipeline threads cannot starve UI/service RPC workers.
            # Keep stop visible after cancellation of this async shell.
            stop_event.set()
            pool.shutdown(wait=False, cancel_futures=True)



__all__ = ["CoreVerticalWorker", "CoreWorkerInterrupted"]

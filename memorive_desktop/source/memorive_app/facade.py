from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .capabilities import build_capability_snapshot, evaluate_preflight
from .contracts import (
    CONTRACT_REVISION,
    canonical_sha256,
    immutable_copy,
    require_sha256,
    require_text,
    resolved_profile_snapshot,
    utc_now,
)
from .diagnostics import DiagnosticCore
from .errors import (
    CapabilityBlocked,
    ContractViolation,
    PauseUnsupported,
    ResumeInputDrift,
)
from .lifecycle import apply_transition
from .support_bundle import SupportBundleBuilder


TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED_BEFORE_START"})


def _job_id(namespace: str, idempotency_key: str, payload_sha256: str) -> str:
    digest = hashlib.sha256(f"{namespace}\0{idempotency_key}\0{payload_sha256}".encode("utf-8")).hexdigest()
    return f"job_{digest[:24]}"


def _profile_from_request(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "profile_id",
        "role",
        "capability_revision",
        "config_revision",
        "config_sha256",
        "credential_refs",
        "execution_channel",
        "fallback_policy",
        "resource_policy",
        "budget_policy",
        "egress_policy",
        "data_classification",
        "module_revisions",
        "source_hashes",
        "created_at",
        "resolver_revision",
    }
    if set(value) - (required | {"schema_version", "contract_revision", "snapshot_ref"}):
        raise ContractViolation("PROFILE_SNAPSHOT_UNKNOWN_FIELD")
    missing = sorted(required - set(value))
    if missing:
        raise ContractViolation("PROFILE_SNAPSHOT_MISSING_FIELD:" + ",".join(missing))
    return resolved_profile_snapshot(**{key: value[key] for key in required})


class ApplicationFacade:
    contract_revision = CONTRACT_REVISION

    def __init__(
        self,
        store: Any,
        *,
        directory_layout: Any | None = None,
        schema_root: Path | None = None,
        workflow_provider: Any | None = None,
    ):
        self.store = store
        self._node_progress_live = {}
        self.directory_layout = directory_layout
        self.schema_root = schema_root or Path(__file__).with_name("schemas")
        self.workflow_provider = workflow_provider
        self._last_capability_snapshot: dict[str, Any] = {
            "schema_version": "CapabilitySnapshot-v1",
            "status": "READY",
            "revision": "empty",
            "created_at": utc_now(),
            "ttl_seconds": 60,
            "capabilities": [],
            "snapshot_ref": "0" * 64,
        }

    def _validate_request(self, request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if not isinstance(request, Mapping):
            raise ContractViolation("JOB_REQUEST_TYPE_INVALID")
        required = {
            "contract_revision",
            "request_id",
            "correlation_id",
            "job_type",
            "payload_ref",
            "input_hashes",
            "profile_snapshot",
            "capabilities",
            "pause_supported",
        }
        missing = sorted(required - set(request))
        if missing:
            raise ContractViolation("JOB_REQUEST_MISSING_FIELD:" + ",".join(missing))
        if request["contract_revision"] != CONTRACT_REVISION:
            raise ContractViolation("CONTRACT_REVISION_UNSUPPORTED")
        for key in ("request_id", "correlation_id", "job_type", "payload_ref"):
            require_text(request[key], key)
        if not isinstance(request["input_hashes"], Mapping) or not request["input_hashes"]:
            raise ContractViolation("INPUT_HASHES_INVALID")
        if not isinstance(request["pause_supported"], bool):
            raise ContractViolation("PAUSE_SUPPORTED_INVALID")
        profile = _profile_from_request(request["profile_snapshot"])
        capabilities = build_capability_snapshot(
            request["capabilities"],
            revision=profile["capability_revision"],
            created_at=utc_now(),
            ttl_seconds=60,
            source="start_job_request",
        )
        accepted = immutable_copy(request)
        accepted["profile_snapshot"] = profile
        accepted["capability_snapshot_ref"] = capabilities["snapshot_ref"]
        return accepted, profile, capabilities

    @staticmethod
    def _snapshot(value: Any) -> dict[str, Any]:
        content = immutable_copy(value)
        return {"content": content, "sha256": canonical_sha256(content)}

    def _request_snapshots(self, accepted: Mapping[str, Any]) -> dict[str, Any]:
        resolved_task_input = accepted.get("resolved_task_input") or {
            "payload_ref": accepted["payload_ref"],
            "input_hashes": accepted["input_hashes"],
        }
        workflow_config = accepted.get("workflow_config") or {}
        workflow_id = accepted.get("workflow_definition_id")
        if workflow_id is not None:
            if self.workflow_provider is None:
                raise ContractViolation("WORKFLOW_DEFINITION_PROVIDER_REQUIRED")
            workflow_definition: Any = self.workflow_provider.resolve(workflow_id)
        elif "workflow_definition" in accepted:
            workflow_definition = accepted["workflow_definition"]
        else:
            workflow_definition = {
                "schema_version": "WorkflowDefinitionSnapshot-v1",
                "workflow_id": "UNSPECIFIED",
                "definition": {},
                "definition_sha256": canonical_sha256({}),
                "source_file_sha256": None,
                "source_name": None,
            }
        return {
            "resolved_task_input": self._snapshot(resolved_task_input),
            "workflow_config": self._snapshot(workflow_config),
            "workflow_definition": self._snapshot(workflow_definition),
        }

    def _start_job(
        self,
        request: Mapping[str, Any],
        idempotency_key: str,
        *,
        namespace: str,
        predecessor_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        key = require_text(idempotency_key, "idempotency_key")
        accepted, profile, capabilities = self._validate_request(request)
        snapshots = self._request_snapshots(accepted)
        idempotency_payload = dict(accepted)
        idempotency_payload.pop("capability_snapshot_ref", None)
        idempotency_payload["resolved_snapshot_sha256s"] = {
            name: value["sha256"] for name, value in sorted(snapshots.items())
        }
        payload_sha256 = canonical_sha256(idempotency_payload)
        job_id = _job_id(namespace, key, payload_sha256)
        attempt_id = f"{job_id}_attempt_001"
        preflight = evaluate_preflight(capabilities)
        state = "BLOCKED_BEFORE_START" if preflight["blockers"] else "QUEUED"
        now = utc_now()
        job = {
            "schema_version": "ApplicationJobRecord-v1",
            "contract_revision": CONTRACT_REVISION,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "predecessor_attempt_id": predecessor_attempt_id,
            "request": accepted,
            "request_sha256": payload_sha256,
            "idempotency_key": key,
            "version": 1,
            "control_state": state,
            "state_history": ["CREATED", "PREFLIGHT", state],
            "verification_result": "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
            "technical_handoff_status": "NOT_READY",
            "formalization_status": "NOT_AUTHORIZED",
            "deployment_status": "disabled",
            "profile_snapshot": profile,
            "capability_snapshot": capabilities,
            "preflight": preflight,
            "pause_supported": accepted["pause_supported"],
            "safe_checkpoint": False,
            "frozen_input_hashes": immutable_copy(accepted["input_hashes"]),
            "current_input_hashes": immutable_copy(accepted["input_hashes"]),
            "snapshots": snapshots,
            "action_requests": [],
            "artifact_bindings": [],
            "progress": {
                "sequence": 0,
                "progress_epoch": 1,
                "phase_code": "PREFLIGHT" if state == "BLOCKED_BEFORE_START" else "QUEUED",
                "phase_label": "preflight blocked" if state == "BLOCKED_BEFORE_START" else "queued",
                "completed_units": 0,
                "total_units": None,
                "indeterminate": True,
                "percent": None,
                "last_checkpoint_ref": None,
                "waiting_reason": ",".join(preflight["blockers"]) if preflight["blockers"] else "scheduler",
                "updated_at": now,
            },
            "created_at": now,
            "updated_at": now,
        }
        handle = {
            "schema_version": "JobHandle-v1",
            "contract_revision": CONTRACT_REVISION,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "state": state,
            "observed_version": 1,
            "correlation_id": accepted["correlation_id"],
            "profile_snapshot_ref": profile["snapshot_ref"],
            "capability_snapshot_ref": capabilities["snapshot_ref"],
        }
        result, replay = self.store.create_job_idempotent(
            job,
            namespace=namespace,
            idempotency_key=key,
            payload_sha256=payload_sha256,
            result=handle,
        )
        if replay:
            capabilities = self.store.read_job(result["job_id"])["capability_snapshot"]
        self._last_capability_snapshot = capabilities
        if state == "BLOCKED_BEFORE_START":
            raise CapabilityBlocked(job_id, preflight["blockers"])
        return result

    def start_job(self, request: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        return self._start_job(request, idempotency_key, namespace="start_job")

    @staticmethod
    def _status(job: Mapping[str, Any]) -> dict[str, Any]:
        resolved = job.get("snapshots", {}).get("resolved_task_input", {}).get("content", {})
        display_name = (
            resolved.get("display_name")
            if isinstance(resolved, Mapping)
            else None
        )
        return {
            "schema_version": "JobStatus-v1",
            "contract_revision": CONTRACT_REVISION,
            "job_id": job["job_id"],
            "attempt_id": job["attempt_id"],
            "job_control_state": job["control_state"],
            "verification_result": job["verification_result"],
            "acceptance_verdict": job["acceptance_verdict"],
            "technical_handoff_status": job["technical_handoff_status"],
            "formalization_status": job["formalization_status"],
            "deployment_status": job["deployment_status"],
            "observed_version": job["version"],
            "profile_snapshot_ref": job["profile_snapshot"]["snapshot_ref"],
            "capability_snapshot_ref": job["capability_snapshot"]["snapshot_ref"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "job_type": job.get("request", {}).get("job_type"),
            "display_name": display_name,
        }

    def get_status(self, job_id: str) -> dict[str, Any]:
        return self._status(self.store.read_job(job_id))

    def get_progress(self, job_id: str, after_sequence: int | None = None) -> dict[str, Any]:
        job = self.store.read_job(job_id)
        progress = immutable_copy(job["progress"])
        progress.update({"schema_version": "ProgressSnapshot-v1", "job_id": job_id, "attempt_id": job["attempt_id"], "job_control_state": job["control_state"]})
        if after_sequence is not None and progress["sequence"] <= after_sequence:
            progress["unchanged_since_sequence"] = after_sequence
        return progress

    def list_jobs(self) -> dict[str, Any]:
        return {
            "schema_version": "JobList-v1",
            "jobs": [self._status(job) for job in self.store.list_jobs()],
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = immutable_copy(self.store.read_job(job_id))
        job['node_progress_live'] = dict(self._node_progress_live.get(job_id, {}))
        return job

    def suppress_node_progress(self, job_id, executions):
        leases = self._node_progress_live.get(job_id, {})
        for node, execution in executions.items():
            if leases.get(node) == execution:
                leases.pop(node, None)

    def record_node_progress(self, job_id, event):
        from memorive_workflow.node_progress import apply_event
        from .errors import VersionConflict
        for observation_attempt in range(3):
            current = self.store.read_job(job_id)
            rows = apply_event(current.get('node_progress', {}), event,
                job_id=job_id, attempt_id=current['attempt_id'], control_state=current['control_state'])
            if rows == current.get('node_progress', {}):
                return
            try:
                self.store.update_job(job_id, expected_version=current['version'],
                    event_type='NODE_PROGRESS_RECORDED',
                    mutator=lambda job: dict(job, node_progress=rows))
                if event['action'] == 'BEGIN':
                    self._node_progress_live.setdefault(job_id, {})[event['node_id']] = event['node_execution_id']
                return
            except VersionConflict:
                if observation_attempt == 2:
                    raise

    @staticmethod
    def _suspend_node_progress(job):
        rows=job.get('node_progress', {})
        for row in rows.values() if isinstance(rows,dict) else ():
            if isinstance(row,dict) and row.get('activity') != 'CLOSED':
                row['activity'] = 'SUSPENDED'
        return job

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        accepted = require_text(attempt_id, "attempt_id")
        if callable(getattr(self.store, "read_attempt", None)):
            job = self.store.read_attempt(accepted)
            if job is not None:
                return immutable_copy(job)
            raise ContractViolation("ATTEMPT_NOT_FOUND")
        for job in self.store.list_jobs():
            if job.get("attempt_id") == accepted:
                return immutable_copy(job)
        raise ContractViolation("ATTEMPT_NOT_FOUND")

    def get_job_graph(self, job_id: str) -> dict[str, Any]:
        current = self.store.read_job(job_id)
        lookup = getattr(self.store, "read_attempt", None)
        if not callable(lookup):
            by_attempt = {job["attempt_id"]: job for job in self.store.list_jobs()}
            lookup = by_attempt.get
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor: dict[str, Any] | None = current
        while cursor is not None:
            attempt_id = cursor["attempt_id"]
            if attempt_id in seen:
                raise ContractViolation("JOB_GRAPH_CYCLE")
            seen.add(attempt_id)
            chain.append({
                "job_id": cursor["job_id"],
                "attempt_id": attempt_id,
                "predecessor_attempt_id": cursor.get("predecessor_attempt_id"),
                "control_state": cursor["control_state"],
            })
            predecessor = cursor.get("predecessor_attempt_id")
            cursor = lookup(predecessor) if predecessor else None
        chain.reverse()
        return {"schema_version": "JobGraph-v1", "root_job_id": chain[0]["job_id"], "nodes": chain}

    def get_job_events(self, job_id: str, after_sequence: int = 0) -> dict[str, Any]:
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ContractViolation("EVENT_SEQUENCE_INVALID")
        self.store.read_job(job_id)
        events = self.store.read_events(job_id=job_id, after_sequence=after_sequence)
        next_sequence = max((event["sequence"] for event in events), default=after_sequence)
        return {
            "schema_version": "JobEventPage-v1",
            "job_id": job_id,
            "after_sequence": after_sequence,
            "next_sequence": next_sequence,
            "events": events,
        }

    def watch_job_events(self, job_id: str, after_sequence: int = 0) -> dict[str, Any]:
        """Return a deterministic non-blocking event page; transports own waiting."""
        return self.get_job_events(job_id, after_sequence=after_sequence)

    def list_action_requests(self, job_id: str) -> dict[str, Any]:
        job = self.store.read_job(job_id)
        return {
            "schema_version": "ActionRequestList-v1",
            "job_id": job_id,
            "attempt_id": job["attempt_id"],
            "action_requests": immutable_copy(job.get("action_requests", [])),
        }

    def record_action_request(
        self,
        job_id: str,
        *,
        expected_version: int,
        action_request_id: str,
        action: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_id = require_text(action_request_id, "action_request_id")
        action_name = require_text(action, "action")
        payload_copy = immutable_copy(payload)

        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            rows = job.setdefault("action_requests", [])
            if any(row["action_request_id"] == request_id for row in rows):
                raise ContractViolation("ACTION_REQUEST_ID_CONFLICT")
            rows.append({
                "schema_version": "ActionRequest-v1",
                "action_request_id": request_id,
                "action": action_name,
                "payload": payload_copy,
                "payload_sha256": canonical_sha256(payload_copy),
                "state": "PENDING",
                "response": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            })
            return job

        updated = self.store.update_job(
            job_id,
            expected_version=expected_version,
            event_type="ACTION_REQUEST_RECORDED",
            mutator=mutate,
        )
        row = next(row for row in updated["action_requests"] if row["action_request_id"] == request_id)
        return {**immutable_copy(row), "observed_version": updated["version"]}

    def respond_to_action(
        self,
        job_id: str,
        action_request_id: str,
        response: Mapping[str, Any],
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_id = require_text(action_request_id, "action_request_id")
        key = require_text(idempotency_key, "idempotency_key")
        response_copy = immutable_copy(response)
        payload_sha256 = canonical_sha256({"action_request_id": request_id, "response": response_copy})

        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            for row in job.get("action_requests", []):
                if row["action_request_id"] == request_id:
                    if row["state"] != "PENDING":
                        raise ContractViolation("ACTION_REQUEST_ALREADY_RESOLVED")
                    row["state"] = "RESPONDED"
                    row["response"] = response_copy
                    row["response_sha256"] = canonical_sha256(response_copy)
                    row["updated_at"] = utc_now()
                    return job
            raise ContractViolation("ACTION_REQUEST_NOT_FOUND")

        def result_factory(job: dict[str, Any]) -> dict[str, Any]:
            row = next(row for row in job["action_requests"] if row["action_request_id"] == request_id)
            return {
                "schema_version": "ActionResponseReceipt-v1",
                "job_id": job_id,
                "attempt_id": job["attempt_id"],
                "action_request_id": request_id,
                "state": row["state"],
                "response_sha256": row["response_sha256"],
                "observed_version": job["version"],
            }

        result, _ = self.store.update_job_idempotent(
            job_id,
            expected_version=expected_version,
            namespace="respond_to_action",
            idempotency_key=key,
            payload_sha256=payload_sha256,
            event_type="ACTION_REQUEST_RESPONDED",
            mutator=mutate,
            result_factory=result_factory,
        )
        return result

    def bind_artifact(
        self,
        job_id: str,
        *,
        expected_version: int,
        artifact_id: str,
        locator: str,
        sha256: str,
    ) -> dict[str, Any]:
        accepted_id = require_text(artifact_id, "artifact_id")
        accepted_locator = require_text(locator, "locator")
        accepted_sha256 = require_sha256(sha256, "artifact_sha256")

        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            rows = job.setdefault("artifact_bindings", [])
            if any(row["artifact_id"] == accepted_id for row in rows):
                raise ContractViolation("ARTIFACT_ID_CONFLICT")
            rows.append({
                "schema_version": "ArtifactBinding-v1",
                "artifact_id": accepted_id,
                "locator": accepted_locator,
                "sha256": accepted_sha256,
                "bound_at": utc_now(),
            })
            return job

        updated = self.store.update_job(
            job_id,
            expected_version=expected_version,
            event_type="ARTIFACT_BOUND",
            mutator=mutate,
        )
        row = next(row for row in updated["artifact_bindings"] if row["artifact_id"] == accepted_id)
        return {**immutable_copy(row), "observed_version": updated["version"]}

    def get_artifact_bindings(self, job_id: str) -> dict[str, Any]:
        job = self.store.read_job(job_id)
        return {
            "schema_version": "ArtifactBindingList-v1",
            "job_id": job_id,
            "attempt_id": job["attempt_id"],
            "artifacts": immutable_copy(job.get("artifact_bindings", [])),
        }

    def get_terminal_receipt(self, job_id: str) -> dict[str, Any]:
        job = self.store.read_job(job_id)
        if job["control_state"] not in TERMINAL_STATES:
            raise ContractViolation("JOB_NOT_TERMINAL")
        receipt = {
            "schema_version": "TerminalReceipt-v1",
            "contract_revision": CONTRACT_REVISION,
            "job_id": job_id,
            "attempt_id": job["attempt_id"],
            "terminal_state": job["control_state"],
            "observed_version": job["version"],
            "request_sha256": job["request_sha256"],
            "snapshot_sha256s": {key: value["sha256"] for key, value in sorted(job["snapshots"].items())},
            "artifact_sha256s": [row["sha256"] for row in job.get("artifact_bindings", [])],
            "updated_at": job["updated_at"],
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return receipt

    def resolve_job_locator(self, locator: str) -> dict[str, Any]:
        accepted = require_text(locator, "job_locator")
        if accepted.startswith("job:"):
            job_id = accepted[4:]
        else:
            parsed = urlparse(accepted)
            if parsed.scheme != "memorive" or parsed.netloc != "job" or parsed.params or parsed.query or parsed.fragment:
                raise ContractViolation("JOB_LOCATOR_INVALID")
            job_id = parsed.path.lstrip("/")
        job = self.store.read_job(require_text(job_id, "job_id"))
        return {
            "schema_version": "JobLocatorResolution-v1",
            "locator": accepted,
            "job_id": job["job_id"],
            "attempt_id": job["attempt_id"],
            "observed_version": job["version"],
        }

    def lookup_start_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any]:
        key = require_text(idempotency_key, "idempotency_key")
        result = self.store.lookup_idempotency("start_job", key)
        if result is None:
            raise ContractViolation("START_IDEMPOTENCY_KEY_NOT_FOUND")
        return result

    def _transition(self, job_id: str, expected_version: int, target: str, event_type: str) -> dict[str, Any]:
        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            job["control_state"] = apply_transition(job["control_state"], target)
            job["state_history"].append(target)
            if target != 'RUNNING':
                self._suspend_node_progress(job)
            return job

        return self.store.update_job(job_id, expected_version=expected_version, event_type=event_type, mutator=mutate)

    @staticmethod
    def _control_receipt(job: Mapping[str, Any], *, action: str, idempotency_key: str) -> dict[str, Any]:
        return {
            "schema_version": "ControlReceipt-v1",
            "contract_revision": CONTRACT_REVISION,
            "request_id": job["request"]["request_id"],
            "correlation_id": job["request"]["correlation_id"],
            "job_id": job["job_id"],
            "attempt_id": job["attempt_id"],
            "idempotency_key": idempotency_key,
            "action": action,
            "state": job["control_state"],
            "observed_version": job["version"],
            "updated_at": job["updated_at"],
        }

    def _apply_control(
        self,
        job_id: str,
        *,
        expected_version: int,
        action: str,
        idempotency_key: str | None,
        mutator: Any,
    ) -> dict[str, Any]:
        key = require_text(idempotency_key or f"auto:{job_id}:{expected_version}", "idempotency_key")
        payload_sha256 = canonical_sha256({"action": action, "job_id": job_id, "expected_version": expected_version})
        result, _ = self.store.update_job_idempotent(
            job_id,
            expected_version=expected_version,
            namespace=f"control:{action}",
            idempotency_key=key,
            payload_sha256=payload_sha256,
            event_type=f"{action.upper()}_CONTROL_APPLIED",
            mutator=lambda job: self._suspend_node_progress(mutator(job)),
            result_factory=lambda job: self._control_receipt(job, action=action, idempotency_key=key),
        )
        return result

    def mark_running(self, job_id: str, *, expected_version: int) -> dict[str, Any]:
        return self._status(self._transition(job_id, expected_version, "RUNNING", "JOB_RUNNING"))

    def set_safe_checkpoint(self, job_id: str, *, expected_version: int, safe: bool) -> dict[str, Any]:
        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            job["safe_checkpoint"] = bool(safe)
            if safe:
                job["pause_supported"] = True
            return job

        return self._status(self.store.update_job(job_id, expected_version=expected_version, event_type="SAFE_CHECKPOINT_UPDATED", mutator=mutate))

    def pause_job(self, job_id: str, *, expected_version: int, idempotency_key: str | None = None) -> dict[str, Any]:
        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            if not job["pause_supported"]:
                raise PauseUnsupported(job_id)
            job["control_state"] = apply_transition(job["control_state"], "PAUSE_REQUESTED")
            job["state_history"].append("PAUSE_REQUESTED")
            if job["safe_checkpoint"]:
                job["control_state"] = apply_transition(job["control_state"], "PAUSED")
                job["state_history"].append("PAUSED")
            return job

        return self._apply_control(job_id, expected_version=expected_version, action="pause", idempotency_key=idempotency_key, mutator=mutate)

    def cancel_job(self, job_id: str, *, expected_version: int, idempotency_key: str | None = None) -> dict[str, Any]:
        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            prior = job["control_state"]
            job["control_state"] = apply_transition(prior, "CANCEL_REQUESTED")
            job["state_history"].append("CANCEL_REQUESTED")
            if job["safe_checkpoint"] or prior == "QUEUED":
                job["control_state"] = apply_transition(job["control_state"], "CANCELLED")
                job["state_history"].append("CANCELLED")
            return job

        return self._apply_control(job_id, expected_version=expected_version, action="cancel", idempotency_key=idempotency_key, mutator=mutate)

    def resume_job(self, job_id: str, *, expected_version: int, idempotency_key: str | None = None) -> dict[str, Any]:
        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            if job["frozen_input_hashes"] != job["current_input_hashes"]:
                raise ResumeInputDrift(job_id)
            job["control_state"] = apply_transition(job["control_state"], "RESUME_REQUESTED")
            job["state_history"].append("RESUME_REQUESTED")
            job["control_state"] = apply_transition(job["control_state"], "RUNNING")
            job["state_history"].append("RUNNING")
            return job

        return self._apply_control(job_id, expected_version=expected_version, action="resume", idempotency_key=idempotency_key, mutator=mutate)

    def simulate_input_drift(self, job_id: str, *, expected_version: int) -> dict[str, Any]:
        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            job["current_input_hashes"] = dict(job["current_input_hashes"])
            key = sorted(job["current_input_hashes"])[0]
            job["current_input_hashes"][key] = "F" * 64
            return job

        return self._status(self.store.update_job(job_id, expected_version=expected_version, event_type="INPUT_DRIFT_OBSERVED", mutator=mutate))

    def record_progress(
        self,
        job_id: str,
        *,
        completed_units: int,
        total_units: int | None,
        phase_code: str,
        phase_label: str,
        last_checkpoint_ref: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(completed_units, bool) or not isinstance(completed_units, int) or completed_units < 0:
            raise ValueError("PROGRESS_COMPLETED_INVALID")
        if total_units is not None and (isinstance(total_units, bool) or not isinstance(total_units, int) or total_units <= 0 or completed_units > total_units):
            raise ValueError("PROGRESS_TOTAL_INVALID")
        current = self.store.read_job(job_id)

        def mutate(job: dict[str, Any]) -> dict[str, Any]:
            prior = job["progress"]
            percent = None if total_units is None else round(completed_units * 100.0 / total_units, 6)
            if prior["percent"] is not None and percent is not None and percent < prior["percent"]:
                raise ValueError("PROGRESS_REGRESSION")
            job["progress"] = {
                "sequence": prior["sequence"] + 1,
                "progress_epoch": prior["progress_epoch"],
                "phase_code": require_text(phase_code, "phase_code"),
                "phase_label": require_text(phase_label, "phase_label"),
                "completed_units": completed_units,
                "total_units": total_units,
                "indeterminate": total_units is None,
                "percent": percent,
                "last_checkpoint_ref": last_checkpoint_ref,
                "waiting_reason": None,
                "updated_at": utc_now(),
            }
            return job

        # Progress has no caller-owned expected version. A concurrent control
        # action must remain authoritative; reapply only this monotonic projection
        # to its fresh version, never rerun the completed model operation.
        from .errors import VersionConflict
        while True:
            try:
                updated = self.store.update_job(job_id, expected_version=current["version"], event_type="PROGRESS_RECORDED", mutator=mutate)
                return immutable_copy(updated["progress"])
            except VersionConflict:
                current = self.store.read_job(job_id)

    def retry_job(self, job_id: str, retry_policy: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        predecessor = self.store.read_job(job_id)
        if predecessor["control_state"] not in TERMINAL_STATES:
            raise ContractViolation("RETRY_PREDECESSOR_NOT_TERMINAL")
        request = deepcopy(predecessor["request"])
        request.pop("capability_snapshot_ref", None)
        request.pop("workflow_definition_id", None)
        frozen_definition = predecessor["snapshots"]["workflow_definition"]["content"]
        request["workflow_definition"] = frozen_definition
        workflow_config = retry_policy.get("workflow_config")
        if workflow_config is not None and not isinstance(workflow_config, Mapping):
            raise ContractViolation("RETRY_WORKFLOW_CONFIG_INVALID")
        request["workflow_config"] = immutable_copy(
            workflow_config
            if workflow_config is not None
            else predecessor["snapshots"]["workflow_config"]["content"]
        )
        request["resolved_task_input"] = predecessor["snapshots"]["resolved_task_input"]["content"]
        request["request_id"] = require_text(retry_policy.get("request_id"), "retry_request_id")
        request["correlation_id"] = require_text(retry_policy.get("correlation_id"), "retry_correlation_id")
        resume_from_node_id = retry_policy.get("resume_from_node_id")
        rollback_request_id = retry_policy.get("rollback_request_id")
        if resume_from_node_id is not None or rollback_request_id is not None:
            request["core_retry"] = {
                "resume_from_node_id": require_text(
                    resume_from_node_id, "retry_resume_from_node_id"
                ),
                "rollback_request_id": require_text(
                    rollback_request_id, "retry_rollback_request_id"
                ),
                "predecessor_job_id": predecessor["job_id"],
                "predecessor_attempt_id": predecessor["attempt_id"],
            }
        handle = self._start_job(
            request,
            idempotency_key,
            namespace=f"retry_job:{predecessor['attempt_id']}",
            predecessor_attempt_id=predecessor["attempt_id"],
        )
        return {**handle, "predecessor_attempt_id": predecessor["attempt_id"]}

    def list_capabilities(self, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = immutable_copy(self._last_capability_snapshot)
        result["context"] = immutable_copy(context or {})
        return result

    def run_doctor(self, scope: str, mode: str) -> dict[str, Any]:
        return DiagnosticCore(
            store=self.store,
            directory_layout=self.directory_layout,
            capability_snapshot=self._last_capability_snapshot,
            schema_root=self.schema_root,
        ).run(scope=scope, mode=mode)

    def build_support_bundle(self, scope: str, redaction_profile: str) -> dict[str, Any]:
        doctor = self.run_doctor(scope, "read_only")
        projections = {
            "build.json": {"contract_revision": CONTRACT_REVISION, "schema_root": str(self.schema_root)},
            "doctor.json": doctor,
            "jobs.json": {"jobs": [self._status(job) for job in self.store.list_jobs()]},
        }
        return SupportBundleBuilder().build(
            scope=scope,
            redaction_profile=redaction_profile,
            generated_at=doctor["generated_at"],
            projections=projections,
        )


__all__ = ["ApplicationFacade"]

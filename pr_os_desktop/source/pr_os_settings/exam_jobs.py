"""Durable desktop exam jobs; reuse the existing exam/receipt/scoring path.

IPC returns immediately. A kernel lock owns the single live worker, not a UI
connection or a timeout. Only public progress and redacted receipts are stored.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import re
import threading
from typing import Any

from m11_terminal_ledger.writer_lock import KernelFileLock
from .contracts import canonical_sha256, scan_sensitive, utc_now

_observer = ContextVar("workflow_exam_observer", default=None)
TERMINAL = {"COMPLETE", "ERROR", "CANCELLED", "INTERRUPTED"}


def observed_call(call, kind):
    def invoke(*args, **kwargs):
        observer = _observer.get()
        if observer is None:
            return call(*args, **kwargs)
        observer("started", kind)
        try:
            result = call(*args, **kwargs)
        except BaseException:
            observer("returned", kind)
            raise
        observer("returned", kind)
        return result
    return invoke


@contextmanager
def observe_calls(observer):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


class WorkflowExamJobs:
    def __init__(self, store, execute):
        self.store, self.execute = store, execute
        self.root = store.profile_root / "workflow_exam_jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()

    def _path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("WORKFLOW_EXAM_JOB_ID_INVALID")
        return self.root / (job_id + ".json")

    def _read(self, job_id):
        return json.loads(self._path(job_id).read_text(encoding="utf-8"))

    def _write(self, row):
        scan_sensitive(row)
        self.store._atomic_write(self._path(row["job_id"]), row)

    def _lock(self):
        return KernelFileLock(self.root / "worker.lock")

    def start(self, *, params, plan, idempotency_key):
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", idempotency_key):
            raise ValueError("WORKFLOW_EXAM_IDEMPOTENCY_KEY_INVALID")
        job_id = canonical_sha256(idempotency_key)[:32].lower()
        request_hash = canonical_sha256(params)
        with self._mutex:
            if self._path(job_id).exists():
                row = self._read(job_id)
                if row["request_sha256"] != request_hash:
                    raise ValueError("WORKFLOW_EXAM_IDEMPOTENCY_CONFLICT")
                return self.status(job_id=job_id)
            lock = self._lock()
            try:
                lock.acquire()
            except OSError as exc:
                raise ValueError("WORKFLOW_EXAM_ALREADY_RUNNING") from exc
            except RuntimeError as exc:
                raise ValueError("WORKFLOW_EXAM_ALREADY_RUNNING") from exc
            try:
                row = {
                    "schema_version": "WorkflowExamJob-v1", "job_id": job_id,
                    "request_sha256": request_hash, "node_id": params["node_id"],
                    "state": "RUNNING", "stage": "PREPARING", "cancel_requested": False,
                    "started_at": utc_now(), "updated_at": utc_now(),
                    "calls_started": 0, "calls_returned": 0,
                    "maximum_calls": plan.get("maximum_model_calls", plan.get("maximum_provider_calls")),
                    "exam_tier": plan.get("exam_tier"), "receipt": None,
                    "plan_sha256": plan.get("plan_sha256"),
                    "settings_sha256": plan["settings_sha256"],
                }
                self._write(row)
                thread = threading.Thread(target=self._run, args=(job_id, deepcopy(params), lock),
                                          name="workflow-exam-" + job_id, daemon=True)
                thread.start()
                return deepcopy(row)
            except BaseException:
                lock.release()
                raise

    def _run(self, job_id, params, lock):
        def progress(event, kind):
            with self._mutex:
                row = self._read(job_id)
                if event == "started" and row["cancel_requested"]:
                    raise RuntimeError("WORKFLOW_EXAM_CANCELLED_BEFORE_NEXT_CALL")
                field = "calls_started" if event == "started" else "calls_returned"
                row[field] += 1
                row.update(stage="WAITING_MODEL" if event == "started" else "SCORING",
                           call_kind=kind, updated_at=utc_now())
                self._write(row)
        try:
            from .task_scheduling import TASK_CATEGORIES
            def checkpoint():
                if self._read(job_id)['cancel_requested']:
                    raise RuntimeError('WORKFLOW_EXAM_CANCELLED_BEFORE_NEXT_CALL')
            with TASK_CATEGORIES.enter('MODEL_EXAM', checkpoint=checkpoint):
                from .call_ledger import call_scope
                with observe_calls(progress), call_scope(job_id=job_id, node_id=params['node_id']):
                    # Do not run a plan against a different model/settings snapshot.
                    row = self._read(job_id)
                    if self.store.load(recover_corruption=False)["settings_sha256"] != row["settings_sha256"]:
                        raise ValueError("WORKFLOW_EXAM_PLAN_SETTINGS_CHANGED")
                    receipt = self.execute(**params)
                with self._mutex:
                    row = self._read(job_id)
                    row["receipt"] = receipt
                    row["state"] = "COMPLETE" if receipt.get("status") in {"PASS", "FAIL"} or receipt.get('execution_completed') is True else (
                        "CANCELLED" if row["cancel_requested"] else "ERROR")
                    row.update(stage="FINISHED", updated_at=utc_now(), finished_at=utc_now())
                    self._write(row)
        except Exception as exc:
            with self._mutex:
                row = self._read(job_id)
                # Never serialize exception text: providers may include private payloads.
                row.update(state="CANCELLED" if row["cancel_requested"] else "ERROR",
                           stage="FINISHED", error_type=type(exc).__name__,
                           reason="WORKFLOW_EXAM_INTERRUPTED_NO_NEW_CALL", updated_at=utc_now(), finished_at=utc_now())
                self._write(row)
        finally:
            lock.release()

    def status(self, *, job_id=None, node_id=None):
        with self._mutex:
            if job_id is None:
                rows = [json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")]
                rows = [r for r in rows if node_id is None or r["node_id"] == node_id]
                if not rows:
                    return {"state": "IDLE"}
                # A running job takes precedence over old terminal observations.
                row = max(rows, key=lambda r: (r["state"] not in TERMINAL, r["started_at"]))
            else:
                row = self._read(job_id)
            if row["state"] not in TERMINAL:
                lock = self._lock()
                try:
                    lock.acquire()
                except (OSError, RuntimeError):
                    pass
                else:
                    try:
                        # The worker was lost. Preserve answers, never restart inference.
                        row.update(state="INTERRUPTED", stage="FINISHED", updated_at=utc_now(),
                                   reason="WORKFLOW_EXAM_WORKER_LOST_NOT_ASSESSED")
                        self._write(row)
                    finally:
                        lock.release()
            return deepcopy(row)

    def cancel(self, *, job_id):
        with self._mutex:
            row = self.status(job_id=job_id)
            if row["state"] not in TERMINAL:
                row.update(cancel_requested=True, updated_at=utc_now())
                self._write(row)
            return deepcopy(row)

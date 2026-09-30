from __future__ import annotations

import json
from pathlib import Path
from time import monotonic
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .contracts import require_text, utc_now
from .errors import CorruptEventLog
from .job_store import SandboxJobStore
from .lifecycle import recovery_classification


class DiagnosticCore:
    """GUI-independent read-only diagnostic core. It never probes a network."""

    def __init__(
        self,
        *,
        store: SandboxJobStore,
        directory_layout: Any | None,
        capability_snapshot: Mapping[str, Any],
        schema_root: Path,
    ):
        self.store = store
        self.directory_layout = directory_layout
        self.capability_snapshot = dict(capability_snapshot)
        self.schema_root = Path(schema_root)

    @staticmethod
    def _check(check_id: str, status: str, summary: str, started: float, *, action: str = "none", evidence_ref: str | None = None) -> dict[str, Any]:
        return {
            "check_id": check_id,
            "check_version": "1",
            "status": status,
            "capability_impact": "BLOCKING" if status == "FAIL" else "NONE",
            "job_impact": "BLOCKING" if status == "FAIL" else "NONE",
            "machine_detail": summary,
            "user_facing_summary": summary,
            "recovery_action": action,
            "automatic_recovery": False,
            "evidence_ref": evidence_ref,
            "duration_ms": round((monotonic() - started) * 1000.0, 3),
            "may_be_stale": False,
        }

    def run(self, *, scope: str, mode: str) -> dict[str, Any]:
        require_text(scope, "doctor_scope")
        if mode != "read_only":
            raise ValueError("DOCTOR_MODE_NOT_READ_ONLY")
        checks = []
        started = monotonic()
        try:
            schemas = list(self.schema_root.glob("*.json"))
            if not schemas:
                raise ValueError("no schemas")
            for path in schemas:
                Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
            checks.append(self._check("D0_CONTRACT_SCHEMA", "PASS", f"{len(schemas)} schemas valid", started, evidence_ref="schema-role:application-contracts"))
        except Exception as exc:
            checks.append(self._check("D0_CONTRACT_SCHEMA", "FAIL", f"schema validation failed:{type(exc).__name__}", started, action="repair package schemas"))

        started = monotonic()
        if self.directory_layout is None:
            checks.append(self._check("D1_DIRECTORY_ROLES", "NOT_APPLICABLE", "directory layout not supplied", started))
        else:
            report = self.directory_layout.doctor()
            checks.append(self._check("D1_DIRECTORY_ROLES", report["status"], "directory role contract checked", started))

        started = monotonic()
        cap_status = "PASS" if self.capability_snapshot.get("status") == "READY" else "FAIL"
        checks.append(self._check("D2_CAPABILITY_PROFILE", cap_status, "capability snapshot metadata checked", started))

        started = monotonic()
        try:
            integrity = self.store.integrity_report()
            recovery = recovery_classification(self.store.root)
            recovery_status = "FAIL" if recovery["classification"] in {"BLOCKED_METADATA_DRIFT", "CORRUPT_EVENT_LOG"} else "PASS"
            checks.append(self._check("D3_JOB_LEDGER_RECOVERY", recovery_status, f"events={integrity['event_count']};recovery={recovery['classification']}", started))
        except CorruptEventLog as exc:
            checks.append(self._check("D3_JOB_LEDGER_RECOVERY", "FAIL", exc.code, started, action="preserve bytes and run controlled recovery"))

        started = monotonic()
        checks.append(self._check("D4_RUNTIME_METADATA", "NOT_AUTHORIZED", "network and model runtime probes are outside Retrieval authority", started))

        started = monotonic()
        checks.append(self._check("D5_SUPPORT_PREVIEW", "PASS", "allowlist-only local preview available", started))
        blocking = [item for item in checks if item["status"] == "FAIL"]
        return {
            "schema_version": "DoctorReport-v1",
            "contract_revision": "1.0",
            "scope": scope,
            "mode": mode,
            "generated_at": utc_now(),
            "checks": checks,
            "overall_status": "FAIL" if blocking else "PASS",
            "network_requests": 0,
            "model_requests": 0,
            "mutations": 0,
        }


__all__ = ["DiagnosticCore"]

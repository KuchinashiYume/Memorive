from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import ProtocolViolation
from .protocol import canonical_json_bytes


REQUIRED_D38_METHODS = (
    "list_jobs",
    "get_job",
    "get_attempt",
    "get_job_graph",
    "get_job_events",
    "watch_job_events",
    "list_action_requests",
    "respond_to_action",
    "get_artifact_bindings",
    "get_terminal_receipt",
    "resolve_job_locator",
    "lookup_start_by_idempotency_key",
)

REAL_ADDITIONAL_METHODS = (
    "start_job",
    "get_status",
    "get_progress",
    "record_action_request",
    "bind_artifact",
    "mark_running",
    "set_safe_checkpoint",
    "pause_job",
    "cancel_job",
    "resume_job",
    "record_progress",
    "retry_job",
    "list_capabilities",
    "run_doctor",
    "build_support_bundle",
)


def _immutable(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


class FacadeAdapter:
    adapter_kind = "base"

    def __init__(self, facade: Any, *, allowed_methods: tuple[str, ...], watch_batch_max: int = 64):
        if watch_batch_max <= 0:
            raise ValueError("WATCH_BATCH_MAX_INVALID")
        self.facade = facade
        self.allowed_methods = tuple(allowed_methods)
        self.watch_batch_max = watch_batch_max
        missing = [name for name in self.allowed_methods if not callable(getattr(facade, name, None))]
        if missing:
            raise ProtocolViolation("FACADE_METHOD_MISSING:" + ",".join(missing))

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in self.allowed_methods:
            raise ProtocolViolation(f"FACADE_METHOD_NOT_ALLOWLISTED:{method}")
        if not isinstance(params, Mapping):
            raise ProtocolViolation("FACADE_PARAMS_OBJECT_REQUIRED")
        target = getattr(self.facade, method)
        try:
            inspect.signature(target).bind(**dict(params))
        except TypeError as exc:
            raise ProtocolViolation(f"FACADE_PARAMS_INVALID:{method}") from exc
        result = target(**dict(params))
        accepted = _immutable(result)
        if method == "watch_job_events":
            if not isinstance(accepted, dict) or not isinstance(accepted.get("events"), list):
                raise ProtocolViolation("WATCH_RESULT_SHAPE_INVALID")
            events = accepted["events"]
            accepted["backpressure"] = {
                "schema_version": "WatchBackpressure-v1",
                "available_count": len(events),
                "returned_count": min(len(events), self.watch_batch_max),
                "truncated": len(events) > self.watch_batch_max,
                "resume_after_sequence": accepted.get("next_sequence", params.get("after_sequence", 0)),
            }
            accepted["events"] = events[: self.watch_batch_max]
            if accepted["events"]:
                accepted["next_sequence"] = accepted["events"][-1]["sequence"]
        return accepted

    def parity_projection(self) -> dict[str, Any]:
        return {
            "schema_version": "FacadeAdapterParityProjection-v1",
            "adapter_kind": self.adapter_kind,
            "required_methods": list(REQUIRED_D38_METHODS),
            "present_methods": [name for name in REQUIRED_D38_METHODS if callable(getattr(self.facade, name, None))],
            "missing_methods": [name for name in REQUIRED_D38_METHODS if not callable(getattr(self.facade, name, None))],
            "allowed_methods": list(self.allowed_methods),
        }


class RealFacadeAdapter(FacadeAdapter):
    adapter_kind = "real_application_facade"

    def __init__(self, facade: Any, *, watch_batch_max: int = 64):
        super().__init__(
            facade,
            allowed_methods=REQUIRED_D38_METHODS + REAL_ADDITIONAL_METHODS,
            watch_batch_max=watch_batch_max,
        )


class FakeFacadeAdapter(FacadeAdapter):
    adapter_kind = "synthetic_fake_facade"

    def __init__(self, facade: Any, *, watch_batch_max: int = 64):
        super().__init__(facade, allowed_methods=REQUIRED_D38_METHODS, watch_batch_max=watch_batch_max)

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        accepted = super().call(method, params)
        if method in {"get_job", "get_attempt"} and isinstance(accepted, dict):
            accepted.setdefault("schema_version", "ApplicationJobRecord-v1")
        return accepted


def load_d38_contract(contract_path: Path | str) -> dict[str, Any]:
    value = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    observed = tuple(value.get("projected_required_facade_methods", []))
    if observed != REQUIRED_D38_METHODS:
        raise ProtocolViolation("D38_METHOD_DENOMINATOR_DRIFT")
    return _immutable(value)


__all__ = [
    "FacadeAdapter",
    "FakeFacadeAdapter",
    "REAL_ADDITIONAL_METHODS",
    "REQUIRED_D38_METHODS",
    "RealFacadeAdapter",
    "load_d38_contract",
]

from __future__ import annotations

import inspect
from typing import Any, Mapping

from .contracts import InboxError, immutable


INBOX_METHODS = (
    "inbox.get_contract",
    "inbox.import_paths",
    "inbox.enqueue_session_refinement",
    "inbox.find_session_refinement",
    "inbox.begin_session_refinement",
    "inbox.complete_session_refinement",
    "inbox.cancel_session_refinement",
    "inbox.get_auto_run",
    "inbox.set_auto_run",
    "inbox.retry_import",
    "inbox.recover",
    "inbox.dispatch",
    "inbox.record_dispatch_receipt",
    "inbox.cancel_dispatch",
    "inbox.set_starred",
    "inbox.soft_delete",
    "inbox.undo",
    "inbox.redo",
    "inbox.refresh",
    "inbox.projection",
    "inbox.item_detail",
    "inbox.close_session",
    "inbox.effect_metrics",
)
METHOD_TARGETS = {method: method.split(".", 1)[1] for method in INBOX_METHODS}


class InboxServiceAdapter:
    adapter_kind = "inbox_inbox_sandbox"

    def __init__(self, controller: Any):
        self.controller = controller
        missing = [target for target in METHOD_TARGETS.values() if not callable(getattr(controller, target, None))]
        if missing:
            raise InboxError("INBOX_CONTROLLER_METHOD_MISSING:" + ",".join(missing))

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in METHOD_TARGETS:
            raise InboxError("INBOX_METHOD_NOT_ALLOWLISTED")
        if not isinstance(params, Mapping):
            raise InboxError("INBOX_PARAMS_OBJECT_REQUIRED")
        target = getattr(self.controller, METHOD_TARGETS[method])
        try:
            inspect.signature(target).bind(**dict(params))
        except TypeError as exc:
            raise InboxError(f"INBOX_PARAMS_INVALID:{method}") from exc
        return immutable(target(**dict(params)))

    def parity_projection(self) -> dict[str, Any]:
        return {
            "schema_version": "InboxServiceParityProjection-v1",
            "adapter_kind": self.adapter_kind,
            "required_methods": list(INBOX_METHODS),
            "present_methods": list(INBOX_METHODS),
            "missing_methods": [],
            "ui_direct_filesystem_access": 0,
            "ui_direct_database_access": 0,
            "production_ingestion": 0,
            "external_calls": 0,
        }


__all__ = ["INBOX_METHODS", "InboxServiceAdapter"]

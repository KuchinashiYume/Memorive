from __future__ import annotations

from typing import Any, Mapping

from pr_os_inbox.service_adapter import INBOX_METHODS, InboxServiceAdapter
from pr_os_settings.service_adapter import SETTINGS_METHODS, SettingsServiceAdapter

from .adapters import RealFacadeAdapter
from .errors import ProtocolViolation


CURRENT_TASK_FIXTURE_METHODS = ("current_task.fixture_transition",)


class CompositeServiceAdapter:
    adapter_kind = "real_application_facade_plus_t04_settings_plus_t05_inbox_plus_t06_current_task_fixture"

    def __init__(
        self,
        application: RealFacadeAdapter,
        settings: SettingsServiceAdapter,
        inbox: InboxServiceAdapter,
    ):
        self.application = application
        self.settings = settings
        self.inbox = inbox
        self.facade = application.facade

    def _fixture_transition(self, params: Mapping[str, Any]) -> Any:
        if set(params) != {"job_id", "expected_version", "target", "event_type"}:
            raise ProtocolViolation("T06_FIXTURE_TRANSITION_FIELDS_INVALID")
        job = self.facade.get_job(str(params["job_id"]))
        request = job.get("request", {})
        profile = request.get("profile_snapshot", {})
        if (
            request.get("job_type") != "synthetic-p08-t06-current-task"
            or profile.get("data_classification") != "SYNTHETIC_PUBLIC_SAFE"
            or profile.get("egress_policy") != "ZERO_EGRESS"
        ):
            raise ProtocolViolation("T06_PRODUCTION_FIXTURE_TRANSITION_FORBIDDEN")
        target = str(params["target"])
        if target not in {"FAILED", "SUCCEEDED"}:
            raise ProtocolViolation("T06_FIXTURE_TRANSITION_TARGET_INVALID")
        updated = self.facade._transition(
            str(params["job_id"]),
            int(params["expected_version"]),
            target,
            str(params["event_type"]),
        )
        return {
            "schema_version": "P08T06SyntheticFixtureTransitionReceipt-v1",
            "job_id": updated["job_id"],
            "attempt_id": updated["attempt_id"],
            "control_state": updated["control_state"],
            "observed_version": updated["version"],
            "synthetic_only": True,
            "production_jobs_touched": 0,
            "status": "PASS",
        }

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method in SETTINGS_METHODS:
            return self.settings.call(method, params)
        if method in INBOX_METHODS:
            return self.inbox.call(method, params)
        if method == "current_task.fixture_transition":
            return self._fixture_transition(params)
        return self.application.call(method, params)

    def parity_projection(self) -> dict[str, Any]:
        application = self.application.parity_projection()
        settings = self.settings.parity_projection()
        inbox = self.inbox.parity_projection()
        missing = [*application["missing_methods"], *settings["missing_methods"], *inbox["missing_methods"]]
        return {
            "schema_version": "P08T06CompositeServiceParityProjection-v1",
            "adapter_kind": self.adapter_kind,
            "required_methods": application["required_methods"],
            "present_methods": application["present_methods"],
            "missing_methods": missing,
            "allowed_methods": [
                *application["allowed_methods"],
                *SETTINGS_METHODS,
                *INBOX_METHODS,
                *CURRENT_TASK_FIXTURE_METHODS,
            ],
            "application": application,
            "settings": settings,
            "inbox": inbox,
            "settings_method_count": len(SETTINGS_METHODS),
            "inbox_method_count": len(INBOX_METHODS),
            "current_task_fixture_method_count": len(CURRENT_TASK_FIXTURE_METHODS),
            "total_required_product_methods": len(application["required_methods"]) + len(SETTINGS_METHODS) + len(INBOX_METHODS) + 16,
            "credential_value_reads": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "production_ingestion": 0,
            "production_job_access": 0,
        }


__all__ = ["CompositeServiceAdapter", "CURRENT_TASK_FIXTURE_METHODS"]

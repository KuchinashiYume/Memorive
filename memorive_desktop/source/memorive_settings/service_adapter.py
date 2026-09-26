from __future__ import annotations

import inspect
from typing import Any, Mapping

from memorive_desktop_service.protocol import canonical_json_bytes


SETTINGS_METHODS = (
    "settings.research_chat_freeze",
    "settings.research_chat_execute",
    "settings.research_image_execute",
    "settings.research_embedding_freeze",
    "settings.research_embedding_execute",
    "settings.report_profile_freeze",
    "settings.report_profile_execute",
    "settings.user_get",
    "settings.user_save",
    "settings.research_get",
    "settings.research_save",
    "settings.leaderboard_get",
    "settings.leaderboard_save",
    "settings.leaderboard_refresh",
    "settings.get_contract",
    "settings.get_state",
    "settings.external_sources_get",
    "settings.external_sources_save",
    "settings.external_sources_refresh",
    "settings.external_model_reference",
    "settings.register_directory",
    "settings.preview",
    "settings.save",
    "settings.save_cli_services",
    "settings.revert",
    "settings.reset_scope",
    "settings.export_redacted",
    "settings.import_redacted",
    "settings.capability_state",
    "settings.credential_create",
    "settings.credential_replace",
    "settings.credential_status",
    "settings.credential_delete",
    "settings.model_service_remove",
    "settings.verify_api_model",
    "settings.start_api_verification",
    "settings.api_verification_status",
    "settings.verify_cli_model",
    "settings.start_cli_verification",
    "settings.cli_verification_status",
    "settings.cancel_cli_verification",
    "settings.execute_structured_chat",
    "settings.test_workflow_node",
    "settings.start_workflow_exam",
    "settings.workflow_exam_status",
    "settings.cancel_workflow_exam",
    "settings.diagnostics",
    "settings.support_bundle",
    "settings.effect_metrics",
)

METHOD_TARGETS = {
    "settings.research_chat_freeze": "research_chat_freeze",
    "settings.research_chat_execute": "research_chat_execute",
    "settings.research_image_execute": "research_image_execute",
    "settings.research_embedding_freeze": "research_embedding_freeze",
    "settings.research_embedding_execute": "research_embedding_execute",
    "settings.report_profile_freeze": "report_profile_freeze",
    "settings.report_profile_execute": "report_profile_execute",
    "settings.user_get": "user_get",
    "settings.user_save": "user_save",
    "settings.research_get": "research_get",
    "settings.research_save": "research_save",
    "settings.leaderboard_get": "leaderboard_get",
    "settings.leaderboard_save": "leaderboard_save",
    "settings.leaderboard_refresh": "leaderboard_refresh",
    "settings.get_contract": "get_contract",
    "settings.get_state": "get_state",
    "settings.external_sources_get": "external_sources_get",
    "settings.external_sources_save": "external_sources_save",
    "settings.external_sources_refresh": "external_sources_refresh",
    "settings.external_model_reference": "external_model_reference",
    "settings.register_directory": "register_directory",
    "settings.preview": "preview",
    "settings.save": "save",
    "settings.save_cli_services": "save_cli_services",
    "settings.revert": "revert",
    "settings.reset_scope": "reset_scope",
    "settings.export_redacted": "export_redacted",
    "settings.import_redacted": "import_redacted",
    "settings.capability_state": "capability_state",
    "settings.credential_create": "credential_create",
    "settings.credential_replace": "credential_replace",
    "settings.credential_status": "credential_status",
    "settings.credential_delete": "credential_delete",
    "settings.model_service_remove": "model_service_remove",
    "settings.verify_api_model": "verify_api_model",
    "settings.start_api_verification": "start_api_verification",
    "settings.api_verification_status": "api_verification_status",
    "settings.verify_cli_model": "verify_cli_model",
    "settings.start_cli_verification": "start_cli_verification",
    "settings.cli_verification_status": "cli_verification_status",
    "settings.cancel_cli_verification": "cancel_cli_verification",
    "settings.execute_structured_chat": "execute_structured_chat",
    "settings.test_workflow_node": "test_workflow_node",
    "settings.start_workflow_exam": "start_workflow_exam",
    "settings.workflow_exam_status": "workflow_exam_status",
    "settings.cancel_workflow_exam": "cancel_workflow_exam",
    "settings.diagnostics": "diagnostics",
    "settings.support_bundle": "support_bundle",
    "settings.effect_metrics": "effect_metrics",
}


def immutable(value: Any) -> Any:
    import json

    return json.loads(canonical_json_bytes(value).decode("utf-8"))


class SettingsServiceAdapter:
    adapter_kind = "configuration_settings_synthetic"

    def __init__(self, controller: Any):
        self.controller = controller
        missing = [
            target
            for target in METHOD_TARGETS.values()
            if not callable(getattr(controller, target, None))
        ]
        if missing:
            raise ValueError("SETTINGS_CONTROLLER_METHOD_MISSING:" + ",".join(missing))

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in METHOD_TARGETS:
            raise ValueError(f"SETTINGS_METHOD_NOT_ALLOWLISTED:{method}")
        if not isinstance(params, Mapping):
            raise ValueError("SETTINGS_PARAMS_OBJECT_REQUIRED")
        target = getattr(self.controller, METHOD_TARGETS[method])
        try:
            inspect.signature(target).bind(**dict(params))
        except TypeError as exc:
            raise ValueError(f"SETTINGS_PARAMS_INVALID:{method}") from exc
        return immutable(target(**dict(params)))

    def parity_projection(self) -> dict[str, Any]:
        return {
            "schema_version": "SettingsServiceParityProjection-v1",
            "adapter_kind": self.adapter_kind,
            "required_methods": list(SETTINGS_METHODS),
            "present_methods": [
                method
                for method, target in METHOD_TARGETS.items()
                if callable(getattr(self.controller, target, None))
            ],
            "missing_methods": [],
            "credential_value_reads": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }


__all__ = ["SETTINGS_METHODS", "SettingsServiceAdapter"]

from __future__ import annotations
from memorive_settings.provider_catalog_aliases import matches_result_model

from copy import deepcopy
from inspect import signature
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .capability import CapabilityProjection
from .contracts import (
    DEFAULT_PROFILE_BY_NODE,
    canonical_sha256,
    immutable,
    risk_ids,
    scan_sensitive,
    settings_field_contract,
    validate_settings,
)
from .credentials import CredentialReferenceManager
from .directory_policy import DirectoryPolicy
from .kimi_endpoint import (
    KIMI_ENDPOINTS,
    is_kimi_service,
    kimi_auto_resolution_requested,
    resolve_kimi_api_platform,
)
from .model_validation import FailClosedModelValidationRunner, ModelValidationRunner
from .model_capabilities import infer_api_model_capability
from .store import SettingsStore
from .external_sources import ExternalDataSources
from .verification_state import api_identity, cli_identity, invalidate_changed_targets


RESET_SCOPE_PREFERENCE_FIELDS = {
    "viewer": (
        "document_viewer",
        "auto_open_preview",
        "external_refresh",
    ),
    "notice": (
        "notifications_enabled",
        "task_complete_notification",
        "task_error_notification",
        "approval_notification",
        "weekly_report_notification",
        "notification_sound",
        "do_not_disturb_start",
        "do_not_disturb_end",
        "notification_open_task",
    ),
    "behavior": (
        "restore_last_view",
        "restore_last_document",
        "close_behavior",
        "warn_on_close_running",
        "launch_at_startup",
        "keep_tasks_in_background",
        "remember_panel_state",
    ),
    "network": (
        "proxy_mode",
        "proxy_address",
        "request_timeout_seconds",
    ),
    "privacy": (
        "log_level",
        "retention_days",
        "cache_limit_mb",
        "telemetry_enabled",
    ),
    "appearance": (
        "language",
        "font_scale_percent",
        "show_full_tooltips",
    ),
}


class SettingsController:
    def __init__(
        self,
        *,
        store: SettingsStore,
        credentials: CredentialReferenceManager,
        capabilities: CapabilityProjection,
        directory_policy: DirectoryPolicy,
        model_validation_runner: ModelValidationRunner | None = None,
        local_profile_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        external_data_sources: ExternalDataSources | None = None,
    ):
        self.store = store
        self.credentials = credentials
        self.capabilities = capabilities
        self.directory_policy = directory_policy
        self.model_validation_runner = (
            model_validation_runner or FailClosedModelValidationRunner()
        )
        self.local_profile_resolver = local_profile_resolver
        from .report_execution import ReportProfileExecution
        self.report_execution = ReportProfileExecution(self)
        from .research_chat_execution import ResearchChatExecution
        self.research_chat_execution = ResearchChatExecution(self)
        self.external_data_sources = external_data_sources or ExternalDataSources(
            store.profile_root / "external_data"
        )
        from .export_bundle import recover
        recover(self)
        from .exam_jobs import WorkflowExamJobs
        self.exam_jobs = WorkflowExamJobs(store, self.test_workflow_node)
        from .cli_verification_jobs import CliVerificationJobs
        self.cli_verification_jobs = CliVerificationJobs(store, self.verify_cli_model, self._prepare_cli_verification)
        from .api_verification_jobs import ApiVerificationJobs
        self.api_verification_jobs = ApiVerificationJobs(store, self.verify_api_model, self._prepare_api_verification)
        self.metrics = {
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_process_launches": 0,
            "production_writes": 0,
            "route_or_profile_enablements": 0,
            "model_validation_requests": 0,
        }
        # One refresh coordinator shared by the background worker and all views.
        self._leaderboard_service()

    def get_contract(self) -> dict[str, Any]:
        return settings_field_contract()

    def user_get(self):
        from .user_preferences import UserPreferences
        return UserPreferences(self.store.profile_root).get()

    def user_save(self, *, config, expected_revision):
        from .user_preferences import UserPreferences
        with self.external_data_sources._write_lock():
            return UserPreferences(self.store.profile_root).save(config=config, expected_revision=expected_revision)

    def get_state(self) -> dict[str, Any]:
        from .api_request_options import reasoning_profiles
        envelope = self.store.load()
        catalog_method = getattr(
            self.model_validation_runner, "workflow_exam_score_catalog", None
        )
        catalog = (
            dict(catalog_method())
            if callable(catalog_method)
            else {
                "schema_version": "WorkflowModelExamScoreCatalogProjection-v2",
                "entries": [],
                "historical_entries": [],
            }
        )
        try:
            external_state = self.external_data_sources.get_state()
        except (ValueError, OSError, TypeError, KeyError):
            # A third-party reference failure must not block local settings or exams.
            external_state = {"status":"UNAVAILABLE", "reason":"EXTERNAL_SOURCE_CONFIG_INVALID",
                              "sources":[], "external_model_calls":0, "auto_refresh":False}
        return {
            "schema_version": "SettingsStateProjection-v1",
            "revision": envelope["revision"],
            "template_id": envelope["template_id"],
            "settings": envelope["settings"],
            "settings_sha256": envelope["settings_sha256"],
            "capabilities": self.capabilities.snapshot(),
            "workflow_exam_score_catalog": catalog,
            "external_data_sources": external_state,
            "api_reasoning_profiles": reasoning_profiles(),
        }


    def _leaderboard_service(self):
        if not hasattr(self, "_leaderboard_instance"):
            from .leaderboard import LeaderboardService
            self._leaderboard_instance = LeaderboardService(
                self.external_data_sources, self.store.profile_root,
                network_preferences=lambda: self.store.load(recover_corruption=False)["settings"]["preferences"])
        return self._leaderboard_instance

    def leaderboard_get(self):
        return self._leaderboard_service().get()

    def leaderboard_save(self, *, expected_revision, changes):
        return self._leaderboard_service().save(expected_revision=expected_revision, changes=changes)

    def leaderboard_refresh(self):
        return self._leaderboard_service().refresh()

    def external_sources_get(self) -> dict[str, Any]:
        return self.external_data_sources.get_state()

    def research_get(self):
        from memorive_research_runtime.common import read, defaults, sealed
        path=self.store.profile_root/'research_preferences.json'
        if not path.exists():return {'schema_version':'ResearchPreferences-v1','revision':0,'config':defaults()}
        saved=read(path)
        # Read-compatible migration; unrelated settings are not rewritten.
        return sealed(dict(saved,config=dict(defaults(),**saved['config'])))

    def research_save(self, *, config, expected_revision):
        from memorive_research_runtime.common import validate_config_edit, sealed
        with self.external_data_sources._write_lock():
            current=self.research_get()
            if type(expected_revision) is not int or expected_revision!=current['revision']:raise ValueError('RESEARCH_REVISION_CONFLICT')
            accepted=validate_config_edit(config,current['config'])
            prior={d['id']:d for d in current['config'].get('directions',[])}
            for direction in accepted['directions']:
                old=prior.get(direction['id'])
                if old is None:continue
                semantic_changed=any(old[k]!=direction[k] for k in ('query','keywords','seed_ids'))
                expected=old['revision']+int(semantic_changed)
                if direction['revision']!=expected:raise ValueError('RESEARCH_DIRECTION_REVISION_CONFLICT')
            value=sealed({'schema_version':'ResearchPreferences-v1','revision':current['revision']+1,'config':accepted})
            self.store._atomic_write(self.store.profile_root/'research_preferences.json',value)
        return value

    def external_sources_save(self, *, source: Mapping[str, Any], expected_revision: int) -> dict[str, Any]:
        return self.external_data_sources.save(source, expected_revision=expected_revision)

    def external_sources_refresh(self, *, source_id: str, expected_revision: int) -> dict[str, Any]:
        settings = self.store.load(recover_corruption=False)["settings"]
        result = self.external_data_sources.refresh(
            source_id, expected_revision=expected_revision, preferences=settings["preferences"]
        )
        self.metrics["external_network_calls"] += int(result.get("external_network_calls") or 0)
        return result

    def external_model_reference(self, *, node_id: str, expected_profile_ref: str | None = None,
                                 preview_profile_ref: str | None = None) -> dict[str, Any]:
        from .exam_tiers import ROLE_BY_NODE
        settings = self.store.load(recover_corruption=False)["settings"]
        node = next((r for r in settings["workflow"]["nodes"] if r["node_id"] == node_id), None)
        if node is None or node_id not in ROLE_BY_NODE:
            raise ValueError("WORKFLOW_NODE_MODEL_TEST_NOT_APPLICABLE")
        if expected_profile_ref is not None and expected_profile_ref != node["profile_ref"]:
            raise ValueError("EXTERNAL_REFERENCE_REQUIRES_SAVED_MODEL_MAPPING")
        profile_ref = preview_profile_ref if preview_profile_ref is not None else node["profile_ref"]
        target = self._workflow_model_target(settings, profile_ref)
        if preview_profile_ref is not None and target.get("kind") not in {"API", "CLI", "LOCAL"}:
            raise ValueError("EXTERNAL_REFERENCE_PROFILE_NOT_CONFIGURED")
        lookup = getattr(self.model_validation_runner, "cached_workflow_node_score", None)
        cached = lookup(immutable({**node, "profile_ref":profile_ref}), immutable(target)) if callable(lookup) else None
        score = cached.get("score") if isinstance(cached, Mapping) else None
        if type(score) is not int or not 0 <= score <= 100:
            score = None
        # Resolve from the same frozen/history/executor binding as the exam.
        # Never copy another selection's mutable display score or send inference.
        try:
            result = self.external_data_sources.references(ROLE_BY_NODE[node_id], target, internal_exam_score=score)
        except (ValueError, OSError, TypeError, KeyError):
            from .external_sources import role_references
            result = role_references(ROLE_BY_NODE[node_id], target, [], internal_exam_score=score)
            result['unavailable_sources'] = ['EXTERNAL_SOURCE_CONFIG_INVALID']
        result.update(node_id=node_id, profile_ref=profile_ref, cached_exam_score_resolved=True,
                      persistent_mutation=False, external_model_calls=0)
        return result

    def _workflow_model_target(
        self, settings: Mapping[str, Any], profile_ref: str
    ) -> dict[str, Any]:
        target: dict[str, Any] = {
            "kind": "BUILTIN_PROFILE",
            "profile_ref": profile_ref,
        }
        for service in settings["model_services"]:
            if service["config_id"] == profile_ref:
                return {"kind": "API", **service}
        for cli_service in settings["cli_services"]:
            model = next(
                (
                    row
                    for row in cli_service["models"]
                    if row["profile_ref"] == profile_ref
                ),
                None,
            )
            if model is not None:
                return {
                    "kind": "CLI",
                    "adapter_id": cli_service["adapter_id"],
                    "config_id": cli_service["config_id"],
                    "enabled": cli_service.get("enabled"),
                    "executable": cli_service.get("executable"),
                    **model,
                }
        if isinstance(profile_ref, str) and profile_ref.startswith("local:") and self.local_profile_resolver is not None:
            try:
                local = self.local_profile_resolver(profile_ref)
            except (OSError, RuntimeError, TypeError, ValueError):
                local = None
            if isinstance(local, Mapping):
                return {
                    "kind": "LOCAL",
                    "profile_ref": profile_ref,
                    "config_id": profile_ref,
                    **dict(local),
                }
        return target

    def _embedding_binding_errors(
        self, settings: Mapping[str, Any]
    ) -> list[str]:
        node = next(
            row
            for row in settings["workflow"]["nodes"]
            if row["node_id"] == "chunk_embedding"
        )
        profile_ref = node.get("profile_ref")
        if profile_ref is None:
            return []
        service = next(
            (
                row
                for row in settings["model_services"]
                if row["config_id"] == profile_ref
            ),
            None,
        )
        if service is not None:
            credential_stored = any(
                row["credential_ref"] == service["credential_ref"]
                and row["status"] == "STORED_UNVERIFIED"
                for row in settings["credential_references"]
            )
            if service.get("connection_status") != "AVAILABLE" or not credential_stored:
                return ["WORKFLOW_API_EMBEDDING_PROFILE_UNAVAILABLE"]
            return []
        if not isinstance(profile_ref, str) or not profile_ref.startswith("local:"):
            return ["WORKFLOW_EMBEDDING_PROFILE_UNAVAILABLE"]
        if self.local_profile_resolver is None:
            return ["WORKFLOW_LOCAL_EMBEDDING_PROFILE_UNAVAILABLE"]
        try:
            local = self.local_profile_resolver(profile_ref)
        except (OSError, RuntimeError, TypeError, ValueError):
            local = None
        if not isinstance(local, Mapping) or not (
            local.get("capability") == "EMBEDDING"
            and local.get("connection_status") == "AVAILABLE"
            and local.get("endpoint_kind") == "ollama"
            and local.get("exact_identity_available") is True
            and local.get("execution_eligible") is True
        ):
            return ["WORKFLOW_LOCAL_EMBEDDING_PROFILE_UNAVAILABLE"]
        return []

    def _reranker_binding_errors(
        self, settings: Mapping[str, Any]
    ) -> list[str]:
        node = next(
            row
            for row in settings["workflow"]["nodes"]
            if row["node_id"] == "context_pack"
        )
        profile_ref = node.get("profile_ref")
        if profile_ref is None:
            return []
        service = next(
            (
                row
                for row in settings["model_services"]
                if row["config_id"] == profile_ref
            ),
            None,
        )
        if service is not None:
            credential_stored = any(
                row["credential_ref"] == service["credential_ref"]
                and row["status"] == "STORED_UNVERIFIED"
                for row in settings["credential_references"]
            )
            if (
                service.get("connection_status") != "AVAILABLE"
                or not credential_stored
            ):
                return ["WORKFLOW_API_RERANKER_PROFILE_UNAVAILABLE"]
            return []
        for cli in settings.get("cli_services", []):
            for model in cli.get("models", []):
                if model.get("profile_ref") == profile_ref:
                    return [] if cli.get("enabled") is True and model.get("connection_status") == "AVAILABLE" else ["WORKFLOW_CLI_RERANKER_PROFILE_UNAVAILABLE"]
        if not isinstance(profile_ref, str) or not profile_ref.startswith(
            "local:"
        ):
            return ["WORKFLOW_RERANKER_PROFILE_UNAVAILABLE"]
        if self.local_profile_resolver is None:
            return ["WORKFLOW_LOCAL_RERANKER_PROFILE_UNAVAILABLE"]
        try:
            local = self.local_profile_resolver(profile_ref)
        except (OSError, RuntimeError, TypeError, ValueError):
            local = None
        if not isinstance(local, Mapping) or not (
            (local.get("capability") == "RERANKER" or "CHAT" in local.get("capabilities", [local.get("capability")]))
            and local.get("connection_status") == "AVAILABLE"
            and local.get("endpoint_kind") in {"loopback_reranker", "ollama"}
            and local.get("exact_identity_available") is True
            and local.get("execution_eligible") is True
        ):
            return ["WORKFLOW_LOCAL_RERANKER_PROFILE_UNAVAILABLE"]
        return []

    def _hydrate_frozen_workflow_scores(
        self, settings: Mapping[str, Any]
    ) -> dict[str, Any]:
        accepted = deepcopy(settings)
        lookup = getattr(
            self.model_validation_runner, "cached_workflow_node_score", None
        )
        if not callable(lookup):
            return accepted
        for node in accepted["workflow"]["nodes"]:
            profile_ref = node.get("profile_ref")
            if (
                node.get("node_id") in {"card_admission", "human_judgment"}
                or not isinstance(profile_ref, str)
                or profile_ref == "AUTO_HETEROGENEOUS"
            ):
                continue
            row = lookup(
                immutable(node),
                immutable(self._workflow_model_target(accepted, profile_ref)),
            )
            if isinstance(row, Mapping):
                score = row.get("score")
                if (
                    isinstance(score, int)
                    and not isinstance(score, bool)
                    and 0 <= score <= 100
                ):
                    node["test_score"] = score
        return accepted

    def import_cached_exam_scores(self) -> dict[str, Any]:
        """Refresh only bound scores; preserve the exact preceding settings."""
        before = self.store.load(recover_corruption=False)
        accepted = self._hydrate_frozen_workflow_scores(before["settings"])
        changes = [{"node_id":old["node_id"], "before":old.get("test_score"), "after":new.get("test_score")}
                   for old,new in zip(before["settings"]["workflow"]["nodes"], accepted["workflow"]["nodes"])
                   if old.get("test_score") != new.get("test_score")]
        if not changes:
            return {"status":"UNCHANGED", "changes":[], "external_model_calls":0}
        directory = self.store.profile_root / "exam_score_imports"
        directory.mkdir(exist_ok=True)
        identity = canonical_sha256({"before":before, "changes":changes})
        self.store._atomic_write(directory / (identity + ".before.json"), before)
        saved = self.store.save(accepted, expected_revision=before["revision"])
        receipt = {"schema_version":"ExistingExamScoreImport-v1", "status":"IMPORTED", "changes":changes,
                   "before_revision":before["revision"], "after_revision":saved["revision"],
                   "before_sha256":before["settings_sha256"], "after_sha256":saved["settings_sha256"],
                   "model_mapping_changed":False, "external_model_calls":0}
        self.store._atomic_write(directory / (identity + ".receipt.json"), receipt)
        return receipt

    def register_directory(self, *, role: str, path: str) -> dict[str, Any]:
        if role not in {"workspace_root", "artifact_root", "external_library"}:
            raise ValueError("DIRECTORY_ROLE_INVALID")
        resolved = self.directory_policy.register_user_selected_root(path)
        return {
            "schema_version": "SettingsDirectoryRegistrationReceipt-v1",
            "role": role,
            "path": str(resolved),
            "user_selected": True,
            "persistent_mutation": False,
            "status": "PASS",
        }

    def preview(
        self,
        settings: Mapping[str, Any],
        confirmed_risk_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        confirmed = set(confirmed_risk_ids or [])
        try:
            accepted = validate_settings(settings)
            directory = self.directory_policy.inspect(accepted)
            risks = risk_ids(accepted)
            pending = [risk for risk in risks if risk not in confirmed]
            errors = list(directory["errors"])
            errors.extend(self._embedding_binding_errors(accepted))
            errors.extend(self._reranker_binding_errors(accepted))
        except Exception as exc:
            return {
                "schema_version": "SettingsPreview-v1",
                "status": "BLOCKED",
                "errors": [str(exc)],
                "pending_risks": [],
                "restart_required": False,
                "network_requests": 0,
                "credential_value_reads": 0,
            }
        return {
            "schema_version": "SettingsPreview-v1",
            "status": "PASS" if not errors and not pending else "BLOCKED",
            "errors": errors,
            "pending_risks": pending,
            "settings_sha256": canonical_sha256(accepted),
            "directory_preflight": directory,
            "restart_required": False,
            "network_requests": 0,
            "credential_value_reads": 0,
        }

    def save(
        self,
        settings: Mapping[str, Any],
        *,
        expected_revision: int,
        confirmed_risk_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        current = self.store.load(recover_corruption=False)
        accepted = validate_settings(settings)
        if (
            accepted["credential_references"]
            != current["settings"]["credential_references"]
        ):
            raise ValueError("CREDENTIAL_REFERENCES_MUTATION_REQUIRES_DEDICATED_FLOW")
        accepted = self._hydrate_frozen_workflow_scores(accepted)
        preview = self.preview(accepted, confirmed_risk_ids)
        if preview["status"] != "PASS":
            raise ValueError(
                "SETTINGS_PREVIEW_BLOCKED:"
                + ",".join(preview["errors"] + preview["pending_risks"])
            )
        capability_before = self.capabilities.snapshot()
        before_embedding_ref = next(
            row["profile_ref"]
            for row in current["settings"]["workflow"]["nodes"]
            if row["node_id"] == "chunk_embedding"
        )
        after_embedding_ref = next(
            row["profile_ref"]
            for row in accepted["workflow"]["nodes"]
            if row["node_id"] == "chunk_embedding"
        )
        accepted = invalidate_changed_targets(current['settings'], accepted)
        saved = self.store.save(accepted, expected_revision=expected_revision)
        if not self.capabilities.assert_unchanged(capability_before):
            raise RuntimeError("CAPABILITY_PROJECTION_MUTATED_BY_SAVE")
        return self._mutation_receipt(
            "SAVE",
            saved,
            restart_required=preview["restart_required"],
            credentials_preserved=True,
            embedding_binding_changed=before_embedding_ref != after_embedding_ref,
        )

    def save_cli_services(
        self,
        cli_services: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        def blocked(error_code: str) -> dict[str, Any]:
            return immutable(
                {
                    "schema_version": "SettingsCliServicesSaveReceipt-v1",
                    "status": "BLOCKED",
                    "error_code": error_code,
                    "persistent_mutation": False,
                    "cli_only": True,
                    "global_settings_preserved": True,
                }
            )

        if mode not in {"ADVANCED", "DEVELOPER"}:
            return blocked("CLI_MODE_NORMAL_FORBIDDEN")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            return blocked("EXPECTED_REVISION_INVALID")
        current = self.store.load(recover_corruption=False)
        if expected_revision != current["revision"]:
            return blocked("EXPECTED_REVISION_CONFLICT")
        settings = deepcopy(current["settings"])
        settings["mode"] = mode
        settings["cli_services"] = deepcopy(cli_services)
        try:
            accepted = validate_settings(settings)
            accepted = invalidate_changed_targets(current['settings'], accepted)
            saved = self.store.save(accepted, expected_revision=expected_revision)
        except (KeyError, TypeError, ValueError) as exc:
            return blocked(str(exc) or "CLI_SETTINGS_INVALID")
        receipt = self._mutation_receipt(
            "SAVE_CLI_SERVICES",
            saved,
            restart_required=False,
            credentials_preserved=True,
        )
        receipt.update(
            {
                "schema_version": "SettingsCliServicesSaveReceipt-v1",
                "status": "PASS",
                "mutation_kind": "SAVE_CLI_SERVICES",
                "persistent_mutation": True,
                "cli_only": True,
                "global_settings_preserved": True,
                "model_services_preserved": True,
                "credential_references_preserved": True,
                "workflow_preserved": True,
                "preferences_preserved": True,
            }
        )
        return immutable(receipt)

    def revert(self) -> dict[str, Any]:
        current = self.store.load()
        return {
            "schema_version": "SettingsRevertReceipt-v1",
            "revision": current["revision"],
            "settings": current["settings"],
            "settings_sha256": current["settings_sha256"],
            "persistent_mutation": False,
        }

    def reset(self, *, expected_revision: int) -> dict[str, Any]:
        del expected_revision
        raise ValueError("GLOBAL_SETTINGS_RESET_DISABLED")

    def reset_scope(
        self,
        *,
        scope: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        if scope not in RESET_SCOPE_PREFERENCE_FIELDS:
            raise ValueError(f"SETTINGS_RESET_SCOPE_FORBIDDEN:{scope}")
        current = self.store.load(recover_corruption=False)
        default = self.store.default_factory()
        settings = deepcopy(current["settings"])
        for key in RESET_SCOPE_PREFERENCE_FIELDS[scope]:
            settings["preferences"][key] = deepcopy(default["preferences"][key])
        if scope == "viewer":
            settings["directories"] = deepcopy(default["directories"])
        saved = self.store.save(settings, expected_revision=expected_revision)
        receipt = self._mutation_receipt(
            "RESET_SETTINGS_SCOPE",
            saved,
            restart_required=scope == "network",
            credentials_preserved=True,
        )
        receipt.update(
            {
                "mutation_kind": "RESET_SETTINGS_SCOPE",
                "scope": scope,
                "global_reset": False,
                "model_services_preserved": True,
                "cli_services_preserved": True,
                "workflow_preserved": True,
            }
        )
        return immutable(receipt)

    def export_redacted(self) -> dict[str, Any]:
        from .export_bundle import extensions
        current = self.store.load()
        payload = {
            "schema_version": "SettingsRedactedExport-v2",
            "source_revision": current["revision"],
            "settings": current["settings"],
            "credential_values_included": False,
            "extensions": extensions(self),
        }
        scan_sensitive(payload)
        payload["export_sha256"] = canonical_sha256(payload)
        return immutable(payload)

    def import_redacted(
        self,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
        confirmed_risk_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if set(payload) - {
            "schema_version",
            "source_revision",
            "settings",
            "credential_values_included",
            "export_sha256",
            "extensions",
        }:
            raise ValueError("SETTINGS_IMPORT_UNKNOWN_FIELD")
        if payload.get("schema_version") not in {"SettingsRedactedExport-v1", "SettingsRedactedExport-v2"}:
            raise ValueError("SETTINGS_IMPORT_SCHEMA_INVALID")
        if payload.get('export_sha256') != canonical_sha256({k:v for k,v in payload.items() if k!='export_sha256'}):
            raise ValueError('SETTINGS_IMPORT_CHECKSUM_MISMATCH')
        if (payload['schema_version']=='SettingsRedactedExport-v2') != ('extensions' in payload):
            raise ValueError('SETTINGS_IMPORT_EXTENSIONS_INVALID')
        if payload.get("credential_values_included") is not False:
            raise ValueError("SETTINGS_IMPORT_CREDENTIAL_VALUE_REJECTED")
        scan_sensitive(payload)
        settings = validate_settings(payload["settings"])
        current = self.store.load(recover_corruption=False)
        if (
            settings["credential_references"]
            != current["settings"]["credential_references"]
        ):
            raise ValueError("SETTINGS_IMPORT_CREDENTIAL_REFERENCE_DRIFT")
        if payload['schema_version']=='SettingsRedactedExport-v2':
            from .export_bundle import import_bundle
            return import_bundle(self,payload,expected_revision,confirmed_risk_ids)
        receipt = self.save(
            settings,
            expected_revision=expected_revision,
            confirmed_risk_ids=confirmed_risk_ids,
        )
        receipt["action"] = "IMPORT_REDACTED"
        return receipt

    def capability_state(self) -> dict[str, Any]:
        return self.capabilities.snapshot()

    def credential_create(
        self,
        *,
        provider: str,
        logical_key: str,
        secret_input: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        current = self.store.load(recover_corruption=False)
        if current["revision"] != expected_revision:
            raise ValueError("SETTINGS_REVISION_CONFLICT")
        reference = self.credentials.reference(provider, logical_key)
        if any(
            row["credential_ref"] == reference
            for row in current["settings"]["credential_references"]
        ):
            raise ValueError("CREDENTIAL_REFERENCE_ALREADY_EXISTS")
        metadata = self.credentials.bind(
            provider=provider,
            logical_key=logical_key,
            secret_input=secret_input,
        )
        settings = deepcopy(current["settings"])
        settings["credential_references"].append(
            {
                key: metadata[key]
                for key in (
                    "credential_ref",
                    "provider",
                    "logical_key",
                    "status",
                    "created_at",
                    "updated_at",
                )
            }
        )
        try:
            saved = self.store.save(settings, expected_revision=expected_revision)
        except Exception:
            self.credentials.delete(reference)
            raise
        return {
            **metadata,
            "settings_revision": saved["revision"],
            "settings_sha256": saved["settings_sha256"],
        }

    def credential_replace(
        self,
        *,
        credential_ref: str,
        secret_input: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        current = self.store.load(recover_corruption=False)
        matches = [
            row
            for row in current["settings"]["credential_references"]
            if row["credential_ref"] == credential_ref
        ]
        if len(matches) != 1:
            raise ValueError("CREDENTIAL_REFERENCE_NOT_FOUND")
        prior = matches[0]
        metadata = self.credentials.bind(
            provider=prior["provider"],
            logical_key=prior["logical_key"],
            secret_input=secret_input,
            created_at=prior["created_at"],
        )
        settings = deepcopy(current["settings"])
        invalidated_model_service_count = 0
        kimi_auto_route_cache_cleared_count = 0
        for row in settings["credential_references"]:
            if row["credential_ref"] == credential_ref:
                row.update(
                    {
                        key: metadata[key]
                        for key in (
                            "status",
                            "created_at",
                            "updated_at",
                        )
                    }
                )
        for service in settings["model_services"]:
            if service["credential_ref"] != credential_ref:
                continue
            service["connection_status"] = "UNVERIFIED"
            invalidated_model_service_count += 1
            if (
                is_kimi_service(service)
                and service.get("api_platform") == "AUTO"
            ):
                service["api_protocol"] = "AUTO"
                service["api_base_url"] = ""
                kimi_auto_route_cache_cleared_count += 1
        saved = self.store.save(settings, expected_revision=expected_revision)
        return {
            **metadata,
            "settings_revision": saved["revision"],
            "settings_sha256": saved["settings_sha256"],
            "invalidated_model_service_count": invalidated_model_service_count,
            "kimi_auto_route_cache_cleared_count": (
                kimi_auto_route_cache_cleared_count
            ),
        }

    def credential_status(self, credential_ref: str) -> dict[str, Any]:
        return self.credentials.status(credential_ref)

    def credential_delete(
        self, *, credential_ref: str, expected_revision: int
    ) -> dict[str, Any]:
        current = self.store.load(recover_corruption=False)
        settings = deepcopy(current["settings"])
        removed_config_ids = {
            row["config_id"]
            for row in settings["model_services"]
            if row["credential_ref"] == credential_ref
        }
        settings["model_services"] = [
            row
            for row in settings["model_services"]
            if row["credential_ref"] != credential_ref
        ]
        settings["credential_references"] = [
            row
            for row in settings["credential_references"]
            if row["credential_ref"] != credential_ref
        ]
        for node in settings["workflow"]["nodes"]:
            if node["profile_ref"] in removed_config_ids:
                node["profile_ref"] = DEFAULT_PROFILE_BY_NODE[node["node_id"]]
        saved = self.store.save(settings, expected_revision=expected_revision)
        deletion = self.credentials.delete(credential_ref)
        return {
            **deletion,
            "settings_revision": saved["revision"],
            "settings_sha256": saved["settings_sha256"],
        }

    def model_service_remove(
        self, *, config_id: str, expected_revision: int
    ) -> dict[str, Any]:
        current = self.store.load(recover_corruption=False)
        settings = deepcopy(current["settings"])
        matches = [
            row for row in settings["model_services"] if row["config_id"] == config_id
        ]
        if len(matches) != 1:
            raise ValueError("MODEL_SERVICE_NOT_FOUND")
        credential_ref = matches[0]["credential_ref"]
        settings["model_services"] = [
            row for row in settings["model_services"] if row["config_id"] != config_id
        ]
        for node in settings["workflow"]["nodes"]:
            if node["profile_ref"] == config_id:
                node["profile_ref"] = DEFAULT_PROFILE_BY_NODE[node["node_id"]]
                node["test_score"] = None
        credential_deleted = not any(
            row["credential_ref"] == credential_ref
            for row in settings["model_services"]
        )
        if credential_deleted:
            settings["credential_references"] = [
                row
                for row in settings["credential_references"]
                if row["credential_ref"] != credential_ref
            ]
        saved = self.store.save(settings, expected_revision=expected_revision)
        if credential_deleted:
            self.credentials.delete(credential_ref)
        return immutable(
            {
                "schema_version": "SettingsModelServiceRemovalReceipt-v1",
                "removed_config_id": config_id,
                "credential_ref": credential_ref,
                "credential_deleted": credential_deleted,
                "settings_revision": saved["revision"],
                "settings_sha256": saved["settings_sha256"],
                "other_settings_preserved": True,
            }
        )

    @staticmethod
    def _validation_status(result: Mapping[str, Any]) -> str:
        status = result.get("status")
        if status not in {"AVAILABLE", "INVALID", "PASS", "FAIL", "NOT_RUN"}:
            raise ValueError("MODEL_VALIDATION_RESULT_STATUS_INVALID")
        return str(status)

    def _record_validation_effects(self, result: Mapping[str, Any]) -> None:
        self.metrics["model_validation_requests"] += 1
        for field in (
            "external_network_calls",
            "provider_calls",
            "external_model_calls",
            "external_process_launches",
        ):
            count = result.get(field, 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("MODEL_VALIDATION_EFFECT_COUNT_INVALID")
            self.metrics[field] += count

    def _validation_receipt(
        self,
        *,
        kind: str,
        target_ref: str,
        result: Mapping[str, Any],
        envelope: Mapping[str, Any],
        connection_status: str | None = None,
        score: int | None = None,
        persistent_mutation: bool,
    ) -> dict[str, Any]:
        reason = result.get("reason", "NONE")
        if not isinstance(reason, str) or not reason or len(reason) > 256:
            raise ValueError("MODEL_VALIDATION_REASON_INVALID")
        requested_model = result.get("requested_model")
        returned_model = result.get("returned_model")
        for value in (requested_model, returned_model):
            if value is not None and (
                not isinstance(value, str) or not value or len(value) > 256
            ):
                raise ValueError("MODEL_VALIDATION_MODEL_IDENTITY_INVALID")
        available_models = result.get("available_models", [])
        if (
            not isinstance(available_models, (list, tuple))
            or len(available_models) > 50
        ):
            raise ValueError("MODEL_VALIDATION_AVAILABLE_MODELS_INVALID")
        normalized_available_models: list[str] = []
        for model_id in available_models:
            if (
                not isinstance(model_id, str)
                or not model_id
                or len(model_id) > 128
                or model_id in normalized_available_models
            ):
                raise ValueError("MODEL_VALIDATION_AVAILABLE_MODELS_INVALID")
            normalized_available_models.append(model_id)
        suggested_model = result.get("suggested_model")
        if suggested_model is not None and (
            not isinstance(suggested_model, str)
            or suggested_model not in normalized_available_models
        ):
            raise ValueError("MODEL_VALIDATION_SUGGESTED_MODEL_INVALID")
        model_capability = result.get("model_capability")
        if model_capability is not None and model_capability not in {
            "CHAT",
            "EMBEDDING",
            "VISION_OCR",
        }:
            raise ValueError("MODEL_VALIDATION_CAPABILITY_INVALID")
        validation_endpoint_kind = result.get("validation_endpoint_kind")
        if validation_endpoint_kind is not None and validation_endpoint_kind != "MODEL_CATALOG":
            raise ValueError("MODEL_VALIDATION_ENDPOINT_KIND_INVALID")
        duration_ms = result.get("duration_ms", 0)
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms < 0
        ):
            raise ValueError("MODEL_VALIDATION_DURATION_INVALID")
        exam_fields: dict[str, Any] = {}
        for field in ("exam_mode", "exam_run_id", "cost_semantics"):
            value = result.get(field)
            if value is not None:
                if not isinstance(value, str) or not value or len(value) > 160:
                    raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
                exam_fields[field] = value
        for field in (
            "score_semantics",
            "qualification_verdict",
            "quality_exact_repair_qualification_verdict",
            "operational_eligibility_verdict",
            "operational_safety_gate",
            "operational_policy_revision",
            "core_lifecycle_revision",
            "core_closure_verdict",
            "repair_prompt_projection_revision",
            "scoring_system_revision",
            "scoring_projection_revision",
            "exam_category_id",
            "role_id",
            "reference_pack_id",
            "reference_pack_revision",
            "scoring_protocol_revision",
            "comparison_cohort_id",
            "sample_revision",
            "output_limit_policy",
        ):
            value = result.get(field)
            if value is not None:
                if not isinstance(value, str) or not value or len(value) > 160:
                    raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
                exam_fields[field] = value
        qualification_verdict = exam_fields.get("qualification_verdict")
        if qualification_verdict is not None and qualification_verdict not in {
            "PASS",
            "FAIL",
            "DISQUALIFIED",
        }:
            raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
        quality_qualification = exam_fields.get(
            "quality_exact_repair_qualification_verdict"
        )
        if quality_qualification is not None and quality_qualification not in {
            "PASS",
            "FAIL",
            "DISQUALIFIED",
        }:
            raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
        operational_verdict = exam_fields.get(
            "operational_eligibility_verdict"
        )
        if operational_verdict is not None and operational_verdict not in {
            "SUITABLE",
            "SUITABLE_WITH_CALIBRATION",
            "SUITABLE_WITH_GOVERNED_DISPOSITION",
            "NOT_RECOMMENDED",
            "REJECT",
            "NOT_ASSESSED",
        }:
            raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
        operational_safety_gate = exam_fields.get("operational_safety_gate")
        if operational_safety_gate is not None and operational_safety_gate not in {
            "PASS",
            "FAIL",
            "NOT_ASSESSED",
        }:
            raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
        for field in (
            "score_exact",
            "repair_outcome_score",
            "core_closure_score",
            "repair_completion_rate",
            "exact_case_repair_rate",
            "case_score_deficit_recovery_rate",
            "collision_adjusted_repair_diagnostic_rate",
        ):
            value = result.get(field)
            if value is not None:
                maximum = (
                    1.0
                    if field
                    in {
                        "repair_completion_rate",
                        "exact_case_repair_rate",
                        "case_score_deficit_recovery_rate",
                        "collision_adjusted_repair_diagnostic_rate",
                    }
                    else 100.0
                )
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0.0 <= float(value) <= maximum
                ):
                    raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
                exam_fields[field] = float(value)
        evidence_sha256 = result.get("exam_evidence_sha256")
        if evidence_sha256 is not None:
            if (
                not isinstance(evidence_sha256, str)
                or len(evidence_sha256) != 64
                or any(character not in "0123456789ABCDEF" for character in evidence_sha256)
            ):
                raise ValueError("MODEL_VALIDATION_EXAM_EVIDENCE_INVALID")
            exam_fields["exam_evidence_sha256"] = evidence_sha256
        for field in (
            "reference_pack_sha256",
            "scoring_protocol_sha256",
            "comparison_binding_sha256",
            "sample_manifest_sha256",
        ):
            value = result.get(field)
            if value is not None:
                if (
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(
                        character not in "0123456789ABCDEF"
                        for character in value
                    )
                ):
                    raise ValueError("MODEL_VALIDATION_EXAM_EVIDENCE_INVALID")
                exam_fields[field] = value
        for field in (
            "failed_exam_item_count",
            "repair_rounds",
            "semantic_repair_rounds",
            "structural_repair_turns",
            "governed_disposition_case_count",
            "sample_slot",
            "initial_total_effective_prompt_chars",
            "safety_blocking_residual_count",
            "calibration_residual_count",
            "repair_metadata_collision_suspect_count",
        ):
            value = result.get(field)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
                exam_fields[field] = value
        diagnostic_only = result.get(
            "collision_adjusted_repair_diagnostic_only"
        )
        if diagnostic_only is not None:
            if diagnostic_only is not True:
                raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
            exam_fields[
                "collision_adjusted_repair_diagnostic_only"
            ] = True
        estimated_cost_cny = result.get("estimated_cost_cny")
        if estimated_cost_cny is not None:
            if (
                isinstance(estimated_cost_cny, bool)
                or not isinstance(estimated_cost_cny, (int, float))
                or estimated_cost_cny < 0
            ):
                raise ValueError("MODEL_VALIDATION_EXAM_COST_INVALID")
            exam_fields["estimated_cost_cny"] = float(estimated_cost_cny)
        if "actual_cost" in result:
            actual_cost = result.get("actual_cost")
            if actual_cost is not None:
                raise ValueError("MODEL_VALIDATION_EXAM_ACTUAL_COST_INVALID")
            exam_fields["actual_cost"] = None
        for field in (
            "reference_regression_only",
            "blind_holdout_eligible",
            "qualification_eligible",
            "provider_received_answer_key",
            "horizontal_comparison_eligible",
            "horizontal_comparison_allowed_only_with_same_cohort",
            "cross_category_comparison_forbidden",
            "global_ranking_forbidden",
        ):
            value = result.get(field)
            if value is not None:
                if not isinstance(value, bool):
                    raise ValueError("MODEL_VALIDATION_EXAM_METADATA_INVALID")
                exam_fields[field] = value
        token_usage = result.get("token_usage")
        if token_usage is not None:
            if not isinstance(token_usage, Mapping):
                raise ValueError("MODEL_VALIDATION_TOKEN_USAGE_INVALID")
            normalized_usage: dict[str, int] = {}
            for key, value in token_usage.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise ValueError("MODEL_VALIDATION_TOKEN_USAGE_INVALID")
                normalized_usage[key] = value
            exam_fields["token_usage"] = normalized_usage
        api_platform = result.get("api_platform")
        if api_platform is not None:
            if api_platform not in KIMI_ENDPOINTS:
                raise ValueError("MODEL_VALIDATION_API_PLATFORM_INVALID")
            resolved_api_base_url = result.get("resolved_api_base_url")
            if resolved_api_base_url != KIMI_ENDPOINTS[api_platform]:
                raise ValueError("MODEL_VALIDATION_API_PLATFORM_INVALID")
            platform_probe_count = result.get("platform_probe_count")
            if (
                isinstance(platform_probe_count, bool)
                or not isinstance(platform_probe_count, int)
                or platform_probe_count not in {1, 2}
            ):
                raise ValueError("MODEL_VALIDATION_API_PLATFORM_INVALID")
            if (
                result.get("key_text_inspected") is not False
                or result.get("secret_hash_recorded") is not False
                or result.get("external_model_calls") != 0
            ):
                raise ValueError("MODEL_VALIDATION_API_PLATFORM_PROBE_INVALID")
            exam_fields.update(
                api_platform=api_platform,
                resolved_api_base_url=resolved_api_base_url,
                platform_probe_count=platform_probe_count,
                key_text_inspected=False,
                secret_hash_recorded=False,
            )
        receipt = {
            "schema_version": "SettingsModelValidationReceipt-v1",
            "kind": kind,
            "target_ref": target_ref,
            "status": self._validation_status(result),
            "reason": reason,
            "connection_status": connection_status,
            "score": score,
            "requested_model": requested_model,
            "returned_model": returned_model,
            "available_models": normalized_available_models,
            "suggested_model": suggested_model,
            "model_capability": model_capability,
            "validation_endpoint_kind": validation_endpoint_kind,
            "duration_ms": duration_ms,
            "revision": envelope["revision"],
            "settings_sha256": envelope["settings_sha256"],
            "persistent_mutation": persistent_mutation,
            "external_network_calls": int(result.get("external_network_calls", 0)),
            "provider_calls": int(result.get("provider_calls", 0)),
            "external_model_calls": int(result.get("external_model_calls", 0)),
            "external_process_launches": int(
                result.get("external_process_launches", 0)
            ),
            **exam_fields,
        }
        if kind == 'CLI' and isinstance(result.get('cli_diagnostic'), Mapping):
            diagnostic=result['cli_diagnostic']
            receipt['cli_diagnostic']={key:deepcopy(diagnostic[key]) for key in (
                'hard_timeout_seconds','cancelled','last_stage','event_types','attempt_id') if key in diagnostic}
        scan_sensitive(receipt)
        return immutable(receipt)

    def _prepare_api_verification(self, *, config_id, target_sha256=None):
        current = self.store.load(recover_corruption=False)
        settings = deepcopy(current['settings'])
        service = next((r for r in settings['model_services'] if r['config_id'] == config_id), None)
        if service is None: raise ValueError('MODEL_SERVICE_NOT_FOUND')
        if target_sha256 is not None and api_identity(settings, service) != target_sha256:
            raise ValueError('API_VERIFICATION_TARGET_CHANGED')
        if service['connection_status'] == 'UNVERIFIED': return current
        service['connection_status'] = 'UNVERIFIED'
        return self.store.save(settings, expected_revision=current['revision'])

    def start_api_verification(self, *, config_id, idempotency_key):
        current = self.store.load(recover_corruption=False)
        service = next((r for r in current['settings']['model_services'] if r['config_id'] == config_id), None)
        if service is None: raise ValueError('MODEL_SERVICE_NOT_FOUND')
        return self.api_verification_jobs.start(params={'config_id':config_id,
            'target_sha256':api_identity(current['settings'],service)}, idempotency_key=idempotency_key)

    def api_verification_status(self, *, job_id=None, config_id=None, idempotency_key=None):
        return self.api_verification_jobs.status(job_id=job_id, config_id=config_id, idempotency_key=idempotency_key)

    def verify_api_model(self, *, config_id: str, target_sha256=None) -> dict[str, Any]:
        current = self.store.load(recover_corruption=False)
        services = current["settings"]["model_services"]
        service = next((row for row in services if row["config_id"] == config_id), None)
        if service is None:
            raise ValueError("MODEL_SERVICE_NOT_FOUND")
        identity = api_identity(current['settings'], service)
        if target_sha256 is not None and identity != target_sha256:
            raise ValueError('API_VERIFICATION_TARGET_CHANGED')
        current = self._prepare_api_verification(config_id=config_id, target_sha256=identity)
        service = next(r for r in current['settings']['model_services'] if r['config_id'] == config_id)
        if kimi_auto_resolution_requested(service):
            requested_model = str(service.get("model_name") or "")
            credential_ref = str(service.get("credential_ref") or "")
            resolution = resolve_kimi_api_platform(
                credential_ref=credential_ref,
                requested_model=requested_model,
                platform="AUTO",
                verify_api_model=lambda candidate: self.model_validation_runner.verify_api_model(
                    immutable(candidate)
                ),
                # Clicking the existing API verification control is the
                # explicit user action that authorizes metadata-only probing.
                allow_cross_platform_metadata_probe=True,
            )
            resolved = resolution.get("status") == "RESOLVED"
            result = {
                "status": "AVAILABLE" if resolved else "INVALID",
                "reason": str(resolution.get("reason") or "KIMI_PLATFORM_NOT_RESOLVED"),
                "requested_model": requested_model,
                "returned_model": requested_model if resolved else None,
                "available_models": [requested_model] if resolved else [],
                "model_capability": infer_api_model_capability(
                    str(service.get("provider") or ""), requested_model
                ),
                "validation_endpoint_kind": "MODEL_CATALOG",
                "duration_ms": 0,
                "external_network_calls": int(
                    resolution.get("external_network_calls") or 0
                ),
                "provider_calls": int(
                    resolution.get("provider_metadata_calls") or 0
                ),
                "external_model_calls": int(
                    resolution.get("external_model_calls") or 0
                ),
                "external_process_launches": 0,
                "api_platform": resolution.get("platform") if resolved else None,
                "resolved_api_base_url": (
                    resolution.get("api_base_url") if resolved else None
                ),
                "platform_probe_count": len(resolution.get("attempts") or []),
                "key_text_inspected": False,
                "secret_hash_recorded": False,
            }
        else:
            result = dict(
                self.model_validation_runner.verify_api_model(immutable(service))
            )
        status = self._validation_status(result)
        self._record_validation_effects(result)
        if status == "NOT_RUN":
            return self._validation_receipt(
                kind="API",
                target_ref=config_id,
                result=result,
                envelope=current,
                connection_status=service["connection_status"],
                persistent_mutation=False,
            )
        if status not in {"AVAILABLE", "INVALID"}:
            raise ValueError("API_VALIDATION_RESULT_INVALID")
        current = self.store.load(recover_corruption=False)
        settings = deepcopy(current["settings"])
        target = next((row for row in settings["model_services"] if row["config_id"] == config_id), None)
        if target is None or api_identity(settings, target) != identity:
            raise ValueError('API_VERIFICATION_TARGET_CHANGED')
        target["connection_status"] = status
        resolved_api_base_url = result.get("resolved_api_base_url")
        if status == "AVAILABLE" and isinstance(resolved_api_base_url, str):
            target["api_protocol"] = "OPENAI_COMPATIBLE"
            target["api_base_url"] = resolved_api_base_url
        saved = self.store.save(settings, expected_revision=current["revision"])
        return self._validation_receipt(
            kind="API",
            target_ref=config_id,
            result=result,
            envelope=saved,
            connection_status=status,
            persistent_mutation=True,
        )

    def _cli_target_hash(self, service, model):
        return cli_identity(self.store.load(recover_corruption=False)['settings'], service, model)

    def start_cli_verification(self, *, config_id, profile_ref, idempotency_key):
        # Resolve once and freeze the requested target; never silently switch models.
        current = self.store.load(recover_corruption=False)
        service = next(r for r in current['settings']['cli_services'] if r['config_id'] == config_id)
        model = next(r for r in service['models'] if r['profile_ref'] == profile_ref)
        return self.cli_verification_jobs.start(params={
            'config_id': config_id, 'profile_ref': profile_ref,
            'target_sha256': self._cli_target_hash(service, model),
        }, idempotency_key=idempotency_key)

    def _prepare_cli_verification(self, *, config_id, profile_ref, target_sha256=None):
        current=self.store.load(recover_corruption=False)
        settings=deepcopy(current['settings'])
        service=next(row for row in settings['cli_services'] if row['config_id']==config_id)
        model=next(row for row in service['models'] if row['profile_ref']==profile_ref)
        if target_sha256 is not None and self._cli_target_hash(service,model)!=target_sha256:
            raise ValueError('CLI_VERIFICATION_TARGET_CHANGED')
        if model['connection_status']=='UNVERIFIED':return current
        model['connection_status']='UNVERIFIED'
        service['connection_status']='AVAILABLE' if any(row['connection_status']=='AVAILABLE' for row in service['models']) else 'UNVERIFIED'
        return self.store.save(settings,expected_revision=current['revision'])

    def cli_verification_status(self, *, job_id=None, profile_ref=None, idempotency_key=None):
        return self.cli_verification_jobs.status(job_id=job_id, profile_ref=profile_ref, idempotency_key=idempotency_key)

    def cancel_cli_verification(self, *, job_id):
        return self.cli_verification_jobs.cancel(job_id=job_id)

    def verify_cli_model(self, *, config_id: str, profile_ref: str, target_sha256=None) -> dict[str, Any]:
        current = self.store.load(recover_corruption=False)
        cli_service = next(
            (row for row in current["settings"]["cli_services"] if row["config_id"] == config_id),
            None,
        )
        if cli_service is None:
            raise ValueError("CLI_SERVICE_NOT_FOUND")
        model = next((row for row in cli_service["models"] if row["profile_ref"] == profile_ref), None)
        if model is None:
            raise ValueError("CLI_MODEL_NOT_FOUND")
        identity = self._cli_target_hash(cli_service, model)
        if target_sha256 is not None and identity != target_sha256:
            raise ValueError('CLI_VERIFICATION_TARGET_CHANGED')
        # A new verification cannot keep displaying an old successful result.
        current=self._prepare_cli_verification(config_id=config_id,profile_ref=profile_ref,target_sha256=identity)
        cli_service=next(row for row in current['settings']['cli_services'] if row['config_id']==config_id)
        model=next(row for row in cli_service['models'] if row['profile_ref']==profile_ref)
        result = dict(
            self.model_validation_runner.verify_cli_model(
                immutable(cli_service), immutable(model)
            )
        )
        status = self._validation_status(result)
        self._record_validation_effects(result)
        if status == "NOT_RUN":
            return self._validation_receipt(
                kind="CLI",
                target_ref=profile_ref,
                result=result,
                envelope=current,
                connection_status=model["connection_status"],
                persistent_mutation=False,
            )
        if status not in {"AVAILABLE", "INVALID"}:
            raise ValueError("CLI_VALIDATION_RESULT_INVALID")
        # Do not overwrite unrelated edits made while a CLI was waiting.
        current = self.store.load(recover_corruption=False)
        settings = deepcopy(current["settings"])
        target_service = next(row for row in settings["cli_services"] if row["config_id"] == config_id)
        target_model = next(row for row in target_service["models"] if row["profile_ref"] == profile_ref)
        if self._cli_target_hash(target_service, target_model) != identity:
            raise ValueError('CLI_VERIFICATION_TARGET_CHANGED')
        resolved_executable = result.get("resolved_executable")
        if resolved_executable is not None:
            if (
                not isinstance(resolved_executable, str)
                or not resolved_executable
                or len(resolved_executable) > 260
                or not Path(resolved_executable).is_absolute()
                or not Path(resolved_executable).is_file()
            ):
                raise ValueError("CLI_RESOLVED_EXECUTABLE_INVALID")
            target_service["executable"] = resolved_executable
        target_model["connection_status"] = status
        target_service["connection_status"] = (
            "AVAILABLE"
            if any(row["connection_status"] == "AVAILABLE" for row in target_service["models"])
            else "INVALID"
        )
        saved = self.store.save(settings, expected_revision=current["revision"])
        return self._validation_receipt(
            kind="CLI",
            target_ref=profile_ref,
            result=result,
            envelope=saved,
            connection_status=status,
            persistent_mutation=True,
        )

    def research_embedding_freeze(self, *, run_id, profile_ref):
        from .research_chat_execution import ResearchEmbeddingExecution
        return ResearchEmbeddingExecution(self).freeze(run_id=run_id,period='embedding',profile_ref=profile_ref)

    def research_embedding_execute(self, *, snapshot_id, inputs):
        from .research_chat_execution import ResearchEmbeddingExecution
        return ResearchEmbeddingExecution(self).execute(snapshot_id=snapshot_id,inputs=inputs)

    def research_chat_freeze(self, *, run_id, profile_ref):
        return self.research_chat_execution.freeze(run_id=run_id,profile_ref=profile_ref)

    def research_image_execute(self, *, snapshot_id, image_base64, mime_type):
        return self.research_chat_execution.execute_image(snapshot_id=snapshot_id,image_base64=image_base64,mime_type=mime_type)

    def research_chat_execute(self, *, snapshot_id, prompt, response_schema):
        return self.research_chat_execution.execute(snapshot_id=snapshot_id,prompt=prompt,response_schema=response_schema)

    def report_profile_freeze(self, *, run_id, period, profile_ref):
        return self.report_execution.freeze(run_id=run_id, period=period, profile_ref=profile_ref)

    def report_profile_execute(self, *, snapshot_id, prompt, response_schema):
        return self.report_execution.execute(snapshot_id=snapshot_id, prompt=prompt, response_schema=response_schema)

    def execute_structured_chat(
        self,
        *,
        profile_ref: str,
        prompt: str,
        response_schema: Mapping[str, Any],
        purpose: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(profile_ref, str)
            or not profile_ref
            or not isinstance(prompt, str)
            or not prompt.strip()
            or not isinstance(response_schema, Mapping)
            or purpose != "conversation_refinement"
        ):
            raise ValueError("STRUCTURED_CHAT_REQUEST_INVALID")
        current = self.store.load(recover_corruption=False)
        settings = current["settings"]
        profile_kind = ""
        service: Mapping[str, Any] | None = next(
            (
                row
                for row in settings["model_services"]
                if row["config_id"] == profile_ref
            ),
            None,
        )
        model: Mapping[str, Any] | None = service
        if service is not None:
            profile_kind = "API"
            if service.get("connection_status") != "AVAILABLE":
                raise ValueError("STRUCTURED_CHAT_PROFILE_NOT_AVAILABLE")
            if (
                infer_api_model_capability(
                    str(service.get("provider") or ""),
                    str(service.get("model_name") or ""),
                )
                != "CHAT"
            ):
                raise ValueError("STRUCTURED_CHAT_PROFILE_CAPABILITY_INVALID")
        else:
            for candidate in settings["cli_services"]:
                if candidate.get("enabled") is not True:
                    continue
                matched = next(
                    (
                        row
                        for row in candidate.get("models", [])
                        if row.get("profile_ref") == profile_ref
                    ),
                    None,
                )
                if matched is not None:
                    service = candidate
                    model = matched
                    profile_kind = "CLI"
                    break
            if service is None and profile_ref.startswith('local:'):
                service = self.local_profile_resolver(profile_ref) if callable(self.local_profile_resolver) else None
                model = service
                profile_kind = 'LOCAL'
                if service and (service.get('execution_eligible') is not True or service.get('exact_identity_available') is not True or service.get('capability') != 'CHAT'):
                    raise ValueError('STRUCTURED_CHAT_PROFILE_NOT_AVAILABLE')
            if service is None or model is None:
                raise ValueError("STRUCTURED_CHAT_PROFILE_NOT_FOUND")
            if model.get("connection_status") != "AVAILABLE":
                raise ValueError("STRUCTURED_CHAT_PROFILE_NOT_AVAILABLE")
        runner = getattr(self.model_validation_runner, "execute_structured_chat", None)
        if not callable(runner):
            raise ValueError("STRUCTURED_CHAT_RUNNER_NOT_CONFIGURED")
        from .call_ledger import call_scope
        with call_scope(job_id=task_id, profile_ref=profile_ref, task_type=purpose):
            result = dict(
                runner(
                    profile_kind=profile_kind,
                    service=immutable(service),
                    model=immutable(model),
                    prompt=prompt,
                    response_schema=immutable(response_schema),
                    purpose=purpose,
                )
            )
        self._record_validation_effects(result)
        requested_model = str(model.get("model_name") or "")
        if (
            result.get("schema_version")
            != "SettingsStructuredChatRunnerResult-v1"
            or result.get("status") != "PASS"
            or result.get("requested_model") != requested_model
            or not matches_result_model(result, requested_model)
            or not isinstance(result.get("response"), Mapping)
            or result.get("external_model_calls") != 1
            or (profile_kind == "LOCAL" and (result.get("external_network_calls") != 0 or result.get("provider_calls") != 0))
            or result.get("external_network_calls") not in {0, 1}
            or result.get("provider_calls") not in {0, 1}
            or not isinstance(result.get("execution_receipt"), Mapping)
            or result["execution_receipt"].get("status") != "PASS"
        ):
            reason = result.get("reason")
            raise ValueError(
                reason if isinstance(reason, str) and reason else "STRUCTURED_CHAT_EXECUTION_FAILED"
            )
        receipt = {
            **dict(result["execution_receipt"]),
            "profile_ref": profile_ref,
            "settings_revision": current["revision"],
            "settings_sha256": current["settings_sha256"],
        }
        payload = {
            "schema_version": "SettingsStructuredChatExecutionResult-v1",
            "status": "PASS",
            "profile_ref": profile_ref,
            "requested_model": requested_model,
            "returned_model": result["returned_model"],
            "response": result["response"],
            "external_model_calls": 0 if profile_kind == "LOCAL" else result["external_model_calls"],
            "external_network_calls": result["external_network_calls"],
            "provider_calls": result["provider_calls"],
            "execution_receipt": receipt,
        }
        scan_sensitive(payload)
        return immutable(payload)

    def start_workflow_exam(self, *, node_id: str, idempotency_key: str,
                            expected_plan_sha256: str, authorization=None,
                            exam_sample_slot=None, exam_tier="FULL", force_exam=True):
        params = dict(node_id=node_id, authorization=authorization,
                      exam_sample_slot=exam_sample_slot, exam_tier=exam_tier, force_exam=force_exam)
        plan = self.test_workflow_node(**params, plan_only=True)
        if plan.get("status") != "READY" or plan.get("plan_sha256") != expected_plan_sha256:
            raise ValueError("WORKFLOW_EXAM_PLAN_CHANGED_OR_UNAVAILABLE")
        return self.exam_jobs.start(params=params, plan=plan, idempotency_key=idempotency_key)

    def workflow_exam_status(self, *, job_id=None, node_id=None):
        return self.exam_jobs.status(job_id=job_id, node_id=node_id)

    def cancel_workflow_exam(self, *, job_id):
        return self.exam_jobs.cancel(job_id=job_id)

    def test_workflow_node(
        self,
        *,
        node_id: str,
        authorization: Mapping[str, Any] | None = None,
        plan_only: bool = False,
        exam_sample_slot: int | None = None,
        exam_tier: str | None = None,
        force_exam: bool = False,
    ) -> dict[str, Any]:
        if type(force_exam) is not bool:
            raise ValueError("WORKFLOW_NODE_MODEL_TEST_FORCE_INVALID")
        if not isinstance(plan_only, bool):
            raise ValueError("WORKFLOW_NODE_MODEL_TEST_PLAN_ONLY_INVALID")
        if authorization is not None and not isinstance(authorization, Mapping):
            raise ValueError("WORKFLOW_NODE_MODEL_TEST_AUTHORIZATION_INVALID")
        if exam_tier is not None:
            from .exam_tiers import validate_tier
            validate_tier(exam_tier)
        if exam_sample_slot is not None and (
            isinstance(exam_sample_slot, bool)
            or not isinstance(exam_sample_slot, int)
            or exam_sample_slot not in {1, 2, 3}
        ):
            raise ValueError("WORKFLOW_NODE_MODEL_TEST_SAMPLE_SLOT_INVALID")
        current = self.store.load(recover_corruption=False)
        node = next(
            (row for row in current["settings"]["workflow"]["nodes"] if row["node_id"] == node_id),
            None,
        )
        if node is None:
            raise ValueError("WORKFLOW_NODE_NOT_FOUND")
        if node_id in {"card_admission", "human_judgment"}:
            raise ValueError("WORKFLOW_NODE_MODEL_TEST_NOT_APPLICABLE")
        profile_ref = node["profile_ref"]
        if profile_ref is None or profile_ref == "AUTO_HETEROGENEOUS":
            raise ValueError("WORKFLOW_NODE_MODEL_NOT_CONFIGURED")
        target = self._workflow_model_target(current["settings"], profile_ref)
        low_cost_service = next(
            (
                service
                for service in current["settings"]["model_services"]
                if service.get("model_name") == "deepseek-v4-flash"
                and service.get("thinking") is True
                and service.get("tier") == "flash"
                and service.get("connection_status") == "AVAILABLE"
            ),
            None,
        )
        context: dict[str, Any] = {
            "settings_revision": current["revision"],
            "settings_sha256": current["settings_sha256"],
            "low_cost_target": (
                {"kind": "API", **low_cost_service}
                if low_cost_service is not None
                else None
            ),
        }
        if exam_sample_slot is not None:
            context["workflow_exam_sample_slot"] = exam_sample_slot
        if exam_tier is not None:
            context["workflow_exam_tier"] = exam_tier
        if force_exam:
            context["workflow_exam_force_live"] = True
        if plan_only:
            planner = getattr(
                self.model_validation_runner, "plan_workflow_node_exam", None
            )
            if not callable(planner):
                plan: dict[str, Any] = {
                    "schema_version": "WorkflowModelExamPlan-v2",
                    "status": "NOT_AVAILABLE",
                    "mode": "NONE",
                    "reason": "WORKFLOW_NODE_EXAM_PLANNER_NOT_CONFIGURED",
                    "external_network_calls": 0,
                    "provider_calls": 0,
                    "external_model_calls": 0,
                }
            else:
                plan = dict(
                    planner(
                        immutable(node),
                        immutable(target),
                        immutable(context),
                    )
                )
            plan["target_ref"] = node_id
            plan["settings_revision"] = current["revision"]
            plan["settings_sha256"] = current["settings_sha256"]
            scan_sensitive(plan)
            return immutable(plan)
        test_method = self.model_validation_runner.test_workflow_node
        parameters = signature(test_method).parameters
        if "context" in parameters and "authorization" in parameters:
            result = dict(
                test_method(
                    immutable(node),
                    immutable(target),
                    context=immutable(context),
                    authorization=(
                        immutable(authorization)
                        if authorization is not None
                        else None
                    ),
                )
            )
        else:
            result = dict(test_method(immutable(node), immutable(target)))
        engine_status = result.get("status")
        engine_operational_verdict = result.get("operational_eligibility_verdict")
        diagnostic = result.get("current_panel_score_diagnostic")
        completed_panel = (
            result.get("status") in {"NOT_RUN", "NOT_ASSESSED"}
            and result.get("reason") == "ANALYSIS_PRIMARY_PANEL_COMPLETED_COHORT_PENDING"
            and result.get("execution_outcome") == "COMPLETED"
            and result.get("panel_evidence_only") is True
            and isinstance(diagnostic, (int, float))
            and not isinstance(diagnostic, bool)
            and 0 <= diagnostic <= 100
        )
        if completed_panel and result.get("status") == "NOT_ASSESSED":
            # The examiner completed inference but has no stable cohort score.
            # Adapt that domain status before the generic validation contract;
            # the source exam and its qualification verdict remain unchanged.
            result["assessment_status"] = "NOT_ASSESSED"
            result["status"] = "NOT_RUN"
        if completed_panel and engine_operational_verdict == "PENDING_THREE_PANEL_COHORT":
            result["operational_eligibility_verdict"] = "NOT_ASSESSED"
        status = self._validation_status(result)
        self._record_validation_effects(result)
        if status == "NOT_RUN":
            receipt = self._validation_receipt(
                kind="WORKFLOW_NODE",
                target_ref=node_id,
                result=result,
                envelope=current,
                score=node["test_score"],
                persistent_mutation=False,
            )
            if isinstance(result.get("exam_tier"), Mapping):
                receipt.update(exam_tier=immutable(result["exam_tier"]), attempt_score=None,
                               existing_score_preserved=True,
                               tier_result_is_provisional=result["exam_tier"].get("tier") != "FULL")
            diagnostic = result.get('current_panel_score_diagnostic')
            if (result.get('reason') == 'ANALYSIS_PRIMARY_PANEL_COMPLETED_COHORT_PENDING'
                    and result.get('execution_outcome') == 'COMPLETED'
                    and result.get('panel_evidence_only') is True
                    and isinstance(diagnostic, (int,float)) and not isinstance(diagnostic,bool) and 0 <= diagnostic <= 100):
                receipt.update(execution_completed=True, assessment_status='COHORT_PENDING',
                    diagnostic_score=float(diagnostic), cohort_panel_count=result.get('cohort_panel_count',1),
                    existing_score_preserved=True, stable_score_cache_eligible=False,
                    exam_run_id=result.get('run_id'), source_exam_status=engine_status,
                    source_operational_eligibility_verdict=engine_operational_verdict)
            return receipt
        if status not in {"PASS", "FAIL"}:
            raise ValueError("WORKFLOW_NODE_TEST_RESULT_INVALID")
        score = result.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError("WORKFLOW_NODE_TEST_SCORE_INVALID")
        tier_data = result.get("exam_tier")
        if isinstance(tier_data, Mapping) and tier_data.get("tier") != "FULL":
            # Subset observations must not overwrite a full score on the mapping.
            # The executor's immutable run remains the evidence source.
            receipt = self._validation_receipt(
                kind="WORKFLOW_NODE", target_ref=node_id, result=result, envelope=current,
                score=score, persistent_mutation=False,
            )
            receipt.update(exam_tier=immutable(tier_data), tier_result_is_provisional=True,
                           repair_adjusted_exam_score=result.get("repair_adjusted_exam_score"))
            return receipt
        latest = self.store.load(recover_corruption=False)
        mapping_unchanged = latest["revision"] == current["revision"]
        saved = latest
        if mapping_unchanged:
            settings = deepcopy(current["settings"])
            target_node = next(row for row in settings["workflow"]["nodes"] if row["node_id"] == node_id)
            target_node["test_score"] = score
            try:
                saved = self.store.save(settings, expected_revision=current["revision"])
            except ValueError:
                # A settings edit does not invalidate a completed exam or its cache.
                if self.store.load(recover_corruption=False)["revision"] == current["revision"]:
                    raise
                mapping_unchanged = False
                saved = self.store.load(recover_corruption=False)
        receipt = self._validation_receipt(
            kind="WORKFLOW_NODE",
            target_ref=node_id,
            result=result,
            envelope=saved,
            score=score,
            persistent_mutation=mapping_unchanged,
        )
        receipt["saved_mapping_changed"] = not mapping_unchanged
        if isinstance(tier_data, Mapping):
            receipt["exam_tier"] = immutable(tier_data)
            receipt["repair_adjusted_exam_score"] = result.get("repair_adjusted_exam_score")
        return receipt

    def diagnostics(self) -> dict[str, Any]:
        current = self.store.load()
        directory = self.directory_policy.inspect(current["settings"])
        capabilities = self.capabilities.snapshot()
        blocked = [
            row["capability_id"]
            for row in capabilities["capabilities"]
            if row["blocked"]
        ]
        return {
            "schema_version": "SettingsDiagnosticReport-v1",
            "settings_revision": current["revision"],
            "settings_sha256": current["settings_sha256"],
            "directory_preflight": directory,
            "blocked_capabilities": blocked,
            "credential_reference_count": len(
                current["settings"]["credential_references"]
            ),
            "credential_value_reads": self.credentials.metrics[
                "credential_value_reads"
            ],
            "network_requests": 0,
            "provider_requests": 0,
            "model_requests": 0,
            "mutations": 0,
            "status": "PASS"
            if directory["status"] == "PASS"
            and self.credentials.metrics["credential_value_reads"] == 0
            else "BLOCKED",
        }

    def support_bundle(self) -> dict[str, Any]:
        state = self.get_state()
        payload = {
            "schema_version": "SettingsSupportBundle-v1",
            "settings": {
                "revision": state["revision"],
                "settings_sha256": state["settings_sha256"],
                "mode": state["settings"]["mode"],
                "credential_references": state["settings"][
                    "credential_references"
                ],
                "model_services": state["settings"]["model_services"],
                "cli_services": state["settings"]["cli_services"],
                "workflow": state["settings"]["workflow"],
                "preferences": state["settings"]["preferences"],
            },
            "capabilities": state["capabilities"],
            "diagnostics": self.diagnostics(),
            "credential_values_included": False,
            "raw_payloads_included": False,
        }
        scan_sensitive(payload)
        payload["bundle_sha256"] = canonical_sha256(payload)
        return immutable(payload)

    def _mutation_receipt(
        self,
        action: str,
        envelope: Mapping[str, Any],
        *,
        restart_required: bool,
        credentials_preserved: bool,
        embedding_binding_changed: bool = False,
    ) -> dict[str, Any]:
        return {
            "schema_version": "SettingsMutationReceipt-v1",
            "action": action,
            "revision": envelope["revision"],
            "template_id": envelope["template_id"],
            "settings_sha256": envelope["settings_sha256"],
            "restart_required": restart_required,
            "credentials_preserved": credentials_preserved,
            "credential_values_in_receipt": False,
            "route_or_profile_enablements": 0,
            "embedding_binding_changed": embedding_binding_changed,
            "reembedding_required": embedding_binding_changed,
        }

    def effect_metrics(self) -> dict[str, Any]:
        return {
            **self.metrics,
            **self.credentials.metrics,
            "cost_cny": 0,
            "cost_usd": 0,
        }


__all__ = ["SettingsController"]

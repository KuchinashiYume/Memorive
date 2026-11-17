"""The settings page's desktop actions: SettingsPageMixin.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping
from memorive.platform.clock import utc_now as _utc_now
from memorive.platform.windows_paths import windows_io_path


class SettingsPageMixin:
    """The settings page's desktop actions: the console connection, directories, the external
    library test, settings import and export files, the model dashboard, call readiness,
    the support bundle, logs, the temporary cache and the network test."""

    def get_console_connection(self) -> dict[str, Any]:
        """Read the actual authenticated console bridge, never telemetry flags."""
        bridge = getattr(self, '_console_bridge', None)
        connected = bool(bridge is not None and getattr(bridge, '_server', None) is not None)
        allowed = self._read_console_permission()
        enabled = connected and allowed and bridge._access_enabled and not bridge._closing
        expressions = bridge._expressions.state() if connected else {}
        return {'schema_version':'DesktopConsoleConnection-v1',
                'status':('CONNECTED' if enabled else 'PAUSED') if connected else 'NOT_CONNECTED',
                'isolated_session':connected, 'telemetry_upload_enabled':False,
                'automatic_execution_enabled':False,'access_enabled':bool(allowed),
                'can_toggle':not connected or not bridge._closing,
                'scope':'CURRENT_PROFILE',
                'interfaces':{'business':bool(enabled),'expressions':bool(enabled and expressions.get('renderer_ready'))},
                'expression_event_count':33 if expressions.get('renderer_ready') else 0,
                'active_expression_state':(expressions.get('active') or {}).get('state')}

    def _read_console_permission(self) -> bool:
        from console_permission import read_permission
        return read_permission(self._state_dir)

    def set_console_connection(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request,dict) or set(request)!={'enabled'} or type(request['enabled']) is not bool:
            raise ValueError('CONSOLE_ACCESS_BOOLEAN_REQUIRED')
        bridge=getattr(self,'_console_bridge',None)
        if bridge is not None and bridge._closing:
            raise ValueError('CONSOLE_SESSION_CLOSING')
        from console_permission import write_permission
        with self._lock:
            write_permission(self._state_dir,request['enabled'])
        if bridge is not None:
            bridge.set_access(request['enabled'])
        return self.get_console_connection()

    def pick_settings_directory(self, role: str) -> dict[str, Any]:
        if role not in {"workspace_root", "artifact_root", "external_library"}:
            raise ValueError("DIRECTORY_ROLE_INVALID")
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.FOLDER,
            directory=str(self._effective_directory(role)),
            allow_multiple=False,
        )
        if not selected:
            return {
                "schema_version": "DesktopAssistantDirectoryPickerReceipt-v1",
                "role": role,
                "path": None,
                "status": "CANCELLED",
            }
        selected_path = Path(selected[0]).resolve(strict=False)
        selected_io = windows_io_path(selected_path)
        if not selected_io.is_dir() or selected_io.is_symlink() or self._is_reparse_point(selected_io):
            raise ValueError("DIRECTORY_SELECTION_NOT_REGULAR_DIRECTORY")
        path = str(selected_path)
        receipt = self._service.call("settings.register_directory", {"role": role, "path": path})
        return {
            "schema_version": "DesktopAssistantDirectoryPickerReceipt-v1",
            "role": role,
            "path": receipt["path"],
            "status": "SELECTED",
        }

    def test_external_library(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {
            "provider_kind", "display_name", "root"
        }:
            raise ValueError("EXTERNAL_LIBRARY_TEST_FIELDS_INVALID")
        provider_kind = str(request["provider_kind"]).upper()
        if provider_kind not in {"OBSIDIAN", "LOGSEQ", "ZOTERO", "CUSTOM"}:
            raise ValueError("EXTERNAL_LIBRARY_PROVIDER_INVALID")
        display_name = request["display_name"]
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 80
        ):
            raise ValueError("EXTERNAL_LIBRARY_DISPLAY_NAME_REQUIRED")
        root_value = request["root"]
        if not isinstance(root_value, str) or not root_value:
            raise ValueError("EXTERNAL_LIBRARY_ROOT_REQUIRED")
        raw_root = Path(root_value)
        raw_root_io = windows_io_path(raw_root)
        if (
            not raw_root.is_absolute()
            or not raw_root_io.exists()
            or not raw_root_io.is_dir()
            or raw_root_io.is_symlink()
            or self._is_reparse_point(raw_root_io)
        ):
            raise ValueError("EXTERNAL_LIBRARY_ROOT_INVALID")
        root = raw_root.resolve(strict=False)
        marker_name: str | None = None
        marker_kind: str | None = None
        if provider_kind == "OBSIDIAN":
            marker_name, marker_kind = ".obsidian", "directory"
        elif provider_kind == "LOGSEQ":
            marker_name, marker_kind = "logseq", "directory"
        elif provider_kind == "ZOTERO":
            marker_name, marker_kind = "zotero.sqlite", "file"
        if marker_name is not None:
            marker = root / marker_name
            marker_io = windows_io_path(marker)
            marker_valid = (
                marker_io.is_dir() if marker_kind == "directory" else marker_io.is_file()
            )
            if (
                not marker_valid
                or marker_io.is_symlink()
                or self._is_reparse_point(marker_io)
            ):
                raise ValueError(f"{provider_kind}_VAULT_MARKER_MISSING")
        registration = self._service.call(
            "settings.register_directory",
            {"role": "external_library", "path": str(root)},
        )
        return {
            "schema_version": "DesktopExternalLibraryTestReceipt-v1",
            "provider_kind": provider_kind,
            "display_name": display_name.strip(),
            "root": registration["path"],
            "marker": marker_name,
            "marker_kind": marker_kind,
            "directory_access_checked": True,
            "persistent_mutation": False,
            "external_process_launches": 0,
            "external_network_calls": 0,
            "status": "PASS",
        }

    def record_settings_save_receipt(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {"overall_status", "stages"}:
            raise ValueError("SETTINGS_SAVE_RECEIPT_FIELDS_INVALID")
        if request["overall_status"] not in {"PASS", "WARNING", "FAILED"}:
            raise ValueError("SETTINGS_SAVE_RECEIPT_STATUS_INVALID")
        raw_stages = request.get("stages")
        allowed_stages = ("settings", "refinement", "assistant", "refresh")
        if not isinstance(raw_stages, Mapping) or set(raw_stages) != set(allowed_stages):
            raise ValueError("SETTINGS_SAVE_RECEIPT_STAGES_INVALID")
        stages: dict[str, dict[str, Any]] = {}
        for name in allowed_stages:
            raw = raw_stages[name]
            if not isinstance(raw, Mapping):
                raise ValueError("SETTINGS_SAVE_RECEIPT_STAGE_INVALID")
            status = str(raw.get("status") or "")
            if status not in {"PASS", "WARNING", "FAILED", "SKIPPED"}:
                raise ValueError("SETTINGS_SAVE_RECEIPT_STAGE_STATUS_INVALID")
            error_code = re.sub(
                r"[^A-Z0-9_:-]", "_", str(raw.get("error_code") or "").upper()
            )[:120]
            revision = raw.get("revision")
            if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
                raise ValueError("SETTINGS_SAVE_RECEIPT_REVISION_INVALID")
            stages[name] = {
                "status": status,
                "error_code": error_code or None,
                "revision": revision,
            }
        with self._lock:
            revision = 1
            if self._settings_save_receipt_path.is_file():
                try:
                    previous = json.loads(
                        self._settings_save_receipt_path.read_text(encoding="utf-8")
                    )
                    revision = int(previous.get("revision", 0)) + 1
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    revision = 1
            receipt = {
                "schema_version": "DesktopSettingsSaveAggregateReceipt-v1",
                "revision": revision,
                "saved_at": _utc_now(),
                "overall_status": request["overall_status"],
                "stages": stages,
                "credential_values_included": False,
                "raw_private_content_included": False,
                "status": "PASS",
            }
            self._write_json_file(self._settings_save_receipt_path, receipt)
            return deepcopy(receipt)

    def _save_json_dialog(self, payload: dict[str, Any], filename: str) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.SAVE,
            directory=str(self._effective_directory("artifact_root")),
            save_filename=filename,
            file_types=("JSON files (*.json)",),
        )
        if not selected:
            return {"path": None, "bytes": 0, "status": "CANCELLED"}
        selected_path = selected if isinstance(selected, str) else selected[0]
        destination = Path(selected_path).resolve(strict=False)
        if destination.suffix.lower() != ".json":
            destination = destination.with_suffix(".json")
        if not windows_io_path(destination.parent).is_dir():
            raise ValueError("JSON_EXPORT_PARENT_MISSING")
        self._write_json_file(destination, payload)
        destination_io = windows_io_path(destination)
        return {
            "path": str(destination),
            "bytes": destination_io.stat().st_size,
            "sha256": hashlib.sha256(destination_io.read_bytes()).hexdigest().upper(),
            "status": "EXPORTED",
        }

    def export_cli_template_file(self, service: dict[str, Any]) -> dict[str, Any]:
        prepared = self._service.call("settings.cli_template_prepare", {"action": "export", "payload": service})
        receipt = self._save_json_dialog(prepared["payload"], "Memorive-CLI-template.json")
        return {"schema_version": "DesktopCLITemplateExportReceipt-v1", **receipt}

    def export_redacted_settings_file(self) -> dict[str, Any]:
        payload = self._service.call("settings.export_redacted", {})
        receipt = self._save_json_dialog(payload, "Memorive-settings-redacted.json")
        return {"schema_version": "DesktopAssistantSettingsFileExportReceipt-v1", **receipt}

    def import_redacted_settings_file(self) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(self._effective_directory("workspace_root")),
            allow_multiple=False,
            file_types=("JSON files (*.json)",),
        )
        if not selected:
            return {
                "schema_version": "DesktopAssistantSettingsFileImportProjection-v1",
                "payload": None,
                "status": "CANCELLED",
            }
        source = Path(selected[0]).resolve(strict=False)
        source_io = windows_io_path(source)
        if not source_io.is_file() or source_io.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("SETTINGS_IMPORT_FILE_INVALID")
        payload = json.loads(source_io.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("SETTINGS_IMPORT_OBJECT_REQUIRED")
        return {
            "schema_version": "DesktopAssistantSettingsFileImportProjection-v1",
            "payload": payload,
            "source_sha256": hashlib.sha256(source_io.read_bytes()).hexdigest().upper(),
            "status": "SELECTED",
        }

    def model_dashboard(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only bottom panel; no model execution or metadata refresh."""
        request = request or {}
        if not isinstance(request, dict) or set(request) - {"view"} or request.get("view") not in {"ranking", "billing"}:
            return {"status": "ERROR", "reason": "DASHBOARD_REQUEST_INVALID"}
        try:
            module = importlib.import_module("memorive.desktop.settings.model_dashboard")
            exam_root = self._profile_path("MODEL_VALIDATION")
            if request["view"] == "billing":
                data = module.billing_projection(exam_root, self._profile_path("Core_JOBS"))
            else:
                projection = self._service.call("settings.get_state", {})
                history_module = importlib.import_module("memorive.exams.score_history")
                history = history_module.ExamScoreHistory(exam_root / "workflow_exam_scores").rows()
                external_root = self._profile_path("SETTINGS") / "external_data"
                external = {"rows": [], "unavailable_sources": []}
                if external_root.is_dir():
                    try:
                        sources_module = importlib.import_module("memorive.desktop.settings.external_sources")
                        external = sources_module.ExternalDataSources(external_root, environment={}).public_catalog()
                    except (OSError, ValueError, TypeError, KeyError):
                        # A corrupt external cache must not hide validated exams.
                        external["unavailable_sources"] = ["EXTERNAL_CACHE_UNAVAILABLE"]
                data = module.ranking_projection(projection.get("workflow_exam_score_catalog") or {}, history, external["rows"])
                data["unavailable_sources"] = external["unavailable_sources"]
            return {"status": "PASS", "data": data}
        except Exception:
            # Provider strings, paths and raw records must not become UI errors.
            return {"status": "ERROR", "reason": "DASHBOARD_READ_FAILED"}

    def api_call_readiness(self) -> dict[str, Any]:
        """Project pre-send readiness without resolving a credential or sending a request."""

        projection = self._service.call("settings.get_state", {})
        settings = projection.get("settings", {}) if isinstance(projection, Mapping) else {}
        raw_profiles = projection.get("api_profiles", []) if isinstance(projection, Mapping) else []
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raw_profiles = settings.get("model_services", []) if isinstance(settings, Mapping) else []
        raw_references = settings.get("credential_references", []) if isinstance(settings, Mapping) else []
        reference_status = {
            str(value.get("credential_ref") or "").strip(): str(value.get("status") or "UNKNOWN")
            for value in raw_references
            if isinstance(value, Mapping) and str(value.get("credential_ref") or "").strip()
        }
        profiles: list[dict[str, Any]] = []
        for value in raw_profiles if isinstance(raw_profiles, list) else []:
            if not isinstance(value, Mapping):
                continue
            profile_ref = str(value.get("profile_ref") or value.get("config_id") or "").strip()
            model_name = str(value.get("display_name") or value.get("model_name") or "").strip()
            provider = str(value.get("provider") or value.get("provider_name") or "").strip()
            credential_ref = str(value.get("credential_ref") or "").strip()
            credential_status = reference_status.get(credential_ref, "UNKNOWN")
            if not profile_ref:
                continue
            blockers = []
            if not model_name:
                blockers.append("MODEL_NAME_MISSING")
            if credential_ref and credential_status not in {"STORED_UNVERIFIED", "STORED_VERIFIED"}:
                blockers.append("CREDENTIAL_REFERENCE_NOT_STORED")
            profiles.append(
                {
                    "profile_ref": profile_ref,
                    "provider": provider,
                    "model_name": model_name,
                    "tier": str(value.get("tier") or "").strip(),
                    "credential_reference_status": credential_status,
                    "connection_status": str(value.get("connection_status") or "UNVERIFIED"),
                    "configuration_ready": not blockers,
                    "blockers": blockers,
                }
            )
        ready = [row for row in profiles if row["configuration_ready"]]
        return {
            "schema_version": "DesktopApiCallReadinessProjection-v1",
            "configured_profile_count": len(profiles),
            "configuration_ready_count": len(ready),
            "profiles": profiles,
            "required_before_send": [
                "MODEL_GATEWAY route/profile binding",
                "requested and returned model evidence",
                "region and egress evidence",
                "token, cost and latency receipt",
                "low-cost rehearsal when required",
            ],
            "request_sent": False,
            "credential_value_read": False,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "status": "READY_FOR_AUTHORIZED_PREFLIGHT" if ready else "BLOCKED_NO_CONFIGURED_MODEL",
        }

    def export_support_bundle_file(self) -> dict[str, Any]:
        payload = self._service.call("settings.support_bundle", {})
        receipt = self._save_json_dialog(payload, "Memorive-support-bundle.json")
        return {"schema_version": "DesktopAssistantSupportBundleFileReceipt-v1", **receipt}

    def open_settings_log_directory(self) -> dict[str, Any]:
        log_root = (self._state_dir / "service-runtime").resolve(strict=True)
        process = subprocess.Popen(["explorer.exe", str(log_root)], shell=False, close_fds=True)
        return {
            "schema_version": "DesktopAssistantOpenLogDirectoryReceipt-v1",
            "process_id": process.pid,
            "status": "OPENED",
        }

    def clear_temporary_cache(self) -> dict[str, Any]:
        cache_root = self._inbox_source_root.resolve(strict=False)
        cache_root_io = windows_io_path(cache_root)
        if not cache_root_io.is_dir():
            raise RuntimeError("CACHE_ROOT_UNAVAILABLE")
        removed = 0
        removed_bytes = 0
        for child_io in cache_root_io.iterdir():
            child = cache_root / child_io.name
            resolved = child.resolve(strict=False)
            if not resolved.is_relative_to(cache_root) or resolved == cache_root:
                raise RuntimeError("CACHE_TARGET_ESCAPE")
            if child_io.is_dir() and not child_io.is_symlink():
                removed_bytes += sum(path.stat().st_size for path in child_io.rglob("*") if path.is_file())
                shutil.rmtree(child_io)
                removed += 1
        return {
            "schema_version": "DesktopAssistantTemporaryCacheClearReceipt-v1",
            "removed_stage_directories": removed,
            "removed_bytes": removed_bytes,
            "formal_logs_deleted": 0,
            "controlled_inbox_files_deleted": 0,
            "status": "PASS",
        }

    def test_network_connection(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        accepted = dict(request or {})
        if set(accepted) - {"proxy_mode", "proxy_address", "timeout_seconds"}:
            raise ValueError("NETWORK_TEST_FIELDS_INVALID")
        preferences = self._current_preferences()
        proxy_mode = accepted.get("proxy_mode", preferences["proxy_mode"])
        proxy_address = accepted.get("proxy_address", preferences["proxy_address"])
        timeout_seconds = accepted.get("timeout_seconds", preferences["request_timeout_seconds"])
        self._network_test_count += 1
        try:
            receipt = self._network_runner(
                proxy_mode=proxy_mode,
                proxy_address=proxy_address,
                timeout_seconds=timeout_seconds,
            )
            return {
                **receipt,
                "test_sequence": self._network_test_count,
                "settings_revision_source": self._service.call("settings.get_state", {})["revision"],
            }
        except Exception as error:
            return {
                "schema_version": "DesktopNetworkDiagnosticReceipt-v1",
                "status": "FAIL",
                "reason_code": str(error)[:160] or type(error).__name__,
                "local_ip": None,
                "local_addresses": [],
                "egress_ip": None,
                "country_code": "",
                "colo": "",
                "proxy_mode": str(proxy_mode).upper(),
                "test_sequence": self._network_test_count,
                "external_network_calls": 1,
                "provider_calls": 0,
                "external_model_calls": 0,
                "credential_value_reads": 0,
            }

"""SessionsProductController: construction, the archive it uses and call(), which dispatches the
sessions.* methods; everything else comes from the mixins.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import queue
import re
import threading
from typing import Any, Mapping
from memorive.sessions.archive import SessionArchive
from memorive.sessions.controller.common import (
    SESSIONS_METHODS,
    SessionProductError,
    _VISIBLE_UI_PROFILES,
    _projection_state_lock,
    _text,
)
from memorive.sessions.controller.state import SessionStateMixin
from memorive.sessions.controller.local_cli import LocalCliSessionsMixin
from memorive.sessions.controller.browser import BrowserSessionsMixin
from memorive.sessions.controller.listing import SessionListingMixin
from memorive.sessions.controller.refinement_queue import RefinementQueueMixin
from memorive.sessions.controller.refinement_tasks import RefinementTasksMixin


class SessionsProductController(
    SessionStateMixin,
    LocalCliSessionsMixin,
    BrowserSessionsMixin,
    SessionListingMixin,
    RefinementQueueMixin,
    RefinementTasksMixin,
):
    adapter_kind = "analysis_sessions_local_cli_read_only_metadata_first"
    discovery_window_days = 30

    def __init__(
        self,
        service: Any,
        control_store: Any,
        contract_root: Path,
        fixture_root: Path,
        state_root: Path,
        *,
        source_roots: Mapping[str, Path] | None = None,
        visible_ui_reader: Any | None = None,
        legacy_deepseek_visible_ui_projection: bool = True,
        local_model_registry: Any | None = None,
        conversation_refinement_channel: Any | None = None,
        refinement_task_manager: Any | None = None,
        refinement_lifecycle_observer: Any | None = None,
        refinement_queue_observer: Any | None = None,
        refinement_result_observer: Any | None = None,
    ):
        self.service = service
        self.control_store = control_store
        self.contract_root = contract_root.resolve()
        self.fixture_root = fixture_root.resolve()
        self.state_root = state_root.resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        # Preserve the v1 synthetic projection for audit/replay.  The live UI
        # owns a separate metadata-only index and never migrates sample rows.
        self.state_path = self.state_root / "session_projection_v2.json"
        # pywebview may create more than one controller for the same profile
        # and dispatch bootstrap, refresh and filter changes from separate
        # threads.  Every controller targeting this projection therefore
        # shares one load/mutate/save critical section.  RLock is required
        # because bulk operations deliberately re-enter ``call``.
        self._state_lock = _projection_state_lock(self.state_path)
        self._source_roots = self._resolve_source_roots(source_roots)
        self._source_root_selection = self._source_root_selection_metadata(source_roots)
        self._last_codex_internal_exclusions = 0
        if isinstance(visible_ui_reader, Mapping):
            unknown = set(visible_ui_reader) - set(_VISIBLE_UI_PROFILES)
            if unknown:
                raise SessionProductError("VISIBLE_UI_READER_PROVIDER_INVALID")
            self._visible_ui_readers = dict(visible_ui_reader)
        elif visible_ui_reader is None:
            self._visible_ui_readers = {}
        else:
            # Backward-compatible visible-chat/chat-archive injection surface.
            self._visible_ui_readers = {"deepseek": visible_ui_reader}
        self._visible_ui_reader = self._visible_ui_readers.get("deepseek")
        self._legacy_deepseek_visible_ui_projection = bool(
            legacy_deepseek_visible_ui_projection
        )
        self._local_model_registry = local_model_registry
        self._conversation_refinement_channel = conversation_refinement_channel
        self._refinement_task_manager = refinement_task_manager
        if refinement_lifecycle_observer is not None and not callable(
            refinement_lifecycle_observer
        ):
            raise SessionProductError("SESSION_REFINEMENT_OBSERVER_INVALID")
        self._refinement_lifecycle_observer = refinement_lifecycle_observer
        if refinement_queue_observer is not None and not callable(
            refinement_queue_observer
        ):
            raise SessionProductError("SESSION_REFINEMENT_QUEUE_OBSERVER_INVALID")
        self._refinement_queue_observer = refinement_queue_observer
        if refinement_result_observer is not None and not callable(
            refinement_result_observer
        ):
            raise SessionProductError("SESSION_REFINEMENT_RESULT_OBSERVER_INVALID")
        self._refinement_result_observer = refinement_result_observer
        self._refinement_lock = threading.RLock()
        self._refinement_workers: dict[str, threading.Thread] = {}
        self._refinement_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._refinement_queue_items: set[str] = set()
        self._refinement_queue_worker: threading.Thread | None = None
        self._visible_ui_root = self.state_root / "visible_ui_private"
        self._visible_ui_root.mkdir(parents=True, exist_ok=True)
        self._browser_session_root = self.state_root / "browser_sessions_private"
        self._browser_session_root.mkdir(parents=True, exist_ok=True)
        self._session_archive = SessionArchive(
            self.state_root / "session_archive_v1.sqlite3",
            self.state_root / "session_source_snapshots_private",
        )
        self._refinement_root = self.state_root / "refinement_private"
        self._refinement_root.mkdir(parents=True, exist_ok=True)
        self._refinement_settings_path = self.state_root / "refinement_settings_v1.json"
        if not self._refinement_settings_path.exists():
            self._write_private_json(
                self._refinement_settings_path,
                {
                    "schema_version": "DesktopSessionRefinementSettings-v1",
                    "profile_ref": "",
                    "include_tasks": True,
                    "include_risks": True,
                },
            )
        self._metrics = {
            "synthetic_fixture_reads": 0,
            "local_projection_writes": 0,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "provider_calls": 0,
            "credential_value_reads": 0,
            "cookie_value_reads": 0,
            "browser_storage_value_reads": 0,
            "cli_session_reads": 0,
            "ui_session_reads": 0,
            "real_session_content_captured": 0,
            "remote_session_mutations": 0,
            "account_mutations": 0,
            "permanent_deletes": 0,
            "raw_private_external_writes": 0,
            "metadata_refreshes": 0,
            "browser_bundle_imports": 0,
            "refinement_drafts_created": 0,
            "refinement_tasks_created": 0,
            "refinement_model_calls": 0,
            "refinement_tasks_failed": 0,
            "refinement_lifecycle_projection_failures": 0,
            "refinement_queue_projection_failures": 0,
        }
        if not self.state_path.exists():
            self._save(
                {
                    "schema_version": "SessionsSessionProjectionState-v3",
                    "revision": 0,
                    "sessions": {},
                    "capture_jobs": {},
                    "fault_history": [],
                    "last_filter": {
                        "source": "all",
                        "search": "",
                        "limit_per_source": 30,
                    },
                    "source_status": {},
                    "hidden_projection_ids": [],
                }
            )
        else:
            raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
            migrated_state = self._migrate_state(raw_state)
            if migrated_state != raw_state:
                self._save(migrated_state)
        self._validate_state(self._load())

    @property
    def session_archive(self) -> SessionArchive:
        """The archive of captured sessions; the browser companion counts it for its sync cursor."""
        return self._session_archive

    @staticmethod
    def _reject_sensitive_channel_result(value: Any, path: str = "result") -> None:
        forbidden = re.compile(
            r"^(api[_-]?key|authorization|cookie[_-]?value|credential[_-]?value|password|secret[_-]?value)$",
            re.IGNORECASE,
        )
        if isinstance(value, Mapping):
            for key, child in value.items():
                if forbidden.fullmatch(str(key)):
                    raise SessionProductError("SESSION_REFINEMENT_RESULT_SENSITIVE_FIELD_FORBIDDEN")
                SessionsProductController._reject_sensitive_channel_result(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                SessionsProductController._reject_sensitive_channel_result(child, f"{path}[{index}]")

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        with self._state_lock:
            return self._call_unlocked(method, params)

    def _call_unlocked(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in SESSIONS_METHODS:
            raise SessionProductError("SESSIONS_PRODUCT_METHOD_NOT_ALLOWLISTED")
        accepted = dict(params)
        if any(str(key).startswith("_") for key in accepted):
            raise SessionProductError("SESSIONS_PRODUCT_PRIVATE_PARAM_FORBIDDEN")
        if method == "sessions.get_contract":
            self._require_keys(accepted, set())
            return self._contract()
        if method == "sessions.bootstrap":
            return self._bootstrap(accepted)
        if method == "sessions.list":
            return self._list_sessions(accepted)
        if method == "sessions.get":
            return self._get_session(accepted)
        if method == "sessions.get_provider_capabilities":
            self._require_keys(accepted, set(), {"provider_id"})
            provider_id = accepted.get("provider_id")
            rows = [row for row in self._source_capabilities() if provider_id is None or row["provider_id"] == provider_id]
            return {
                "schema_version": "SessionsProviderCapabilityProjection-v2",
                "sources": deepcopy(rows),
                "source_count": len(rows),
                "status": "PASS",
            }
        if method == "sessions.capture_intent":
            self._require_keys(accepted, {"channel"})
            return self._capture_intent(str(accepted["channel"]))
        if method in {"sessions.capture_progress", "sessions.capture_cancel", "sessions.capture_retry"}:
            return self._capture_control(method, accepted)
        if method == "sessions.refinement_settings_get":
            self._require_keys(accepted, set())
            return self._refinement_settings_projection()
        if method == "sessions.refinement_settings_save":
            return self._save_refinement_settings(accepted)
        if method == "sessions.refinement_get":
            return self._get_refinement(accepted)
        if method == "sessions.refine":
            self._require_keys(accepted, {"local_projection_id"})
            projection_id = str(accepted["local_projection_id"])
            return self._create_refinement_task(projection_id)
        if method == "sessions.refinement_cancel":
            self._require_keys(accepted, {"inbox_item_id"})
            return self._cancel_refinement_task(str(accepted["inbox_item_id"]))
        if method == "sessions.refinement_start":
            self._require_keys(accepted, {"job_id"})
            return self._start_refinement_task(str(accepted["job_id"]))
        if method == "sessions.delete_local":
            return self._delete_local(accepted)
        if method == "sessions.bulk_delete_local":
            return self._bulk_delete_local(accepted)
        if method == "sessions.refresh":
            return self._refresh_sessions(accepted)
        if method == "sessions.resolve_locator":
            return self._resolve_locator(accepted)
        if method == "sessions.inject_fault":
            return self._inject_fault(accepted)
        if method == "sessions.restore":
            self._require_keys(accepted, set())
            state = self._load()
            return {
                "schema_version": "SessionsSessionRestartRecoveryReceipt-v2",
                "revision": state["revision"],
                "session_count": len(self._rows()),
                "capture_jobs": deepcopy(state["capture_jobs"]),
                "stable_locator_replay": True,
                "status": "PASS",
            }
        self._require_keys(accepted, set())
        return {
            "schema_version": "SessionsSessionEffectMetrics-v2",
            **deepcopy(self._metrics),
            "source_access_envelope_present": True,
            "source_access_envelope_status": "ACTIVE_LOCAL_READ_ONLY",
            "real_source_channels_assessed": sum(
                bool(value.get("available")) for value in self._load()["source_status"].values()
            ),
            "status": "PASS",
        }

    def _bootstrap(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, set())
        if not self._service_ready():
            raise SessionProductError("SESSIONS_SERVICE_HEALTH_CONTRACT_INVALID")
        refresh = self._refresh()
        rows, pagination = self._page_rows(
            self._filtered_rows(),
            page_offset=0,
            page_size=30,
        )
        total_count = len(self._filtered_rows())
        return {
            "schema_version": "SessionsSessionsBootstrapProjection-v2",
            "records": rows,
            "record_count": len(rows),
            "total_count": total_count,
            "discovery_window_days": self.discovery_window_days,
            "source_capabilities": self._source_capabilities(),
            "source_count": len(self._source_capabilities()),
            "source_status": refresh["source_status"],
            "source_access_envelope_status": "ACTIVE_LOCAL_READ_ONLY",
            "real_source_channels_assessed": refresh["available_source_count"],
            "synthetic_only": False,
            "sample_fixture_retained_for_tests": (self.fixture_root / "synthetic_sessions.json").is_file(),
            "raw_private_content_included": False,
            "sort_order": "CONVERSATION_TIME_DESC_UNKNOWN_LAST",
            **pagination,
            "status": "PASS",
        }

    def _list_sessions(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(
            accepted,
            set(),
            {"source", "search", "limit_per_source", "page_offset", "page_size"},
        )
        source = str(accepted.get("source", "all"))
        search = str(accepted.get("search", ""))
        limit_per_source = int(accepted.get("limit_per_source", 30))
        rows = self._rows(source, search, limit_per_source)
        pagination: dict[str, Any] = {}
        if "page_offset" in accepted or "page_size" in accepted:
            # Pagination must traverse the same collection as total_count.
            # The per-source preview cap applies only to unpaged previews.
            rows, pagination = self._page_rows(
                self._filtered_rows(source, search),
                page_offset=int(accepted.get("page_offset", 0)),
                page_size=int(accepted.get("page_size", 30)),
            )
        total_count = len(self._filtered_rows(source, search))
        state = self._load()
        state["last_filter"] = {
            "source": source,
            "search": search,
            "limit_per_source": limit_per_source,
        }
        self._save(state)
        return {
            "schema_version": "SessionsSessionListProjection-v2",
            "source": source,
            "search_sha256": hashlib.sha256(search.encode("utf-8")).hexdigest().upper(),
            "rows": rows,
            "row_count": len(rows),
            "total_count": total_count,
            "limit_per_source": limit_per_source,
            "discovery_window_days": self.discovery_window_days,
            "raw_private_content_included": False,
            "sort_order": "CONVERSATION_TIME_DESC_UNKNOWN_LAST",
            **pagination,
            "status": "PASS",
        }

    def _get_session(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"local_projection_id"})
        row = self._get_internal(str(accepted["local_projection_id"]))
        messages, completeness = self._messages_for(row, presentation=True)
        session = self._public(row)
        session["partial"] = completeness.get("status") != "COMPLETE"
        return {
            "schema_version": "SessionsSessionDetailProjection-v2",
            "session": session,
            "messages": messages,
            "message_count": len(messages),
            "completeness": completeness,
            "local_private_content_included": bool(messages),
            "raw_private_content_externalized": False,
            "external_network_calls": 0,
            "status": "PASS",
        }

    def _capture_control(self, method: str, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"job_id"}, {"progress"})
        state = self._load()
        job = state["capture_jobs"].get(str(accepted["job_id"]))
        if not job:
            raise SessionProductError("CAPTURE_JOB_NOT_FOUND")
        if method == "sessions.capture_progress":
            value = int(accepted.get("progress", 50))
            if not 0 <= value <= 100:
                raise SessionProductError("CAPTURE_PROGRESS_INVALID")
            job.update({"progress": value, "status": "COMPLETED" if value == 100 else "WORKING"})
        elif method == "sessions.capture_cancel":
            job["status"] = "CANCELLED"
        else:
            job.update({"status": "WAITING", "progress": 0, "retry_count": int(job["retry_count"]) + 1})
        self._save(state)
        return {
            "schema_version": "SessionsCaptureControlReceipt-v2",
            **deepcopy(job),
            "synthetic_only": True,
            "status": "PASS",
        }

    def _save_refinement_settings(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"profile_ref", "include_tasks", "include_risks"})
        profile_ref = _text(accepted.get("profile_ref"), 160)
        if not isinstance(accepted.get("include_tasks"), bool) or not isinstance(accepted.get("include_risks"), bool):
            raise SessionProductError("SESSION_REFINEMENT_OPTIONS_INVALID")
        allowed = {row["profile_ref"] for row in self._configured_refinement_models()}
        if profile_ref and profile_ref not in allowed:
            raise SessionProductError("SESSION_REFINEMENT_PROFILE_NOT_CONFIGURED")
        settings = {
            "schema_version": "DesktopSessionRefinementSettings-v1",
            "profile_ref": profile_ref,
            "include_tasks": accepted["include_tasks"],
            "include_risks": accepted["include_risks"],
        }
        self._write_private_json(self._refinement_settings_path, settings)
        self._metrics["local_projection_writes"] += 1
        return {
            "schema_version": "DesktopSessionRefinementSettingsSaveReceipt-v1",
            "settings": settings,
            "external_model_calls": 0,
            "status": "PASS",
        }

    def _get_refinement(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"local_projection_id"})
        projection_id = str(accepted["local_projection_id"])
        self._get_internal(projection_id)
        task = self._refinement_task_for_projection(projection_id)
        if task is None:
            inbox_item = self._find_inbox_refinement(projection_id)
            if inbox_item is not None:
                return {
                    "schema_version": "DesktopSessionRefinementTaskProjection-v2",
                    "task": None,
                    "inbox_item": inbox_item,
                    "draft": None,
                    "status": (
                        "INBOX_QUEUED"
                        if inbox_item.get("state") == "QUEUED"
                        else "INBOX_PROCESSING"
                        if inbox_item.get("state") == "PROCESSING"
                        else "INBOX_FAILED"
                    ),
                }
        if task is not None and task.get("control_state") != "SUCCEEDED":
            return {
                "schema_version": "DesktopSessionRefinementTaskProjection-v1",
                "task": task,
                "draft": None,
                "status": {
                    "QUEUED": "TASK_QUEUED",
                    "RUNNING": "TASK_RUNNING",
                    "FAILED": "TASK_FAILED",
                    "CANCELLED": "TASK_CANCELLED",
                }.get(str(task.get("control_state")), "TASK_STATE_UNKNOWN"),
            }
        path = self._refinement_root / f"{projection_id}.json"
        if not path.is_file():
            raise SessionProductError("SESSION_REFINEMENT_DRAFT_NOT_FOUND")
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SessionProductError("SESSION_REFINEMENT_DRAFT_UNAVAILABLE") from error
        return {
            "schema_version": "DesktopSessionRefinementDraftProjection-v1",
            "draft": draft,
            "task": task,
            "status": "PASS",
        }

    def _delete_local(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"local_projection_id"})
        projection_id = str(accepted["local_projection_id"])
        self._get_internal(projection_id)
        state = self._load()
        hidden = set(state["hidden_projection_ids"])
        hidden.add(projection_id)
        state["hidden_projection_ids"] = sorted(hidden)
        self._save(state)
        return {
            "schema_version": "SessionsDeleteReceipt-v2",
            "local_projection_id": projection_id,
            "local_projection_hidden": True,
            "local_projection_deleted": False,
            "source_record_deleted": False,
            "remote_session_mutated": False,
            "status": "PASS",
        }

    def _bulk_delete_local(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"local_projection_ids"})
        values = accepted["local_projection_ids"]
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value for value in values)
            or len(values) != len(set(values))
        ):
            raise SessionProductError("SESSION_IDS_INVALID")
        receipts = [self.call("sessions.delete_local", {"local_projection_id": value}) for value in values]
        return {
            "schema_version": "SessionsBulkDeleteReceipt-v2",
            "selected_count": len(values),
            "success_count": len(values),
            "blocked_count": 0,
            "receipts": receipts,
            "remote_session_mutations": 0,
            "status": "PASS",
        }

    def _refresh_sessions(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(
            accepted,
            set(),
            {"source", "search", "limit_per_source", "page_offset", "page_size"},
        )
        prior_filter = self._load().get("last_filter", {})
        source = str(accepted.get("source", prior_filter.get("source", "all")))
        search = str(accepted.get("search", prior_filter.get("search", "")))
        limit_per_source = int(
            accepted.get(
                "limit_per_source",
                prior_filter.get("limit_per_source", 30),
            )
        )
        refresh = self._refresh()
        rows = self._rows(source, search, limit_per_source)
        pagination: dict[str, Any] = {}
        if "page_offset" in accepted or "page_size" in accepted:
            # Pagination must traverse the same collection as total_count.
            # The per-source preview cap applies only to unpaged previews.
            rows, pagination = self._page_rows(
                self._filtered_rows(source, search),
                page_offset=int(accepted.get("page_offset", 0)),
                page_size=int(accepted.get("page_size", 30)),
            )
        state = self._load()
        state["last_filter"] = {
            "source": source,
            "search": search,
            "limit_per_source": limit_per_source,
        }
        self._save(state)
        return {
            "schema_version": "SessionsSessionRefreshReceipt-v2",
            **refresh,
            "records": rows,
            "total_count": len(self._filtered_rows(source, search)),
            "filter": deepcopy(state["last_filter"]),
            "sort_order": "CONVERSATION_TIME_DESC_UNKNOWN_LAST",
            "provider_calls": 0,
            "external_network_calls": 0,
            **pagination,
            "status": "PASS",
        }

    def _resolve_locator(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"locator"})
        locator = str(accepted["locator"])
        prefix = "memorive://session/"
        if not locator.startswith(prefix):
            raise SessionProductError("SESSION_LOCATOR_INVALID")
        projection_id = locator[len(prefix) :]
        self._get_internal(projection_id)
        return {
            "schema_version": "SessionsSessionLocatorReceipt-v2",
            "locator": locator,
            "local_projection_id": projection_id,
            "route": "sessions",
            "fuzzy_match_attempted": False,
            "status": "PASS",
        }

    def _inject_fault(self, accepted: dict[str, Any]) -> Any:
        self._require_keys(accepted, {"fault"})
        fault = str(accepted["fault"])
        allowed = {"duplicate", "partial", "corrupt", "revoked", "expired", "source_drift", "interrupted_capture"}
        if fault not in allowed:
            raise SessionProductError("SESSION_FAULT_UNKNOWN")
        classification = {
            "duplicate": "DEDUPED_BY_PROVIDER_SESSION_AND_CONTENT_HASH",
            "partial": "PARTIAL_LOCAL_PRIVATE_PROJECTION",
            "corrupt": "REJECTED_FAIL_CLOSED",
            "revoked": "SOURCE_DISABLED_NOT_ASSESSED",
            "expired": "SOURCE_DISABLED_NOT_ASSESSED",
            "source_drift": "SOURCE_DRIFT_REQUIRES_REFRESH",
            "interrupted_capture": "RECOVERABLE_FROM_LOCAL_METADATA_INDEX",
        }[fault]
        return {
            "schema_version": "SessionsSessionFaultReceipt-v2",
            "fault": fault,
            "classification": classification,
            "raw_private_content_exposed": False,
            "remote_session_mutated": False,
            "status": "PASS",
        }

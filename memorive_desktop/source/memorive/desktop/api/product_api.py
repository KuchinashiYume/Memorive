"""ProductApi: construction, runtime preferences, dispatch of typed events and facade calls,
call(), runtime status and close; everything else comes from the mixins.
"""
from __future__ import annotations

from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Mapping
from application_behavior import WindowsAutostartManager
from network_diagnostics import run_network_diagnostic
from memorive.platform.json_files import lenient_sha256 as _sha256_json, write_json_indented
from memorive.platform.windows_paths import windows_io_path
from memorive.desktop.api.common import (
    ACCOUNTING_METHODS,
    ALLOWED_EVENTS,
    ASSISTANT_PREFERENCE_METHODS,
    CURRENT_TASK_METHODS,
    INBOX_METHODS,
    LIBRARY_METHODS,
    LOCAL_MODEL_METHODS,
    MESSAGES_METHODS,
    REVIEW_METHODS,
    SESSIONS_METHODS,
    SETTINGS_METHODS,
    WORK_LOG_METHODS,
    _ensure_source_importable,
)
from memorive.desktop.api.service_process import ServiceProcessManager
from memorive.desktop.api.page_events import PageEventsMixin
from memorive.desktop.api.assistant import AssistantCallsMixin
from memorive.desktop.api.notifications import NotificationsMixin
from memorive.desktop.api.window import WindowMixin
from memorive.desktop.api.inbox_files import InboxFilesMixin
from memorive.desktop.api.research_memo import ResearchMemoMixin
from memorive.desktop.api.artifacts import ArtifactsMixin
from memorive.desktop.api.settings_page import SettingsPageMixin
from memorive.desktop.api.browser_companion import BrowserCompanionCallsMixin
from memorive.desktop.api.links import LinksMixin


class ProductApi(
    PageEventsMixin, AssistantCallsMixin, NotificationsMixin, WindowMixin, InboxFilesMixin,
    ResearchMemoMixin, ArtifactsMixin, SettingsPageMixin, BrowserCompanionCallsMixin, LinksMixin,
):
    def _profile_path(self, logical_directory: str) -> Path:
        """Resolve a declared profile directory through the packaged policy.

        Normal startup creates the manager in ``__init__``. Recovery helpers
        and frozen test harnesses can intentionally construct ``ProductApi``
        without running that startup path, so initialize the same manager
        lazily instead of reverting to ad-hoc path joins.
        """

        profile_root = Path(self._profile_root).resolve(strict=False)
        manager = getattr(self, "_folder_manager", None)
        if manager is None or Path(manager.profile_root).resolve(strict=False) != profile_root:
            source_root = getattr(self, "_source_root", None)
            if source_root is not None:
                _ensure_source_importable(Path(source_root))
            folder_module = importlib.import_module("memorive.platform.folders")
            manager = folder_module.ProfileFolderManager(profile_root)
            self._folder_manager = manager
        return manager.path_for(logical_directory)

    def __init__(
        self,
        state_dir: Path,
        source_root: Path,
        method_binding_path: Path,
        *,
        network_runner: Any = run_network_diagnostic,
        system_effects_enabled: bool = False,
        autostart_manager: WindowsAutostartManager | None = None,
        session_source_roots: Mapping[str, Path] | None = None,
        session_visible_ui_reader: Any | None = None,
        test_fixture_mode: bool = False,
        private_profile_selection: Mapping[str, Any] | None = None,
    ):
        requested_state_dir = state_dir.resolve()
        self._private_profile_selection: dict[str, Any] | None = None
        if bool(getattr(sys, "frozen", False)) and system_effects_enabled and not test_fixture_mode:
            from product_identity import BINDING

            if BINDING.get("channel") == "SANDBOX_TEST_ONLY_UNSIGNED":
                # app.main owns private-profile selection and bootstrapping.  A
                # second selection here used ``state_dir.parent`` as a new data
                # root and created state/private-profiles/state/private-profiles.
                # Accept only the exact receipt for the already-selected root.
                selection = dict(private_profile_selection or {})
                if selection.get("status") != "READY":
                    raise RuntimeError("PRIVATE_TEST_PROFILE_PRESELECTION_REQUIRED")
                if str(selection.get("package_id") or "") != str(BINDING.get("package_id") or ""):
                    raise RuntimeError("PRIVATE_TEST_PROFILE_PRESELECTION_BUILD_MISMATCH")
                selected_root = Path(str(selection.get("state_root") or "")).resolve(strict=False)
                if os.path.normcase(str(selected_root)) != os.path.normcase(str(requested_state_dir)):
                    raise RuntimeError("PRIVATE_TEST_PROFILE_PRESELECTION_ROOT_MISMATCH")
                self._private_profile_selection = selection
        self._state_dir = requested_state_dir
        from memorive.models.scheduling import TASK_CATEGORIES
        TASK_CATEGORIES.bind(self._state_dir / 'profile')
        self._source_root = source_root.resolve()
        self._method_binding_path = method_binding_path.resolve()
        # Sample mode (test_fixture_mode) reads made-up data from fixtures/ and the fixtures_* folders beside source/;
        # see fixtures/README.md.
        self._fixture_root = self._source_root.parent / "fixtures" / "source"
        self._window: Any | None = None
        self._notification_presenter: Any | None = None
        self._notification_delivery_count = 0
        self._last_notification_receipt: dict[str, Any] | None = None
        self._core_event_lock = threading.RLock()
        self._core_event_stop = threading.Event()
        self._core_event_thread: threading.Thread | None = None
        self._core_event_revision = 0
        self._core_event_fingerprint: str | None = None
        self._core_event_previous_snapshot: dict[str, Any] | None = None
        self._core_event_last_receipt: dict[str, Any] | None = None
        self._exit_requested = False
        self._network_runner = network_runner
        self._network_test_count = 0
        self._system_effects_enabled = bool(system_effects_enabled)
        self._autostart_manager = autostart_manager or WindowsAutostartManager()
        self._last_runtime_preferences_receipt: dict[str, Any] | None = None
        self._state_dir.mkdir(parents=True, exist_ok=True)
        _ensure_source_importable(self._source_root)
        binding = json.loads(self._method_binding_path.read_text(encoding="utf-8"))
        if binding.get("status") != "PASS" or binding.get("method_count") != 12:
            raise RuntimeError("FACADE_METHOD_BINDING_INVALID")
        self._method_binding = binding
        self._facade_methods = frozenset(binding["required_methods"])
        self._lock = threading.RLock()
        # Product calls remain serialized, but they must not hold the state /
        # durable-receipt lock while invoking WinForms or WebView presenters.
        # A presenter can synchronously write a receipt through ``self._lock``
        # on the UI thread; sharing that lock here creates a cross-thread cycle.
        self._product_call_lock = threading.RLock()
        self._sequence = 0  # numbers the UI event receipts of this run
        profile_root = self._state_dir / "profile"
        self._profile_root = profile_root
        folder_module = importlib.import_module("memorive.platform.folders")
        self._folder_manager = folder_module.ProfileFolderManager(profile_root)
        self._inbox_root = self._profile_path("INBOX")
        self._inbox_source_root = self._profile_path("INBOX_SOURCE")
        windows_io_path(self._inbox_source_root).mkdir(parents=True, exist_ok=True)
        ui_root = self._profile_path("UI_STATE")
        ui_root.mkdir(parents=True, exist_ok=True)
        self._core_event_receipt_path = ui_root / "core_event_pump_receipt_v1.json"
        self._settings_save_receipt_path = ui_root / "settings_save_receipt_v1.json"
        control_module = importlib.import_module("memorive.desktop.service.control_store")
        instance_module = importlib.import_module("memorive.desktop.service.single_instance")
        self._control_store = control_module.ControlStore(ui_root)
        self._control_envelope = self._control_store.load()
        self._single_instance = instance_module.SingleInstanceCoordinator(ui_root)
        if not self._single_instance.acquire_primary():
            raise RuntimeError("DESKTOP_INSTANCE_ALREADY_ACTIVE")
        self._service = ServiceProcessManager(
            self._source_root,
            profile_root,
            self._state_dir / "service-runtime",
            test_fixture_mode=test_fixture_mode,
        )
        self._closed = False
        try:
            self._service.start()
            local_models_module = importlib.import_module("memorive.models.local.product_controller")
            self._local_models = local_models_module.LocalModelProductController(
                self._state_dir / "local_models"
            )
            accounting_module = importlib.import_module("memorive.desktop.accounting")
            self._accounting = accounting_module.AccountingProductController(
                settings_provider=lambda: self._service.call("settings.get_state", {}),
                local_model_registry=self._local_models,
                leaderboard_provider=lambda: self._service.call("settings.leaderboard_get", {}),
                exam_root=self._profile_path("MODEL_VALIDATION"),
                core_root=self._profile_path("Core_JOBS"),
            )
            refinement_module = importlib.import_module(
                "memorive.sessions.refinement.model_channel"
            )
            self._conversation_refinement_channel = (
                refinement_module.ExistingConversationRefinementChannel(
                    local_model_registry=self._local_models,
                    profile_executor=self._service,
                )
            )
            refinement_tasks_module = importlib.import_module(
                "memorive.desktop.current_task.refinement_tasks"
            )
            self._refinement_queue_tasks = (
                refinement_tasks_module.SessionRefinementTaskStore(
                    self._state_dir / "sessions" / "refinement_queue_tasks"
                )
            )
            controller_module = importlib.import_module("memorive.desktop.current_task.product_controller")
            self._current_task = controller_module.CurrentTaskProductController(
                self._service,
                self._control_store,
                self._source_root.parent / "contracts" / "verification",
                settings_provider=lambda: self._service.call("settings.get_state", {}),
                local_model_registry=self._local_models,
                refinement_store=self._refinement_queue_tasks,
                include_synthetic_projection=test_fixture_mode,
                completed_success_retention_seconds=900,
                cancelled_retention_seconds=60,
                history_retention_seconds=30 * 24 * 60 * 60,
                task_lifecycle_root=self._state_dir / "current_task",
                core_task_control_root=self._profile_path(
                    "Core_TASK_CONTROLS"
                ),
            )
            messages_module = importlib.import_module("memorive.desktop.messages.product_controller")
            self._messages = messages_module.MessagesProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "retrieval",
                self._source_root.parent / "fixtures_retrieval",
                self._state_dir / "messages",
                include_synthetic_projection=test_fixture_mode,
                core_job_provider=lambda: self._current_task.core_job_rows(include_history=True),
            )
            from memorive.documents.library.core_products import CoreProducts
            self._core_products = CoreProducts(
                service=self._service, profile_path=self._profile_path, state_dir=self._state_dir,
                write_json_file=self._write_json_file, is_reparse_point=self._is_reparse_point,
            )
            library_module = importlib.import_module("memorive.documents.library.product_controller")
            self._library = library_module.LibraryProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "integration",
                self._source_root.parent / "fixtures_integration",
                self._state_dir / "library",
                include_synthetic_projection=test_fixture_mode,
                core_artifact_provider=self._core_products.rows,
                core_job_artifact_provider=lambda job_id: self._core_products.rows(job_id),
            )
            sessions_module = importlib.import_module("memorive.sessions.controller")
            if session_visible_ui_reader is None:
                visible_ui_module = importlib.import_module("memorive.sessions.edge_visible_ui")
                session_visible_ui_reader = {
                    "deepseek": visible_ui_module.EdgeDeepSeekVisibleUIReader(),
                    "gemini": visible_ui_module.EdgeGeminiVisibleUIReader(),
                    "kimi": visible_ui_module.EdgeKimiVisibleUIReader(),
                }
            self._sessions = sessions_module.SessionsProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "analysis",
                self._source_root.parent / "fixtures_analysis",
                self._state_dir / "sessions",
                source_roots=session_source_roots,
                visible_ui_reader=session_visible_ui_reader,
                legacy_deepseek_visible_ui_projection=(
                    os.environ.get("MEMORIVE_ENABLE_RETIRED_CURRENT_PAGE_SNAPSHOTS") == "1"
                ),
                local_model_registry=self._local_models,
                conversation_refinement_channel=self._conversation_refinement_channel,
                refinement_task_manager=self._refinement_queue_tasks,
                refinement_lifecycle_observer=self._on_refinement_lifecycle,
                refinement_queue_observer=self._on_refinement_queue_projection,
                refinement_result_observer=self._on_refinement_result,
            )
            self._sessions.end_interrupted_refinements()
            self._sessions.sync_refinement_queue()
            from memorive.sessions.companion.desktop import BrowserCompanionDesktop
            self._browser_companion = BrowserCompanionDesktop(
                profile_root=self._profile_root, source_root=self._source_root, sessions=self._sessions,
                profile_path=self._profile_path, write_json_file=self._write_json_file,
                effective_directory=self._effective_directory,
                system_effects_enabled=self._system_effects_enabled,
            )
            self._sessions.browser_sites_provider = self._browser_companion.browser_companion_sites
            work_log_module = importlib.import_module("memorive.desktop.work_log.product_controller")
            self._work_log = work_log_module.WorkLogProductController(
                self._service, self._control_store,
                self._source_root.parent / "contracts" / "logging",
                self._source_root.parent / "fixtures_logging",
                self._state_dir / "work_log",
                include_synthetic_projection=test_fixture_mode,
                refinement_task_provider=self._refinement_queue_tasks.list_jobs,
                refinement_product_provider=self._library.refinement_activity_rows,
                core_job_provider=self._work_log_core_job_rows,
                core_product_provider=self._library.core_activity_rows,
            )
            from memorive.desktop.assistant.desktop import AssistantDesktop
            self._desk_assistant = AssistantDesktop(
                ui_root=ui_root, control_store=self._control_store, service=self._service, lock=self._lock,
                messages=self._messages, current_task=self._current_task,
                refinement_tasks=self._refinement_queue_tasks,
                bring_main_window_to_foreground=self._bring_main_window_to_foreground,
                sample_job=self._sample_job, sample_message=self._sample_message,
            )
            self._browser_companion_auto_update = self._browser_companion.refresh_prepared_package()
            from memorive.research.host import ResearchHost
            from memorive.research.runtime import ResearchRuntime
            self._research = ResearchRuntime(ResearchHost(
                service=self._service, library=self._library, messages=self._messages,
                work_log=self._work_log, local_models=self._local_models,
                profile_path=self._profile_path, desktop_call=self.call,
                open_target=self.assistant_launch_target,
                library_job_rows=self._core_products.job_rows,
                library_failed_job_rows=self._core_products.failed_job_rows,
                notify_window=self._send_page_event,
                research_workspace=lambda: getattr(self, "_memo", None)))
            self._library.research_artifact_provider = self._research.library_rows
            from memorive.research.task_projection import log_rows
            self._work_log.research_provider=lambda:log_rows(self._research)
            self._desk_assistant.start_deliveries()
        except Exception:
            self._service.close()
            self._single_instance.release()
            raise

    def _current_preferences(self) -> dict[str, Any]:
        state = self._service.call("settings.get_state", {})
        settings = state.get("settings") if isinstance(state, dict) else None
        preferences = settings.get("preferences") if isinstance(settings, dict) else None
        if not isinstance(preferences, dict):
            raise RuntimeError("SETTINGS_PREFERENCES_UNAVAILABLE")
        return dict(preferences)

    def _apply_runtime_preferences(self, preferences: dict[str, Any]) -> dict[str, Any]:
        autostart = (
            self._autostart_manager.apply(bool(preferences.get("launch_at_startup")))
            if self._system_effects_enabled
            else {
                "schema_version": "DesktopAutostartMutationReceipt-v1",
                "enabled_requested": bool(preferences.get("launch_at_startup")),
                "mutation_count": 0,
                "status": "NOT_APPLIED_TEST_OR_SOURCE_MODE",
            }
        )
        panel_state_cleared = False
        if preferences.get("remember_panel_state") is False:
            def clear_panel_state(state: dict[str, Any]) -> None:
                nonlocal panel_state_cleared
                filters = dict(state["filters"])
                panel_state_cleared = filters.pop("panel_state_v1", None) is not None
                state["filters"] = filters

            self._control_envelope = self._control_store.update(clear_panel_state)
        live_ui_status = "WINDOW_NOT_ATTACHED"
        if self._window is not None:
            try:
                self._window.evaluate_js(
                    "window.Memorive?.app?.applyPreferences("
                    + json.dumps(preferences, ensure_ascii=False, separators=(",", ":"))
                    + ")"
                )
                live_ui_status = "APPLIED"
            except Exception as error:
                live_ui_status = "ERROR:" + type(error).__name__
        receipt = {
            "schema_version": "DesktopRuntimePreferencesReceipt-v1",
            "language": preferences.get("language"),
            "font_scale_percent": preferences.get("font_scale_percent"),
            "show_full_tooltips": preferences.get("show_full_tooltips"),
            "remember_panel_state": preferences.get("remember_panel_state"),
            "panel_state_cleared": panel_state_cleared,
            "autostart": autostart,
            "live_ui_status": live_ui_status,
            "status": "PASS",
        }
        self._last_runtime_preferences_receipt = receipt
        return receipt

    def _params(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        specification = self._method_binding["methods"][method]
        positional = specification["positional_parameters"]
        if len(args) > len(positional):
            raise ValueError("FACADE_POSITIONAL_ARGUMENT_COUNT_INVALID")
        allowed = set(positional) | set(specification["keyword_only_parameters"])
        if not specification["allows_varkw"] and set(kwargs) - allowed:
            raise ValueError("FACADE_KEYWORD_ARGUMENT_INVALID")
        params = dict(kwargs)
        for index, value in enumerate(args):
            name = positional[index]
            if name in params:
                raise ValueError("FACADE_ARGUMENT_DUPLICATED")
            params[name] = value
        return params

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or sorted(request) != ["event_type", "payload", "request_id", "source"]:
            raise ValueError("typed event request key set mismatch")
        event_type = request["event_type"]
        if event_type not in ALLOWED_EVENTS:
            raise ValueError("typed event is not allowlisted")
        if not isinstance(request["request_id"], str) or not request["request_id"].startswith("desktop-ui-"):
            raise ValueError("request_id invalid")
        if not isinstance(request["source"], str) or not 1 <= len(request["source"]) <= 96:
            raise ValueError("source invalid")
        if not isinstance(request["payload"], dict):
            raise ValueError("payload must be an object")
        with self._lock:
            control_state_mutated = False
            if event_type == "navigation.select" and isinstance(request["payload"].get("route"), str):
                route = request["payload"]["route"]
                self._control_envelope = self._control_store.update(lambda state: state.__setitem__("route", route))
                control_state_mutated = True
            self._sequence += 1
            receipt = {
                "schema_version": "WorkLogTypedEventReceipt-v1",
                "sequence": self._sequence,
                "request_id": request["request_id"],
                "event_type": event_type,
                "source": request["source"],
                "payload_sha256": _sha256_json(request["payload"]),
                "transport_status": "LOCAL_PYWEBVIEW_DISPATCHED",
                "control_state_mutated": control_state_mutated,
                "control_store_revision": self._control_envelope["revision"],
                "business_state_mutated": False,
            }
            return receipt

    def invoke_facade(self, request: dict[str, Any]) -> Any:
        """The application facade's allowlisted methods (facade_method_binding.json), kept for callers of the
        earlier desktop interface. Queries and commands go to the settings service; a command repeated with
        the same idempotency key is answered by the facade with its first result (the command outbox that
        recorded them here as well was removed in 1232c35)."""
        if not isinstance(request, dict) or sorted(request) != ["args", "kwargs", "method", "request_id"]:
            raise ValueError("facade request key set mismatch")
        method = request["method"]
        if method not in self._facade_methods:
            raise ValueError("facade method is not allowlisted")
        args = request["args"]
        kwargs = request["kwargs"]
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError("facade args/kwargs invalid")
        if any(str(key).startswith("_") for key in kwargs):
            raise ValueError("private facade kwarg forbidden")
        params = self._params(method, args, kwargs)
        with self._lock:
            return self._service.call(method, params)

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        from update_service import METHODS as UPDATE_METHODS
        if method in UPDATE_METHODS:
            with self._lock:
                if not hasattr(self, '_updates'):
                    from update_service import UpdateService
                    self._updates = UpdateService(self)
            return self._updates.call(method, dict(params or {}))
        from memorive.research.home import METHODS as HOME_METHODS, call as home_call
        from memorive.research.briefing.jobs import METHODS as AI_METHODS
        if method in HOME_METHODS:
            return home_call(self._research.host,method,dict(params or {}))
        if method in AI_METHODS:
            return self._research.ai.call(method,dict(params or {}))
        from memorive.research.opportunities.runs import METHODS as OPPORTUNITY_METHODS
        if method in OPPORTUNITY_METHODS:
            return self._research.opportunities.call(method,dict(params or {}))
        from memorive.research.workspace.service import DESKTOP_METHODS
        if method in DESKTOP_METHODS:
            with self._lock:
                if not hasattr(self, '_memo'):
                    from memorive.research.workspace.desktop import create
                    self._memo=create(service=self._service,library=self._library,
                        local_models=getattr(self,'_local_models',None),profile_root=self._profile_root,
                        workspace_root=self._profile_path('WORKSPACE')/'research_workspace',
                        desktop_call=self.call,stage_inbox_paths=self.stage_inbox_paths)
            return self._memo.call(method,dict(params or {}),desktop=True)
        from memorive.research.runtime import METHODS as RESEARCH_METHODS
        if method in RESEARCH_METHODS:
            return self._research.call(method,dict(params or {}))
        # Research answers messages that point at its runs and draws its runs on the current-task page.
        research=getattr(self,'_research',None)
        if research is not None and (method=='messages.navigate' or method in CURRENT_TASK_METHODS):
            from memorive.research import routes as research_routes
            answer=(research_routes.navigate(research,dict(params or {})) if method=='messages.navigate'
                    else research_routes.current_task(research,method,dict(params or {}),self._current_task.call))
            if answer is not None:
                return answer
        if not isinstance(method, str) or method not in REVIEW_METHODS | SETTINGS_METHODS | ACCOUNTING_METHODS | INBOX_METHODS | CURRENT_TASK_METHODS | MESSAGES_METHODS | SESSIONS_METHODS | LOCAL_MODEL_METHODS | ASSISTANT_PREFERENCE_METHODS | LIBRARY_METHODS | WORK_LOG_METHODS:
            raise ValueError("product method is not allowlisted")
        accepted = deepcopy(dict(params or {}))
        if any(str(key).startswith("_") for key in accepted):
            raise ValueError("private product param forbidden")
        if method == "settings.leaderboard_get":
            # The leaderboard is a read-only, internally synchronized service
            # projection.  Do not queue it behind unrelated UI mutations or a
            # long-running task request guarded by the product-wide lock.
            return self._service.call(method, accepted)
        with self._product_call_lock:
            if method in REVIEW_METHODS:
                return self._service.call(method,accepted)
            if method in CURRENT_TASK_METHODS:
                return self._current_task.call(method, accepted)
            if method in MESSAGES_METHODS:
                if method == "messages.notification_deliver":
                    if set(accepted) != {"message_id"}:
                        raise ValueError("MESSAGES_NOTIFICATION_PARAMS_INVALID")
                    message_id = accepted["message_id"]
                    if not isinstance(message_id, str) or not message_id:
                        raise ValueError("MESSAGES_NOTIFICATION_ID_INVALID")
                    message = self._messages.store.get(message_id)
                    if message.notification_state == "TOAST_SENT":
                        return {
                            "schema_version": "DesktopNativeNotificationDeliveryReceipt-v1",
                            "message_id": message_id,
                            "status": "DUPLICATE_SUPPRESSED",
                            "windows_presenter_called": False,
                            "in_app_message_preserved": True,
                        }
                    kind = {
                        "RED": "ERROR",
                        "YELLOW": "APPROVAL",
                        "GREEN": "COMPLETE",
                        "BLUE": "WEEKLY",
                    }[message.severity]
                    from memorive.research.routes import literature_notification
                    if literature_notification(self._research,message.target_locator):
                        kind='LITERATURE'
                    delivery = self._deliver_windows_notification(
                        kind=kind,
                        locator=message.target_locator,
                        source_message_id=message_id,
                    )
                    stored_state = {
                        "DELIVERED": "TOAST_SENT",
                        "BLOCKED_DISABLED": "BLOCKED_DISABLED",
                        "BLOCKED_QUIET": "BLOCKED_QUIET",
                        "BLOCKED_UNAVAILABLE": "BLOCKED_UNAVAILABLE",
                    }.get(delivery["status"], "NOT_ELIGIBLE")
                    self._messages.store.update_record(
                        message_id,
                        lambda raw: raw.update({"notification_state": stored_state}),
                    )
                    return {
                        **delivery,
                        "message_id": message_id,
                        "notification_state": stored_state,
                        "in_app_message_preserved": True,
                    }
                return self._messages.call(method, accepted)
            if method in SESSIONS_METHODS:
                return self._sessions.call(method, accepted)
            if method in LOCAL_MODEL_METHODS:
                return self._local_models.call(method, accepted)
            if method in ACCOUNTING_METHODS:
                return self._accounting.call(method, accepted)
            if method == "assistant.preference_get":
                if accepted:
                    raise ValueError("ASSISTANT_PREFERENCES_GET_PARAMS_INVALID")
                return self._desk_assistant.preference_get()
            if method == "assistant.preference_save":
                return self._desk_assistant.preference_save(accepted)
            if method in LIBRARY_METHODS:
                return self._library.call(method, accepted)
            if method in WORK_LOG_METHODS:
                return self._work_log.call(method, accepted)
            if method in {"settings.preview", "settings.save"}:
                settings = accepted.get("settings")
                if isinstance(settings, dict) and isinstance(settings.get("preferences"), dict):
                    settings["preferences"]["restore_last_document"] = False
            assistant_before: dict[str, Any] | None = None
            assistant_scope_receipt: dict[str, Any] | None = None
            reset_scope = accepted.get("scope") if method == "settings.reset_scope" else None
            if reset_scope in {"notice", "behavior", "appearance"}:
                settings_before = self._service.call("settings.get_state", {})
                if accepted.get("expected_revision") == settings_before.get("revision"):
                    assistant_before = self._desk_assistant.load_preferences()
                    assistant_candidate = self._desk_assistant.preferences_for_reset_scope(
                        reset_scope,
                        assistant_before,
                    )
                    try:
                        assistant_scope_receipt = self._desk_assistant.preference_save(
                            self._desk_assistant.preference_save_params(assistant_candidate)
                        )
                    except Exception:
                        self._write_json_file(
                            self._desk_assistant.preferences_path,
                            assistant_before,
                        )
                        raise
            try:
                result = self._service.call(method, accepted)
            except Exception:
                if assistant_before is not None:
                    try:
                        self._desk_assistant.preference_save(
                            self._desk_assistant.preference_save_params(assistant_before)
                        )
                    except Exception as rollback_error:
                        self._write_json_file(
                            self._desk_assistant.preferences_path,
                            assistant_before,
                        )
                        raise RuntimeError(
                            "SETTINGS_SCOPE_RESET_ASSISTANT_ROLLBACK_FAILED"
                        ) from rollback_error
                raise
            if method == "inbox.set_auto_run":
                self._sessions.sync_refinement_queue()
            if method in {
                "settings.save",
                "settings.save_cli_services",
                "settings.verify_cli_model",
                "settings.reset_scope",
                "settings.import_redacted",
                "settings.model_service_remove",
            } and not (
                isinstance(result, dict)
                and result.get("persistent_mutation") is False
            ):
                try:
                    workflow_snapshot = self._current_task.update_workflow_settings(
                        self._service.call("settings.get_state", {})
                    )
                    preferences = self._current_preferences()
                    runtime_receipt = self._apply_runtime_preferences(preferences)
                except Exception:
                    if method not in {"settings.save_cli_services", "settings.verify_cli_model"}:
                        raise
                    if isinstance(result, dict):
                        result = {
                            **result,
                            "settings_persisted": True,
                            "restart_required": False,
                            "runtime_effects_status": "WARNING",
                            "runtime_effect_error_code": "CLI_RUNTIME_REFRESH_DEFERRED",
                        }
                else:
                    if isinstance(result, dict):
                        enriched_result = {
                            **result,
                            "restart_required": False,
                            "runtime_effects": runtime_receipt,
                            "core_workflow_mapping": {
                                "settings_revision": workflow_snapshot["settings_revision"],
                                "settings_sha256": workflow_snapshot["settings_sha256"],
                                "snapshot_sha256": workflow_snapshot["snapshot_sha256"],
                                "configuration_ready": workflow_snapshot["configuration_ready"],
                                "external_route_activated": workflow_snapshot["external_route_activated"],
                            },
                        }
                        if method in {"settings.save_cli_services", "settings.verify_cli_model"}:
                            enriched_result["settings_persisted"] = True
                            enriched_result["runtime_effects_status"] = "PASS"
                        if method == "settings.reset_scope":
                            enriched_result["assistant_scope_reset"] = {
                                "scope": reset_scope,
                                "applied": assistant_scope_receipt is not None,
                                "status": "PASS",
                            }
                        result = enriched_result
            return result

    # The profile and UI state files are written whole, indented (memorive/platform/json_files.py).
    _write_json_file = staticmethod(write_json_indented)

    def get_runtime_status(self) -> dict[str, Any]:
        with self._lock:
            health = self._service.call("service.health", {})
            capabilities = self._service.call("service.capabilities", {})
            adapter = capabilities["adapter"]
            return {
                "schema_version": "WorkLogProductRuntimeStatus-v1",
                "typed_event_sequence": self._sequence,
                "facade_method_count": len(self._facade_methods),
                "settings_method_count": len(SETTINGS_METHODS),
                "inbox_method_count": len(INBOX_METHODS),
                "current_task_method_count": len(CURRENT_TASK_METHODS),
                "messages_method_count": len(MESSAGES_METHODS),
                "sessions_method_count": len(SESSIONS_METHODS),
                "library_method_count": len(LIBRARY_METHODS),
                "work_log_method_count": len(WORK_LOG_METHODS),
                "assistant_method_count": 5,
                "total_required_product_methods": (
                    len(self._facade_methods) + len(SETTINGS_METHODS) + len(INBOX_METHODS)
                    + len(CURRENT_TASK_METHODS) + len(MESSAGES_METHODS) + len(SESSIONS_METHODS)
                    + len(LIBRARY_METHODS) + len(WORK_LOG_METHODS) + 5
                ),
                "adapter_kind": adapter["adapter_kind"],
                "messages_adapter_kind": self._messages.adapter_kind,
                "sessions_adapter_kind": self._sessions.adapter_kind,
                "library_adapter_kind": self._library.adapter_kind,
                "work_log_adapter_kind": self._work_log.adapter_kind,
                "missing_required_methods": adapter["missing_methods"],
                "control_store_revision": self._control_envelope["revision"],
                "restored_route": self._control_envelope["state"]["route"],
                "service": self._service.public_status(),
                "service_health": health,
                "single_instance_primary": True,
                "assistant_window_attached": self._desk_assistant.window is not None,
                "assistant_sample_job_consumed": False,
                "network_calls": 0,
                "external_network_calls": 0,
                "model_calls": 0,
                "credential_value_reads": 0,
            }

    def close(self, *, graceful_service: bool = True) -> dict[str, Any]:
        bridge = getattr(self, '_console_bridge', None)
        if bridge is not None:
            bridge._stop.set()
            bridge._expressions.close()
        if hasattr(self, '_memo'):
            self._memo.close()
        if hasattr(self, '_research'):
            self._research.close()
        self._stop_core_event_pump()
        with self._lock:
            if self._closed:
                return {
                    "schema_version": "WorkLogProductShutdownReceipt-v1",
                    "already_closed": True,
                    "status": "STOPPED",
                }
            service_receipt = self._service.close(graceful=True) if graceful_service or getattr(self,'_system_session_ending',False) else self._service.close()
            # Window lifetime belongs to exit_application / the native loop.
            # A synchronous GUI call here can wait on a loop already torn down.
            self._single_instance.release()
            self._closed = True
            return {
                "schema_version": "WorkLogProductShutdownReceipt-v1",
                "service": service_receipt,
                "single_instance_released": True,
                "status": "STOPPED",
            }

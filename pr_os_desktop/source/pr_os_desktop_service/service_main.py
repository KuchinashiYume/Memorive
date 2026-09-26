from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import hmac
from importlib.util import find_spec
import json
import os
from pathlib import Path
import re
import signal
import sys
from typing import Mapping
import uuid

from pr_os_app.durable_store import DurableJobStore
from pr_os_app.facade import ApplicationFacade
from pr_os_folder_management import ProfileFolderManager
from pr_os_folder_management.policy import windows_io_path

from .adapters import RealFacadeAdapter
from .composite_adapter import CompositeServiceAdapter
from pr_os_inbox import InboxController, InboxServiceAdapter
from pr_os_local_models import LocalModelProductController
from pr_os_current_task import build_phase1_execution_snapshot
from pr_os_phase1_runtime import (
    Phase1PipelineExecutor,
    Phase1VerticalWorker,
    TaskControlStore,
)
from pr_os_settings import (
    CapabilityProjection, CredentialReferenceManager, DirectoryPolicy,
    LiveModelValidationRunner, SettingsController, SettingsServiceAdapter,
    SettingsStore, Phase2AnalysisExamExecutor, Phase2CardDistillerExamExecutor,
    Phase2AnalysisReviewerExamSuccessorExecutor,
    Phase2NewEmbeddingExamExecutor,
    Phase2NewCardDistillerLanguageBankExamExecutor,
    Phase2OcrPageExamExecutor,
    Phase2NewRerankerTextLanguageBankExamExecutor,
    Phase2CardDistillerDirectExamExecutor,
    Phase2CardReviewerExamSuccessorExecutor,
)
from pr_os_settings.contracts import default_settings
from .protocol import canonical_json_bytes
from .service import ApplicationServiceHost


SECRET_ENV_NAME = "PROS_T03_IPC_SECRET_HEX"


def _configure_shutdown_order(raw_parent_level, *, kernel=None):
    """Run after our GUI in Windows session end, preserving the existing flags.

    Windows ends higher levels first; 0x100..0x3ff is application-owned.
    No guessed timeout or SHUTDOWN_NORETRY/forced termination is introduced.
    """
    result = {'status': 'UNBOUND', 'parent_level': None, 'level': None}
    if not isinstance(raw_parent_level, str) or not raw_parent_level.isdecimal():
        return result
    parent_level = int(raw_parent_level)
    result['parent_level'] = parent_level
    if not 0x100 < parent_level <= 0x3ff:
        return result
    if kernel is None:
        if os.name != 'nt':
            return result
        kernel = ctypes.windll.kernel32
    level, flags = ctypes.c_uint(), ctypes.c_uint()
    result['status'] = 'UNAVAILABLE'
    if not kernel.GetProcessShutdownParameters(ctypes.byref(level), ctypes.byref(flags)):
        return result
    result.update(previous_level=level.value, flags=flags.value)
    if not kernel.SetProcessShutdownParameters(parent_level - 1, flags.value):
        return result
    if not kernel.GetProcessShutdownParameters(ctypes.byref(level), ctypes.byref(flags)):
        return result
    result.update(level=level.value, flags=flags.value)
    if level.value == parent_level - 1:
        result['status'] = 'APPLIED'
    return result


def _stop_requested(endpoint_file, secret, parent_pid, pid):
    path=endpoint_file.with_name(endpoint_file.name.replace('.endpoint.json','.stop.json'))
    try:
        item=json.loads(path.read_text(encoding='utf8'))
        if set(item)!={'parent_pid','pid','action','hmac_sha256'}:return False
        payload={k:item[k] for k in ('parent_pid','pid','action')}
        if payload!={'parent_pid':parent_pid,'pid':pid,'action':'STOP'}:return False
        expected=hmac.new(secret,json.dumps(payload,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
        return isinstance(item['hmac_sha256'],str) and hmac.compare_digest(item['hmac_sha256'],expected)
    except (OSError,ValueError,TypeError):return False


def _phase2_exam_package() -> tuple[str, Path] | None:
    packaged_candidates: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str) and frozen_root:
        packaged_candidates.append(
            Path(frozen_root).resolve() / "pr_os_phase2_m14"
        )
    packaged_candidates.append(
        Path(__file__).resolve().parents[1] / "pr_os_phase2_m14"
    )
    seen: set[Path] = set()
    for packaged in packaged_candidates:
        if packaged in seen:
            continue
        seen.add(packaged)
        if (
            (packaged / "__init__.py").is_file()
            and (packaged / "card_reviewer_exam.py").is_file()
        ):
            return "pr_os_phase2_m14", packaged
    formal = find_spec("m14_quality_sentinel")
    locations = tuple(formal.submodule_search_locations or ()) if formal else ()
    if len(locations) != 1:
        return None
    root = Path(locations[0]).resolve()
    if not (root / "card_reviewer_exam.py").is_file():
        return None
    return "m14_quality_sentinel", root


def _atomic_write(path: Path, value: dict[str, object]) -> None:
    target = windows_io_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".aw.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_settings_controller(profile_root: Path) -> SettingsController:
    from pr_os_settings.task_scheduling import TASK_CATEGORIES
    TASK_CATEGORIES.bind(profile_root)
    folders = ProfileFolderManager(profile_root)
    settings_root = folders.path_for("SETTINGS")
    workspace = folders.path_for("WORKSPACE")
    artifacts = folders.path_for("ARTIFACTS")
    workspace.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    from pr_os_settings.public_defaults import capabilities
    fixture = capabilities()
    store = SettingsStore(
        settings_root,
        default_factory=lambda: default_settings(
            workspace_root=str(workspace), artifact_root=str(artifacts)
        ),
    )
    current = store.load()["settings"]
    allowed_roots = [profile_root]
    for value in (
        current["directories"]["workspace_root"],
        current["directories"]["artifact_root"],
        current["directories"]["external_library"].get("root"),
    ):
        if value and Path(value).exists() and Path(value).is_dir():
            allowed_roots.append(Path(value))
    credentials = CredentialReferenceManager(use_windows_backend=True)
    workflow_exam_catalog = None  # New installations have no inherited exam results.
    exam_scratch = folders.path_for("MODEL_VALIDATION")
    exam_executors: tuple[object, ...] = ()
    phase2_binding = _phase2_exam_package()
    if phase2_binding is not None:
        phase2_package, phase2_root = phase2_binding
        phase2_assets = phase2_root / "assets"
        distiller_executor = Phase2CardDistillerExamExecutor(
            scratch_root=exam_scratch,
            phase2_package=phase2_package,
            reference_pack_root=(
                phase2_assets
                / "card_distiller_exam"
                / "v1"
                / "reference_pack"
            ),
            pricing_profile_path=(
                phase2_assets
                / "card_distiller_exam"
                / "v1"
                / "deepseek_flash_to_pro_profile.public.json"
            ),
        )
        reviewer_executor = Phase2CardReviewerExamSuccessorExecutor(
            scratch_root=exam_scratch,
            phase2_package=phase2_package,
            reference_pack_root=(
                phase2_assets
                / "card_reviewer_exam"
                / "v2"
                / "reference_pack"
            ),
            pricing_profile_path=(
                phase2_assets
                / "card_distiller_exam"
                / "v1"
                / "deepseek_flash_to_pro_profile.public.json"
            ),
        )
        direct_distiller_executor = Phase2NewCardDistillerLanguageBankExamExecutor(
            scratch_root=exam_scratch,
            phase2_package=phase2_package,
            reference_pack_root=(
                phase2_assets
                / "card_distiller_exam"
                / "v2"
                / "reference_pack"
            ),
            pricing_profile_path=(
                phase2_assets
                / "card_distiller_exam"
                / "v1"
                / "deepseek_flash_to_pro_profile.public.json"
            ),
        )
        analysis_executor = Phase2AnalysisExamExecutor(
            scratch_root=exam_scratch,
            phase2_package=phase2_package,
            reference_pack_root=(
                phase2_assets
                / "analysis_exam"
                / "v1"
                / "reference_pack"
            ),
            pricing_profile_path=(
                phase2_assets
                / "card_distiller_exam"
                / "v1"
                / "deepseek_flash_to_pro_profile.public.json"
            ),
        )
        embedding_executor = Phase2NewEmbeddingExamExecutor(
            scratch_root=exam_scratch,
            phase2_package=phase2_package,
            reference_pack_root=(
                phase2_assets
                / "embedding_text_exam"
                / "v3"
                / "reference_pack"
            ),
        )
        ocr_executor = Phase2OcrPageExamExecutor(
            scratch_root=exam_scratch,
            phase2_package=phase2_package,
            reference_pack_root=(
                phase2_assets
                / "ocr_page_exam"
                / "v3"
                / "reference_pack"
            ),
        )
        reranker_executor = Phase2NewRerankerTextLanguageBankExamExecutor(
            scratch_root=exam_scratch,
            phase2_package=phase2_package,
            reference_pack_root=(
                phase2_assets
                / "reranker_text_exam"
                / "v2"
                / "reference_pack"
            ),
        )
        analysis_reviewer_pack_root_text = os.environ.get(
            "PROS_ANALYSIS_REVIEWER_PACK_ROOT"
        )
        analysis_reviewer_pack_root = (
            Path(analysis_reviewer_pack_root_text).resolve()
            if analysis_reviewer_pack_root_text
            else (
                profile_root.parent
                / "formal_local_reference_packs"
                / "analysis_reviewer_a4"
            ).resolve()
        )
        analysis_reviewer_executor = (
            Phase2AnalysisReviewerExamSuccessorExecutor(
                scratch_root=exam_scratch,
                phase2_package=phase2_package,
                reference_pack_root=analysis_reviewer_pack_root,
                pricing_profile_path=(
                    phase2_assets
                    / "card_distiller_exam"
                    / "v1"
                    / "deepseek_flash_to_pro_profile.public.json"
                ),
            )
        )
        exam_executors = (
            direct_distiller_executor,
            distiller_executor,
            reviewer_executor,
            analysis_executor,
            analysis_reviewer_executor,
            embedding_executor,
            ocr_executor,
            reranker_executor,
        )

    def resolve_local_profile(profile_ref: str) -> Mapping[str, object] | None:
        try:
            registry = LocalModelProductController(
                profile_root.parent / "local_models"
            )
            projection = registry.call("local_models.list", {})
        except (OSError, ValueError):
            return None
        recognized = projection.get("recognized_models")
        if not isinstance(recognized, list):
            return None
        return next(
            (
                row
                for row in recognized
                if isinstance(row, dict) and row.get("profile_ref") == profile_ref
            ),
            None,
        )
    validation_runner = LiveModelValidationRunner(
        credential_resolver=credentials.resolve_for_validation,
        preferences_loader=lambda: store.load(recover_corruption=False)["settings"][
            "preferences"
        ],
        scratch_root=folders.path_for("MODEL_VALIDATION"),
        workflow_exam_catalog=workflow_exam_catalog,
        workflow_exam_executors=exam_executors,
        local_profile_resolver=resolve_local_profile,
    )
    controller = SettingsController(
        store=store,
        credentials=credentials,
        capabilities=CapabilityProjection(
            fixture["capabilities"], revision=fixture["revision"]
        ),
        directory_policy=DirectoryPolicy(allowed_roots),
        model_validation_runner=validation_runner,
        local_profile_resolver=resolve_local_profile,
    )
    controller.import_cached_exam_scores()
    return controller


def build_inbox_controller(
    profile_root: Path, *, include_sample_track: bool = False
) -> InboxController:
    folders = ProfileFolderManager(profile_root)
    fixture_root = Path(__file__).resolve().parents[2] / "fixtures" / "source"
    user_source_root = folders.path_for("INBOX_SOURCE")
    user_source_root.mkdir(parents=True, exist_ok=True)
    controller = InboxController(
        source_roots=(
            [fixture_root, user_source_root]
            if include_sample_track
            else [user_source_root]
        ),
        data_root=folders.path_for("INBOX"),
        maximum_bytes=64 * 1024 * 1024,
        minimum_free_bytes=256,
        include_sample_projection=include_sample_track,
    )
    if not include_sample_track:
        return controller
    fixture_paths = [
        fixture_root / "sample.pdf",
        fixture_root / "unicode" / "研究_合成样例.md",
        fixture_root / "unsupported.exe",
        fixture_root / "sample.xlsx",
        fixture_root / "sample.pptx",
        fixture_root / "basic.txt",
    ]
    expected_names = {path.name for path in fixture_paths}
    existing_by_name = {}
    for item in controller.store.list_items():
        if item.get("source_kind") not in {"fixture", "sample"}:
            continue
        if item.get("source_name") not in expected_names:
            continue
        if item.get("source_kind") == "fixture":
            item = controller.store.update_item(
                item["item_id"],
                "SAMPLE_TRACK_METADATA_MIGRATED",
                lambda row: row.update({"source_kind": "sample"}),
            )
        existing_by_name.setdefault(item["source_name"], item)

    missing_paths = [path for path in fixture_paths if path.name not in existing_by_name]
    new_item_ids = set()
    if missing_paths:
        manifest = []
        for path in missing_paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
            manifest.append(f"{path.name}:{path.stat().st_size}:{digest}")
        request_fingerprint = hashlib.sha256("\n".join(manifest).encode("utf-8")).hexdigest()[:16]
        receipt = controller.import_paths(
            missing_paths,
            request_id=f"p08-t05-part4-bootstrap-sample-{request_fingerprint}",
            source_kind="sample",
        )
        for row in receipt["items"]:
            item = row["item"]
            existing_by_name[item["source_name"]] = item
            new_item_ids.add(item["item_id"])

    items = [existing_by_name[path.name] for path in fixture_paths if path.name in existing_by_name]
    if items and items[0]["state"] in {"QUEUED", "ERROR", "PROCESSING"}:
        dispatch_key = (
            f"sample-bootstrap-processing-{items[0]['item_id']}"
            if items[0]["item_id"] in new_item_ids
            else "p08-t05-part4-bootstrap-processing-v1"
        )
        controller.dispatch(items[0]["item_id"], idempotency_key=dispatch_key)
    for item in items[1:3]:
        current = controller.store.read_item(item["item_id"])
        if current["state"] in {"QUEUED", "ERROR"} and not current.get("starred"):
            controller.set_starred(item["item_id"], True)
    return controller


def _parent_process_alive(parent_pid: int) -> bool:
    if isinstance(parent_pid, bool) or not isinstance(parent_pid, int) or parent_pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, parent_pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(parent_pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


async def weekly_external_refresh(controller, stop) -> None:
    """Application-owned catch-up loop; no standalone scheduler or inference."""
    while not stop.is_set():
        try:
            def update():
                controller.external_data_sources.enable_weekly_updates()
                return controller._leaderboard_service().refresh(automatic=True, should_stop=stop.is_set)
            await asyncio.to_thread(update)
        except (ValueError, OSError, TypeError, KeyError):
            # Existing metadata receipts and UI state expose source failures.
            # They never take down the application or its exam service.
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def run(
    profile_root: Path,
    endpoint_file: Path,
    *,
    parent_pid: int,
    build_number: int,
) -> int:
    if not _parent_process_alive(parent_pid):
        raise RuntimeError("APPLICATION_SERVICE_PARENT_NOT_LIVE")
    if isinstance(build_number, bool) or not isinstance(build_number, int) or build_number <= 0:
        raise RuntimeError("APPLICATION_SERVICE_BUILD_NUMBER_INVALID")
    rendered_secret = os.environ.get(SECRET_ENV_NAME, "")
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", rendered_secret):
        raise RuntimeError(f"{SECRET_ENV_NAME}_MISSING_OR_INVALID")
    secret = bytes.fromhex(rendered_secret)
    del rendered_secret
    shutdown_order = _configure_shutdown_order(os.environ.get('PROS_DESKTOP_PARENT_SHUTDOWN_LEVEL', ''))
    shutdown_order.update(schema_version='ApplicationServiceShutdownOrder-v1', pid=os.getpid(), parent_pid=parent_pid)
    _atomic_write(endpoint_file.with_name(endpoint_file.name.replace('.endpoint.json', '.shutdown_order.json')), shutdown_order)
    folders = ProfileFolderManager(profile_root)
    service_root = folders.path_for("SERVICE")
    store = DurableJobStore(service_root / "business.sqlite3", owner_id="p08-t05-application-service")
    # DiagnosticCore's frozen public contract expects a root locator even for
    # the SQLite successor store.  This is metadata only; it does not expose
    # the path through IPC or support projections.
    store.root = service_root
    facade = ApplicationFacade(store)
    settings_controller = build_settings_controller(profile_root)
    inbox_controller = build_inbox_controller(
        profile_root,
        include_sample_track=os.environ.get("PROS_P08_TEST_FIXTURE_MODE") == "1",
    )
    inbox_controller.job_state_provider = facade.get_job
    inbox_controller.recover()

    def phase1_workflow_snapshot() -> Mapping[str, object]:
        state = settings_controller.get_state()
        settings = state.get("settings") if isinstance(state, Mapping) else None
        revision = state.get("revision") if isinstance(state, Mapping) else None
        if not isinstance(settings, Mapping) or not isinstance(revision, int):
            raise RuntimeError("PHASE1_SETTINGS_STATE_UNAVAILABLE")
        local_projection = LocalModelProductController(
            profile_root.parent / "local_models"
        ).call("local_models.list", {})
        local_profiles = local_projection.get("recognized_models")
        if not isinstance(local_profiles, list):
            local_profiles = []
        return build_phase1_execution_snapshot(
            settings,
            settings_revision=revision,
            local_model_profiles=local_profiles,
        )

    phase1_task_controls = TaskControlStore(folders.path_for("PHASE1_TASK_CONTROLS"))
    inbox_controller.job_state_provider = lambda job_id: {
        **facade.get_job(job_id), "pause_node_id":phase1_task_controls.pause_point(job_id)}
    from pr_os_research_runtime.interactions import record_analysis
    phase1_worker = Phase1VerticalWorker(
        facade=facade,
        inbox=inbox_controller,
        profile_root=profile_root,
        workflow_snapshot_provider=phase1_workflow_snapshot,
        executor=Phase1PipelineExecutor(
            validation_runner=settings_controller.model_validation_runner,
            runtime_root=folders.path_for("PHASE1_RUNTIME"),
            task_control_store=phase1_task_controls,
            interaction_sink=lambda context,result:record_analysis(
                folders.path_for("WORKSPACE")/"research",settings_controller.research_get()["config"],context,result),
        ),
        task_control_store=phase1_task_controls,
    )
    product_adapter = CompositeServiceAdapter(
            RealFacadeAdapter(facade),
            SettingsServiceAdapter(settings_controller),
            InboxServiceAdapter(inbox_controller),
        )
    if os.environ.get("PROS_TEST_CONSOLE_AUTO_EXECUTION") == "DISABLED":
        from pr_os_test_console_bridge.service_adapter import ConsoleServiceAdapter
        product_adapter = ConsoleServiceAdapter(product_adapter, phase1_worker, os.environ["PROS_TEST_CONSOLE_SESSION_ID"])
    host = ApplicationServiceHost(product_adapter, secret=secret, host="127.0.0.1", port=0)
    await host.start()
    descriptor = {
        "schema_version": "ApplicationServiceEndpoint-v1",
        "protocol_version": "1.0",
        "host": "127.0.0.1",
        "port": host.bound_port,
        "pid": os.getpid(),
        "parent_pid": parent_pid,
        "build_number": build_number,
        "profile_root": str(profile_root),
        "secret_env_name": SECRET_ENV_NAME,
        "secret_value_recorded": False,
        "external_network_calls": 0,
        "external_model_calls": 0,
        "credential_value_reads": 0,
        "status": "READY",
    }
    _atomic_write(endpoint_file, descriptor)
    print(json.dumps({key: descriptor[key] for key in ("schema_version", "protocol_version", "host", "port", "pid", "status")}, sort_keys=True), flush=True)
    stop = asyncio.Event()

    async def monitor_parent() -> None:
        while _parent_process_alive(parent_pid):
            if _stop_requested(endpoint_file,secret,parent_pid,os.getpid()):break
            await asyncio.sleep(0.25)
        stop.set()

    parent_monitor = asyncio.create_task(monitor_parent())
    phase1_worker_task = (
        None
        if os.environ.get("PROS_P08_TEST_FIXTURE_MODE") == "1" or os.environ.get("PROS_TEST_CONSOLE_AUTO_EXECUTION") == "DISABLED"
        else asyncio.create_task(phase1_worker.run_forever(stop))
    )
    reference_worker_task = (
        None if os.environ.get("PROS_P08_TEST_FIXTURE_MODE") == "1" or os.environ.get("PROS_TEST_CONSOLE_AUTO_EXECUTION") == "DISABLED"
        else asyncio.create_task(weekly_external_refresh(settings_controller, stop))
    )

    def request_stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(event, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(event, lambda *_: loop.call_soon_threadsafe(request_stop))
    try:
        await stop.wait()
    finally:
        parent_monitor.cancel()
        if phase1_worker_task is not None:
            phase1_worker_task.cancel()
        if reference_worker_task is not None:
            reference_worker_task.cancel()
            await asyncio.gather(reference_worker_task, return_exceptions=True)
        await asyncio.gather(
            *(
                [parent_monitor, phase1_worker_task]
                if phase1_worker_task is not None
                else [parent_monitor]
            ),
            return_exceptions=True,
        )
        await host.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PR-OS local loopback application service")
    parser.add_argument("--profile-root", type=Path, required=True)
    parser.add_argument("--endpoint-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--build-number", type=int, required=True)
    arguments = parser.parse_args(argv)
    return asyncio.run(
        run(
            arguments.profile_root.resolve(),
            arguments.endpoint_file.resolve(),
            parent_pid=arguments.parent_pid,
            build_number=arguments.build_number,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
